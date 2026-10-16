"""LegacyLens FastAPI app: CORS, router registration, /health."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import VERSION, get_settings
from app.models import GraphStats, HealthResponse
from app.services.search import get_search


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LegacyLens", version=VERSION)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", response_model=HealthResponse, tags=["meta"])
    def health() -> HealthResponse:
        return HealthResponse(
            use_mocks=settings.use_mocks,
            version=VERSION,
            artifacts_indexed=get_search().count(),
            graph=GraphStats(),  # filled in once the graph store exists
        )

    # Routers are registered here as modules land: ingest, risks, interviews,
    # artifacts, ask, graph, people (see Plan.md section 12).
    return app


app = create_app()
