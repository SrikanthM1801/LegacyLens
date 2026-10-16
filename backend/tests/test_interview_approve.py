import pytest
from fastapi.testclient import TestClient

from app.graph import get_graph
from app.ingestion.pipeline import ingest_lock
from app.main import app
from app.services import interview_store, risk_store
from app.services.search import get_search

GW, ST = "risk_sys_payment_gateway", "risk_sys_settlement_batch_job"
ANSWERS = [
    "First I run `gwctl pool-status` and read the stale count; above 20% means idle sockets, so drain and restart deploy/pg-adapter.",
    "Restart with `gwctl drain` then `kubectl rollout restart deploy/pg-adapter`, never between 02:00 and 03:00 UTC.",
    "If the cert pin changed, update PARTNER_CERT_PIN in the pg-adapter configmap and restart; INC-171 was this.",
    "Call the acquirer NOC after 10 minutes without recovery; their number is +91 98450 12345.",
]


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def fresh(client):
    assert client.post("/ingest").status_code == 200  # clean graph, index, sessions, risk cache
    yield


def interview(client, risk=GW, n=4, texts=ANSWERS):
    s = client.post(f"/interviews/{risk}/start").json()
    for q, text in list(zip(s["questions"], texts))[:n]:
        assert client.post(f"/interviews/{s['id']}/answer", json={"question_id": q["id"], "text": text}).status_code == 200
    return s


def test_approving_publishes_answers_and_drops_the_risk_red_to_green(client):
    s = interview(client)
    r = client.post(f"/interviews/{s['id']}/approve")
    assert r.status_code == 200
    body = r.json()
    before, after = body["risk_before"], body["risk_after"]
    assert before["risk_level"] == "high" and before["risk_score"] == 1.0 and before["missing_doc_types"]
    assert after["risk_level"] == "low" and after["risk_score"] < 0.1 and after["coverage_score"] > 0.9
    assert after["missing_doc_types"] == [] and after["id"] == GW
    assert body["artifacts_published"] == 4 and body["chunks_indexed"] >= 4
    sess = body["session"]
    assert sess["status"] == "completed" and sess["completed_at"] and all(a["artifact_id"] for a in sess["answers"])
    assert client.get(f"/risks/{GW}").json()["risk_score"] == after["risk_score"], "cache refreshed"
    assert client.get("/risks").json()["items"][0]["id"] != GW, "gateway is no longer the top risk"


def test_other_risks_are_unaffected(client):
    before = {r["id"]: r["risk_score"] for r in client.get("/risks").json()["items"]}
    client.post(f"/interviews/{interview(client)['id']}/approve")
    after = {r["id"]: r["risk_score"] for r in client.get("/risks").json()["items"]}
    assert after[GW] < before[GW]
    assert {k: v for k, v in after.items() if k != GW} == {k: v for k, v in before.items() if k != GW}, \
        "interviewing about one topic must not move any other risk"


def test_published_knowledge_is_searchable_cited_and_redacted(client):
    client.post(f"/interviews/{interview(client)['id']}/approve")
    hits = get_search().search("drain and restart pg-adapter gwctl pool-status stale", top_k=10, artifact_types=["interview"])
    assert hits and all(h.artifact_type == "interview" and h.title == "Interview: Payment Gateway" for h in hits)
    assert all("98450" not in h.snippet for h in get_search().search("acquirer NOC number", top_k=10, artifact_types=["interview"]))
    g = get_graph()
    docs = [e.target for e in g.edges(source="sys_payment_gateway", relation="DOCUMENTED_BY")]
    assert len([d for d in docs if d.startswith("art_interview_")]) == 4
    assert g.stats().nodes_by_type["artifact:interview"] == 4 and g.edges(source="ravi", relation="AUTHORED", target=docs[-1])


def test_a_single_answer_already_lowers_the_risk_but_a_fuller_interview_lowers_it_more(client):
    one = client.post(f"/interviews/{interview(client, n=1)['id']}/approve").json()
    assert one["artifacts_published"] == 1
    assert one["risk_after"]["risk_score"] < one["risk_before"]["risk_score"]
    client.post("/ingest")
    full = client.post(f"/interviews/{interview(client, n=4)['id']}/approve").json()
    assert full["risk_after"]["coverage_score"] > one["risk_after"]["coverage_score"]


def test_unanswered_questions_are_not_published(client):
    s = interview(client, n=2)
    body = client.post(f"/interviews/{s['id']}/approve").json()
    assert body["artifacts_published"] == 2 and sum(q["answered"] for q in body["session"]["questions"]) == 2


def test_error_cases(client):
    s = client.post(f"/interviews/{GW}/start").json()
    r = client.post(f"/interviews/{s['id']}/approve")
    assert r.status_code == 409 and "no answers" in r.json()["detail"]
    assert client.post("/interviews/sess_nope/approve").status_code == 404
    done = interview(client)
    assert client.post(f"/interviews/{done['id']}/approve").status_code == 200
    assert client.post(f"/interviews/{done['id']}/approve").status_code == 409  # not twice
    late = client.post(f"/interviews/{done['id']}/answer", json={"question_id": done["questions"][0]["id"], "text": "x"})
    assert late.status_code == 409


def test_approval_waits_out_a_running_ingest(client):
    s = interview(client)
    assert ingest_lock.acquire(blocking=False)
    try:
        assert client.post(f"/interviews/{s['id']}/approve").status_code == 409
    finally:
        ingest_lock.release()
    assert client.post(f"/interviews/{s['id']}/approve").status_code == 200


def test_a_failed_publish_leaves_the_session_open_and_retry_works(client, monkeypatch):
    from app.services import interview_publish as pub

    s = interview(client)
    real = pub.index_artifacts
    monkeypatch.setattr(pub, "index_artifacts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("azure down")))
    r = client.post(f"/interviews/{s['id']}/approve")
    assert r.status_code == 502 and "retry" in r.json()["detail"]
    assert client.get(f"/interviews/{s['id']}").json()["status"] == "in_progress"
    monkeypatch.setattr(pub, "index_artifacts", real)
    ok = client.post(f"/interviews/{s['id']}/approve")
    assert ok.status_code == 200 and ok.json()["session"]["status"] == "completed"
    assert get_graph().stats().nodes_by_type["artifact:interview"] == 4
