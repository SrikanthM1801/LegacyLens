"""
Cached risk analysis. `GET /risks` just reads this; whoever changes the graph or
the index (ingest, interview approval) calls `invalidate()` so the next read
recomputes. Computation is serialized by a lock so concurrent readers share one run.
"""

from __future__ import annotations

import logging
import threading
from typing import Optional

from app.agents.gap_analyzer import analyze_risks
from app.graph import get_graph
from app.models import RiskItem, RiskListResponse
from app.services.search import get_search

log = logging.getLogger(__name__)

_lock = threading.Lock()
_cached: Optional[RiskListResponse] = None


def invalidate() -> None:
    """Drop the cache; the next `get_risks()` recomputes."""
    global _cached
    with _lock:
        _cached = None


def get_risks() -> RiskListResponse:
    global _cached
    with _lock:
        if _cached is None:
            items = analyze_risks(get_graph(), get_search())
            _cached = RiskListResponse(items=items, total=len(items))
            log.info("risks computed: %d topics, %d high", len(items), sum(r.risk_level == "high" for r in items))
        return _cached


def get_risk(risk_id: str) -> Optional[RiskItem]:
    return next((r for r in get_risks().items if r.id == risk_id), None)
