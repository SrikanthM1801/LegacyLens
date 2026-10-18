"""
Onboarding sessions: one JSON document per candidate assessment.

Blob backend: container AZURE_STORAGE_CONTAINER (`knowledge`), `onboarding/sessions/<id>.json`,
keyless via DefaultAzureCredential. Memory backend for LIVE_M365=false. Sessions hold the
candidate's learning questions and answers, which the candidate is told about on assignment.
"""

from __future__ import annotations

import logging
import threading
from functools import lru_cache
from typing import Optional, Protocol

from app.config import get_settings
from app.models import OnboardingSession

log = logging.getLogger(__name__)

PREFIX = "onboarding/sessions/"
ACTIVE = ("learning", "assessing", "evaluating")


class OnboardingStore(Protocol):
    def get(self, session_id: str) -> Optional[OnboardingSession]: ...
    def save(self, session: OnboardingSession) -> OnboardingSession: ...
    def list(self) -> list[OnboardingSession]: ...


def active_for(store: OnboardingStore, candidate_aad_id: str) -> Optional[OnboardingSession]:
    """The candidate's open session (newest first), if any."""
    open_ = [s for s in store.list() if s.candidate_aad_id == candidate_aad_id and s.status in ACTIVE]
    return max(open_, key=lambda s: s.assigned_at) if open_ else None


class MemoryOnboardingStore:
    def __init__(self) -> None:
        self._s: dict[str, OnboardingSession] = {}
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Optional[OnboardingSession]:
        s = self._s.get(session_id)
        return s.model_copy(deep=True) if s else None

    def save(self, session: OnboardingSession) -> OnboardingSession:
        with self._lock:
            self._s[session.id] = session.model_copy(deep=True)
        return session

    def list(self) -> list[OnboardingSession]:
        return [s.model_copy(deep=True) for s in self._s.values()]

    def clear(self) -> None:
        with self._lock:
            self._s.clear()


class BlobOnboardingStore:
    def __init__(self, account_url: str, container: str) -> None:
        from azure.identity import DefaultAzureCredential
        from azure.storage.blob import BlobServiceClient

        self._c = BlobServiceClient(account_url, credential=DefaultAzureCredential()).get_container_client(container)

    def get(self, session_id: str) -> Optional[OnboardingSession]:
        from azure.core.exceptions import ResourceNotFoundError

        try:
            data = self._c.get_blob_client(f"{PREFIX}{session_id}.json").download_blob().readall()
        except ResourceNotFoundError:
            return None
        return OnboardingSession.model_validate_json(data)

    def save(self, session: OnboardingSession) -> OnboardingSession:
        self._c.get_blob_client(f"{PREFIX}{session.id}.json").upload_blob(
            session.model_dump_json(indent=2).encode("utf-8"), overwrite=True)
        return session

    def list(self) -> list[OnboardingSession]:
        out = []
        for b in self._c.list_blobs(name_starts_with=PREFIX):
            out.append(OnboardingSession.model_validate_json(self._c.get_blob_client(b.name).download_blob().readall()))
        return out


@lru_cache
def get_onboarding_store() -> OnboardingStore:
    s = get_settings()
    if s.live_m365 and s.azure_storage_account_url:
        return BlobOnboardingStore(s.azure_storage_account_url, s.azure_storage_container)
    return MemoryOnboardingStore()
