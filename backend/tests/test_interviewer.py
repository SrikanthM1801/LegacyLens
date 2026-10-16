import pytest

from app.agents.gap_analyzer import analyze_risks
from app.agents.interviewer import (
    GeneratedQuestion, QuestionSet, best_quote, generate_questions, template_questions,
)
from app.config import get_settings
from app.graph import NetworkXGraphStore
from app.ingestion.extractor import RuleExtractor
from app.ingestion.pipeline import run_ingest
from app.services.llm import MockLLM
from app.services.search import InMemorySearch


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    llm = MockLLM(get_settings())
    g = NetworkXGraphStore(tmp_path_factory.mktemp("g") / "g.json")
    s = InMemorySearch(llm)
    run_ingest(search=s, graph=g, extractor=RuleExtractor(), llm=llm)
    risks = {r.entity_id: r for r in analyze_risks(g, s)}
    return g, s, llm, risks


class ScriptedLLM:
    model_name = "scripted"

    def __init__(self, questions=None, fail=False):
        self.questions, self.fail, self.prompts = questions or [], fail, []

    def chat_json(self, system, user, schema):
        self.prompts.append((system, user))
        if self.fail:
            raise ValueError("boom")
        return QuestionSet(questions=self.questions)


def test_best_quote_prefers_specific_commands_and_strips_chat_prefix():
    snippet = ("Channel: #payments-oncall\n[2025-11-03 09:25] Arjun: Hello there everyone on the call today.\n"
               "[2025-11-03 09:40] Ravi: Do it in order. First thing I always check: `gwctl pool-status` and read the stale count.")
    q = best_quote(snippet)
    assert q.startswith("First thing I always check: `gwctl pool-status`") and "Ravi:" not in q
    both = ("[2025-11-03 09:25] Arjun: Please check `gwctl pool-status` first, then the logs.\n"
            "[2025-11-03 09:40] Ravi: It is always the stale sockets in the connection pool, every time.")
    assert best_quote(both).startswith("Please check")                      # richest sentence wins by default
    assert best_quote(both, speaker="Ravi Kumar").startswith("It is always")  # but the interviewee's words win when known
    assert best_quote("short") == "short" and len(best_quote("x" * 500 + ".")) <= 180


def test_template_questions_for_a_system_are_grounded_and_distinct(world):
    g, s, llm, risks = world
    qs = generate_questions(risks["sys_payment_gateway"], g, s, llm, interviewee_name="Ravi Kumar", max_questions=4)
    assert len(qs) == 4 and len({q.text for q in qs}) == 4
    for q in qs:
        assert q.text.endswith("?") and len(q.text) < 500 and q.rationale
        assert q.evidence and q.evidence[0].snippet and q.target_entity_id == "sys_payment_gateway"
        title = q.evidence[0].title
        assert title in q.text, "each question names the evidence it comes from"
    joined = " ".join(q.text for q in qs)
    assert "Channel:" not in joined and "[20" not in joined, "no raw chat headers in questions"
    assert "gwctl" in joined or "pool" in joined.lower(), "quotes real details from the chats"
    assert all(q.evidence[0].artifact_type in {"chat", "email", "ticket"} for q in qs)


def test_decision_questions_ask_about_criteria_and_alternatives(world):
    g, s, llm, risks = world
    qs = generate_questions(risks["dec_kafka_over_rabbitmq"], g, s, llm, max_questions=5)
    text = " ".join(q.text.lower() for q in qs)
    assert len(qs) == 4 and "criteria" in text and "rejected" in text and "trade-offs" in text
    assert any(q.evidence and q.evidence[0].artifact_type == "email" for q in qs)


def test_generation_is_deterministic_and_bounded(world):
    g, s, llm, risks = world
    a = [q.text for q in generate_questions(risks["sys_settlement_batch_job"], g, s, llm, max_questions=3)]
    b = [q.text for q in generate_questions(risks["sys_settlement_batch_job"], g, s, llm, max_questions=3)]
    assert a == b and len(a) == 3
    assert len(generate_questions(risks["sys_settlement_batch_job"], g, s, llm, max_questions=1)) == 1


def test_llm_questions_are_validated_against_the_evidence(world):
    g, s, _, risks = world
    llm = ScriptedLLM([
        GeneratedQuestion(text="What do you check first when stale connections appear?", rationale="r1", category="first_check", evidence=[1, 99]),
        GeneratedQuestion(text="An ungrounded question with no evidence?", evidence=[]),
        GeneratedQuestion(text="Not actually a question", evidence=[1]),
        GeneratedQuestion(text="What do you check first when stale connections appear?", evidence=[2]),  # duplicate
        GeneratedQuestion(text="Who do you call if the restart fails?", rationale="r2", evidence=[2]),
        GeneratedQuestion(text="Why " + "x" * 600 + "?", evidence=[1]),
        GeneratedQuestion(text="Third valid one?", evidence=[3]),
    ])
    qs = generate_questions(risks["sys_payment_gateway"], g, s, llm, interviewee_name="Ravi Kumar", max_questions=3)
    assert [q.text for q in qs] == [
        "What do you check first when stale connections appear?", "Who do you call if the restart fails?", "Third valid one?"]
    assert len(qs[0].evidence) == 1, "out-of-range evidence numbers are discarded"
    system, user = llm.prompts[0]
    assert "between 3 and 3 questions" in system and "INTERVIEWEE: Ravi Kumar" in user
    assert "[1] " in user and "TOPIC: Payment Gateway" in user and "gwctl" in user


@pytest.mark.parametrize("llm", [ScriptedLLM(fail=True), ScriptedLLM([]), ScriptedLLM([GeneratedQuestion(text="No evidence?")])])
def test_falls_back_to_templates(world, llm):
    g, s, _, risks = world
    qs = generate_questions(risks["sys_payment_gateway"], g, s, llm, max_questions=4)
    assert len(qs) == 4 and all(q.evidence for q in qs)


def test_no_evidence_means_no_questions(world):
    g, s, llm, risks = world
    risk = risks["sys_payment_gateway"].model_copy(update={"entity_id": "does_not_exist"})
    assert generate_questions(risk, g, s, llm) == []
    assert template_questions(risks["sys_payment_gateway"], [], 4) == []
