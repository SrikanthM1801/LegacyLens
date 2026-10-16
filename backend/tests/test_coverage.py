import pytest

from app.agents.coverage import (
    Coverage, PASSING_MENTION, TITLE_MENTION, compute_coverage, noisy_or,
)
from app.config import get_settings
from app.graph import NetworkXGraphStore
from app.ingestion.extractor import RuleExtractor
from app.ingestion.pipeline import run_ingest
from app.models import Citation, Edge, Entity, EntityType, RelationType as R
from app.services.llm import MockLLM
from app.services.search import InMemorySearch


def test_noisy_or():
    assert noisy_or() == 0 and noisy_or(0.5) == 0.5
    assert noisy_or(0.5, 0.5) == 0.75 and noisy_or(1, 0.3) == 1 and noisy_or(-1, 2) == 1


class FakeSearch:
    def __init__(self, hits):
        self.hits, self.queries = hits, []

    def search(self, query, *, top_k=6, artifact_types=None, author=None):
        self.queries.append((query, artifact_types))
        return self.hits


def _hit(title, snippet, score=1.0):
    return Citation(artifact_id=f"a_{title}", title=title, snippet=snippet, score=score)


ENT = Entity(id="sys_x", type=EntityType.SYSTEM, name="Widget Service", aliases=["WS", "widget"])


def empty_graph(tmp_path):
    g = NetworkXGraphStore(tmp_path / "g.json")
    g.add_entity(ENT)
    return g


def test_no_documents_means_zero_coverage(tmp_path):
    c = compute_coverage(ENT, empty_graph(tmp_path), FakeSearch([]))
    assert c == Coverage(score=0.0, edge_score=0.0, search_score=0.0, documents=[])


def test_search_is_restricted_to_documentation_types(tmp_path):
    fs = FakeSearch([])
    compute_coverage(ENT, empty_graph(tmp_path), fs)
    assert set(fs.queries[0][1]) == {"doc", "interview"} and "Widget Service" in fs.queries[0][0]


def test_mention_strength_ladder_ignores_scores_and_unrelated_hits(tmp_path):
    g = empty_graph(tmp_path)
    passing = compute_coverage(ENT, g, FakeSearch([_hit("Other", "talks about the widget once", 0.02)]))
    assert passing.search_score == PASSING_MENTION
    same_big_score = compute_coverage(ENT, g, FakeSearch([_hit("Other", "talks about the widget once", 99.0)]))
    assert same_big_score.search_score == passing.search_score          # scale independent
    title = compute_coverage(ENT, g, FakeSearch([_hit("Widget Service guide", "x", 0.01)]))
    assert title.search_score == TITLE_MENTION
    unrelated = compute_coverage(ENT, g, FakeSearch([_hit("Billing", "nothing relevant", 5.0)]))
    assert unrelated.score == 0 and unrelated.documents == []
    assert compute_coverage(ENT, g, FakeSearch([_hit("A", "widget"), _hit("B", "widget")])).search_score == pytest.approx(noisy_or(0.15, 0.15))


def test_documented_by_edges_raise_coverage_and_are_cited(tmp_path):
    g = empty_graph(tmp_path)
    g.add_artifact_node("doc1", "doc", "Widget guide")
    g.add_artifact_node("int1", "interview", "Interview: widget recovery")
    base = compute_coverage(ENT, g, FakeSearch([])).score
    g.add_edge(Edge(source="sys_x", target="doc1", relation=R.DOCUMENTED_BY))
    one = compute_coverage(ENT, g, FakeSearch([]))
    g.add_edge(Edge(source="sys_x", target="int1", relation=R.DOCUMENTED_BY))
    two = compute_coverage(ENT, g, FakeSearch([]))
    assert base == 0 and one.edge_score == pytest.approx(0.7) and two.edge_score == pytest.approx(0.91)
    assert [d.title for d in two.documents] == ["Widget guide", "Interview: widget recovery"]
    assert two.documents[1].artifact_type == "interview"


# --- on the sample dataset ---------------------------------------------------------


@pytest.fixture(scope="module")
def sample(tmp_path_factory):
    llm = MockLLM(get_settings())
    g = NetworkXGraphStore(tmp_path_factory.mktemp("g") / "g.json")
    s = InMemorySearch(llm)
    run_ingest(search=s, graph=g, extractor=RuleExtractor(), llm=llm)
    return g, s


def cov(sample, entity_id):
    g, s = sample
    return compute_coverage(g.get_entity(entity_id), g, s)


def test_planted_topics_have_low_coverage(sample):
    for tid in ("sys_payment_gateway", "sys_settlement_batch_job", "dec_kafka_over_rabbitmq"):
        c = cov(sample, tid)
        assert c.edge_score == 0 and c.score <= 0.35, (tid, c)


def test_documented_topics_have_high_coverage(sample):
    for tid in ("proc_release_process", "proc_onboarding", "sys_api_authentication"):
        c = cov(sample, tid)
        assert c.edge_score >= 0.7 and c.score >= 0.8, (tid, c)
        assert all(d.artifact_type == "doc" for d in c.documents)


def test_an_approved_interview_closes_most_of_the_gap(sample):
    g, s = sample
    before = cov(sample, "sys_payment_gateway").score
    g.add_artifact_node("art_interview_x", "interview", "Interview: gateway recovery")
    g.add_edge(Edge(source="sys_payment_gateway", target="art_interview_x", relation=R.DOCUMENTED_BY))
    after = cov(sample, "sys_payment_gateway")
    assert after.score > 0.7 > before and after.edge_score == pytest.approx(0.7)


def test_overlapping_aliases_count_as_one_mention(tmp_path):
    ent = Entity(id="sys_pg", type=EntityType.SYSTEM, name="Payment Gateway", aliases=["gateway", "PG"])
    g = NetworkXGraphStore(tmp_path / "g.json")
    g.add_entity(ent)
    one = compute_coverage(ent, g, FakeSearch([_hit("Arch", "The payment gateway adapter talks to the partner")]))
    two = compute_coverage(ent, g, FakeSearch([_hit("Arch", "The payment gateway adapter and the PG pool")]))
    assert one.search_score == PASSING_MENTION and two.search_score > PASSING_MENTION
