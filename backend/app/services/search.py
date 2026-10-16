"""
Search service: store Chunks and retrieve them as Citations.

`SearchService` is the interface; `InMemorySearch` is the offline mock (hybrid
cosine + keyword scoring) and `AzureSearch` wraps Azure AI Search. The index is
created by code (`reset`) so its schema always matches the Chunk model.
"""

from __future__ import annotations

import logging
import math
from functools import lru_cache
from typing import Optional, Protocol

from app.config import Settings, get_settings
from app.models import ArtifactType, Chunk, Citation
from app.services.llm import LLMService, get_llm, tokenize

log = logging.getLogger(__name__)

_UPLOAD_BATCH = 500


class SearchService(Protocol):
    def reset(self) -> None:
        """Drop and recreate the index (empty)."""
        ...

    def upsert(self, chunks: list[Chunk]) -> int:
        """Index chunks (embedding them if needed). Returns the number indexed."""
        ...

    def search(
        self,
        query: str,
        *,
        top_k: int = 6,
        artifact_types: Optional[list[str]] = None,
        author: Optional[str] = None,
    ) -> list[Citation]: ...

    def count(self) -> int: ...


def _snippet(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _to_citation(c: Chunk, score: float) -> Citation:
    return Citation(
        artifact_id=c.artifact_id,
        snippet=_snippet(c.text),
        url_or_path=c.url_or_path,
        title=c.title,
        artifact_type=c.artifact_type,
        author=c.author or None,
        timestamp=c.timestamp,
        score=round(score, 4),
    )


def _type_values(types: Optional[list]) -> Optional[list[str]]:
    return [t.value if isinstance(t, ArtifactType) else str(t) for t in types] if types else None


class InMemorySearch:
    def __init__(self, llm: LLMService) -> None:
        self._llm = llm
        self._chunks: dict[str, Chunk] = {}

    def reset(self) -> None:
        self._chunks.clear()

    def upsert(self, chunks: list[Chunk]) -> int:
        missing = [c for c in chunks if c.embedding is None]
        if missing:
            for c, emb in zip(missing, self._llm.embed([c.text for c in missing])):
                c.embedding = emb
        for c in chunks:
            self._chunks[c.id] = c
        return len(chunks)

    def search(self, query, *, top_k=6, artifact_types=None, author=None):
        types = _type_values(artifact_types)
        q_emb = self._llm.embed([query])[0]
        q_tokens = set(tokenize(query))
        scored: list[tuple[float, Chunk]] = []
        for c in self._chunks.values():
            ctype = c.artifact_type if isinstance(c.artifact_type, str) else c.artifact_type.value
            if types and ctype not in types:
                continue
            if author and c.author != author:
                continue
            cos = sum(a * b for a, b in zip(q_emb, c.embedding or []))
            overlap = len(q_tokens & set(tokenize(c.text + " " + c.title))) / (len(q_tokens) or 1)
            score = 0.5 * max(cos, 0.0) + 0.5 * overlap
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda t: (-t[0], t[1].id))  # deterministic tie-break
        return [_to_citation(c, s) for s, c in scored[:top_k]]

    def count(self) -> int:
        return len(self._chunks)


class AzureSearch:
    def __init__(self, settings: Settings, llm: LLMService) -> None:
        from azure.core.credentials import AzureKeyCredential
        from azure.search.documents import SearchClient
        from azure.search.documents.indexes import SearchIndexClient

        self._s = settings
        self._llm = llm
        cred = AzureKeyCredential(settings.azure_search_api_key)
        self._index_client = SearchIndexClient(settings.azure_search_endpoint, cred)
        self._client = SearchClient(
            settings.azure_search_endpoint, settings.azure_search_index_name, cred
        )

    def reset(self) -> None:
        from azure.search.documents.indexes.models import (
            HnswAlgorithmConfiguration,
            SearchableField,
            SearchField,
            SearchFieldDataType,
            SearchIndex,
            SimpleField,
            VectorSearch,
            VectorSearchProfile,
        )

        name = self._s.azure_search_index_name
        try:
            self._index_client.delete_index(name)
        except Exception:  # index may not exist yet
            log.debug("delete_index(%s) skipped", name)
        fields = [
            SimpleField(name="id", type=SearchFieldDataType.String, key=True),
            SimpleField(name="artifact_id", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="artifact_type", type=SearchFieldDataType.String, filterable=True, facetable=True),
            SimpleField(name="ordinal", type=SearchFieldDataType.Int32),
            SearchableField(name="title", type=SearchFieldDataType.String),
            SearchableField(name="text", type=SearchFieldDataType.String),
            SimpleField(name="author", type=SearchFieldDataType.String, filterable=True),
            SimpleField(name="timestamp", type=SearchFieldDataType.DateTimeOffset, filterable=True, sortable=True),
            SimpleField(name="url_or_path", type=SearchFieldDataType.String),
            SearchField(
                name="embedding",
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self._s.embedding_dim,
                vector_search_profile_name="vec-profile",
            ),
        ]
        vector_search = VectorSearch(
            algorithms=[HnswAlgorithmConfiguration(name="hnsw")],
            profiles=[VectorSearchProfile(name="vec-profile", algorithm_configuration_name="hnsw")],
        )
        self._index_client.create_index(SearchIndex(name=name, fields=fields, vector_search=vector_search))

    def upsert(self, chunks: list[Chunk]) -> int:
        missing = [c for c in chunks if c.embedding is None]
        if missing:
            for c, emb in zip(missing, self._llm.embed([c.text for c in missing])):
                c.embedding = emb
        docs = []
        for c in chunks:
            d = c.model_dump(mode="json", exclude={"embedding"})
            d["embedding"] = c.embedding
            docs.append(d)
        for i in range(0, len(docs), _UPLOAD_BATCH):
            self._client.merge_or_upload_documents(docs[i : i + _UPLOAD_BATCH])
        return len(docs)

    def search(self, query, *, top_k=6, artifact_types=None, author=None):
        from azure.search.documents.models import VectorizedQuery

        clauses = []
        types = _type_values(artifact_types)
        if types:
            clauses.append("(" + " or ".join(f"artifact_type eq '{t}'" for t in types) + ")")
        if author:
            safe = author.replace("'", "''")
            clauses.append(f"author eq '{safe}'")
        vq = VectorizedQuery(
            vector=self._llm.embed([query])[0], k_nearest_neighbors=top_k, fields="embedding"
        )
        results = self._client.search(
            search_text=query,
            vector_queries=[vq],
            filter=" and ".join(clauses) or None,
            select=["id", "artifact_id", "artifact_type", "ordinal", "title", "text", "author", "timestamp", "url_or_path"],
            top=top_k,
        )
        out = []
        for r in results:
            chunk = Chunk.model_validate({k: v for k, v in r.items() if not k.startswith("@")})
            out.append(_to_citation(chunk, r["@search.score"]))
        return out

    def count(self) -> int:
        return self._client.get_document_count()


@lru_cache
def get_search() -> SearchService:
    s = get_settings()
    llm = get_llm()
    if s.use_mocks or not s.azure_search_configured:
        if not s.use_mocks:
            log.warning("USE_MOCKS=false but Azure Search is not configured; using in-memory search")
        return InMemorySearch(llm)
    return AzureSearch(s, llm)
