"""
Load the project roster without waiting for a Teams message.

The bot normally refreshes the roster during a turn (bot/agent.py). This calls the same Bot
Framework roster API directly with the bot's own credentials, for any channel we have a consent
record for, so scripts and startup can rebuild it. Teams returns each member's Entra id as
`objectId`. No Graph permission is needed.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from app.config import get_settings
from app.models import ProjectTeam
from app.services import audit
from app.services.consent_store import ConsentStore

log = logging.getLogger(__name__)

#: Global Bot Connector endpoint for Teams; regional ones redirect from here.
SERVICE_URL = "https://smba.trafficmanager.net/teams/"


async def _bot_token() -> str:
    import msal

    s = get_settings()
    app = msal.ConfidentialClientApplication(
        s.bot_app_id, client_credential=s.bot_app_password,
        authority=f"https://login.microsoftonline.com/{s.bot_app_tenant_id}",
    )
    result = await asyncio.to_thread(app.acquire_token_for_client, scopes=["https://api.botframework.com/.default"])
    if "access_token" not in result:
        raise RuntimeError(f"bot token failed: {result.get('error')}")
    return result["access_token"]


async def fetch_member_ids(team_conversation_id: str) -> list[str]:
    headers = {"Authorization": f"Bearer {await _bot_token()}"}
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(f"{SERVICE_URL}v3/conversations/{team_conversation_id}/members", headers=headers)
    r.raise_for_status()
    return [m.get("aadObjectId") or m.get("objectId") for m in r.json() if m.get("aadObjectId") or m.get("objectId")]


async def rebuild_rosters(store: ConsentStore) -> int:
    """Refresh the roster for every channel LegacyLens was added to. Returns members found."""
    known = {p.team_id: p for p in store.projects()}
    total = 0
    for rec in store.list():
        if rec.scope_type != "channel":
            continue
        try:
            members = await fetch_member_ids(rec.scope_id)
        except Exception as e:  # keep going; one bad channel must not block the rest
            log.warning("roster load failed for a channel: %s", type(e).__name__)
            continue
        prev = known.get(rec.scope_id)
        store.save_project(ProjectTeam(
            team_id=rec.scope_id, aad_group_id=rec.team_id or (prev.aad_group_id if prev else None),
            name=rec.display_name or (prev.name if prev else ""), member_aad_ids=members,
        ))
        total += len(members)
        log.info("project roster loaded: %d members", len(members))
        if prev is None or set(prev.member_aad_ids) != set(members):
            audit.record("roster.updated", source="teams", scope_type="team", scope_id=rec.scope_id,
                         scope_name=rec.display_name or None, count=len(members), details={"trigger": "reload"})
    return total
