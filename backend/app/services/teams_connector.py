"""
Proactive Teams messages through the Bot Connector REST API (outside a user's turn).

Used when LegacyLens speaks first: opening a candidate's 1:1 chat after an owner assigns them,
and posting an assessment result card to the project channel. Authenticates as the bot
(BOT_APP_ID / BOT_APP_PASSWORD). A 1:1 chat can be created with the user's Entra object id once
the app is installed for that user (graph_api.install_app_for_user). Mock: records what was sent.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional, Protocol

import httpx

from app.config import get_settings

log = logging.getLogger(__name__)

GLOBAL_SERVICE_URL = "https://smba.trafficmanager.net/teams/"


class ConnectorError(RuntimeError):
    def __init__(self, status: int, what: str):
        super().__init__(f"Teams {what} failed with HTTP {status}")
        self.status = status


class TeamsConnector(Protocol):
    async def open_personal_chat(self, user_aad_id: str, tenant_id: str, service_url: str) -> str: ...
    async def send(self, conversation_id: str, activity: dict, service_url: str) -> str: ...
    async def post_to_channel(self, channel_id: str, activity: dict, service_url: str) -> str: ...


def card_activity(card: dict, text: Optional[str] = None) -> dict:
    activity = {"type": "message", "attachments": [
        {"contentType": "application/vnd.microsoft.card.adaptive", "content": card}]}
    if text:
        activity["text"] = text
    return activity


class BotConnector:
    async def _post(self, url: str, body: dict, what: str) -> dict:
        from app.bot.roster import _bot_token

        headers = {"Authorization": f"Bearer {await _bot_token()}"}
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json=body, headers=headers)
        if r.status_code >= 400:
            log.warning("teams %s -> HTTP %s", what, r.status_code)
            raise ConnectorError(r.status_code, what)
        return r.json() if r.content else {}

    @staticmethod
    def _base(service_url: str) -> str:
        return (service_url or GLOBAL_SERVICE_URL).rstrip("/") + "/"

    async def open_personal_chat(self, user_aad_id: str, tenant_id: str, service_url: str) -> str:
        s = get_settings()
        d = await self._post(self._base(service_url) + "v3/conversations", {
            "bot": {"id": f"28:{s.bot_app_id}"},
            "members": [{"id": user_aad_id}],  # Entra object id works in personal scope
            "channelData": {"tenant": {"id": tenant_id or s.bot_app_tenant_id}},
            "isGroup": False,
        }, "open 1:1 chat")
        return d["id"]

    async def send(self, conversation_id: str, activity: dict, service_url: str) -> str:
        d = await self._post(f"{self._base(service_url)}v3/conversations/{conversation_id}/activities",
                             activity, "send message")
        return d.get("id", "")

    async def post_to_channel(self, channel_id: str, activity: dict, service_url: str) -> str:
        d = await self._post(self._base(service_url) + "v3/conversations", {
            "isGroup": True,
            "channelData": {"channel": {"id": channel_id}},
            "activity": activity,
        }, "post to channel")
        return d.get("id", "")


class MockConnector:
    def __init__(self) -> None:
        self.sent: list[tuple[str, dict]] = []        # (conversation or channel id, activity)
        self.opened: list[str] = []

    async def open_personal_chat(self, user_aad_id: str, tenant_id: str, service_url: str) -> str:
        self.opened.append(user_aad_id)
        return f"a:personal-{user_aad_id}"

    async def send(self, conversation_id: str, activity: dict, service_url: str) -> str:
        self.sent.append((conversation_id, activity))
        return f"msg-{len(self.sent)}"

    async def post_to_channel(self, channel_id: str, activity: dict, service_url: str) -> str:
        self.sent.append((channel_id, activity))
        return f"post-{len(self.sent)}"

    def reset(self) -> None:
        self.__init__()


@lru_cache
def get_connector() -> TeamsConnector:
    s = get_settings()
    if s.live_m365 and s.bot_app_id and s.bot_app_password:
        return BotConnector()
    return MockConnector()
