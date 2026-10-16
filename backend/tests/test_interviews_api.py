import json

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.services import interview_store

GW, ST, DEC = "risk_sys_payment_gateway", "risk_sys_settlement_batch_job", "risk_dec_kafka_over_rabbitmq"


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean():
    interview_store.clear()


def test_start_creates_a_session_with_grounded_questions(client):
    r = client.post(f"/interviews/{GW}/start")
    assert r.status_code == 201
    s = r.json()
    assert s["risk_id"] == GW and s["topic"] == "Payment Gateway" and s["interviewee"] == "ravi"
    assert s["status"] == "pending" and s["answers"] == [] and s["completed_at"] is None
    assert len(s["questions"]) == 4 and s["id"].startswith("sess_")
    for q in s["questions"]:
        assert q["text"].endswith("?") and q["evidence"] and q["answered"] is False
        assert q["target_entity_id"] == "sys_payment_gateway"


def test_interviewee_defaults_to_best_owner_and_can_be_overridden(client):
    assert client.post(f"/interviews/{ST}/start").json()["interviewee"] == "arjun"
    assert client.post(f"/interviews/{DEC}/start").json()["interviewee"] == "priya"
    r = client.post(f"/interviews/{GW}/start", json={"interviewee": "meera", "max_questions": 2})
    assert r.status_code == 201 and r.json()["interviewee"] == "meera" and len(r.json()["questions"]) == 2
    assert client.post(f"/interviews/{GW}/start", json={"interviewee": "ghost"}).status_code == 400


def test_departing_owner_is_preferred(client, monkeypatch):
    from app.api import interviews
    from app.graph import get_graph
    from app.models import RiskItem

    risk = RiskItem(topic="T", owner_candidates=["priya", "ravi"])  # priya is the stronger owner...
    assert interviews.pick_interviewee(risk, get_graph()) == "ravi"  # ...but ravi is departing


def test_unknown_risk_and_bad_bodies(client):
    assert client.post("/interviews/risk_nope/start").status_code == 404
    assert client.post(f"/interviews/{GW}/start", json={"max_questions": 0}).status_code == 422
    assert client.post(f"/interviews/{GW}/start", json={"bogus": 1}).status_code == 422


def test_starting_again_resumes_the_open_session(client):
    first = client.post(f"/interviews/{GW}/start")
    again = client.post(f"/interviews/{GW}/start")
    assert again.status_code == 200 and again.json()["id"] == first.json()["id"]
    fresh = client.post(f"/interviews/{GW}/start", json={"force_new": True})
    assert fresh.status_code == 201 and fresh.json()["id"] != first.json()["id"]
    assert len(client.get("/interviews").json()) == 2


def test_get_and_list(client):
    a = client.post(f"/interviews/{GW}/start").json()
    b = client.post(f"/interviews/{ST}/start").json()
    assert client.get(f"/interviews/{a['id']}").json() == a
    assert client.get("/interviews/sess_nope").status_code == 404
    assert [s["id"] for s in client.get("/interviews", params={"risk_id": ST}).json()] == [b["id"]]
    assert {s["id"] for s in client.get("/interviews").json()} == {a["id"], b["id"]}


def test_sessions_are_persisted_and_reload(client):
    s = client.post(f"/interviews/{GW}/start").json()
    on_disk = json.loads((get_settings().state_dir / "interviews.json").read_text())
    assert [x["id"] for x in on_disk] == [s["id"]]
    interview_store._sessions = None  # simulate a process restart: force a reload from disk
    assert client.get(f"/interviews/{s['id']}").json() == s


def test_reset_ingest_wipes_sessions(client):
    client.post(f"/interviews/{GW}/start")
    assert client.post("/ingest").status_code == 200
    assert client.get("/interviews").json() == []
