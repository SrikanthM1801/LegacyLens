"""
Interview sessions: in-memory, mirrored to <state_dir>/interviews.json (atomic writes)
so a restart of a real-Azure deployment doesn't lose them. A full re-ingest with
reset wipes them, because the interview artifacts they produced are wiped too.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from typing import Optional

from app.config import get_settings
from app.models import InterviewSession, InterviewStatus

log = logging.getLogger(__name__)

_lock = threading.RLock()
_sessions: Optional[dict[str, InterviewSession]] = None  # None = not loaded yet


def _path():
    return get_settings().state_dir / "interviews.json"


def _load() -> dict[str, InterviewSession]:
    global _sessions
    if _sessions is None:
        _sessions = {}
        try:
            for raw in json.loads(_path().read_text(encoding="utf-8")):
                s = InterviewSession.model_validate(raw)
                _sessions[s.id] = s
        except FileNotFoundError:
            pass
        except (OSError, ValueError):
            log.warning("could not read %s; starting with no interview sessions", _path())
    return _sessions


def _persist() -> None:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    data = [s.model_dump(mode="json") for s in _load().values()]
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def save(session: InterviewSession) -> InterviewSession:
    """Insert or replace a session."""
    with _lock:
        _load()[session.id] = session
        _persist()
    return session


def get(session_id: str) -> Optional[InterviewSession]:
    with _lock:
        return _load().get(session_id)


def list_sessions(risk_id: Optional[str] = None) -> list[InterviewSession]:
    with _lock:
        items = [s for s in _load().values() if risk_id is None or s.risk_id == risk_id]
    return sorted(items, key=lambda s: (s.created_at, s.id), reverse=True)  # newest first


def find_open(risk_id: str, interviewee: str) -> Optional[InterviewSession]:
    """The newest unfinished session for this risk and person, if any."""
    return next(
        (s for s in list_sessions(risk_id) if s.interviewee == interviewee and s.status != InterviewStatus.COMPLETED.value),
        None,
    )


def clear() -> None:
    global _sessions
    with _lock:
        _sessions = {}
        _persist()
