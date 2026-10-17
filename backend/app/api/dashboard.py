"""Dashboard endpoints: org -> account -> project views over the audit trail and the consent register.

Every number comes from the audit trail (Azure Blob) and the consent register (Azure Tables), nothing
else: not the knowledge base, not the sample dataset, not a mock. With LIVE_M365=true the API refuses to
answer unless both are real Azure backends, and says why when it cannot read them; it never falls back to
in-memory or demo data. Offline (LIVE_M365=false) it reads this process's in-memory trail, or generated demo
data when DASHBOARD_DEMO=true, and the response says which (`data_source`).

All routes need the admin key (X-Admin-Key), like GET /audit. Summaries are counts; the event view replaces
names with roles; only /dashboard/consents names the person who clicked Allow.
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Literal, Optional, TypeVar

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.audit import require_admin
from app.config import get_settings
from app.models import (
    AuditEvent, ConsentRecord, ConsentRegisterRow, DashboardEventsResponse, DashboardSummary, HierarchyNode,
    ProjectTeam,
)
from app.services import audit, dashboard
from app.services.audit import BlobAuditLog
from app.services.audit_demo import DEMO_ACCOUNTS
from app.services.consent_store import TableConsentStore, get_consent_store

log = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])

Level = Literal["org", "account", "project"]
T = TypeVar("T")
CACHE_TTL_S = 30.0
_cache: dict[tuple, tuple[float, object]] = {}
_cache_lock = threading.Lock()


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _cached(key: tuple, build: Callable[[], T]) -> T:
    """Reading a month of audit blobs is slow, so identical requests within CACHE_TTL_S share one read.
    Failures are never cached."""
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and hit[0] > now:
            return hit[1]  # type: ignore[return-value]
    value = build()
    with _cache_lock:
        _cache[key] = (now + CACHE_TTL_S, value)
    return value


# ---------------------------------------------------------------------------
# Where the data comes from
# ---------------------------------------------------------------------------


def data_source() -> str:
    """"azure" | "memory" | "demo". In live mode anything but real Azure backends is refused (503), so a live
    dashboard can never show in-memory or demo data by accident."""
    s = get_settings()
    if s.live_m365:
        if not isinstance(audit.get_audit_log(), BlobAuditLog):
            raise HTTPException(status_code=503, detail=(
                "LIVE_M365 is on but the audit trail is not configured: set AZURE_STORAGE_ACCOUNT_URL. "
                "The dashboard never shows in-memory or demo data in live mode."))
        if not isinstance(get_consent_store(), TableConsentStore):
            raise HTTPException(status_code=503, detail=(
                "LIVE_M365 is on but the consent register is not configured: set AZURE_TABLES_ACCOUNT_URL. "
                "The dashboard never shows in-memory or demo data in live mode."))
        return "azure"
    return "demo" if s.dashboard_demo else "memory"


def _read(what: str, source: str, fn: Callable[[], T]) -> T:
    """Run a storage read; a failure against Azure becomes a clear 503 instead of a bare 500."""
    try:
        return fn()
    except HTTPException:
        raise
    except Exception as e:
        log.warning("dashboard: could not read %s (%s)", what, type(e).__name__)
        if source != "azure":
            raise
        raise HTTPException(status_code=503, detail=(
            f"Could not read the {what} from Azure ({type(e).__name__}). Check you are signed in "
            "(az login) and have Storage Blob/Table Data Reader on the storage account.")) from e


def _window(since: Optional[datetime], until: Optional[datetime]) -> tuple[datetime, datetime]:
    until = (until or datetime.now(timezone.utc))
    until = until.replace(tzinfo=timezone.utc) if until.tzinfo is None else until.astimezone(timezone.utc)
    since = since or until - timedelta(days=30)
    since = since.replace(tzinfo=timezone.utc) if since.tzinfo is None else since.astimezone(timezone.utc)
    if since > until:
        raise HTTPException(status_code=422, detail="'since' must be before 'until'")
    return max(since, until - timedelta(days=audit.MAX_QUERY_DAYS)), until


def _load(source: str) -> tuple[dashboard.Org, list[ConsentRecord]]:
    """The hierarchy and consent register, from the consent tables."""
    s = get_settings()

    def build() -> tuple[list[ConsentRecord], list[ProjectTeam]]:
        store = get_consent_store()
        return store.list(), store.projects()

    records, projects = _cached(("consents", source), lambda: _read("consent register", source, build))
    mapping = s.account_by_project or (DEMO_ACCOUNTS if source == "demo" else None)
    return dashboard.build_org(records, projects, s, mapping=mapping), records


def _events(since: datetime, until: datetime, source: str) -> list[AuditEvent]:
    # Round to the minute so repeated calls with a moving "now" share a cache entry.
    key = ("events", source, since.replace(second=0, microsecond=0), until.replace(second=0, microsecond=0))
    return _cached(key, lambda: _read("audit trail", source, lambda: audit.get_audit_log().query(since, until)))


def _resolve(org: dashboard.Org, level: str, node_id: str) -> HierarchyNode:
    node = org.node(level, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Unknown {level} '{node_id}'")
    return node


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/hierarchy", response_model=HierarchyNode)
def hierarchy(x_admin_key: Optional[str] = Header(None)) -> HierarchyNode:
    """Org -> accounts -> projects, for the scope selector."""
    require_admin(x_admin_key)
    return _load(data_source())[0].root


@router.get("/summary", response_model=DashboardSummary)
def summary(
    level: Level = "org", id: str = Query(dashboard.ORG_ID, description="Node id from /dashboard/hierarchy"),
    since: Optional[datetime] = Query(None, description="Default: 30 days before 'until' (max 31 days)"),
    until: Optional[datetime] = Query(None, description="Default: now"),
    x_admin_key: Optional[str] = Header(None),
) -> DashboardSummary:
    require_admin(x_admin_key)
    source = data_source()
    since, until = _window(since, until)
    org, records = _load(source)
    _resolve(org, level, id)
    return dashboard.summarize(org, level, id, _events(since, until, source), records, since, until, source=source)


@router.get("/events", response_model=DashboardEventsResponse)
def events(
    level: Level = "org", id: str = dashboard.ORG_ID, since: Optional[datetime] = None, until: Optional[datetime] = None,
    category: Optional[str] = Query(None, description="e.g. consent, capture, ask, interview"),
    action: Optional[str] = None, outcome: Optional[Literal["success", "failure", "skipped"]] = None,
    limit: int = Query(100, ge=1, le=dashboard.MAX_EVENTS), x_admin_key: Optional[str] = Header(None),
) -> DashboardEventsResponse:
    """The audit trail without people: roles instead of names, no authors or message ids."""
    require_admin(x_admin_key)
    source = data_source()
    since, until = _window(since, until)
    org, _ = _load(source)
    _resolve(org, level, id)
    hits = dashboard.filter_events(org, level, id, _events(since, until, source), since, until)
    actions = sorted({e.action for e in hits})
    hits = [e for e in hits if (not category or e.category == category) and (not action or e.action == action)
            and (not outcome or e.outcome == outcome)]
    hits.sort(key=lambda e: e.timestamp, reverse=True)
    return DashboardEventsResponse(items=[dashboard.public_event(e) for e in hits[:limit]], total=len(hits), actions=actions)


@router.get("/consents", response_model=list[ConsentRegisterRow])
def consents(level: Level = "org", id: str = dashboard.ORG_ID, x_admin_key: Optional[str] = Header(None)) -> list[ConsentRegisterRow]:
    """The consent register: every conversation LegacyLens knows about, its status and who decided."""
    require_admin(x_admin_key)
    org, records = _load(data_source())
    _resolve(org, level, id)
    return dashboard.consent_register(org, level, id, records)
