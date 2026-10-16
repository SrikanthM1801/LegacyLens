"""Generated documents (runbooks, handover packs): in memory, mirrored to <state_dir>/docs.json."""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

from app.config import get_settings
from app.models import GeneratedDoc

log = logging.getLogger(__name__)

_lock = threading.RLock()
_docs: Optional[dict[str, GeneratedDoc]] = None


def _path():
    return get_settings().state_dir / "docs.json"


def _load() -> dict[str, GeneratedDoc]:
    global _docs
    if _docs is None:
        _docs = {}
        try:
            for raw in json.loads(_path().read_text(encoding="utf-8")):
                d = GeneratedDoc.model_validate(raw)
                _docs[d.id] = d
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("could not read %s; starting with no generated documents", _path())
    return _docs


def _persist() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([d.model_dump(mode="json") for d in _load().values()], indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save(doc: GeneratedDoc) -> GeneratedDoc:
    with _lock:
        _load()[doc.id] = doc
        _persist()
    return doc


def get(doc_id: str) -> Optional[GeneratedDoc]:
    with _lock:
        return _load().get(doc_id)


def list_docs(type: Optional[str] = None) -> list[GeneratedDoc]:
    with _lock:
        items = [d for d in _load().values() if type is None or d.type == type]
    return sorted(items, key=lambda d: (d.generated_at, d.id), reverse=True)


def clear() -> None:
    global _docs
    with _lock:
        _docs = {}
        _persist()
