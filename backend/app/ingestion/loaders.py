"""
Load a source directory (default: data/sample) into normalized Artifacts.

Raw sources are JSON files already shaped like the models (the synthetic dataset
today; a Microsoft Graph connector would emit the same shape later). Loading is
forgiving: a bad record or missing file becomes a warning, never a crash, so one
malformed email can't take down an ingest run.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Optional

from pydantic import ValidationError

from app.config import get_settings
from app.models import Artifact, Person

log = logging.getLogger(__name__)

#: File name -> expected artifact kind. Order is the load order.
ARTIFACT_FILES = ("tickets.json", "emails.json", "chats.json", "wiki.json", "commits.json")
PEOPLE_FILE = "people.json"

_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_BLANK_RUNS = re.compile(r"\n{3,}")


@dataclass
class LoadResult:
    people: list[Person] = field(default_factory=list)
    artifacts: list[Artifact] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def normalize(a: Artifact) -> Artifact:
    """Canonical form: UTC timestamp, LF newlines, no trailing spaces, trimmed text."""
    ts = a.timestamp if a.timestamp.tzinfo else a.timestamp.replace(tzinfo=timezone.utc)
    body = a.body.replace("\r\n", "\n").replace("\r", "\n")
    body = _BLANK_RUNS.sub("\n\n", _TRAILING_WS.sub("", body)).strip()
    return a.model_copy(update={"timestamp": ts.astimezone(timezone.utc), "body": body, "title": a.title.strip()})


def _read_json_list(path: Path, warnings: list[str]) -> list[dict]:
    if not path.exists():
        warnings.append(f"{path.name}: file not found, skipped")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        warnings.append(f"{path.name}: invalid JSON ({e.msg} at line {e.lineno}), skipped")
        return []
    if not isinstance(data, list):
        warnings.append(f"{path.name}: expected a JSON list, skipped")
        return []
    return data


def load_dataset(source_dir: Optional[Path | str] = None) -> LoadResult:
    """Read people + artifacts, validate, normalize, de-dupe on (type, source_id)."""
    root = Path(source_dir) if source_dir else get_settings().data_dir
    result = LoadResult()
    w = result.warnings

    for i, raw in enumerate(_read_json_list(root / PEOPLE_FILE, w)):
        try:
            result.people.append(Person.model_validate(raw))
        except ValidationError as e:
            w.append(f"{PEOPLE_FILE}[{i}]: invalid person ({e.error_count()} errors), skipped")
    known = {p.id for p in result.people}

    seen: set[tuple[str, str]] = set()
    for name in ARTIFACT_FILES:
        for i, raw in enumerate(_read_json_list(root / name, w)):
            try:
                art = normalize(Artifact.model_validate(raw))
            except ValidationError as e:
                w.append(f"{name}[{i}]: invalid artifact ({e.error_count()} errors), skipped")
                continue
            key = (art.type, art.source_id)
            if key in seen:
                w.append(f"{name}[{i}]: duplicate {art.type} '{art.source_id}', skipped")
                continue
            seen.add(key)
            unknown = ({art.author} | set(art.participants)) - known
            if known and unknown:
                w.append(f"{art.source_id}: unknown person id(s) {sorted(unknown)}")
            result.artifacts.append(art)

    result.artifacts.sort(key=lambda a: (a.timestamp, a.type, a.source_id))
    log.info("loaded %d people, %d artifacts (%d warnings)", len(result.people), len(result.artifacts), len(w))
    return result
