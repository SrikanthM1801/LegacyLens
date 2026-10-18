"""BlobAuditLog.query: one listing call, only the hours that overlap the window, parallel reads, size-keyed cache."""

import threading
from datetime import datetime, timedelta, timezone

import pytest
from azure.core.exceptions import ResourceNotFoundError

from app.models import AuditEvent
from app.services import audit
from app.services.audit import BlobAuditLog, blob_hour, blob_name

T0 = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)


class FakeBlobClient:
    def __init__(self, container, name):
        self.c, self.name = container, name

    def download_blob(self):
        self.c.downloads.append(self.name)
        if self.name in self.c.vanished:
            raise ResourceNotFoundError("gone")
        data = self.c.blobs[self.name]
        return type("D", (), {"readall": lambda _s: data})()


class FakeContainer:
    """Blobs with sizes, a call counter, and the threads that downloaded (to prove reads run in parallel)."""

    def __init__(self):
        self.blobs: dict[str, bytes] = {}
        self.vanished: set[str] = set()
        self.listings: list[str] = []
        self.downloads: list[str] = []

    def list_blobs(self, name_starts_with):
        self.listings.append(name_starts_with)
        return [type("B", (), {"name": n, "size": len(b)})() for n, b in sorted(self.blobs.items()) if n.startswith(name_starts_with)]

    def get_blob_client(self, name):
        return FakeBlobClient(self, name)


def event(when, action="capture.message", **kw):
    return AuditEvent(timestamp=when, action=action, **kw)


def put(container, events):
    by_blob = {}
    for e in events:
        by_blob.setdefault(blob_name(e.timestamp), []).append(e.model_dump_json())
    for name, lines in by_blob.items():
        container.blobs[name] = (container.blobs.get(name, b"") + ("\n".join(lines) + "\n").encode())


@pytest.fixture
def log():
    log = BlobAuditLog.__new__(BlobAuditLog)          # skip the real __init__: it would open an Azure client
    log._container = FakeContainer()
    return log


def test_blob_names_round_trip_to_their_hour():
    assert blob_hour("events/2026/10/17/15.jsonl") == datetime(2026, 10, 17, 15, tzinfo=timezone.utc)
    assert blob_hour(blob_name(T0)) == datetime(2026, 10, 17, 12, tzinfo=timezone.utc)
    for junk in ("events/2026/10/17/15.txt", "events/x/10/17/15.jsonl", "other/2026/10/17/15.jsonl", "events/2026/10/17/15.jsonl.bak"):
        assert blob_hour(junk) is None


def test_one_listing_call_however_long_the_window(log):
    put(log._container, [event(T0 - timedelta(days=d)) for d in range(0, 30)])
    got = log.query(T0 - timedelta(days=30), T0)
    assert len(got) == 30 and len(log._container.listings) == 1, "was one listing per day of the window"
    assert log._container.listings == ["events/"]


def test_only_the_hours_that_overlap_the_window_are_read(log):
    inside = [event(T0 - timedelta(hours=h), action=f"a{h}") for h in (1, 2, 3)]
    outside = [event(T0 - timedelta(days=5)), event(T0 + timedelta(days=2))]
    put(log._container, inside + outside)
    got = log.query(T0 - timedelta(hours=4), T0)
    assert sorted(e.action for e in got) == ["a1", "a2", "a3"]
    assert len(log._container.downloads) == 3, "blobs outside the window are never downloaded"


def test_events_in_an_overlapping_hour_but_outside_the_window_are_filtered(log):
    put(log._container, [event(T0.replace(minute=5), action="early"), event(T0.replace(minute=50), action="late")])
    got = log.query(T0.replace(minute=20), T0.replace(minute=59))
    assert [e.action for e in got] == ["late"]


def test_results_are_oldest_first(log):
    put(log._container, [event(T0 - timedelta(hours=h), action=f"h{h}") for h in (5, 1, 3, 2, 4)])
    assert [e.action for e in log.query(T0 - timedelta(hours=6), T0)] == ["h5", "h4", "h3", "h2", "h1"]


def test_a_blob_is_downloaded_once_until_it_grows(log):
    put(log._container, [event(T0 - timedelta(hours=2)), event(T0)])
    log.query(T0 - timedelta(hours=3), T0 + timedelta(minutes=30))
    assert len(log._container.downloads) == 2
    log.query(T0 - timedelta(hours=3), T0 + timedelta(minutes=30))
    assert len(log._container.downloads) == 2, "unchanged blobs come from the cache"
    put(log._container, [event(T0 + timedelta(minutes=10), action="new")])      # an append changes the blob's size
    got = log.query(T0 - timedelta(hours=3), T0 + timedelta(minutes=30))
    assert len(log._container.downloads) == 3 and "new" in [e.action for e in got], "a grown blob is re-read, never served stale"


def test_foreign_and_vanished_blobs_are_tolerated(log):
    put(log._container, [event(T0), event(T0 - timedelta(hours=1), action="vanishes")])
    log._container.blobs["events/2026/10/17/notes.txt"] = b"not an audit blob"
    log._container.vanished.add(blob_name(T0 - timedelta(hours=1)))
    assert [e.action for e in log.query(T0 - timedelta(hours=3), T0 + timedelta(minutes=5))] == ["capture.message"]


def test_blobs_are_read_in_parallel(log, monkeypatch):
    put(log._container, [event(T0 - timedelta(hours=h)) for h in range(8)])
    seen, gate = set(), threading.Barrier(4, timeout=5)

    def slow(self):
        seen.add(threading.get_ident())
        gate.wait()                                    # four reads must be in flight at once, or this times out
        return type("D", (), {"readall": lambda _s: log._container.blobs[self.name]})()

    monkeypatch.setattr(FakeBlobClient, "download_blob", slow)
    got = log.query(T0 - timedelta(hours=9), T0 + timedelta(minutes=5))
    assert len(got) == 8 and len(seen) >= 4


def test_the_cache_is_bounded(log, monkeypatch):
    monkeypatch.setattr(audit, "READ_CACHE_BLOBS", 3)
    put(log._container, [event(T0 - timedelta(hours=h)) for h in range(6)])
    log.query(T0 - timedelta(hours=7), T0 + timedelta(minutes=5))
    assert len(log.__dict__["_read_cache"]) <= 3
