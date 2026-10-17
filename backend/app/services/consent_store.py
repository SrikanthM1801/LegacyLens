"""
Consent records and the project roster: who allowed LegacyLens to learn from which conversation,
and who is on the project.

Two backends with the same interface:
  * MemoryConsentStore — in-process, used with LIVE_M365=false (tests, offline demo).
  * TableConsentStore  — Azure Table Storage (`ConsentRecords`, `SyncState`), keyless: it
    authenticates with DefaultAzureCredential (`az login` locally, managed identity in Azure).
Records hold ids, names and status only, never message content.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
from functools import lru_cache
from typing import Optional, Protocol

from app.config import get_settings
from app.models import ConsentRecord, ProjectTeam, utcnow

log = logging.getLogger(__name__)

CONSENT_TABLE = "ConsentRecords"
STATE_TABLE = "SyncState"
PROJECT_PARTITION = "project"


class ConsentStore(Protocol):
    def get(self, scope_id: str) -> Optional[ConsentRecord]: ...
    def save(self, record: ConsentRecord) -> ConsentRecord: ...
    def list(self) -> list[ConsentRecord]: ...
    def save_project(self, team: ProjectTeam) -> ProjectTeam: ...
    def projects(self) -> list[ProjectTeam]: ...


def is_granted(store: ConsentStore, scope_id: str) -> bool:
    rec = store.get(scope_id)
    return bool(rec and rec.status == "granted")


def project_member_ids(store: ConsentStore) -> set[str]:
    return {m for p in store.projects() for m in p.member_aad_ids}


class MemoryConsentStore:
    def __init__(self) -> None:
        self._consents: dict[str, ConsentRecord] = {}
        self._projects: dict[str, ProjectTeam] = {}
        self._lock = threading.Lock()

    def get(self, scope_id: str) -> Optional[ConsentRecord]:
        return self._consents.get(scope_id)

    def save(self, record: ConsentRecord) -> ConsentRecord:
        record = record.model_copy(update={"updated_at": utcnow()})
        with self._lock:
            self._consents[record.scope_id] = record
        return record

    def list(self) -> list[ConsentRecord]:
        return list(self._consents.values())

    def save_project(self, team: ProjectTeam) -> ProjectTeam:
        team = team.model_copy(update={"updated_at": utcnow()})
        with self._lock:
            self._projects[team.team_id] = team
        return team

    def projects(self) -> list[ProjectTeam]:
        return list(self._projects.values())

    def clear(self) -> None:
        with self._lock:
            self._consents.clear()
            self._projects.clear()


def _row_key(scope_id: str) -> str:
    """Teams ids contain ':' '@' ';' — fine — but '/' '#' '?' are not allowed in RowKey; encode to be safe."""
    return base64.urlsafe_b64encode(scope_id.encode()).decode().rstrip("=")


class TableConsentStore:
    def __init__(self, account_url: str) -> None:
        from azure.data.tables import TableServiceClient
        from azure.identity import DefaultAzureCredential

        service = TableServiceClient(endpoint=account_url, credential=DefaultAzureCredential())
        self._consents = service.get_table_client(CONSENT_TABLE)
        self._state = service.get_table_client(STATE_TABLE)

    def get(self, scope_id: str) -> Optional[ConsentRecord]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            row = self._consents.get_entity(partition_key="consent", row_key=_row_key(scope_id))
        except ResourceNotFoundError:
            return None
        return ConsentRecord.model_validate_json(row["record"])

    def save(self, record: ConsentRecord) -> ConsentRecord:
        record = record.model_copy(update={"updated_at": utcnow()})
        self._consents.upsert_entity({
            "PartitionKey": "consent",
            "RowKey": _row_key(record.scope_id),
            "scope_type": record.scope_type,
            "status": record.status,
            "record": record.model_dump_json(),
        })
        return record

    def list(self) -> list[ConsentRecord]:
        rows = self._consents.query_entities("PartitionKey eq 'consent'")
        return [ConsentRecord.model_validate_json(r["record"]) for r in rows]

    def save_project(self, team: ProjectTeam) -> ProjectTeam:
        team = team.model_copy(update={"updated_at": utcnow()})
        self._state.upsert_entity({
            "PartitionKey": PROJECT_PARTITION,
            "RowKey": _row_key(team.team_id),
            "record": team.model_dump_json(),
        })
        return team

    def projects(self) -> list[ProjectTeam]:
        rows = self._state.query_entities(f"PartitionKey eq '{PROJECT_PARTITION}'")
        return [ProjectTeam.model_validate(json.loads(r["record"])) for r in rows]


@lru_cache
def get_consent_store() -> ConsentStore:
    s = get_settings()
    if s.live_m365 and s.azure_tables_account_url:
        return TableConsentStore(s.azure_tables_account_url)
    if s.live_m365:
        log.warning("LIVE_M365=true but AZURE_TABLES_ACCOUNT_URL is not set; consent is kept in memory only")
    return MemoryConsentStore()
