from pathlib import Path

import pytest

from app.ingestion.extractor import (
    LLMExtraction, LLMExtractor, RuleExtractor, topic_mentions,
)
from app.ingestion.loaders import load_dataset
from app.models import Artifact, ArtifactType, EntityType, RelationType as R

ROOT = Path(__file__).resolve().parents[2]
GW, ST, KAFKA, DEC = "sys_payment_gateway", "sys_settlement_batch_job", "sys_kafka_event_bus", "dec_kafka_over_rabbitmq"


@pytest.fixture(scope="module")
def data():
    return load_dataset(ROOT / "data" / "sample")


@pytest.fixture(scope="module")
def result(data):
    return RuleExtractor().extract(data.artifacts, data.people)


def edges(result, rel=None, src=None, dst=None):
    return [e for e in result.edges
            if (rel is None or e.relation == rel.value) and (src is None or e.source == src) and (dst is None or e.target == dst)]


def weight(result, rel, src, dst):
    return sum(e.weight for e in edges(result, rel, src, dst))


def test_alias_resolution_collapses_names_to_one_topic():
    assert GW in topic_mentions("PG is down") and GW in topic_mentions("the payment gateway adapter")
    assert GW in topic_mentions("use gwctl pool-status") and GW in topic_mentions("pg-adapter restart")
    assert GW not in topic_mentions("the pgsql database") and GW not in topic_mentions("pg is lowercase noise")
    assert ST in topic_mentions("rerun with --skip-recon") and ST in topic_mentions("bank file is late")
    assert DEC in topic_mentions("Kafka vs RabbitMQ")


def test_entities_cover_people_topics_and_incidents(result):
    by_type = {}
    for e in result.entities:
        by_type.setdefault(e.type, []).append(e.id)
    assert sorted(by_type["person"]) == ["arjun", "meera", "priya", "ravi"]
    assert {GW, ST, KAFKA} <= set(by_type["system"]) and DEC in by_type["decision"]
    assert sorted(by_type["incident"]) == [f"inc_{n}" for n in (142, 150, 155, 163, 171, 180, 188)]
    ravi = next(e for e in result.entities if e.id == "ravi")
    assert ravi.attributes["is_departing"] is True


def test_planted_expertise_signals(result):
    for inc in ("inc_142", "inc_155", "inc_171", "inc_188"):
        assert edges(result, R.RESOLVED, "ravi", inc) and edges(result, R.AFFECTS, inc, GW)
    for inc in ("inc_150", "inc_163", "inc_180"):
        assert edges(result, R.RESOLVED, "arjun", inc) and edges(result, R.AFFECTS, inc, ST)
    assert weight(result, R.COMMITTED_TO, "ravi", GW) >= 8 and weight(result, R.COMMITTED_TO, "arjun", ST) >= 6
    assert weight(result, R.COMMITTED_TO, "arjun", GW) == 0
    assert weight(result, R.DISCUSSED, "ravi", GW) > 2 * weight(result, R.DISCUSSED, "arjun", GW)
    assert weight(result, R.DISCUSSED, "arjun", ST) > weight(result, R.DISCUSSED, "ravi", ST)


def test_kafka_decision_is_priyas_and_affects_the_event_bus(result):
    assert {e.source for e in edges(result, R.DECIDED, dst=DEC)} == {"priya"}
    assert edges(result, R.AFFECTS, DEC, KAFKA)
    assert next(e for e in result.entities if e.id == DEC).artifact_ids  # evidence exists


def test_documentation_edges_only_for_documented_topics(result):
    documented = {e.source for e in edges(result, R.DOCUMENTED_BY)}
    assert documented == {"proc_release_process", "proc_onboarding", "sys_api_authentication"}
    assert not documented & {GW, ST, KAFKA, DEC}


def test_all_edges_have_evidence_and_valid_endpoints(result, data):
    nodes = {e.id for e in result.entities} | {a.id for a in data.artifacts}
    for e in result.edges:
        assert e.source in nodes and e.target in nodes, e
        assert e.evidence_artifact_ids and all(i in nodes for i in e.evidence_artifact_ids), e
        assert e.weight > 0


