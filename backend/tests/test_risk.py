import pytest

from app.agents.gap_analyzer import (
    ACTIVITY_FLOOR, DEPARTING_BOOST, TopicStats, analyze_risks, normalize_activity, risk_level, risk_score,
)
from app.config import get_settings
from app.graph import NetworkXGraphStore
from app.ingestion.extractor import RuleExtractor
from app.ingestion.pipeline import run_ingest
from app.models import DocType, EntityType, RelationType as R, Edge
from app.services.llm import MockLLM
from app.services.search import InMemorySearch


def test_risk_formula():
    assert risk_score(1.0, 1, 0.0) == 1.0
    assert risk_score(0.8, 2, 0.5) == pytest.approx(0.2)
    assert risk_score(0.5, 1, 1.0) == 0.0                       # fully documented -> no risk
    assert risk_score(0.4, 1, 0.5, departing=True) == pytest.approx(0.2 * DEPARTING_BOOST)
    assert risk_score(1.0, 1, 0.0, departing=True) == 1.0       # capped
    assert risk_score(1.0, 0, 0.0) == 1.0                       # bus factor never divides by zero


@pytest.mark.parametrize("score,level", [(0.0, "low"), (0.299, "low"), (0.3, "medium"), (0.599, "medium"), (0.6, "high"), (1.0, "high")])
def test_risk_levels(score, level):
    assert risk_level(score) == level


def _ts(tid, type_, activity):
    return TopicStats(tid, tid, type_, activity, {"p": activity}, {"p": 1.0}, ["p"], 1, True)


def test_activity_is_normalized_within_type_with_a_floor():
    n = normalize_activity([
        _ts("s1", EntityType.SYSTEM, 100.0), _ts("s2", EntityType.SYSTEM, 25.0),
        _ts("d1", EntityType.DECISION, 18.0), _ts("d2", EntityType.DECISION, 9.0),
        _ts("p1", EntityType.PROCESS, 3.0),
    ])
    assert n["s1"] == 1.0 and n["s2"] == 0.25
    assert n["d1"] == 1.0 and n["d2"] == 0.5             # decisions compare with decisions
    assert n["p1"] == pytest.approx(3.0 / ACTIVITY_FLOOR)  # tiny lone topic is not "100% active"


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    llm = MockLLM(get_settings())
    g = NetworkXGraphStore(tmp_path_factory.mktemp("g") / "g.json")
    s = InMemorySearch(llm)
    run_ingest(search=s, graph=g, extractor=RuleExtractor(), llm=llm)
    return g, s


@pytest.fixture(scope="module")
def risks(sample):
    return analyze_risks(*sample)


def test_acceptance_planted_risks_rank_top_three(risks):
    assert {r.entity_id for r in risks[:3]} == {"sys_payment_gateway", "sys_settlement_batch_job", "dec_kafka_over_rabbitmq"}
    assert [r.risk_level for r in risks[:3]] == ["high", "high", "high"]
    assert risks[0].entity_id == "sys_payment_gateway", "departing owner's topic ranks first"


def test_everything_else_is_low_risk(risks):
    rest = risks[3:]
    assert len(rest) == 5 and all(r.risk_level == "low" for r in rest)
    assert risks[2].risk_score > 2 * rest[0].risk_score, "clear gap between planted and other topics"
    by = {r.entity_id: r for r in risks}
    for tid in ("proc_release_process", "proc_onboarding", "sys_api_authentication"):
        assert by[tid].risk_score < 0.05 and by[tid].bus_factor >= 3 and by[tid].missing_doc_types == []


def test_risk_item_contents(risks):
    gw = next(r for r in risks if r.entity_id == "sys_payment_gateway")
    assert gw.id == "risk_sys_payment_gateway" and gw.topic == "Payment Gateway" and gw.entity_type == "system"
    assert gw.bus_factor == 1 and gw.owner_candidates[0] == "ravi" and gw.activity_score == 1.0
    assert gw.coverage_score <= 0.35 and gw.risk_score == 1.0
    assert gw.missing_doc_types == [DocType.RUNBOOK.value, DocType.HANDOVER.value]
    assert "Only Ravi Kumar" in gw.summary and "departing" in gw.summary and "2026-11-15" in gw.summary
    dec = next(r for r in risks if r.entity_id == "dec_kafka_over_rabbitmq")
    assert dec.missing_doc_types == [DocType.DECISION_LOG.value] and dec.owner_candidates == ["priya"]
    assert "departing" not in dec.summary


def test_every_risk_is_cited_with_real_non_doc_evidence(risks, sample):
    g, _ = sample
    for r in risks[:3]:
        assert 1 <= len(r.evidence) <= 5, r.topic
        for c in r.evidence:
            assert c.snippet and c.artifact_type in {"ticket", "chat", "email", "commit"}
            assert g.edges(source=c.artifact_id, target=r.entity_id), "evidence must mention the topic"
    kafka = next(r for r in risks if r.entity_id == "dec_kafka_over_rabbitmq")
    assert any(c.artifact_type == "email" for c in kafka.evidence)


def test_analysis_is_deterministic(sample):
    a, b = analyze_risks(*sample), analyze_risks(*sample)
    assert [(r.id, r.risk_score) for r in a] == [(r.id, r.risk_score) for r in b]


def test_approved_interview_lowers_risk(sample):
    g, s = sample
    before = {r.entity_id: r for r in analyze_risks(g, s)}["sys_settlement_batch_job"]
    g.add_artifact_node("art_interview_s", "interview", "Interview: settlement")
    g.add_edge(Edge(source="sys_settlement_batch_job", target="art_interview_s", relation=R.DOCUMENTED_BY))
    after = {r.entity_id: r for r in analyze_risks(g, s)}["sys_settlement_batch_job"]
    assert after.coverage_score > before.coverage_score and after.risk_score < before.risk_score
    assert after.risk_level != "high" or after.risk_score < 0.6
