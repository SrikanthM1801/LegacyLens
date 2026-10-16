"""
Coverage score (0..1): how well a topic is formally documented.

Two independent signals, combined with a noisy-or (either one alone can make a
topic well covered; both together make it nearly fully covered):

  edge_score   - DOCUMENTED_BY edges from the graph: docs / approved interviews
                 that are *about* the topic.  1 - 0.3**n for n documents.
  search_score - retrieval restricted to doc + interview artifacts. Hit scores
                 differ wildly between the mock and Azure hybrid search, so we
                 use only the *ranking* and verify each hit really names the
                 topic; a passing mention counts little, a title match a lot.

Interviews approved in T21 add DOCUMENTED_BY edges, which is what moves a risk
from red to green.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.graph.store import GraphStore
from app.ingestion.extractor import alias_regex
from app.models import DOC_ARTIFACT_TYPES, Citation, Entity, RelationType

SEARCH_TOP_K = 5
EDGE_DECAY = 0.3          # each extra documenting artifact removes 70% of the remaining gap
PASSING_MENTION = 0.15    # topic named once in a doc snippet
STRONG_MENTION = 0.35     # named two or more times
TITLE_MENTION = 0.70      # named in the document's title


@dataclass(frozen=True)
class Coverage:
    score: float
    edge_score: float
    search_score: float
    documents: list[Citation] = field(default_factory=list)  # what documents the topic


def noisy_or(*probs: float) -> float:
    miss = 1.0
    for p in probs:
        miss *= 1.0 - min(max(p, 0.0), 1.0)
    return 1.0 - miss


def _patterns(entity: Entity) -> list:
    return [alias_regex(a) for a in dict.fromkeys([entity.name, *entity.aliases]) if a.strip()]


def _distinct_mentions(patterns: list, text: str) -> int:
    """Mentions with overlapping matches merged ('payment gateway' + 'gateway' is one mention)."""
    spans = sorted(m.span() for p in patterns for m in p.finditer(text))
    count, end = 0, -1
    for start, stop in spans:
        if start >= end:
            count += 1
        end = max(end, stop)
    return count


def _mention_strength(entity_patterns: list, title: str, snippet: str) -> float:
    if any(p.search(title) for p in entity_patterns):
        return TITLE_MENTION
    hits = _distinct_mentions(entity_patterns, snippet)
    return STRONG_MENTION if hits >= 2 else PASSING_MENTION if hits == 1 else 0.0


def compute_coverage(entity: Entity, graph: GraphStore, search) -> Coverage:
    documents: dict[str, Citation] = {}

    doc_ids = {e.target for e in graph.edges(source=entity.id, relation=RelationType.DOCUMENTED_BY)}
    for aid in sorted(doc_ids):
        node = graph.artifact_node(aid) or {}
        documents[aid] = Citation(artifact_id=aid, snippet="", title=node.get("title", ""), artifact_type=node.get("type"))
    edge_score = 1.0 - EDGE_DECAY ** len(doc_ids) if doc_ids else 0.0

    query = " ".join(dict.fromkeys([entity.name, *entity.aliases[:3]]))
    patterns = _patterns(entity)
    strengths = []
    for hit in search.search(query, top_k=SEARCH_TOP_K, artifact_types=list(DOC_ARTIFACT_TYPES)):
        s = _mention_strength(patterns, hit.title, hit.snippet)
        if s > 0:
            strengths.append(s)
            documents.setdefault(hit.artifact_id, hit)
    search_score = noisy_or(*strengths)

    return Coverage(
        score=round(noisy_or(edge_score, search_score), 4),
        edge_score=round(edge_score, 4),
        search_score=round(search_score, 4),
        documents=list(documents.values()),
    )
