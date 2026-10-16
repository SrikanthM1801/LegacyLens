"""
The ingestion pipeline: load -> redact -> chunk -> embed -> index, and extract -> graph.

`run_ingest` is the full run behind `POST /ingest` and `scripts/ingest.py`.
`index_artifacts` is the reusable core; the interview flow (T20) calls it with
a single new Artifact(type=interview) so answers join the same index and graph.
NOTE: graph edges accumulate, so incremental calls must be for NEW artifacts.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Optional

from app.config import REPO_ROOT, get_settings
from app.graph import get_graph
from app.graph.store import GraphStore
from app.ingestion.cache import JsonCache, content_key
from app.ingestion.chunker import chunk_artifacts
from app.ingestion.extractor import Extractor, get_extractor
from app.ingestion.loaders import load_dataset
from app.ingestion.redactor import redact_artifact
from app.models import Artifact, Chunk, IngestResponse, Person
from app.services.llm import LLMService, get_llm
from app.services.search import SearchService, get_search

log = logging.getLogger(__name__)

#: Only one ingest at a time (it resets shared state).
ingest_lock = threading.Lock()


class IngestSourceError(ValueError):
    """The requested source directory is not usable."""


def resolve_source_dir(source_dir: Optional[str]) -> Path:
    """Default to settings.data_dir; explicit paths must live under <repo>/data."""
    if not source_dir:
        return get_settings().data_dir
    path = Path(source_dir)
    path = path if path.is_absolute() else REPO_ROOT / path
    path = path.resolve()
    allowed = (REPO_ROOT / "data").resolve()
    if allowed != path and allowed not in path.parents:
        raise IngestSourceError(f"source_dir must be inside {allowed}")
    if not path.is_dir():
        raise IngestSourceError(f"source_dir '{source_dir}' is not a directory")
    return path


def _embed_chunks(chunks: list[Chunk], llm: LLMService) -> None:
    """Attach embeddings, re-using cached vectors (real Azure only: it is rate limited)."""
    settings = get_settings()
    if settings.use_mocks:
        return  # the mock search embeds cheaply itself
    cache = JsonCache(settings.state_dir / "embeddings.json")
    keys = [content_key(settings.azure_openai_embedding_deployment, c.text) for c in chunks]
    todo = [i for i, k in enumerate(keys) if cache.get(k) is None]
    if todo:
        for i, emb in zip(todo, llm.embed([chunks[i].text for i in todo])):
            cache.set(keys[i], emb)
        cache.save()
    for c, k in zip(chunks, keys):
        c.embedding = cache.get(k)
    log.info("embeddings: %d cached, %d computed", len(chunks) - len(todo), len(todo))


def index_artifacts(
    artifacts: list[Artifact],
    people: list[Person],
    *,
    search: SearchService,
    graph: GraphStore,
    extractor: Extractor,
    llm: LLMService,
) -> tuple[IngestResponse, Counter]:
    """Redact, chunk, embed and index artifacts, then extract and write the graph."""
    t0 = time.perf_counter()
    redactions: Counter = Counter()
    clean: list[Artifact] = []
    for a in artifacts:
        red, counts = redact_artifact(a)
        redactions += counts
        clean.append(red)

    chunks = chunk_artifacts(clean)
    _embed_chunks(chunks, llm)
    indexed = search.upsert(chunks)

    extraction = extractor.extract(clean, people)
    for a in clean:
        graph.add_artifact_node(a.id, a.type, a.title, a.timestamp)
    for e in extraction.entities:
        graph.add_entity(e)
    for edge in extraction.edges:
        graph.add_edge(edge)

    resp = IngestResponse(
        artifacts_ingested=len(clean),
        chunks_indexed=indexed,
        entities_extracted=len(extraction.entities),
        edges_created=len(extraction.edges),
        graph=graph.stats(),
        duration_seconds=round(time.perf_counter() - t0, 3),
        warnings=list(extraction.warnings),
    )
    return resp, redactions


def run_ingest(
    source_dir: Optional[str] = None,
    reset: bool = True,
    *,
    search: Optional[SearchService] = None,
    graph: Optional[GraphStore] = None,
    extractor: Optional[Extractor] = None,
    llm: Optional[LLMService] = None,
) -> IngestResponse:
    """Full ingest of a source directory. Dependencies default to the app singletons."""
    t0 = time.perf_counter()
    search, graph = search or get_search(), graph or get_graph()
    extractor, llm = extractor or get_extractor(), llm or get_llm()

    loaded = load_dataset(resolve_source_dir(source_dir))
    if reset:
        search.reset()
        graph.clear()

    resp, redactions = index_artifacts(
        loaded.artifacts, loaded.people, search=search, graph=graph, extractor=extractor, llm=llm
    )
    graph.save()
    resp.warnings = loaded.warnings + resp.warnings
    resp.redactions = dict(redactions)
    resp.duration_seconds = round(time.perf_counter() - t0, 3)
    log.info("ingest done: %s", resp.model_dump(exclude={"graph"}))
    return resp


def ensure_index_ready() -> Optional[IngestResponse]:
    """Mock search is in-memory, so rebuild it at startup if the sample data exists.

    Real Azure Search persists across restarts and is never touched here.
    """
    settings = get_settings()
    if not settings.use_mocks or get_search().count() > 0:
        return None
    if not (settings.data_dir / "people.json").exists():
        return None
    return run_ingest()
