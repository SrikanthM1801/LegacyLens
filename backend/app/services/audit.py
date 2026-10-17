"""
Audit trail: every consent decision, chat detection, message/file read, answer and publish,
written to Azure Blob Storage for the admin page.

Storage layout (container AUDIT_CONTAINER, default `audit`), one append blob per UTC hour:
    events/2026/10/17/15.jsonl      one AuditEvent JSON object per line
Append blobs can only be added to, which suits an audit log; the storage account is keyless
(Entra ID only), so writers and the admin reader are controlled by role assignments.

`record(...)` never blocks a request and never raises: events go on a queue and a background
thread appends them in batches. With LIVE_M365=false (tests, offline demo) events stay in memory.
Events carry ids, names and counts only — never message text, file contents or questions.
"""

from __future__ import annotations

import atexit
import json
import logging
import queue
import threading
from collections import deque
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Iterable, Optional, Protocol

from app.config import get_settings
from app.models import AuditEvent

log = logging.getLogger(__name__)

MAX_ITEM_IDS = 50                  # per event; bigger batches keep the count, not every id
MEMORY_LIMIT = 5000
FLUSH_EVERY_S = 2.0
MAX_QUERY_DAYS = 31


class AuditLog(Protocol):
    def write(self, event: AuditEvent) -> None: ...
    def query(self, since: datetime, until: datetime) -> list[AuditEvent]: ...
    def flush(self) -> None: ...


class MemoryAuditLog:
    def __init__(self) -> None:
        self._events: deque[AuditEvent] = deque(maxlen=MEMORY_LIMIT)
        self._lock = threading.Lock()

    def write(self, event: AuditEvent) -> None:
        with self._lock:
            self._events.append(event)

    def query(self, since: datetime, until: datetime) -> list[AuditEvent]:
        with self._lock:
            return [e for e in self._events if since <= e.timestamp <= until]

    def flush(self) -> None:
        pass

    def clear(self) -> None:
        with self._lock:
            self._events.clear()


def blob_name(ts: datetime) -> str:
    ts = ts.astimezone(timezone.utc)
    return f"events/{ts:%Y/%m/%d/%H}.jsonl"


class BlobAuditLog:
    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self._container = BlobServiceClient(account_url, credential=DefaultAzureCredential()).get_container_client(container)
        self._queue: "queue.Queue[AuditEvent]" = queue.Queue()
        self._created: set[str] = set()
        self._writer = threading.Thread(target=self._run, name="audit-writer", daemon=True)
        self._writer.start()
        atexit.register(self.flush)

    def write(self, event: AuditEvent) -> None:
        self._queue.put(event)

    def _run(self) -> None:
        while True:
            batch = [self._queue.get()]
            deadline = datetime.now().timestamp() + FLUSH_EVERY_S
            while len(batch) < 200 and datetime.now().timestamp() < deadline:
                try:
                    batch.append(self._queue.get(timeout=max(0.0, deadline - datetime.now().timestamp())))
                except queue.Empty:
                    break
            self._append(batch)
            for _ in batch:
                self._queue.task_done()

    def _append(self, batch: list[AuditEvent]) -> None:
        from azure.core.exceptions import ResourceExistsError

        by_blob: dict[str, list[str]] = {}
        for e in batch:
            by_blob.setdefault(blob_name(e.timestamp), []).append(e.model_dump_json())
        for name, lines in by_blob.items():
            try:
                blob = self._container.get_blob_client(name)
                if name not in self._created:
                    try:
                        blob.create_append_blob()
                    except ResourceExistsError:
                        pass
                    self._created.add(name)
                blob.append_block(("\n".join(lines) + "\n").encode("utf-8"))
            except Exception as e:  # the audit trail must never take the app down
                log.warning("audit write failed (%d events): %s", len(lines), type(e).__name__)

    def flush(self) -> None:
        self._queue.join()

    def query(self, since: datetime, until: datetime) -> list[AuditEvent]:
        from azure.core.exceptions import ResourceNotFoundError

        out: list[AuditEvent] = []
        day = since.astimezone(timezone.utc).date()
        while day <= until.astimezone(timezone.utc).date():
            for item in self._container.list_blobs(name_starts_with=f"events/{day:%Y/%m/%d}/"):
                try:
                    data = self._container.get_blob_client(item.name).download_blob().readall()
                except ResourceNotFoundError:
                    continue
                for line in data.decode("utf-8").splitlines():
                    if line.strip():
                        e = AuditEvent.model_validate_json(line)
                        if since <= e.timestamp <= until:
                            out.append(e)
            day += timedelta(days=1)
        return out


@lru_cache
def get_audit_log() -> AuditLog:
    s = get_settings()
    if s.live_m365 and s.azure_storage_account_url:
        return BlobAuditLog(s.azure_storage_account_url, s.audit_container)
    if s.live_m365:
        log.warning("LIVE_M365=true but AZURE_STORAGE_ACCOUNT_URL is not set; audit events are kept in memory only")
    return MemoryAuditLog()


def record(action: str, *, item_ids: Optional[Iterable[str]] = None, **fields: Any) -> None:
    """Add one audit event. Never raises; never blocks on storage."""
    try:
        ids = list(item_ids or [])
        if ids and fields.get("count") is None:
            fields["count"] = len(ids)
        get_audit_log().write(AuditEvent(action=action, item_ids=ids[:MAX_ITEM_IDS], **fields))
    except Exception as e:
        log.warning("audit event %s dropped: %s", action, type(e).__name__)


def query(
    since: Optional[datetime] = None, until: Optional[datetime] = None, *, action: Optional[str] = None,
    scope_id: Optional[str] = None, actor: Optional[str] = None, outcome: Optional[str] = None, limit: int = 200,
) -> tuple[list[AuditEvent], int, list[str]]:
    """Newest first. `action` matches exactly or by category prefix ("consent" -> consent.*)."""
    until = until or datetime.now(timezone.utc)
    since = since or until - timedelta(days=7)
    since = max(since, until - timedelta(days=MAX_QUERY_DAYS))
    events = get_audit_log().query(since, until)
    actions = sorted({e.action for e in events})

    def keep(e: AuditEvent) -> bool:
        if action and not (e.action == action or e.action.startswith(action.rstrip(".") + ".")):
            return False
        if scope_id and e.scope_id != scope_id:
            return False
        if actor and actor.lower() not in (e.actor_name or "").lower() and actor != e.actor_aad_id:
            return False
        return not outcome or e.outcome == outcome

    hits = sorted((e for e in events if keep(e)), key=lambda e: e.timestamp, reverse=True)
    return hits[:limit], len(hits), actions
