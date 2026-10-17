"""POST /ingest: (re)build the search index and knowledge graph from the sample data."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.ingestion.pipeline import IngestSourceError, ingest_lock, run_ingest
from app.models import IngestRequest, IngestResponse
from app.services import audit

router = APIRouter(tags=["ingest"])


@router.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest | None = None) -> IngestResponse:
    req = req or IngestRequest()
    if not ingest_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail="An ingest is already running")
    try:
        resp = run_ingest(req.source_dir, req.reset)
    except IngestSourceError as e:
        audit.record("ingest.completed", outcome="failure", source="api", details={"error": str(e)[:200]})
        raise HTTPException(status_code=400, detail=str(e))
    else:
        audit.record("ingest.completed", source="api", count=resp.artifacts_ingested, details={
            "chunks": resp.chunks_indexed, "entities": resp.entities_extracted, "edges": resp.edges_created,
            "redactions": resp.redactions, "reset": req.reset, "seconds": round(resp.duration_seconds, 2)})
        return resp
    finally:
        ingest_lock.release()
