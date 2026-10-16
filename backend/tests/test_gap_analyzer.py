from datetime import datetime, timedelta, timezone

import pytest

from app.agents.gap_analyzer import bus_factor, compute_topic_stats, shares_of
from app.graph import NetworkXGraphStore
from app.graph.queries import RECENCY_MAX_BOOST, activity_by_topic, recency_multiplier
from app.ingestion.extractor import RuleExtractor
from app.ingestion.pipeline import run_ingest
from app.config import get_settings
from app.models import Edge, Entity, EntityType, RelationType as R
from app.services.llm import MockLLM
from app.services.search import InMemorySearch

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_shares_and_bus_factor():
    assert shares_of({}) == {} and shares_of({"a": 0}) == {}
    s = shares_of({"a": 80, "b": 15, "c": 5})
    assert s == {"a": 0.8, "b": 0.15, "c": 0.05}
    assert bus_factor(s) == 2                     # b is exactly at the 15% threshold
    assert bus_factor({"a": 0.9, "b": 0.1}) == 1
    assert bus_factor({"a": 0.5, "b": 0.5}) == 2
    assert bus_factor({"a": 0.14, "b": 0.14, "c": 0.14, "d": 0.14, "e": 0.14, "f": 0.3}) == 1
    assert bus_factor({}) == 1                    # never reports 0


def test_recency_multiplier_shape():
    assert recency_multiplier(0) == pytest.approx(1 + RECENCY_MAX_BOOST)
    assert recency_multiplier(180) == pytest.approx(1 + RECENCY_MAX_BOOST / 2)
    assert 1.0 < recency_multiplier(3650) < 1.001
    assert recency_multiplier(-5) == recency_multiplier(0)  # future dates are clamped


def _graph(tmp_path):
    g = NetworkXGraphStore(tmp_path / "g.json")
    for pid in ("old", "new"):
        g.add_entity(Entity(id=pid, type=EntityType.PERSON, name=pid))
    g.add_entity(Entity(id="topic", type=EntityType.SYSTEM, name="Topic"))
    g.add_artifact_node("a_old", "commit", "old", NOW - timedelta(days=720))
    g.add_artifact_node("a_new", "commit", "new", NOW)
    g.add_edge(Edge(source="old", target="topic", relation=R.COMMITTED_TO, weight=5, evidence_artifact_ids=["a_old"]))
    g.add_edge(Edge(source="new", target="topic", relation=R.COMMITTED_TO, weight=5, evidence_artifact_ids=["a_new"]))
    return g


def test_recent_activity_outweighs_equal_old_activity(tmp_path):
    g = _graph(tmp_path)
    flat = activity_by_topic(g)["topic"]
    assert flat["old"] == flat["new"] == 15
    boosted = activity_by_topic(g, recency=True)["topic"]
    assert boosted["new"] > boosted["old"] > flat["old"]
    assert boosted["new"] == pytest.approx(15 * 1.5)
    # an explicit as_of far in the future erases the difference
    far = activity_by_topic(g, recency=True, as_of=NOW + timedelta(days=5000))["topic"]
    assert far["new"] == pytest.approx(far["old"], rel=0.001)


def test_activity_without_evidence_timestamps_is_unboosted(tmp_path):
    g = _graph(tmp_path)
    g.add_edge(Edge(source="old", target="topic", relation=R.DISCUSSED, weight=2, evidence_artifact_ids=["unknown"]))
    assert activity_by_topic(g, recency=True)["topic"]["old"] == pytest.approx(15 * recency_multiplier(720) + 2)


@pytest.fixture(scope="module")
def stats(tmp_path_factory):
    llm = MockLLM(get_settings())
    g = NetworkXGraphStore(tmp_path_factory.mktemp("g") / "g.json")
    run_ingest(search=InMemorySearch(llm), graph=g, extractor=RuleExtractor(), llm=llm)
    return {s.topic_id: s for s in compute_topic_stats(g)}


def test_planted_topics_are_single_person_risks(stats):
    for tid, owner in (("sys_payment_gateway", "ravi"), ("sys_settlement_batch_job", "arjun"), ("dec_kafka_over_rabbitmq", "priya")):
        s = stats[tid]
        assert s.top_owner == owner and s.bus_factor == 1 and s.single_person_risk, tid
        assert s.shares[owner] >= 0.7


def test_documented_topics_have_a_wide_bus_factor(stats):
    for tid in ("proc_release_process", "proc_onboarding", "sys_api_authentication"):
        s = stats[tid]
        assert s.bus_factor >= 3 and not s.single_person_risk, tid


def test_stats_are_well_formed_and_sorted(stats):
    for s in stats.values():
        assert sum(s.shares.values()) == pytest.approx(1) and s.owners == sorted(s.owners, key=lambda p: (-s.shares[p], p))
        assert s.activity == pytest.approx(sum(s.by_person.values())) and s.bus_factor >= 1
    ordered = [s.activity for s in sorted(stats.values(), key=lambda s: -s.activity)]
    assert ordered == sorted(ordered, reverse=True)
    assert stats["sys_payment_gateway"].activity > stats["sys_checkout_api"].activity


def test_compute_is_deterministic(tmp_path):
    g = _graph(tmp_path)
    assert compute_topic_stats(g) == compute_topic_stats(g)
