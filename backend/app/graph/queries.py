"""
Read-only questions we ask the knowledge graph. Pure functions over a GraphStore.

`activity_by_topic` is the shared building block for /people now and for the gap
analyzer (T14): who does how much with each topic (system / process / decision).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Optional

from app.graph.store import GraphStore
from app.models import Edge, EntityType, RelationType as R

#: Relative value of one interaction. Tuned so that fixing incidents and shipping
#: code outweigh talking about a topic (Plan.md section 11.2).
WEIGHTS: dict[str, float] = {
    R.RESOLVED.value: 5.0,        # person -> incident (credited to the systems it AFFECTS)
    R.DECIDED.value: 4.0,         # person -> decision
    R.COMMITTED_TO.value: 3.0,    # person -> system / process
    R.PARTICIPATED_IN.value: 2.0, # person -> topic or incident (ticket work)
    "AUTHORED_DOC": 2.0,          # person wrote a doc that is about the topic
    R.DISCUSSED.value: 1.0,       # person spoke about the topic in chat / email
}

#: Recent activity counts up to 1.5x; the boost halves every RECENCY_HALF_LIFE_DAYS.
RECENCY_MAX_BOOST = 0.5
RECENCY_HALF_LIFE_DAYS = 180.0

TOPIC_TYPES = (EntityType.SYSTEM, EntityType.PROCESS, EntityType.DECISION)
_DIRECT = (R.DECIDED, R.COMMITTED_TO, R.PARTICIPATED_IN, R.DISCUSSED)


def topic_ids(graph: GraphStore) -> set[str]:
    return {e.id for t in TOPIC_TYPES for e in graph.entities(t)}


def recency_multiplier(age_days: float) -> float:
    """1.0 for old activity up to 1 + RECENCY_MAX_BOOST for activity happening 'now'."""
    return 1.0 + RECENCY_MAX_BOOST * 0.5 ** (max(age_days, 0.0) / RECENCY_HALF_LIFE_DAYS)


def activity_by_topic(
    graph: GraphStore, *, recency: bool = False, as_of: Optional[datetime] = None
) -> dict[str, dict[str, float]]:
    """{topic_id: {person_id: weighted activity}} for every topic with any activity.

    With recency=True each contribution is boosted by how recent its evidence
    artifacts are, relative to `as_of` (default: the newest artifact in the graph,
    so results depend on the data, not on today's date).
    """
    topics = topic_ids(graph)
    people = {e.id for e in graph.entities(EntityType.PERSON)}
    incidents = {e.id for e in graph.entities(EntityType.INCIDENT)}
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    stamps = graph.artifact_timestamps() if recency else {}
    ref = as_of or (max(stamps.values()) if stamps else None)

    def boost(e: Edge) -> float:
        if not recency or ref is None:
            return 1.0
        ages = [(ref - stamps[i]).total_seconds() / 86400 for i in e.evidence_artifact_ids if i in stamps]
        return recency_multiplier(sum(ages) / len(ages)) if ages else 1.0

    affected: dict[str, list[str]] = defaultdict(list)  # incident -> topics it affects
    for e in graph.edges(relation=R.AFFECTS):
        if e.source in incidents and e.target in topics:
            affected[e.source].append(e.target)

    for rel in _DIRECT:
        for e in graph.edges(relation=rel):
            value = e.weight * WEIGHTS[rel.value] * boost(e)
            if e.source in people and e.target in topics:
                out[e.target][e.source] += value
            elif rel is R.PARTICIPATED_IN and e.source in people and e.target in incidents:
                for t in affected[e.target]:
                    out[t][e.source] += value
    for e in graph.edges(relation=R.RESOLVED):
        if e.source in people:
            for t in affected.get(e.target, []):
                out[t][e.source] += e.weight * WEIGHTS[R.RESOLVED.value] * boost(e)

    authors = {e.target: e.source for e in graph.edges(relation=R.AUTHORED) if e.source in people}
    for e in graph.edges(relation=R.DOCUMENTED_BY):
        if e.source in topics and e.target in authors:
            out[e.source][authors[e.target]] += WEIGHTS["AUTHORED_DOC"] * boost(e)

    return {t: dict(p) for t, p in out.items() if sum(p.values()) > 0}