def test_extraction_is_deterministic(data):
    a = RuleExtractor().extract(data.artifacts, data.people)
    b = RuleExtractor().extract(data.artifacts, data.people)
    assert a.entities == b.entities and a.edges == b.edges


def test_speakers_are_attributed_per_message(data):
    # CHAT-103: Meera asks one question, Ravi explains: only Ravi "discusses" the cert pin message.
    res = RuleExtractor().extract([a for a in data.artifacts if a.source_id == "CHAT-103"], data.people)
    assert weight(res, R.DISCUSSED, "meera", GW) == 0 and weight(res, R.DISCUSSED, "ravi", GW) >= 1


# --- LLM layer (with a fake LLM; no network) ------------------------------------


class FakeLLM:
    model_name = "fake"

    def __init__(self, by_title: dict[str, dict] | None = None, fail: bool = False):
        self.by_title, self.fail, self.calls = by_title or {}, fail, 0

    def chat_json(self, system, user, schema):
        self.calls += 1
        if self.fail:
            raise ValueError("boom")
        for title, payload in self.by_title.items():
            if title in user:
                return schema.model_validate(payload)
        return schema.model_validate({})


def test_llm_extractor_resolves_aliases_adds_new_entities_and_survives_failures(data):
    arts = [a for a in data.artifacts if a.source_id in {"CHAT-101", "MAIL-001", "MAIL-007"}]
    llm = FakeLLM({
        "Gateway timeouts (INC-142)": {"entities": [
            {"name": "PG", "type": "system"},  # known alias -> must reuse sys_payment_gateway
            {"name": "NOC escalation", "type": "process", "description": "Call the acquirer NOC"}]},
        "Message broker for payment events": {"entities": [
            {"name": "Kafka vs RabbitMQ", "type": "decision", "decided_by": "Priya"}]},
    })
    base = RuleExtractor().extract(arts, data.people)
    res = LLMExtractor(llm).extract(arts, data.people)
    ids = {e.id for e in res.entities}
    assert llm.calls == 3 and "proc_noc_escalation" in ids
    assert ids - {"proc_noc_escalation"} == {e.id for e in base.entities}, "aliases must not create duplicates"
    assert any(e.relation == "MENTIONS" and e.target == "proc_noc_escalation" for e in res.edges)
    # LLM mentions of things the rules already found must not double-count
    assert weight(res, R.MENTIONS, "art_chat_chat_101", GW) == weight(base, R.MENTIONS, "art_chat_chat_101", GW)
    assert weight(res, R.DECIDED, "priya", DEC) == weight(base, R.DECIDED, "priya", DEC) + 1


def test_llm_failure_falls_back_to_rules(data):
    arts = data.artifacts[:10]
    res = LLMExtractor(FakeLLM(fail=True)).extract(arts, data.people)
    base = RuleExtractor().extract(arts, data.people)
    assert res.edges == base.edges and res.entities == base.entities
    assert res.warnings and "LLM extraction failed" in res.warnings[0]


def test_llm_extractor_with_mock_llm_equals_rules(data):
    from app.config import get_settings
    from app.services.llm import MockLLM

    res = LLMExtractor(MockLLM(get_settings())).extract(data.artifacts, data.people)
    base = RuleExtractor().extract(data.artifacts, data.people)
    assert res.edges == base.edges and res.entities == base.entities and not res.warnings


def test_extraction_schema_defaults():
    assert LLMExtraction().entities == []


def test_llm_incident_ids_match_ticket_ids(data):
    arts = [a for a in data.artifacts if a.source_id == "CHAT-101"]  # no ticket in scope
    llm = FakeLLM({"Gateway timeouts": {"entities": [{"name": "INC-142", "type": "incident"}]}})
    ids = {e.id for e in LLMExtractor(llm).extract(arts, data.people).entities}
    assert "inc_142" in ids and "inc_inc_142" not in ids
