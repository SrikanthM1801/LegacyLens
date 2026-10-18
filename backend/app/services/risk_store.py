"""
Cached risk analysis. `GET /risks` just reads this; whoever changes the graph or
the index (ingest, interview approval) calls `invalidate()` so the next read
recomputes. Computation is serialized by a lock so concurrent readers share one run.

Against real Azure a recompute takes up to a minute, and live capture invalidates on every message, so
with USE_MOCKS=false an invalidation also schedules a warm-up WARM_DELAY_S later (one at a time): the next
reader (dashboard, `@LegacyLens risks`) usually finds the cache warm instead of waiting.
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
WARM_DELAY_S = 20.0
_warm_timer: Optional[threading.Timer] = None
_warm_lock = threading.Lock()


def invalidate() -> None:
    """Drop the cache; the next `get_risks()` recomputes."""
    global _cached
    with _lock:
        _cached = None
    _schedule_warm()


def _schedule_warm() -> None:
    """Recompute in the background shortly after a change (debounced; real services only)."""
    global _warm_timer
    from app.config import get_settings

    if get_settings().use_mocks:
        return  # mock recomputes are instant, and tests stay free of background threads
    with _warm_lock:
        if _warm_timer is not None:
            return  # one is already pending; it will see this change too
        _warm_timer = threading.Timer(WARM_DELAY_S, _warm)
        _warm_timer.daemon = True
        _warm_timer.start()


def _warm() -> None:
    global _warm_timer
    with _warm_lock:
        _warm_timer = None  # a change from now on schedules another warm-up
    try:
        get_risks()
    except Exception as e:  # best effort: a reader will recompute and see the error itself
        log.warning("risk warm-up failed: %s", type(e).__name__)


def get_risks() -> RiskListResponse:
    global _cached
    with _lock:
        if _cached is None:
            items = analyze_risks(get_graph(), get_search())
            _cached = RiskListResponse(items=items, total=len(items))
            log.info("risks computed: %d topics, %d high", len(items), sum(r.risk_level == "high" for r in items))
        return _cached


def peek() -> Optional[RiskListResponse]:
    """The cached risks without ever computing (None when cold); a cold cache starts a warm-up.
    For replies that must be instant, e.g. the `@LegacyLens admin` card."""
    cached = _cached
    if cached is None and not _lock.locked():  # locked = a recompute is already running
        threading.Thread(target=_warm, daemon=True).start()
    return cached


def get_risk(risk_id: str) -> Optional[RiskItem]:
    return next((r for r in get_risks().items if r.id == risk_id), None)
