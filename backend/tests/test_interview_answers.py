import pytest
from fastapi.testclient import TestClient

from app.agents.interviewer import build_answer_artifact, is_vague
from app.ingestion.chunker import chunk_artifact
from app.ingestion.redactor import redact_artifact
from app.models import InterviewSession
from app.graph import get_graph
from app.main import app
from app.services import interview_store
from app.services.search import get_search

GW = "risk_sys_payment_gateway"
DETAILED = ("First I run `gwctl pool-status` and read the stale count. If stale is above 20% it is the idle-socket "
            "problem, so I drain with `gwctl drain` and then restart deploy/pg-adapter.")


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def session(client):
    interview_store.clear()
    return client.post(f"/interviews/{GW}/start").json()


def answer(client, s, i=0, text=DETAILED, **extra):
    return client.post(f"/interviews/{s['id']}/answer", json={"question_id": s["questions"][i]["id"], "text": text, **extra})


@pytest.mark.parametrize("text,vague", [
    ("Check the pool status and restart.", True),
    ("I am not sure, I would have to look it up again later today when I can.", True),
    ("It depends.", True),
    ("Just restart it.", True),
    ("Run `gwctl pool-status`.", False),
    ("Restart deploy/pg-adapter.", False),
    ("See INC-142.", False),
    (DETAILED, False),
    ("We first look at the connection pool and then at the partner status page, and after that we usually "
     "decide whether to wait or to restart based on how long the problem has lasted and who is on call.", False),
])
def test_is_vague(text, vague):
    assert is_vague(text) is vague


def test_detailed_answer_is_stored_as_a_draft(client, session):
    n_nodes = get_graph().stats().node_count
    r = answer(client, session)
    assert r.status_code == 200
    body = r.json()
    a = body["stored_answer"]
    assert a["text"] == DETAILED and a["answered_by"] == "ravi" and a["session_id"] == session["id"]
    assert a["artifact_id"] is None and body["artifact_id"] == f"art_interview_{a['id']}"
    assert body["chunks_indexed"] == 0 and body["follow_up"] is None
    s = body["session"]
    assert s["status"] == "in_progress" and s["questions"][0]["answered"] is True and len(s["questions"]) == 4
    assert client.get(f"/interviews/{session['id']}").json() == s
    assert get_graph().stats().node_count == n_nodes, "drafts must not touch the graph"
    assert get_search().search("gwctl drain pg-adapter restart stale", top_k=20, artifact_types=["interview"]) == []


def test_vague_answer_triggers_one_follow_up(client, session):
    r = answer(client, session, 0, "Check the pool status and restart.").json()
    fu = r["follow_up"]
    assert fu["follow_up_of"] == session["questions"][0]["id"] and "Check the pool status" in fu["text"]
    assert fu["evidence"] == session["questions"][0]["evidence"] and fu["answered"] is False
    assert len(r["session"]["questions"]) == 5 and r["session"]["questions"][-1]["id"] == fu["id"]
    # answering the same vague question again does not stack another follow-up
    again = answer(client, session, 0, "Check pool status, restart it.").json()
    assert again["follow_up"] is None and len(again["session"]["questions"]) == 5
    # a vague answer to the follow-up itself is accepted without chaining
    last = client.post(f"/interviews/{session['id']}/answer", json={"question_id": fu["id"], "text": "Just restart it."}).json()
    assert last["follow_up"] is None and len(last["session"]["questions"]) == 5


def test_replacing_an_answer_keeps_one_answer_and_drops_unneeded_follow_up(client, session):
    answer(client, session, 0, "Not sure.")
    r = answer(client, session, 0, DETAILED).json()
    s = r["session"]
    assert len(s["answers"]) == 1 and s["answers"][0]["text"] == DETAILED
    assert len(s["questions"]) == 4, "follow-up removed once the real detail arrived"


def test_answering_everything_does_not_complete_without_approval(client, session):
    for i in range(4):
        r = answer(client, session, i)
    s = r.json()["session"]
    assert all(q["answered"] for q in s["questions"]) and len(s["answers"]) == 4
    assert s["status"] == "in_progress" and s["completed_at"] is None


def test_answered_by_override_and_validation(client, session):
    assert answer(client, session, 0, answered_by="meera").json()["stored_answer"]["answered_by"] == "meera"
    assert answer(client, session, 1, answered_by="ghost").status_code == 400
    assert answer(client, session, 1, answered_by="sys_payment_gateway").status_code == 400  # not a person


def test_error_cases(client, session):
    assert answer(client, session, 0, text="   ").status_code == 422
    assert answer(client, session, 0, text="x" * 5001).status_code == 422
    assert client.post("/interviews/sess_nope/answer", json={"question_id": "q", "text": "hi"}).status_code == 404
    assert client.post(f"/interviews/{session['id']}/answer", json={"question_id": "q_nope", "text": "hi"}).status_code == 404
    assert client.post(f"/interviews/{session['id']}/answer", json={"question_id": "q"}).status_code == 422
    s = interview_store.get(session["id"])
    s.status = "completed"
    interview_store.save(s)
    assert answer(client, session, 0).status_code == 409


def test_a_draft_answer_can_become_a_redacted_indexable_artifact(client, session):
    text = DETAILED + " If it still fails call the acquirer NOC on +91 98450 12345."
    body = answer(client, session, 0, text).json()
    s = InterviewSession.model_validate(body["session"])
    art = build_answer_artifact(s, s.questions[0], s.answers[0], "Ravi Kumar")
    assert art.id == body["artifact_id"] and art.type == "interview" and art.author == "ravi"
    assert art.title == "Interview: Payment Gateway" and s.questions[0].text in art.body and "gwctl drain" in art.body
    red, counts = redact_artifact(art)
    assert counts == {"phone": 1} and "98450" not in red.body
    chunks = chunk_artifact(red)
    assert chunks and all(c.artifact_type == "interview" for c in chunks)
