import re

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models import GeneratedDoc

GW, DEC = "risk_sys_payment_gateway", "risk_dec_kafka_over_rabbitmq"
ANSWERS = [
    "First I run `gwctl pool-status` and read the stale count; above 20% means idle sockets, so drain and restart deploy/pg-adapter.",
    "Restart with `gwctl drain` then `kubectl rollout restart deploy/pg-adapter`. Never restart between 02:00 and 03:00 UTC.",
    "If the cert pin changed, update PARTNER_CERT_PIN in the pg-adapter configmap and restart; INC-171 was this.",
    "Confirm recovery on the PG Auth Grafana dashboard. Escalate to the acquirer NOC after 10 minutes without recovery.",
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh(client):
    client.post("/ingest")


def assert_cited(doc: dict):
    """Every item in a cited section ends with [n] refs; refs are dense 1..N and match the source list."""
    d = GeneratedDoc.model_validate(doc)
    body = d.markdown.split("## Sources")[0]
    skip = {"## Runbooks", "## Open questions"}
    section, refs = None, set()
    for ln in body.splitlines():
        if ln.startswith("## "):
            section = ln
        elif section and section not in skip and section != "## Overview" and re.match(r"^(- |\d+\. )", ln):
            nums = [int(n) for n in re.findall(r"\[(\d+)\]", ln)]
            assert nums and ln.rstrip().endswith("]"), f"uncited: {ln}"
            refs |= set(nums)
    assert refs == set(range(1, len(d.citations) + 1))
    assert len(d.markdown.split("## Sources")[1].strip().splitlines()) == len(d.citations)


def interview(client, risk=GW, texts=ANSWERS):
    s = client.post(f"/interviews/{risk}/start").json()
    for q, t in zip(s["questions"], texts):
        client.post(f"/interviews/{s['id']}/answer", json={"question_id": q["id"], "text": t})
    return s


def test_ravis_pack_before_any_interview(client):
    r = client.post("/artifacts/handover", json={"person_id": "ravi"})
    assert r.status_code == 200
    doc = r.json()
    assert doc["type"] == "handover" and doc["subject_person"] == "ravi" and doc["title"] == "Handover pack: Ravi Kumar"
    assert doc["topic"] is None and doc["model"] == "rules"
    md = doc["markdown"]
    assert "departing on 2026-11-15" in md and "**Payment Gateway**: primary owner" in md
    for inc in ("INC-142", "INC-155", "INC-171", "INC-188"):
        assert f"- {inc}:" in md
    assert "Only Ravi Kumar" in md and "No interview has been published for Payment Gateway" in md
    assert "Nobody else holds Payment Gateway knowledge" in " ".join(doc["open_questions"])
    assert "no runbook generated yet" in md and "## What Ravi told us" not in md
    assert_cited(doc)


def test_pack_picks_up_published_interviews_and_runbooks(client):
    s = interview(client)
    client.post(f"/interviews/{s['id']}/approve")
    rb = client.post("/artifacts/runbook", json={"topic": "Payment Gateway"}).json()
    doc = client.post("/artifacts/handover", json={"person_id": "ravi"}).json()
    md = doc["markdown"]
    assert "## What Ravi told us in interviews" in md and "`gwctl drain`" in md
    assert any(c["artifact_type"] == "interview" for c in doc["citations"])
    assert f"runbook “Runbook: Payment Gateway” (document {rb['id']})" in md
    assert not any("No interview has been published for Payment Gateway" in q for q in doc["open_questions"])
    assert not any(q.startswith("There is no runbook for Payment Gateway") for q in doc["open_questions"])
    assert "98450" not in md
    assert_cited(doc)


def test_unanswered_interview_questions_are_flagged(client):
    s = interview(client, texts=ANSWERS[:2])
    doc = client.post("/artifacts/handover", json={"person_id": "ravi"}).json()
    assert any("interview question(s) about Payment Gateway are still unanswered" in q for q in doc["open_questions"])
    assert "## What Ravi told us" not in doc["markdown"], "drafts are not published knowledge"


def test_decision_pack_carries_the_rationale_from_priyas_emails(client):
    doc = client.post("/artifacts/handover", json={"person_id": "priya"}).json()
    md = doc["markdown"]
    assert "## Key decisions and their rationale" in md
    decision_lines = [ln for ln in md.splitlines() if ln.startswith("- Kafka over RabbitMQ:")]
    assert decision_lines and any("replay" in ln.lower() for ln in decision_lines)
    assert any(c["artifact_type"] == "email" for c in doc["citations"])
    assert "Incidents handled" not in md, "Priya resolved no incidents"
    assert_cited(doc)


def test_topics_filter_and_ordering_of_default_selection(client):
    filtered = client.post("/artifacts/handover", json={"person_id": "ravi", "topics": ["Kafka Event Bus"]}).json()
    assert "areas most at risk (Kafka Event Bus)" in filtered["markdown"]
    default = client.post("/artifacts/handover", json={"person_id": "arjun"}).json()["markdown"]
    assert "areas most at risk (Settlement Batch Job)" in default and "Only Arjun Mehta" in default


def test_who_else_to_ask_lists_cited_backups(client):
    md = client.post("/artifacts/handover", json={"person_id": "priya"}).json()["markdown"]
    assert "## Who else to ask" in md and "Ravi Kumar" in md.split("## Who else to ask")[1].split("##")[0]


def test_every_person_gets_a_valid_cited_pack(client):
    for pid in ("ravi", "priya", "arjun", "meera"):
        r = client.post("/artifacts/handover", json={"person_id": pid})
        assert r.status_code == 200, pid
        assert_cited(r.json())


def test_stored_listed_and_errors(client):
    doc = client.post("/artifacts/handover", json={"person_id": "meera"}).json()
    assert client.get(f"/artifacts/{doc['id']}").json() == doc
    assert [d["id"] for d in client.get("/artifacts", params={"type": "handover"}).json()] == [doc["id"]]
    assert client.post("/artifacts/handover", json={"person_id": "ghost"}).status_code == 404
    assert client.post("/artifacts/handover", json={"person_id": "sys_payment_gateway"}).status_code == 404
    assert client.post("/artifacts/handover", json={"person_id": "ravi", "topics": ["Nope"]}).status_code == 404
    assert client.post("/artifacts/handover", json={}).status_code == 422
