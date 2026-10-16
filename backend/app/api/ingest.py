"""POST /ingest: (re)build the search index and knowledge graph from the sample data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.ingestion.pipeline import IngestSourceError, ingest_lock, run_ingest
from app.models import IngestRequest, IngestResponse

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest | None = None) -> IngestResponse:
    req = req or IngestRequest()
    if not ingest_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ingest is already running")
    try:
        return run_ingest(req.source_dir, req.reset)
    except IngestSourceError as e:
        raise HTTPException(status_code=400, detail=str(e))
    finally:
        ingest_lock.release()
