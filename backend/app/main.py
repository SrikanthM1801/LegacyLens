"""LegacyLens FastAPI app: CORS, router registration, /health."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api import artifacts, ask, graph, ingest, interviews, people, risks
from app.config import VERSION, get_settings
from app.graph import get_graph
from app.ingestion.pipeline import ensure_index_ready
from app.models import HealthResponse
from app.services import risk_store

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Mock search lives in memory; rebuild it from the sample data on boot.
    try:
        resp = await run_in_threadpool(ensure_index_ready)
        if resp:
            log.info("startup ingest: %d artifacts, %d chunks", resp.artifacts_ingested, resp.chunks_indexed)
        if get_graph().stats().node_count:
            await run_in_threadpool(risk_store.get_risks)  # warm the cache so the first GET /risks is instant
    except Exception:  # a bad dataset must not stop the API from booting
        log.exception("startup ingest failed; call POST /ingest to retry")
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegacyLens", version=VERSION, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        stats = get_graph().stats()
        artifacts = sum(n for k, n in stats.nodes_by_type.items() if k.startswith("artifact:"))
        return HealthResponse(
            use_mocks=settings.use_mocks, version=VERSION, artifacts_indexed=artifacts, graph=stats
        )

    app.include_router(ingest.router)
    app.include_router(graph.router)
    app.include_router(people.router)
    app.include_router(risks.router)
    app.include_router(interviews.router)
    app.include_router(artifacts.router)
    app.include_router(ask.router)
    return app


app = create_app()
