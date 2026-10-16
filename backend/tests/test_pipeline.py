import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.graph import NetworkXGraphStore
from app.ingestion.extractor import RuleExtractor
from app.ingestion.pipeline import resolve_source_dir, IngestSourceError, run_ingest
from app.main import app
from app.services.llm import MockLLM
from app.services.search import InMemorySearch


@pytest.fixture
def deps(tmp_path):
    llm = MockLLM(get_settings())
    return dict(search=InMemorySearch(llm), graph=NetworkXGraphStore(tmp_path / "g.json"),
                extractor=RuleExtractor(), llm=llm)


def test_full_ingest_populates_index_and_graph(deps, tmp_path):
    resp = run_ingest(**deps)
    assert resp.artifacts_ingested == 87 and resp.chunks_indexed == deps["search"].count() > 87
    assert resp.entities_extracted >= 19 and resp.edges_created > 200 and resp.warnings == []
    assert resp.graph == deps["graph"].stats()
    assert resp.graph.nodes_by_type["person"] == 4 and resp.graph.nodes_by_type["artifact:chat"] == 14
    assert (tmp_path / "g.json").exists()


def test_reingest_is_idempotent_with_reset(deps):
    first = run_ingest(**deps)
    second = run_ingest(**deps)
    assert second.graph == first.graph and second.chunks_indexed == first.chunks_indexed


def test_pii_is_redacted_before_indexing(deps):
    resp = run_ingest(**deps)
    assert resp.redactions == {"phone": 1}
    assert all("98450" not in h.snippet for h in deps["search"].search("98450 12345 acquirer NOC", top_k=20))
    hits = deps["search"].search("call Daniel at the acquirer NOC", top_k=3)
    assert any("[REDACTED_PHONE]" in h.snippet for h in hits)


def test_planted_knowledge_is_retrievable(deps):
    run_ingest(**deps)
    top = deps["search"].search("how to fix stale gateway connections gwctl pool-status", top_k=3)
    gateway_chats = {"art_chat_chat_10" + str(n) for n in (1, 2, 3, 4)}
    assert {h.artifact_id for h in top} <= gateway_chats and "art_chat_chat_101" in {h.artifact_id for h in top}
    kafka = deps["search"].search("why Kafka over RabbitMQ replay", top_k=3)
    assert kafka[0].artifact_id.startswith("art_email_mail_00") and kafka[0].artifact_type == "email"


def test_source_dir_is_confined_to_data_dir():
    assert resolve_source_dir(None) == get_settings().data_dir
    for bad in ("/etc", "../backend", "data/does-not-exist"):
        with pytest.raises(IngestSourceError):
            resolve_source_dir(bad)


def test_ingest_endpoint_and_health():
    with TestClient(app) as client:  # lifespan rebuilds the mock index at boot
        h = client.get("/health").json()
        assert h["artifacts_indexed"] == 87 and h["graph"]["node_count"] > 87
        r = client.post("/ingest")
        assert r.status_code == 200 and r.json()["artifacts_ingested"] == 87
        assert client.post("/ingest", json={"reset": True}).status_code == 200
        assert client.post("/ingest", json={"source_dir": "/etc"}).status_code == 400
        assert client.post("/ingest", json={"unknown": 1}).status_code == 422
