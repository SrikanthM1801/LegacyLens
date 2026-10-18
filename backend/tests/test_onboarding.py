"""Onboarding assessment end to end, offline: assign in the channel -> learn 1:1 -> start ->
2 answers -> evaluation -> candidate sees the result first, then the channel gets the card."""

import asyncio

import pytest
from fastapi.testclient import TestClient

from app.agents import assessor
from app.bot import agent, onboarding
from app.graph import get_graph
from app.main import app
from app.models import OnboardingSession, ProjectTeam
from app.services import audit
from app.services.consent_store import get_consent_store
from app.services.graph_api import get_graph_api
from app.services.onboarding_store import get_onboarding_store
from app.services.teams_connector import get_connector
from tests.test_consent import BOT, CHANNEL, TEAM

OWNER = {"id": "29:owner", "name": "Owner Olga", "aadObjectId": "aad-owner"}
KAVI = {"id": "29:kavi", "name": "A, Kaviraj", "aadObjectId": "aad-kavi"}
CHANNEL_DATA = {"team": {**TEAM, "aadGroupId": "group-1"}}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/ingest")
        yield c


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for s in (get_onboarding_store(), get_consent_store(), audit.get_audit_log()):
        s.clear()
    get_graph_api().reset()
    get_connector().reset()
    get_graph_api().owners["group-1"] = {"aad-owner"}
    get_consent_store().save_project(ProjectTeam(team_id=TEAM["id"], member_aad_ids=["aad-owner", "aad-kavi"]))
    spawned = []
    monkeypatch.setattr(agent, "_spawn", spawned.append)

    async def roster(_c, _t):
        return ["aad-owner", "aad-kavi"]

    async def resolve(_c, _t, member_id):
        return {"29:kavi": "aad-kavi", "29:owner": "aad-owner"}.get(member_id)

    monkeypatch.setattr(agent, "fetch_team_member_ids", roster)
    monkeypatch.setattr(agent, "resolve_member_aad", resolve)
    yield spawned


def post(client, conversation, frm, text="", value=None, entities=None, channel_data=None):
    body = {"type": "message", "id": "m", "channelId": "msteams", "serviceUrl": "https://smba.trafficmanager.net/in/",
            "from": frm, "recipient": BOT, "conversation": conversation, "deliveryMode": "expectReplies",
            "channelData": channel_data or {}, "text": text, "entities": entities or []}
    if value is not None:
        body["value"] = value
    r = client.post("/api/messages", json=body)
    assert r.status_code == 200, r.text
    return [a for a in r.json()["activities"] if a.get("type") != "typing"]


def assign(client, frm=OWNER, target=KAVI):
    mentions = [{"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"},
                {"type": "mention", "mentioned": {"id": target["id"], "name": target["name"]}, "text": f"<at>{target['name']}</at>"}]
    return post(client, CHANNEL, frm, f"<at>LegacyLens</at> assign <at>{target['name']}</at>", entities=mentions,
                channel_data=CHANNEL_DATA)


def personal(client, text="", value=None):
    return post(client, {"id": "a:personal-aad-kavi", "conversationType": "personal"}, KAVI, text, value)


def session() -> OnboardingSession:
    [s] = get_onboarding_store().list()
    return s


def test_only_owners_can_assign_and_candidate_gets_a_welcome(client):
    [reply] = assign(client, frm={**KAVI})
    assert "Only a team owner" in reply["text"] and get_onboarding_store().list() == []
    [reply] = assign(client)
    assert "Assigned an onboarding assessment" in reply["text"]
    s = session()
    assert (s.status, s.candidate_aad_id, s.assigned_by_name) == ("learning", "aad-kavi", "Owner Olga")
    assert "aad-kavi" in get_graph_api().user_installs and get_connector().opened == ["aad-kavi"]
    [(conv, welcome)] = get_connector().sent
    assert conv == s.conversation_id and "How you'll be assessed" in str(welcome)
    assert "already has an onboarding session" in assign(client)[0]["text"]


def test_full_journey_learn_start_answer_evaluate_post(client, fresh):
    assign(client)
    personal(client, "How did Ravi usually handle gateway timeouts?")
    personal(client, "What do I do if the bank file is late?")
    personal(client, "topics")
    s = session()
    assert [e.kind for e in s.learning_log] == ["ask", "ask", "topics"] and s.learning_log[0].topics
    status = personal(client, "status")[0]["text"]
    assert "Questions asked: **3**" in status

    replies = personal(client, "start")
    assert "2 questions" in replies[0]["text"] and replies[1]["attachments"]
    s = session()
    assert s.status == "assessing" and [q.focus for q in s.questions] == ["studied", "unexplored"]
    assert all(q.expected_points and q.source_artifact_ids for q in s.questions)

    q1, q2 = s.questions
    replies = personal(client, value={"verb": onboarding.ANSWER_VERB, "session_id": s.id, "question_id": q1.id,
                                      "answer": " ".join(q1.expected_points)})
    assert "Next question" in replies[0]["text"] and q2.text in str(replies[1])
    replies = personal(client, "I am not sure, something about the configuration")  # typed answer works too
    assert "evaluating" in replies[0]["text"] and fresh

    asyncio.run(fresh.pop())  # finish(): evaluate + deliver
    s = session()
    ev = s.evaluation
    assert s.status == "posted" and ev.answer_scores[0].score > ev.answer_scores[1].score
    assert 0 <= ev.overall <= 100 and ev.readiness in ("ready", "almost", "needs_more_time")
    assert ev.topics_explored and ev.recommended_docs
    sent = get_connector().sent
    assert sent[-2][0] == s.conversation_id and "Your onboarding result" in str(sent[-2][1])   # candidate first
    assert sent[-1][0] == CHANNEL["id"] and "Onboarding assessment · A, Kaviraj" in str(sent[-1][1])
    actions = {e.action for e in audit.get_audit_log().query(s.assigned_at.replace(year=2000), ev.evaluated_at.replace(year=2100))}
    assert {"onboarding.assigned", "onboarding.question_asked", "onboarding.assessment_started",
            "onboarding.answered", "onboarding.evaluated", "onboarding.result_posted"} <= actions


