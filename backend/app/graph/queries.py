"""
Read-only questions we ask the knowledge graph. Pure functions over a GraphStore.

`activity_by_topic` is the shared building block for /people now and for the gap
analyzer (T14): who does how much with each topic (system / process / decision).
"""

from __future__ import annotations

from collections import defaultdict

from app.graph.store import GraphStore
from app.models import EntityType, RelationType as R

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

TOPIC_TYPES = (EntityType.SYSTEM, EntityType.PROCESS, EntityType.DECISION)
_DIRECT = (R.DECIDED, R.COMMITTED_TO, R.PARTICIPATED_IN, R.DISCUSSED)


def topic_ids(graph: GraphStore) -> set[str]:
    return {e.id for t in TOPIC_TYPES for e in graph.entities(t)}


def activity_by_topic(graph: GraphStore) -> dict[str, dict[str, float]]:
    """{topic_id: {person_id: weighted activity}} for every topic with any activity."""
    topics = topic_ids(graph)
    people = {e.id for e in graph.entities(EntityType.PERSON)}
    incidents = {e.id for e in graph.entities(EntityType.INCIDENT)}
    out: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    affected: dict[str, list[str]] = defaultdict(list)  # incident -> topics it affects
    for e in graph.edges(relation=R.AFFECTS):
        if e.source in incidents and e.target in topics:
            affected[e.source].append(e.target)

    for rel in _DIRECT:
        for e in graph.edges(relation=rel):
            if e.source in people and e.target in topics:
                out[e.target][e.source] += e.weight * WEIGHTS[rel.value]
            elif rel is R.PARTICIPATED_IN and e.source in people and e.target in incidents:
                for t in affected[e.target]:
                    out[t][e.source] += e.weight * WEIGHTS[rel.value]
    for e in graph.edges(relation=R.RESOLVED):
        if e.source in people:
            for t in affected.get(e.target, []):
                out[t][e.source] += e.weight * WEIGHTS[R.RESOLVED.value]

    authors = {e.target: e.source for e in graph.edges(relation=R.AUTHORED) if e.source in people}
    for e in graph.edges(relation=R.DOCUMENTED_BY):
        if e.source in topics and e.target in authors:
            out[e.source][authors[e.target]] += WEIGHTS["AUTHORED_DOC"]

    return {t: dict(p) for t, p in out.items() if sum(p.values()) > 0}
