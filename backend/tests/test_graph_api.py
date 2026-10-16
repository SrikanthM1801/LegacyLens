import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:  # lifespan builds the mock index and graph
        yield c


def test_graph_hides_artifacts_by_default(client):
    body = client.get("/graph").json()
    assert {n["kind"] for n in body["nodes"]} == {"entity"}
    ids = {n["id"] for n in body["nodes"]}
    assert len(ids) == 19 and {"ravi", "sys_payment_gateway", "inc_142"} <= ids
    assert all(e["source"] in ids and e["target"] in ids for e in body["edges"])
    assert {e["relation"] for e in body["edges"]} >= {"RESOLVED", "AFFECTS", "DECIDED", "COMMITTED_TO"}
    assert body["stats"]["node_count"] > len(ids)  # stats still describe the whole graph


def test_graph_can_include_artifacts(client):
    body = client.get("/graph", params={"include_artifacts": True}).json()
    assert {n["kind"] for n in body["nodes"]} == {"entity", "artifact"}
    assert len(body["nodes"]) == body["stats"]["node_count"] == len({n["id"] for n in body["nodes"]})


def test_people_show_planted_concentration(client):
    people = {p["id"]: p for p in client.get("/people").json()}
    assert list(people) == sorted(people, key=lambda i: people[i]["name"]), "alphabetical, not ranked"
    assert people["ravi"]["is_departing"] is True and people["ravi"]["role"]
    assert "Payment Gateway" in people["ravi"]["concentrated_topics"]
    assert "Settlement Batch Job" in people["arjun"]["concentrated_topics"]
    assert "Kafka over RabbitMQ" in people["priya"]["concentrated_topics"]
    # Meera alone built the (low-activity) checkout work: a real but minor concentration.
    assert people["meera"]["concentrated_topics"] == ["Checkout API"]


def test_documented_topics_are_not_concentrated(client):
    concentrated = {t for p in client.get("/people").json() for t in p["concentrated_topics"]}
    assert not concentrated & {"Release Process", "Onboarding", "API Authentication"}


def test_shares_are_consistent(client):
    shares: dict[str, float] = {}
    for p in client.get("/people").json():
        for t in p["topics"]:
            assert 0 < t["share"] <= 1 and t["activity"] > 0
            shares[t["entity_id"]] = shares.get(t["entity_id"], 0) + t["share"]
    assert all(abs(total - 1) < 0.02 for total in shares.values())
