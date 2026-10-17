"""
Microsoft 365 Agents SDK wiring for the LegacyLens Teams bot.

Azure Bot Service posts every Teams activity to `POST /api/messages` (api/bot.py). This module
builds the SDK pieces once: an MSAL connection for the single-tenant bot identity (BOT_APP_ID /
BOT_APP_PASSWORD / BOT_APP_TENANT_ID), a CloudAdapter that sends replies back through Bot
Service, and an AgentApplication whose handlers only strip the @mention and call `replies.py`.

We use hosting-core directly with a ~20-line FastAPI shim (`process`) rather than
microsoft-agents-hosting-fastapi, because that package requires Starlette 1.x and our FastAPI
pin ships Starlette 0.41. With no BOT_APP_ID set (local dev, tests) the bot runs anonymously,
which only works for callers that read replies from the HTTP response (deliveryMode
"expectReplies") such as the Agents Playground or our tests.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    HttpAdapterBase,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.core.authorization.jwt import _authorize_request

from app.bot import replies
from app.config import Settings, get_settings

log = logging.getLogger(__name__)


class _FastApiRequest:
    """Adapts a FastAPI Request to the SDK's HttpRequestProtocol."""

    def __init__(self, request: Request, claims_identity: Any):
        self._request = request
        self._claims = claims_identity

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def headers(self):
        return self._request.headers

    async def json(self):
        return await self._request.json()

    def get_claims_identity(self):
        return self._claims

    def get_path_param(self, name: str) -> str:
        return self._request.path_params.get(name, "")


class CloudAdapter(HttpAdapterBase):
    """HttpAdapterBase bound to FastAPI request/response types."""


@dataclass
class Bot:
    app: AgentApplication
    adapter: CloudAdapter
    auth_config: AgentAuthConfiguration

    async def process(self, request: Request) -> Response:
        """Validate the Bot Service JWT, run the turn, return the SDK's HTTP response."""
        identity = await _authorize_request(request.headers.get("Authorization"), self.auth_config)
        if not hasattr(identity, "get_claim_value"):  # an HttpResponse describing the auth failure
            return JSONResponse(content=identity.body, status_code=identity.status_code)
        resp = await self.adapter.process_request(_FastApiRequest(request, identity), self.app)
        if resp.body is not None:
            return JSONResponse(content=resp.body, status_code=resp.status_code, headers=resp.headers)
        return Response(status_code=resp.status_code, headers=resp.headers)


def _sdk_config(s: Settings) -> dict:
    """The nested dict `load_configuration_from_env` would build from CONNECTIONS__... env vars."""
    if not s.bot_app_id:
        settings: dict[str, Any] = {"ANONYMOUS_ALLOWED": True}
    else:
        settings = {
            "AUTHTYPE": "ClientSecret",
            "CLIENTID": s.bot_app_id,
            "CLIENTSECRET": s.bot_app_password,
            "TENANTID": s.bot_app_tenant_id,
        }
    return {"CONNECTIONS": {"SERVICE_CONNECTION": {"SETTINGS": settings}}, "CONNECTIONSMAP": [], "AGENTAPPLICATION": {}}


def build_bot(s: Optional[Settings] = None) -> Bot:
    s = s or get_settings()
    config = _sdk_config(s)
    connections = MsalConnectionManager(**config)
    storage = MemoryStorage()
    app = AgentApplication[TurnState](storage=storage, connection_manager=connections, **config)

    @app.conversation_update("membersAdded")
    async def on_members_added(context: TurnContext, _state: TurnState):
        bot_id = context.activity.recipient.id if context.activity.recipient else None
        if any(m.id == bot_id for m in context.activity.members_added or []):
            await context.send_activity(replies.WELCOME)

    @app.activity("message")
    async def on_message(context: TurnContext, _state: TurnState):
        text = TurnContext.remove_recipient_mention(context.activity) or ""
        try:
            reply = await asyncio.to_thread(replies.reply_for, text)
        except Exception:  # never leave the user without an answer; details stay in our logs
            log.exception("bot reply failed")
            reply = "Sorry, something went wrong while answering. Please try again in a moment."
        await context.send_activity(reply)

    @app.error
    async def on_error(context: TurnContext, error: Exception):
        log.error("bot turn error: %s", type(error).__name__)

    adapter = CloudAdapter(connection_manager=connections)
    return Bot(app=app, adapter=adapter, auth_config=connections.get_default_connection_configuration())


@lru_cache
def get_bot() -> Bot:
    return build_bot()
