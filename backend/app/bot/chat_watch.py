"""
Detect new project group chats and invite LegacyLens to ask for consent.

Graph notifies `POST /webhooks/graph` whenever a chat is created in the tenant (subscription on
`/chats`, Chat.ReadBasic.All: chat type and members only, never messages). For each new chat:
  1. skip unless it is a group chat with >= PROJECT_CHAT_MIN_MEMBERS members of a project team;
  2. skip if we already have a consent record for it (asked before);
  3. record it as `pending` + `auto_installed`, then install LegacyLens into the chat.
The install makes Teams send the bot a conversationUpdate in that chat, and the bot posts the
consent card (bot/agent.py). Nothing is read from the chat until someone clicks Allow.

`subscription_loop` keeps the 55-minute subscription alive while the server runs.
"""

from __future__ import annotations

import asyncio
import logging
import re

from app.config import get_settings
from app.models import ConsentRecord
from app.services.consent_store import ConsentStore, project_member_ids
from app.services.graph_api import GraphError, GraphService

log = logging.getLogger(__name__)

RENEW_EVERY_S = 40 * 60
_CHAT_ID = re.compile(r"chats\('([^']+)'\)")


def chat_ids_from_notification(payload: dict, client_state: str) -> list[str]:
    """Chat ids from a Graph change notification; drops items whose clientState doesn't match."""
    out = []
    for item in payload.get("value", []):
        if not client_state or item.get("clientState") != client_state:
            log.warning("graph notification with wrong clientState ignored")
            continue
        if str(item.get("changeType", "")).lower() != "created":
            continue
        chat_id = (item.get("resourceData") or {}).get("id")
        if not chat_id:
            m = _CHAT_ID.search(item.get("resource", ""))
            chat_id = m.group(1) if m else None
        if chat_id:
            out.append(chat_id)
    return out


async def handle_new_chat(chat_id: str, store: ConsentStore, graph: GraphService) -> str:
    """Returns what happened, for logs and tests: invited | not-group | not-project | known | error."""
    if store.get(chat_id):
        return "known"
    try:
        chat = await graph.get_chat(chat_id)
    except GraphError as e:
        log.warning("new chat lookup failed: %s", e)
        return "error"
    if chat.chat_type != "group":
        return "not-group"
    members = project_member_ids(store)
    on_project = [m for m in chat.member_aad_ids if m in members]
    if len(on_project) < get_settings().project_chat_min_members:
        return "not-project"
    record = store.save(ConsentRecord(
        scope_id=chat.id, scope_type="chat", display_name=chat.topic or "Project group chat",
        status="pending", auto_installed=True,
    ))
    try:
        await graph.install_app_in_chat(chat.id)
    except GraphError as e:
        log.warning("could not add LegacyLens to a project chat: %s", e)
        store.save(record.model_copy(update={"status": "declined", "granted_by": "install failed"}))
        return "error"
    log.info("project group chat detected (%d of %d members on the project); consent requested",
             len(on_project), len(chat.member_aad_ids))
    return "invited"


async def scan_existing_chats(store: ConsentStore, graph: GraphService) -> dict[str, str]:
    """Check project members' recent group chats (e.g. created before the roster was known).
    Same rules as a new chat; chats already asked are skipped, so it is safe to re-run."""
    results: dict[str, str] = {}
    for member in sorted(project_member_ids(store)):
        try:
            chat_ids = await graph.recent_group_chat_ids(member)
        except GraphError as e:
            log.warning("could not list a project member's chats: %s", e)
            continue
        for chat_id in chat_ids:
            if chat_id not in results:
                results[chat_id] = await handle_new_chat(chat_id, store, graph)
    return results


async def subscription_loop(graph: GraphService) -> None:
    s = get_settings()
    url = s.public_base_url.rstrip("/") + "/webhooks/graph"
    await asyncio.sleep(5)  # let the server accept Graph's validation request first
    while True:
        try:
            await graph.ensure_chat_subscription(url, s.graph_webhook_client_state)
            log.info("graph chat subscription active")
        except GraphError as e:
            log.warning("graph chat subscription failed: %s", e)
        await asyncio.sleep(RENEW_EVERY_S)
