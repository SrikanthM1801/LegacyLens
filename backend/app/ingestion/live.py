"""
Live knowledge lifecycle: rebuild the index and graph from real sources only.

`rebuild_live_knowledge()` clears the search index, knowledge graph and SharePoint registry, then
reloads every SharePoint file and backfills every Teams conversation that granted consent. Used
once when switching off the sample data, and on every start when search is in-memory (mock mode),
because an in-memory index does not survive a restart. Audited as `knowledge.rebuilt`.
"""

from __future__ import annotations

import asyncio
import logging

from app.bot.consent import backfill
from app.graph import get_graph
from app.ingestion.pipeline import ingest_lock
from app.ingestion.sharepoint import sync_sharepoint
from app.services import audit, risk_store
from app.services.consent_store import get_consent_store
from app.services.graph_api import get_graph_api
from app.services.search import InMemorySearch, get_search
from app.services.source_registry import get_source_registry

log = logging.getLogger(__name__)

LIVE_PREFIXES = ("art_sp_", "art_teams_")


def has_non_live_knowledge() -> bool:
    """True when the graph still holds sample-data (or other non-live) artifacts."""
    return any(not a.startswith(LIVE_PREFIXES) for a in get_graph().artifact_timestamps())


def index_is_ephemeral() -> bool:
    return isinstance(get_search(), InMemorySearch)


def _clear() -> None:
    with ingest_lock:
        graph = get_graph()
        graph.clear()
        graph.save()
        get_search().reset()
        get_source_registry().reset()
    risk_store.invalidate()


async def rebuild_live_knowledge(reason: str) -> dict:
    await asyncio.to_thread(_clear)
    report = await sync_sharepoint(full=True)
    teams = 0
    graph_api = get_graph_api()
    for rec in await asyncio.to_thread(get_consent_store().list):
        if rec.status == "granted" and rec.scope_type in ("chat", "channel"):
            teams += await backfill(rec, graph_api)
    risk_store.invalidate()
    summary = {
        "reason": reason,
        "sharepoint_files": (report.added if report else 0),
        "sharepoint_failed": (report.failed if report else 0),
        "teams_messages": teams,
        "sharepoint_configured": report is not None,
    }
    audit.record("knowledge.rebuilt", source="system", actor_name="LegacyLens", details=summary)
    log.info("knowledge rebuilt from live sources: %s", summary)
    return summary
