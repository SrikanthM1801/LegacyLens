import time

import pytest
from fastapi.testclient import TestClient

from app.graph import get_graph
from app.main import app
from app.models import Edge, RelationType as R
from app.services import risk_store

PLANTED = ["risk_sys_payment_gateway", "risk_dec_kafka_over_rabbitmq", "risk_sys_settlement_batch_job"]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan builds the mock index and graph
        yield c


def test_list_risks_ranked_with_planted_on_top(client):
    body = client.get("/risks").json()
    assert body["total"] == 8 and [r["id"] for r in body["items"][:3]] == PLANTED
    scores = [r["risk_score"] for r in body["items"]]
    assert scores == sorted(scores, reverse=True)
    top = body["items"][0]
    assert top["risk_level"] == "high" and top["evidence"] and top["owner_candidates"][0] == "ravi"
    assert {"snippet", "artifact_id", "title"} <= set(top["evidence"][0])


def test_filters_and_limit(client):
    high = client.get("/risks", params={"level": "high"}).json()
    assert high["total"] == 3 and {r["risk_level"] for r in high["items"]} == {"high"}
    dec = client.get("/risks", params={"entity_type": "decision"}).json()
    assert [r["id"] for r in dec["items"]] == [PLANTED[1]]
    top2 = client.get("/risks", params={"limit": 2}).json()
    assert len(top2["items"]) == 2 and top2["total"] == 8
    assert client.get("/risks", params={"level": "critical"}).status_code == 422
    assert client.get("/risks", params={"limit": 0}).status_code == 422
    assert client.get("/risks", params={"entity_type": "person"}).json()["items"] == []


def test_risk_detail_and_404(client):
    r = client.get(f"/risks/{PLANTED[2]}")
    assert r.status_code == 200 and r.json()["topic"] == "Settlement Batch Job"
    assert client.get("/risks/risk_nope").status_code == 404


def test_reads_are_cached_until_invalidated(client):
    first = client.get("/risks").json()["computed_at"]
    assert client.get("/risks").json()["computed_at"] == first
    risk_store.invalidate()
    time.sleep(0.01)
    assert client.get("/risks").json()["computed_at"] != first


def test_ingest_refreshes_risks(client):
    before = client.get("/risks").json()["computed_at"]
    assert client.post("/ingest").status_code == 200
    after = client.get("/risks").json()
    assert after["computed_at"] != before and [r["id"] for r in after["items"][:3]] == PLANTED


def test_documenting_a_topic_lowers_its_risk_after_invalidation(client):
    g = get_graph()
    before = client.get(f"/risks/{PLANTED[2]}").json()
    g.add_artifact_node("art_interview_t17", "interview", "Interview: settlement")
    g.add_edge(Edge(source="sys_settlement_batch_job", target="art_interview_t17", relation=R.DOCUMENTED_BY))
    assert client.get(f"/risks/{PLANTED[2]}").json() == before, "stale until invalidated, by design"
    risk_store.invalidate()
    after = client.get(f"/risks/{PLANTED[2]}").json()
    assert after["coverage_score"] > before["coverage_score"] and after["risk_score"] < before["risk_score"]
    client.post("/ingest")  # restore a clean graph for any later test
