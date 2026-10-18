"""
Small JSON document store: one blob per record under a prefix in the `knowledge` container
(keyless, DefaultAzureCredential), or in memory with LIVE_M365=false. Used for departures and
person links; sessions and evidence stay in their own stores.
"""

from __future__ import annotations

import threading
from typing import Generic, Optional, TypeVar

from pydantic import BaseModel

from app.config import get_settings

T = TypeVar("T", bound=BaseModel)


class JsonStore(Generic[T]):
    def __init__(self, prefix: str, model: type[T], key: str = "id") -> None:
        self._prefix, self._model, self._key = prefix.rstrip("/") + "/", model, key
        self._mem: dict[str, T] = {}
        self._lock = threading.Lock()
        s = get_settings()
        self._blob = None
        if s.live_m365 and s.azure_storage_account_url:
            from azure.identity import DefaultAzureCredential
            from azure.storage.blob import BlobServiceClient

            self._blob = BlobServiceClient(s.azure_storage_account_url, credential=DefaultAzureCredential()) \
                .get_container_client(s.azure_storage_container)

    def _name(self, rid: str) -> str:
        return f"{self._prefix}{rid.replace('/', '_')}.json"

    def get(self, rid: str) -> Optional[T]:
        if self._blob is None:
            v = self._mem.get(rid)
            return v.model_copy(deep=True) if v else None
        from azure.core.exceptions import ResourceNotFoundError

        try:
            return self._model.model_validate_json(self._blob.get_blob_client(self._name(rid)).download_blob().readall())
        except ResourceNotFoundError:
            return None

    def save(self, item: T) -> T:
        rid = getattr(item, self._key)
        if self._blob is None:
            with self._lock:
                self._mem[rid] = item.model_copy(deep=True)
        else:
            self._blob.get_blob_client(self._name(rid)).upload_blob(item.model_dump_json(indent=2).encode(), overwrite=True)
        return item

    def list(self) -> list[T]:
        if self._blob is None:
            return [v.model_copy(deep=True) for v in self._mem.values()]
        return [self._model.model_validate_json(self._blob.get_blob_client(b.name).download_blob().readall())
                for b in self._blob.list_blobs(name_starts_with=self._prefix)]

    def clear(self) -> None:
        self._mem.clear()
