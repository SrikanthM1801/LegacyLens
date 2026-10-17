"""LegacyLens FastAPI app: CORS, router registration, /health."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware

from app.api import artifacts, ask, audit, bot, graph, ingest, interviews, people, risks, webhooks
from app.bot.chat_watch import subscription_loop
from app.config import VERSION, get_settings
from app.graph import get_graph
from app.ingestion.pipeline import ensure_index_ready
from app.models import HealthResponse
from app.services import audit as audit_log
from app.services import risk_store
from app.services.graph_api import get_graph_api

log = logging.getLogger(__name__)


def _app_logging() -> None:
    """Show LegacyLens INFO logs (roster, consent, capture) next to uvicorn's, without the
    Azure SDKs' chatty request logs (those stay at the root logger's WARNING)."""
    app_log = logging.getLogger("app")
    if not app_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
        app_log.addHandler(handler)
        app_log.setLevel(logging.INFO)
        app_log.propagate = False


_app_logging()


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
    watcher = _start_chat_watch()
    audit_log.record("system.started", details={"version": VERSION, "live_m365": get_settings().live_m365})
    yield
    if watcher:
        watcher.cancel()
    audit_log.get_audit_log().flush()


def _start_chat_watch() -> Optional[asyncio.Task]:
    """Keep the Graph 'new chat' subscription alive when live Microsoft 365 is configured."""
    s = get_settings()
    if not s.live_m365:
        return None
    if not (s.public_base_url and s.graph_webhook_client_state):
        log.warning("LIVE_M365=true but PUBLIC_BASE_URL or GRAPH_WEBHOOK_CLIENT_STATE is missing; "
                    "new project chats will not be detected")
        return None
    return asyncio.create_task(subscription_loop(get_graph_api()))


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
    app.include_router(bot.router)
    app.include_router(webhooks.router)
    app.include_router(audit.router)
    return app


app = create_app()
