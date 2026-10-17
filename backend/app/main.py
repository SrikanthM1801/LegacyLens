"""LegacyLens FastAPI app: CORS, router registration, /health."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api import artifacts, ask, audit, bot, dashboard, graph, ingest, interviews, knowledge, people, risks, webhooks
from app.bot.chat_watch import subscription_loop
from app.config import VERSION, get_settings
from app.graph import get_graph
from app.ingestion.pipeline import ensure_index_ready
from app.models import HealthResponse
from app.services import audit as audit_log
from app.services.audit_demo import seed_demo
from app.services.consent_store import get_consent_store
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
    s = get_settings()
    if s.use_sample_data:
        # Mock search lives in memory; rebuild it from the sample data on boot.
        try:
            resp = await run_in_threadpool(ensure_index_ready)
            if resp:
                log.info("startup ingest: %d artifacts, %d chunks", resp.artifacts_ingested, resp.chunks_indexed)
            if get_graph().stats().node_count:
                await run_in_threadpool(risk_store.get_risks)  # warm the cache so the first GET /risks is instant
        except Exception:  # a bad dataset must not stop the API from booting
            log.exception("startup ingest failed; call POST /ingest to retry")
    _seed_dashboard_demo()
    tasks = [t for t in (_start_chat_watch(), _start_live_sources()) if t]
    audit_log.record("system.started", details={
        "version": VERSION, "live_m365": s.live_m365, "sample_data": s.use_sample_data,
        "sharepoint": bool(s.sharepoint_site_url)})
    yield
    for t in tasks:
        t.cancel()
    audit_log.get_audit_log().flush()


def _start_live_sources() -> Optional[asyncio.Task]:
    """Live knowledge only: rebuild from real sources when needed, then keep SharePoint in sync."""
    s = get_settings()
    if s.use_sample_data:
        return None
    if not s.live_m365:
        log.warning("USE_SAMPLE_DATA=false but LIVE_M365=false: the knowledge base stays empty")
        return None

    async def run() -> None:
        from app.ingestion.live import has_non_live_knowledge, index_is_ephemeral, rebuild_live_knowledge
        from app.ingestion.sharepoint import sharepoint_loop

        if index_is_ephemeral() or await run_in_threadpool(has_non_live_knowledge):
            await asyncio.sleep(3)
            reason = "in-memory index (USE_MOCKS=true) starts empty" if index_is_ephemeral() else "sample data removed"
            await rebuild_live_knowledge(reason)
        if s.sharepoint_site_url:
            await sharepoint_loop()
        else:
            log.warning("SHAREPOINT_SITE_URL is not set; SharePoint files will not be loaded")

    return asyncio.create_task(run())


def _seed_dashboard_demo() -> None:
    """DASHBOARD_DEMO=true (offline only): give the dashboard three weeks of generated activity."""
    s = get_settings()
    if not s.dashboard_demo or s.live_m365:
        return
    try:
        n = seed_demo(audit_log.get_audit_log(), get_consent_store())
        log.info("dashboard demo data loaded: %d audit events", n)
    except Exception:
        log.exception("could not load dashboard demo data")


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
    if (settings.ui_dist_dir / "index.html").is_file():  # the dashboard, same origin as the API
        app.mount("/app", StaticFiles(directory=settings.ui_dist_dir, html=True), name="dashboard-ui")
    app.include_router(audit.router)
    app.include_router(knowledge.router)
    app.include_router(dashboard.router)
    return app


app = create_app()
