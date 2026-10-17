"""Audit trail: events for consent, detection, capture, answers and publishing; /audit filters and auth;
the append-blob backend; and the rule that audit events never contain message text."""

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.bot import agent, consent
from app.bot.chat_watch import chat_ids_from_notification, handle_new_chat
from app.config import get_settings
from app.main import app
from app.models import AuditEvent, ProjectTeam
from app.services import audit
from app.services.consent_store import get_consent_store
from app.services.graph_api import ChatInfo, GraphMessage, get_graph_api
from tests.test_consent import CHANNEL, TEAM, click, install, run_spawned, say

SECRET_PHRASE = "zebra-gateway-override-4417"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/ingest")
        yield c


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    get_consent_store().clear()
    get_graph_api().reset()
    audit.get_audit_log().clear()
    spawned = []
    monkeypatch.setattr(agent, "_spawn", spawned.append)

    async def roster(_context, _team_id):
        return ["aad-alice", "aad-bob"]

    monkeypatch.setattr(agent, "fetch_team_member_ids", roster)
    yield spawned


def actions():
    return [e.action for e in sorted(audit.get_audit_log().query(
        datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc) + timedelta(minutes=1)),
        key=lambda e: e.timestamp)]


def test_consent_flow_is_fully_audited_without_message_text(client, fresh):
    get_graph_api().messages[CHANNEL["id"]] = [GraphMessage(
        id="old-1", created=datetime(2026, 10, 1, tzinfo=timezone.utc), author_aad_id="aad-carol",
        author_name="Carol", html=f"<p>Use {SECRET_PHRASE} when the gateway pool drains</p>")]
    install(client, CHANNEL)
    click(client, CHANNEL, consent.ALLOW)
    run_spawned(fresh)
    say(client, CHANNEL, f"Reminder: {SECRET_PHRASE} is only for incidents", "live-9")
    run_spawned(fresh)

    got = actions()
    for expected in ("roster.updated", "consent.requested", "consent.granted", "capture.backfill", "capture.message"):
        assert expected in got, (expected, got)
    events = audit.get_audit_log().query(datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc))
    granted = next(e for e in events if e.action == "consent.granted")
    assert granted.actor_name == "Bob" and granted.scope_id == CHANNEL["id"]
    backfill = next(e for e in events if e.action == "capture.backfill")
    assert backfill.count == 1 and backfill.details["authors"] == ["Carol"] and backfill.details["message_ids"] == ["old-1"]
    assert all(SECRET_PHRASE not in e.model_dump_json() for e in events)  # ids and counts only


def test_detection_and_webhook_rejection_are_audited():
    store, graph = get_consent_store(), get_graph_api()
    store.save_project(ProjectTeam(team_id=TEAM["id"], member_aad_ids=["aad-alice", "aad-bob"]))
    graph.chats["c1"] = ChatInfo("c1", "group", "Settlement fix", ["aad-alice", "aad-bob"], {"aad-alice": "Alice"})
    graph.chats["c2"] = ChatInfo("c2", "group", "Lunch", ["aad-alice", "aad-x"])
    asyncio.run(handle_new_chat("c1", store, graph))
    asyncio.run(handle_new_chat("c2", store, graph))
    chat_ids_from_notification({"value": [{"clientState": "wrong", "changeType": "created"}]}, "right")
    got = actions()
    assert got.count("detection.chat_invited") == 1  # chats outside the project leave no trace
    assert "security.webhook_rejected" in got


def test_api_answers_and_ingest_are_audited(client):
    client.post("/ask", json={"question": "How did Ravi usually handle gateway timeouts?"})
    client.post("/ingest")
    events = {e.action: e for e in audit.get_audit_log().query(
        datetime.now(timezone.utc) - timedelta(hours=1), datetime.now(timezone.utc))}
    assert events["ask.answered"].item_ids and events["ask.answered"].source == "api"
    assert events["ingest.completed"].count > 0


def test_audit_endpoint_filters_and_category_prefix(client):
    for a in ("consent.granted", "consent.paused", "capture.message"):
        audit.record(a, scope_id="s1", actor_name="Bob")
    audit.record("capture.message", scope_id="s2", actor_name="Alice", outcome="skipped")
    body = client.get("/audit", params={"action": "consent"}).json()
    assert {e["action"] for e in body["items"]} == {"consent.granted", "consent.paused"}
    assert client.get("/audit", params={"scope_id": "s2"}).json()["total"] == 1
    assert client.get("/audit", params={"actor": "bob", "action": "capture"}).json()["total"] == 1
    assert client.get("/audit", params={"outcome": "skipped"}).json()["items"][0]["scope_id"] == "s2"
    newest_first = client.get("/audit").json()["items"]
    assert newest_first[0]["timestamp"] >= newest_first[-1]["timestamp"]


def test_audit_endpoint_requires_admin_key_when_configured(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_api_key", "k3y")
    assert client.get("/audit").status_code == 401
    assert client.get("/audit", headers={"X-Admin-Key": "nope"}).status_code == 401
    assert client.get("/audit", headers={"X-Admin-Key": "k3y"}).status_code == 200
    monkeypatch.setattr(s, "admin_api_key", "")
    monkeypatch.setattr(s, "live_m365", True)
    assert client.get("/audit").status_code == 403  # live data never served without a key


class FakeBlob:
    def __init__(self, store, name):
        self.store, self.name = store, name

    def create_append_blob(self):
        from azure.core.exceptions import ResourceExistsError
        if self.name in self.store:
            raise ResourceExistsError("exists")
        self.store[self.name] = b""

    def append_block(self, data):
        self.store[self.name] += data

    def download_blob(self):
        return type("D", (), {"readall": lambda _: self.store[self.name]})()


class FakeContainer:
    def __init__(self):
        self.blobs: dict[str, bytes] = {}

    def get_blob_client(self, name):
        return FakeBlob(self.blobs, name)

    def list_blobs(self, name_starts_with):
        return [type("B", (), {"name": n})() for n in sorted(self.blobs) if n.startswith(name_starts_with)]


def test_blob_backend_appends_hourly_jsonl_and_reads_back():
    log = audit.BlobAuditLog.__new__(audit.BlobAuditLog)
    log._container, log._created = FakeContainer(), set()
    t = datetime(2026, 10, 17, 15, 5, tzinfo=timezone.utc)
    log._append([AuditEvent(action="consent.granted", timestamp=t),
                 AuditEvent(action="capture.message", timestamp=t + timedelta(hours=1))])
    log._append([AuditEvent(action="consent.paused", timestamp=t + timedelta(minutes=1))])
    assert sorted(log._container.blobs) == ["events/2026/10/17/15.jsonl", "events/2026/10/17/16.jsonl"]
    assert log._container.blobs["events/2026/10/17/15.jsonl"].count(b"\n") == 2
    got = log.query(t - timedelta(minutes=1), t + timedelta(hours=2))
    assert sorted(e.action for e in got) == ["capture.message", "consent.granted", "consent.paused"]
