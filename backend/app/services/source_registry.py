"""
Source registry: which SharePoint files are in LegacyLens's knowledge, from where, and their state.
Also keeps each drive's Graph delta link so the next sync only sees changes.

Two backends, same interface: memory (LIVE_M365=false) and Azure Table Storage (`SyncState`
table, partitions `sp_file` and `sp_delta`), keyless via DefaultAzureCredential.
Metadata only: names, paths, links, versions, counts; never file contents.
"""

from __future__ import annotations

import base64
import logging
import threading
from datetime import datetime
from functools import lru_cache
from typing import Optional, Protocol

from app.config import get_settings
from app.models import SourceFile

log = logging.getLogger(__name__)

STATE_TABLE = "SyncState"
FILES = "sp_file"
DELTAS = "sp_delta"
META = "sp_meta"


def _key(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


class SourceRegistry(Protocol):
    def get(self, item_id: str) -> Optional[SourceFile]: ...
    def save(self, f: SourceFile) -> SourceFile: ...
    def list(self) -> list[SourceFile]: ...
    def delta_link(self, drive_id: str) -> Optional[str]: ...
    def set_delta_link(self, drive_id: str, link: Optional[str]) -> None: ...
    def last_sync(self) -> Optional[datetime]: ...
    def set_last_sync(self, when: datetime) -> None: ...
    def reset(self) -> None: ...


class MemorySourceRegistry:
    def __init__(self) -> None:
        self._files: dict[str, SourceFile] = {}
        self._deltas: dict[str, str] = {}
        self._last: Optional[datetime] = None
        self._lock = threading.Lock()

    def get(self, item_id: str) -> Optional[SourceFile]:
        return self._files.get(item_id)

    def save(self, f: SourceFile) -> SourceFile:
        with self._lock:
            self._files[f.item_id] = f
        return f

    def list(self) -> list[SourceFile]:
        return list(self._files.values())

    def delta_link(self, drive_id: str) -> Optional[str]:
        return self._deltas.get(drive_id)

    def set_delta_link(self, drive_id: str, link: Optional[str]) -> None:
        with self._lock:
            if link:
                self._deltas[drive_id] = link
            else:
                self._deltas.pop(drive_id, None)

    def last_sync(self) -> Optional[datetime]:
        return self._last

    def set_last_sync(self, when: datetime) -> None:
        self._last = when

    def reset(self) -> None:
        with self._lock:
            self._files.clear()
            self._deltas.clear()
            self._last = None


class TableSourceRegistry:
    def __init__(self, account_url: str) -> None:
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        self._t = TableServiceClient(endpoint=account_url, credential=DefaultAzureCredential()).get_table_client(STATE_TABLE)

    def _get(self, pk: str, rk: str) -> Optional[dict]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._t.get_entity(partition_key=pk, row_key=rk)
        except ResourceNotFoundError:
            return None

    def get(self, item_id: str) -> Optional[SourceFile]:
        row = self._get(FILES, _key(item_id))
        return SourceFile.model_validate_json(row["record"]) if row else None

    def save(self, f: SourceFile) -> SourceFile:
        self._t.upsert_entity({"PartitionKey": FILES, "RowKey": _key(f.item_id), "status": f.status,
                               "record": f.model_dump_json()})
        return f

    def list(self) -> list[SourceFile]:
        return [SourceFile.model_validate_json(r["record"])
                for r in self._t.query_entities(f"PartitionKey eq '{FILES}'")]

    def delta_link(self, drive_id: str) -> Optional[str]:
        row = self._get(DELTAS, _key(drive_id))
        return row["link"] if row else None

    def set_delta_link(self, drive_id: str, link: Optional[str]) -> None:
        if link:
            self._t.upsert_entity({"PartitionKey": DELTAS, "RowKey": _key(drive_id), "link": link})
        else:
            try:
                self._t.delete_entity(partition_key=DELTAS, row_key=_key(drive_id))
            except Exception:
                pass

    def last_sync(self) -> Optional[datetime]:
        row = self._get(META, "last_sync")
        return datetime.fromisoformat(row["at"]) if row else None

    def set_last_sync(self, when: datetime) -> None:
        self._t.upsert_entity({"PartitionKey": META, "RowKey": "last_sync", "at": when.isoformat()})

    def reset(self) -> None:
        for pk in (FILES, DELTAS, META):
            for r in list(self._t.query_entities(f"PartitionKey eq '{pk}'", select=["PartitionKey", "RowKey"])):
                self._t.delete_entity(partition_key=r["PartitionKey"], row_key=r["RowKey"])


@lru_cache
def get_source_registry() -> SourceRegistry:
    s = get_settings()
    if s.live_m365 and s.azure_tables_account_url:
        return TableSourceRegistry(s.azure_tables_account_url)
    return MemorySourceRegistry()

