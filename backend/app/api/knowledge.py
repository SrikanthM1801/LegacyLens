"""Knowledge sources for the admin page: which files are loaded from where; trigger a sync or rebuild."""

from __future__ import annotations

from collections import Counter
from typing import Literal, Optional

from fastapi import APIRouter, Header, HTTPException, Query

from app.api.audit import require_admin
from app.config import get_settings
from app.ingestion.live import rebuild_live_knowledge
from app.ingestion.sharepoint import sync_sharepoint
from app.models import SourceListResponse, SourceSyncReport
from app.services.source_registry import get_source_registry

router = APIRouter(tags=["knowledge"])


@router.get("/knowledge/sources", response_model=SourceListResponse)
def list_sources(
    status: Optional[Literal["loaded", "failed", "unsupported", "deleted", "empty"]] = None,
    x_admin_key: Optional[str] = Header(None),
) -> SourceListResponse:
    require_admin(x_admin_key)
    registry = get_source_registry()
    files = registry.list()
    by_status = dict(Counter(f.status for f in files))
    items = sorted((f for f in files if status is None or f.status == status), key=lambda f: (f.path, f.name))
    return SourceListResponse(items=items, total=len(items), by_status=by_status, last_sync=registry.last_sync())


@router.post("/knowledge/sync", response_model=SourceSyncReport)
async def sync_now(full: bool = Query(False, description="Re-read every file instead of only changes"),
                   x_admin_key: Optional[str] = Header(None)) -> SourceSyncReport:
    require_admin(x_admin_key)
    report = await sync_sharepoint(full=full)
    if report is None:
        raise HTTPException(status_code=409, detail="SHAREPOINT_SITE_URL is not configured")
    return report


@router.post("/knowledge/rebuild")
async def rebuild(x_admin_key: Optional[str] = Header(None)) -> dict:
    """Clear the index and graph, then reload SharePoint and consented Teams conversations."""
    require_admin(x_admin_key)
    if get_settings().use_sample_data:
        raise HTTPException(status_code=409, detail="USE_SAMPLE_DATA is on; set it to false to rebuild from live sources")
    return await rebuild_live_knowledge("admin request")
