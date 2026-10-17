"""
SharePoint -> knowledge: keep the project site's documents in the index and graph.

`sync_sharepoint()` reads each document library's Graph delta (first run: every file; later
runs: only what changed since the stored delta link) and for each file:
  added / modified -> download, extract text, (modified: drop the old chunks and graph links),
                      index as Artifact(type=doc) through the normal pipeline (redact, chunk,
                      embed, extract, graph); SharePoint docs count as documentation for coverage
  deleted          -> remove its chunks and graph node
  unchanged        -> same cTag as last time, skipped
Every file outcome is written to the source registry (what is loaded, from where) and the audit
trail as `capture.file`; each run adds one `sync.sharepoint` summary. `sharepoint_loop` repeats
this every SHAREPOINT_SYNC_MINUTES while the server runs.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime, timezone
from typing import Optional

from app.bot.capture import person_id
from app.config import get_settings
from app.graph import get_graph
from app.ingestion.chunker import chunk_artifacts
from app.ingestion.extractor import get_extractor
from app.ingestion.file_text import UnsupportedFile, extract_text, is_supported
from app.ingestion.pipeline import index_artifacts, ingest_lock
from app.models import Artifact, ArtifactType, ConsentRecord, Person, SourceFile, SourceSyncReport
from app.services import audit, risk_store
from app.services.consent_store import get_consent_store
from app.services.graph_api import GraphError, GraphService, SiteInfo, get_graph_api
from app.services.interview_publish import people_from_graph
from app.services.llm import get_llm
from app.services.search import get_search
from app.services.source_registry import SourceRegistry, get_source_registry

log = logging.getLogger(__name__)

_sync_lock = asyncio.Lock()


def artifact_id_for(item_id: str) -> str:
    return "art_sp_" + hashlib.sha1(item_id.encode()).hexdigest()[:16]


def _user(item: dict, key: str) -> tuple[Optional[str], Optional[str]]:
    u = ((item.get(key) or {}).get("user") or {})
    return u.get("id"), u.get("displayName")


def _folder(item: dict) -> str:
    path = (item.get("parentReference") or {}).get("path", "")
    return path.split("root:", 1)[1] if "root:" in path else ""


def _when(item: dict) -> datetime:
    raw = item.get("lastModifiedDateTime")
    return datetime.fromisoformat(raw.replace("Z", "+00:00")) if raw else datetime.now(timezone.utc)


def _forget(artifact_id: str) -> int:
    removed = get_search().delete_artifact(artifact_id)
    get_graph().remove_artifact(artifact_id)
    return removed


def _audit_file(f: SourceFile, change: str, outcome: str = "success", **details) -> None:
    audit.record(
        "capture.file", outcome=outcome, source="sharepoint", actor_name="LegacyLens",
        scope_type="site", scope_id=f.site_id, scope_name=f.site_name or None,
        item_ids=[f.artifact_id] if f.artifact_id else [], count=f.chunks,
        details={"change": change, "file": f.name, "path": f.path, "web_url": f.web_url, "size": f.size,
                 "modified_by": f.modified_by, "modified_at": f.modified_at.isoformat() if f.modified_at else None,
                 "status": f.status, "characters": f.characters, **({"error": f.error} if f.error else {}), **details},
    )


def _ensure_site_consent(site: SiteInfo) -> None:
    """A site is configured by an admin; record that as its consent so the admin page shows it."""
    store = get_consent_store()
    if store.get(site.id) is None:
        store.save(ConsentRecord(scope_id=site.id, scope_type="site", display_name=site.name, status="granted",
                                 granted_by="admin configuration (SHAREPOINT_SITE_URL)"))
        audit.record("consent.granted", source="system", actor_name="admin configuration", scope_type="site",
                     scope_id=site.id, scope_name=site.name, details={"site_url": site.web_url})


async def _load(item: dict, drive_id: str, site: SiteInfo, graph: GraphService,
                registry: SourceRegistry, report: SourceSyncReport, pending: list) -> None:
    prev = registry.get(item["id"])
    by_id, by_name = _user(item, "lastModifiedBy")
    cr_id, cr_name = _user(item, "createdBy")
    f = SourceFile(
        item_id=item["id"], drive_id=drive_id, site_id=site.id, site_name=site.name, name=item.get("name", ""),
        path=_folder(item), web_url=item.get("webUrl"), size=item.get("size", 0), ctag=item.get("cTag", ""),
        modified_at=_when(item), modified_by=by_name, created_by=cr_name, artifact_id=artifact_id_for(item["id"]),
    )
    if prev and prev.ctag == f.ctag and prev.status in ("loaded", "unsupported", "empty"):
        report.unchanged += 1
        return
    change = "modified" if prev and prev.status == "loaded" else "added"
    if not is_supported(f.name):
        f = f.model_copy(update={"status": "unsupported", "artifact_id": None})
        registry.save(f)
        report.unsupported += 1
        _audit_file(f, change, outcome="skipped")
        return
    try:
        text = extract_text(f.name, await graph.download(drive_id, f.item_id))
    except (GraphError, UnsupportedFile, Exception) as e:  # one bad file must not stop the sync
        f = f.model_copy(update={"status": "failed", "error": f"{type(e).__name__}: {str(e)[:150]}"})
        registry.save(f)
        report.failed += 1
        report.errors.append(f"{f.name}: {f.error}")
        _audit_file(f, change, outcome="failure")
        return
    if not text.strip():
        f = f.model_copy(update={"status": "empty"})
        registry.save(f)
        _audit_file(f, change, outcome="skipped")
        return
    art = Artifact(
        id=f.artifact_id, type=ArtifactType.DOC, source_id=f"sharepoint:{drive_id}:{f.item_id}",
        author=person_id(cr_id or by_id or "unknown"),
        participants=[person_id(by_id)] if by_id and by_id != cr_id else [],
        timestamp=f.modified_at, title=f.name.rsplit(".", 1)[0], body=text, url_or_path=f.web_url,
        metadata={"source": "sharepoint", "scope_id": site.id, "scope_type": "site", "site": site.name,
                  "path": f.path, "file": f.name, "drive_item_id": f.item_id, "live": True},
    )
    people = [Person(id=person_id(i), name=n or "Unknown", teams_handle=i) for i, n in ((cr_id, cr_name), (by_id, by_name)) if i]
    pending.append((f.model_copy(update={"characters": len(text)}), art, people, change))


def _index(pending: list, report: SourceSyncReport, registry: SourceRegistry) -> None:
    """Index the downloaded files in one pipeline run (blocking; call in a thread)."""
    graph = get_graph()
    people = {p.id: p for p in people_from_graph(graph)}
    for _, _, ps, _ in pending:
        for p in ps:
            people.setdefault(p.id, p)
    with ingest_lock:
        for _, art, _, _ in pending:
            _forget(art.id)  # old version, or leftovers of a failed attempt (no-op otherwise)
        resp, _ = index_artifacts([a for _, a, _, _ in pending], list(people.values()),
                                           search=get_search(), graph=graph, extractor=get_extractor(), llm=get_llm())
        graph.save()
    report.chunks_indexed += resp.chunks_indexed
    for f, art, _, change in pending:
        f = f.model_copy(update={"status": "loaded", "chunks": len(chunk_artifacts([art])), "error": None})
        registry.save(f)
        if change == "modified":
            report.modified += 1
        else:
            report.added += 1
        _audit_file(f, change)


def _mark_failed(pending: list, error: Exception, report: SourceSyncReport, registry: SourceRegistry) -> None:
    reason = f"indexing failed: {type(error).__name__}"
    report.errors.append(reason)
    for f, _art, _, change in pending:
        f = registry.save(f.model_copy(update={"status": "failed", "error": reason}))
        report.failed += 1
        _audit_file(f, change, outcome="failure")


def _delete(item_id: str, registry: SourceRegistry, report: SourceSyncReport) -> None:
    prev = registry.get(item_id)
    if prev is None or prev.status == "deleted":
        return
    if prev.artifact_id:
        with ingest_lock:
            _forget(prev.artifact_id)
            get_graph().save()
    f = registry.save(prev.model_copy(update={"status": "deleted", "chunks": 0}))
    report.deleted += 1
    _audit_file(f, "deleted")


async def sync_sharepoint(full: bool = False, graph: Optional[GraphService] = None,
                          registry: Optional[SourceRegistry] = None) -> Optional[SourceSyncReport]:
    """One sync pass. `full=True` ignores stored delta links (re-reads every file; unchanged ones
    are still skipped by cTag). Returns None when no site is configured."""
    s = get_settings()
    if not s.sharepoint_site_url:
        return None
    graph = graph or get_graph_api()
    registry = registry or get_source_registry()
    async with _sync_lock:
        t0 = time.perf_counter()
        report = SourceSyncReport(full=full)
        try:
            site = await graph.resolve_site(s.sharepoint_site_url)
            report.site_name = site.name
            await asyncio.to_thread(_ensure_site_consent, site)
            drives = await graph.list_drives(site.id)
        except GraphError as e:
            report.errors.append(str(e))
            audit.record("sync.sharepoint", outcome="failure", source="sharepoint",
                         details={"error": str(e), "hint": "check Sites.Selected grant / SHAREPOINT_SITE_URL"})
            return report
        for drive_id, _name in drives:
            link = None if full else registry.delta_link(drive_id)
            try:
                items, new_link = await graph.drive_delta(drive_id, link)
            except GraphError as e:
                if e.status == 410:  # delta expired: start over for this library
                    items, new_link = await graph.drive_delta(drive_id, None)
                else:
                    report.errors.append(str(e))
                    continue
            pending: list = []
            for item in items:
                if item.get("deleted"):
                    await asyncio.to_thread(_delete, item["id"], registry, report)
                elif item.get("file") is not None:
                    await _load(item, drive_id, site, graph, registry, report, pending)
            if pending:
                try:
                    await asyncio.to_thread(_index, pending, report, registry)
                except Exception as e:  # e.g. Azure OpenAI throttling: mark failed, retry next run
                    log.exception("sharepoint indexing failed")
                    _mark_failed(pending, e, report, registry)
                    continue  # keep the old delta link so these files come back next time
            registry.set_delta_link(drive_id, new_link)
        if report.added or report.modified or report.deleted:
            risk_store.invalidate()
        report.duration_seconds = round(time.perf_counter() - t0, 2)
        registry.set_last_sync(datetime.now(timezone.utc))
        changed = report.added + report.modified + report.deleted + report.failed + report.unsupported
        if changed or full:
            audit.record("sync.sharepoint", outcome="failure" if report.failed else "success", source="sharepoint",
                         scope_type="site", scope_id=site.id, scope_name=site.name,
                         count=report.added + report.modified, details=report.model_dump(exclude={"site_name"}))
        log.info("sharepoint sync: +%d ~%d -%d =%d failed %d unsupported %d (%.1fs)", report.added, report.modified,
                 report.deleted, report.unchanged, report.failed, report.unsupported, report.duration_seconds)
        return report


async def sharepoint_loop() -> None:
    minutes = max(1, get_settings().sharepoint_sync_minutes)
    await asyncio.sleep(3)
    while True:
        try:
            await sync_sharepoint()
        except Exception:  # keep the loop alive; details in the server log
            log.exception("sharepoint sync crashed")
            audit.record("sync.sharepoint", outcome="failure", source="sharepoint", details={"error": "crashed"})
        await asyncio.sleep(minutes * 60)