def test_scoring_is_computed_not_guessed():
    s = OnboardingSession(project_team_id="t", channel_id="c", candidate_aad_id="x", candidate_name="X")
    from app.models import AssessmentQuestion
    s.questions = [AssessmentQuestion(text="q?", topic="T", focus="studied", expected_points=["alpha beta gamma"], answer="alpha beta gamma"),
                   AssessmentQuestion(text="q?", topic="U", focus="unexplored", expected_points=["delta epsilon"], answer="")]
    ev = assessor.evaluate(s, get_graph(), type("L", (), {"model_name": "mock"})())
    assert [a.score for a in ev.answer_scores] == [5, 0] and ev.journey_score == 0
    assert ev.overall == round(100 * (0.65 * 2.5 / 5)) and ev.readiness == "needs_more_time"


def test_start_without_assignment_explains_how(client):
    [reply] = personal(client, "start")
    assert "assign" in reply["text"]


def test_admin_endpoints_list_sessions(client):
    assign(client)
    body = client.get("/onboarding/sessions").json()
    assert len(body) == 1 and body[0]["candidate_name"] == "A, Kaviraj"
    assert client.get(f"/onboarding/sessions/{body[0]['id']}").status_code == 200
    assert client.get("/onboarding/sessions/nope").status_code == 404


def test_learning_topics_follow_the_question_not_the_whole_document():
    from app.models import Edge, Entity, EntityType, RelationType
    from app.graph.networkx_store import NetworkXGraphStore

    g = NetworkXGraphStore()
    g.add_artifact_node("d1", "doc", "Architecture")
    for tid, name in (("t1", "Release Process"), ("t2", "AKS Hosting"), ("t3", "Sprint length")):
        g.add_entity(Entity(id=tid, name=name, type=EntityType.PROCESS, artifact_ids=["d1"]))
        g.add_edge(Edge(source="d1", target=tid, relation=RelationType.MENTIONS, evidence_artifact_ids=["d1"]))
    assert assessor.topics_for_artifacts(g, ["d1"], "How does the release process work?") == ["Release Process"]
    assert len(assessor.topics_for_artifacts(g, ["d1"], "Tell me about the project")) == 2
    assert len(assessor.topics_for_artifacts(g, ["d1"])) == 3


def test_live_extraction_ignores_the_sample_topic_catalog():
    from datetime import datetime, timezone
    from app.ingestion.extractor import RuleExtractor
    from app.models import Artifact, ArtifactType

    a = Artifact(type=ArtifactType.DOC, source_id="x", author="p", timestamp=datetime.now(timezone.utc),
                 title="Release Process", body="The release process and the settlement batch job are documented here.")
    assert any(e.type == "process" for e in RuleExtractor().extract([a], []).entities)          # sample mode
    assert not [e for e in RuleExtractor(catalog=False).extract([a], []).entities if e.type != "person"]  # live mode


def test_assign_someone_outside_the_team_by_email(client):
    get_graph_api().users["testlegacylens@sandbox.onmicrosoft.com"] = ("aad-test", "TestUser LegacyLens")
    mention = [{"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}]
    [reply] = post(client, CHANNEL, OWNER, "<at>LegacyLens</at> assign TestLegacyLens@sandbox.onmicrosoft.com",
                   entities=mention, channel_data=CHANNEL_DATA)
    assert "Assigned an onboarding assessment to **TestUser LegacyLens**" in reply["text"]
    assert session().candidate_aad_id == "aad-test" and "aad-test" in get_graph_api().user_installs


def test_assign_from_a_shared_contact_card(client):
    get_graph_api().users["newjoiner@contoso.com"] = ("aad-new", "New Joiner")
    body_mention = [{"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}]
    card_html = '<span itemscope itemtype="http://schema.skype.com/Contact" data-email="newjoiner@contoso.com">New Joiner</span>'
    [reply] = post(client, CHANNEL, OWNER, f"<at>LegacyLens</at> assign {card_html}", entities=body_mention,
                   channel_data=CHANNEL_DATA)
    assert "New Joiner" in reply["text"] and session().candidate_aad_id == "aad-new"


def test_unknown_email_explains_both_ways_to_assign(client):
    mention = [{"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}]
    [reply] = post(client, CHANNEL, OWNER, "<at>LegacyLens</at> assign nobody@nowhere.com", entities=mention,
                   channel_data=CHANNEL_DATA)
    assert "type their email" in reply["text"] and get_onboarding_store().list() == []


def test_a_session_that_never_reached_the_candidate_can_be_reassigned(client):
    get_onboarding_store().save(OnboardingSession(project_team_id="t", channel_id="c", candidate_aad_id="aad-kavi",
                                                  candidate_name="A, Kaviraj", error="install failed"))
    assert "Assigned" in assign(client)[0]["text"]
    statuses = sorted(x.status for x in get_onboarding_store().list())
    assert statuses == ["cancelled", "learning"]
