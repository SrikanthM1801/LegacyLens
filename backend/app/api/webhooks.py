"""POST /webhooks/graph: Microsoft Graph change notifications (new chats in the tenant)."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Request, Response
from fastapi.responses import PlainTextResponse

from app.bot.chat_watch import chat_ids_from_notification, handle_new_chat
from app.config import get_settings
from app.services.consent_store import get_consent_store
from app.services.graph_api import get_graph_api

log = logging.getLogger(__name__)
router = APIRouter(tags=["webhooks"])


async def _process(chat_ids: list[str]) -> None:
    store, graph = get_consent_store(), get_graph_api()
    for chat_id in chat_ids:
        await handle_new_chat(chat_id, store, graph)


@router.post("/webhooks/graph")
async def graph_notifications(request: Request, background: BackgroundTasks,
                              validationToken: Optional[str] = None) -> Response:
    # Subscription validation: echo the token as text/plain within 10 seconds.
    if validationToken is not None:
        return PlainTextResponse(validationToken)
    try:
        payload = await request.json()
    except ValueError:
        return Response(status_code=400)
    chat_ids = chat_ids_from_notification(payload, get_settings().graph_webhook_client_state)
    if chat_ids:
        background.add_task(_process, chat_ids)
    return Response(status_code=202)  # acknowledge fast; Graph retries slow endpoints
