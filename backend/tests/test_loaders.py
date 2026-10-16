import json
from datetime import datetime, timezone
from pathlib import Path

from app.ingestion.loaders import load_dataset, normalize
from app.models import Artifact, ArtifactType

ROOT = Path(__file__).resolve().parents[2]


def _art(**kw) -> dict:
    base = dict(id="a1", type="chat", source_id="c1", author="ravi", timestamp="2026-01-01T10:00:00Z",
                title=" t ", body="hello  \r\nworld\n\n\n\nend ")
    return {**base, **kw}


def _write(d: Path, name: str, data) -> None:
    (d / name).write_text(json.dumps(data) if not isinstance(data, str) else data)


def test_loads_sample_dataset_cleanly():
    r = load_dataset(ROOT / "data" / "sample")
    assert len(r.people) == 4 and len(r.artifacts) == 18 + 10 + 14 + 6 + 39
    assert r.warnings == []
    stamps = [a.timestamp for a in r.artifacts]
    assert stamps == sorted(stamps)
    assert all(a.timestamp.tzinfo is not None for a in r.artifacts)


def test_normalize_cleans_text_and_forces_utc():
    a = normalize(Artifact.model_validate(_art(timestamp="2026-01-01T10:00:00")))
    assert a.body == "hello\nworld\n\nend" and a.title == "t"
    assert a.timestamp == datetime(2026, 1, 1, 10, tzinfo=timezone.utc)


def test_bad_inputs_become_warnings_not_crashes(tmp_path):
    _write(tmp_path, "people.json", [{"id": "ravi", "name": "Ravi"}, {"nope": 1}])
    _write(tmp_path, "chats.json", [_art(), _art(id="a2"), _art(id="a3", source_id="c3", type="bogus"),
                                    _art(id="a4", source_id="c4", author="ghost")])
    _write(tmp_path, "emails.json", "{not json")
    r = load_dataset(tmp_path)
    assert [a.source_id for a in r.artifacts] == ["c1", "c4"]  # dup + invalid dropped
    joined = " | ".join(r.warnings)
    for needle in ("invalid person", "duplicate", "invalid artifact", "unknown person", "invalid JSON", "tickets.json: file not found"):
        assert needle in joined, needle


def test_same_source_id_in_different_types_is_not_a_duplicate(tmp_path):
    _write(tmp_path, "chats.json", [_art()])
    _write(tmp_path, "emails.json", [_art(id="a2", type="email")])
    assert len(load_dataset(tmp_path).artifacts) == 2
    assert {a.type for a in load_dataset(tmp_path).artifacts} == {ArtifactType.CHAT, ArtifactType.EMAIL}
