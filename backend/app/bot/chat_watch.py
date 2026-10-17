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
from typing import Optional

from app.config import get_settings
from app.models import ConsentRecord
from app.services import audit
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
            audit.record("security.webhook_rejected", outcome="failure", source="graph",
                         details={"reason": "clientState mismatch"})
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


def matching_project(store: ConsentStore, member_aad_ids: list[str]) -> Optional[str]:
    """team_id of the project whose roster overlaps this chat the most (ties: highest team id, for determinism)."""
    ids = set(member_aad_ids)
    overlaps = [(len(ids & set(p.member_aad_ids)), p.team_id) for p in store.projects()]
    overlaps = [o for o in overlaps if o[0]]
    return max(overlaps)[1] if overlaps else None


async def handle_new_chat(chat_id: str, store: ConsentStore, graph: GraphService) -> str:
    """Returns what happened, for logs and tests: invited | not-group | not-project | known | error."""
    if store.get(chat_id):
        return "known"
    try:
        chat = await graph.get_chat(chat_id)
    except GraphError as e:
        log.warning("new chat lookup failed: %s", e)
        audit.record("detection.lookup_failed", outcome="failure", source="graph", scope_type="chat",
                     scope_id=chat_id, details={"error": f"HTTP {e.status}"})
        return "error"
    if chat.chat_type != "group":
        return "not-group"
    members = project_member_ids(store)
    on_project = [m for m in chat.member_aad_ids if m in members]
    if len(on_project) < get_settings().project_chat_min_members:
        return "not-project"
    record = store.save(ConsentRecord(
        scope_id=chat.id, scope_type="chat", display_name=chat.topic or "Project group chat",
        status="pending", auto_installed=True, project_id=matching_project(store, chat.member_aad_ids),
    ))
    try:
        await graph.install_app_in_chat(chat.id)
    except GraphError as e:
        log.warning("could not add LegacyLens to a project chat: %s", e)
        store.save(record.model_copy(update={"status": "declined", "granted_by": "install failed"}))
        audit.record("detection.install_failed", outcome="failure", source="graph", actor_name="LegacyLens",
                     scope_type="chat", scope_id=chat.id, scope_name=chat.topic or None,
                     details={"error": f"HTTP {e.status}"})
        return "error"
    log.info("project group chat detected (%d of %d members on the project); consent requested",
             len(on_project), len(chat.member_aad_ids))
    audit.record("detection.chat_invited", source="graph", actor_name="LegacyLens", scope_type="chat",
                 scope_id=chat.id, scope_name=chat.topic or None, count=len(chat.member_aad_ids),
                 details={"members": len(chat.member_aad_ids), "project_members": len(on_project),
                          "member_names": sorted(n for n in chat.member_names.values() if n)})
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
    from app.bot.roster import rebuild_rosters
    from app.services.consent_store import get_consent_store
    try:
        await rebuild_rosters(get_consent_store())  # roster ready even before anyone types in the channel
    except Exception as e:
        log.warning("startup roster load failed: %s", type(e).__name__)
    active = False
    while True:
        try:
            await graph.ensure_chat_subscription(url, s.graph_webhook_client_state)
            log.info("graph chat subscription active")
            if not active:
                audit.record("system.subscription_active", source="graph", details={"resource": "/chats"})
            active = True
        except GraphError as e:
            log.warning("graph chat subscription failed: %s", e)
            audit.record("system.subscription_failed", outcome="failure", source="graph",
                         details={"resource": "/chats", "error": f"HTTP {e.status}"})
            active = False
        await asyncio.sleep(RENEW_EVERY_S)
