from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import app
from app.models import ArtifactType, Chunk
from app.services.llm import MockLLM
from app.services.search import InMemorySearch


def _search() -> InMemorySearch:
    return InMemorySearch(MockLLM(get_settings()))


def _chunk(i: str, text: str, type_=ArtifactType.CHAT, author="ravi") -> Chunk:
    return Chunk(id=i, artifact_id=f"art_{i}", artifact_type=type_, text=text, author=author)


def test_health_in_mock_mode():
    r = TestClient(app).get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["use_mocks"] is True


def test_embeddings_are_deterministic_and_sized():
    llm = MockLLM(get_settings())
    a, b = llm.embed(["payment gateway timeout"]), llm.embed(["payment gateway timeout"])
    assert a == b and len(a[0]) == get_settings().embedding_dim


def test_search_ranks_relevant_chunk_first_and_filters():
    s = _search()
    s.upsert([
        _chunk("1", "restart the settlement worker when the bank file is late"),
        _chunk("2", "release process uses a weekly train", ArtifactType.DOC, "priya"),
        _chunk("3", "gateway timeouts fixed by recycling the connection pool"),
    ])
    hits = s.search("settlement worker bank file", top_k=3)
    assert hits[0].artifact_id == "art_1"
    docs_only = s.search("release", artifact_types=[ArtifactType.DOC])
    assert [h.artifact_id for h in docs_only] == ["art_2"]
    assert all(h.author == "priya" for h in s.search("release", author="priya"))


def test_reset_clears_index():
    s = _search()
    s.upsert([_chunk("1", "hello world")])
    s.reset()
    assert s.count() == 0


def test_azure_llm_retries_transient_server_errors(monkeypatch):
    import httpx
    import openai

    from app.services import llm as llm_mod

    monkeypatch.setattr(llm_mod.time, "sleep", lambda _s: None)
    s = get_settings().model_copy(update={"azure_openai_endpoint": "https://x.openai.azure.com", "azure_openai_api_key": "k"})
    azure = llm_mod.AzureLLM(s)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            req = httpx.Request("POST", "https://x")
            raise openai.InternalServerError("busy", response=httpx.Response(503, request=req), body=None)
        return "ok"

    assert azure._with_retry(flaky) == "ok" and calls["n"] == 3
