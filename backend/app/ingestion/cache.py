"""Tiny JSON-file key/value cache for expensive, deterministic LLM work (embeddings, extraction)."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Optional


def content_key(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


class JsonCache:
    """Loads lazily; `save()` writes atomically. A corrupt file is treated as empty."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._data: Optional[dict[str, Any]] = None
        self._dirty = False

    def _load(self) -> dict[str, Any]:
        if self._data is None:
            try:
                self._data = json.loads(self._path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self._data = {}
        return self._data

    def get(self, key: str) -> Any:
        return self._load().get(key)

    def set(self, key: str, value: Any) -> None:
        self._load()[key] = value
        self._dirty = True

    def save(self) -> None:
        if not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._load(), separators=(",", ":")), encoding="utf-8")
        os.replace(tmp, self._path)
        self._dirty = False
