"""GET /audit: the audit trail for the admin page (consent, detection, reads, answers, publishes)."""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.config import get_settings
from app.models import AuditListResponse
from app.services import audit

router = APIRouter(tags=["audit"])


def _require_admin(key: Optional[str]) -> None:
    """X-Admin-Key must match ADMIN_API_KEY. Without a configured key the trail is only served
    offline (LIVE_M365=false), so real audit data is never exposed unauthenticated."""
    expected = get_settings().admin_api_key
    if expected:
        if not key or not secrets.compare_digest(key, expected):
            raise HTTPException(status_code=401, detail="Missing or wrong X-Admin-Key")
    elif get_settings().live_m365:
        raise HTTPException(status_code=403, detail="Set ADMIN_API_KEY to read the audit trail")


@router.get("/audit", response_model=AuditListResponse)
def list_audit(
    since: Optional[datetime] = Query(None, description="Start (ISO 8601). Default: 7 days ago"),
    until: Optional[datetime] = Query(None, description="End (ISO 8601). Default: now"),
    action: Optional[str] = Query(None, description="Exact action or category, e.g. consent or capture.backfill"),
    scope_id: Optional[str] = Query(None, description="One conversation / site / mailbox / interview"),
    actor: Optional[str] = Query(None, description="Name contains, or exact Entra id"),
    outcome: Optional[Literal["success", "failure", "skipped"]] = None,
    limit: int = Query(200, ge=1, le=1000),
    x_admin_key: Optional[str] = Header(None),
) -> AuditListResponse:
    _require_admin(x_admin_key)
    if since and until and since > until:
        raise HTTPException(status_code=422, detail="'since' must be before 'until'")
    items, total, actions = audit.query(
        since, until, action=action, scope_id=scope_id, actor=actor, outcome=outcome, limit=limit)
    return AuditListResponse(items=items, total=total, actions=actions)
