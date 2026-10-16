"""
Approve an interview: publish its answers into the knowledge base.

Each answer becomes an Artifact(type=interview) that goes through the normal ingestion
pipeline (redact -> chunk -> embed -> index -> extract -> graph). We then link the topic
to it with DOCUMENTED_BY, which is what lifts the topic's coverage and lowers its risk.
Safe to retry after a partial failure: artifacts already in the graph are skipped.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.agents.interviewer import build_answer_artifact
from app.graph.store import GraphStore
from app.ingestion.extractor import Extractor
from app.ingestion.pipeline import index_artifacts
from app.models import (
    ApproveResponse, Edge, EntityType, InterviewSession, InterviewStatus, Person, RelationType,
)
from app.services import interview_store, risk_store
from app.services.llm import LLMService
from app.services.search import SearchService

log = logging.getLogger(__name__)


class NothingToPublish(ValueError):
    """The session has no answers yet."""


def people_from_graph(graph: GraphStore) -> list[Person]:
    out = []
    for e in graph.entities(EntityType.PERSON):
        departure = e.attributes.get("departure_date")
        out.append(Person(
            id=e.id, name=e.name, role=e.attributes.get("role", ""), aliases=e.aliases,
            is_departing=bool(e.attributes.get("is_departing")),
            departure_date=datetime.fromisoformat(departure) if departure else None,
        ))
    return out


def approve_session(
    session: InterviewSession,
    *,
    graph: GraphStore,
    search: SearchService,
    extractor: Extractor,
    llm: LLMService,
    topic_entity_id: Optional[str] = None,
) -> ApproveResponse:
    if not session.answers:
        raise NothingToPublish("This interview has no answers to publish yet")

    questions = {q.id: q for q in session.questions}
    people = {p.id: p for p in people_from_graph(graph)}
    topic_id = topic_entity_id or next((q.target_entity_id for q in session.questions if q.target_entity_id), None)
    before = risk_store.get_risk(session.risk_id)

    artifacts = []
    for ans in session.answers:
        name = people[ans.answered_by].name if ans.answered_by in people else ans.answered_by
        artifacts.append(build_answer_artifact(session, questions[ans.question_id], ans, name))
    fresh = [a for a in artifacts if not graph.has_node(a.id)]  # skip what a failed attempt already wrote

    chunks = 0
    if fresh:
        resp, _ = index_artifacts(fresh, list(people.values()), search=search, graph=graph, extractor=extractor, llm=llm)
        chunks = resp.chunks_indexed
        for a in fresh:
            if topic_id and graph.has_node(topic_id):
                graph.add_edge(Edge(source=topic_id, target=a.id, relation=RelationType.DOCUMENTED_BY, evidence_artifact_ids=[a.id]))
                graph.add_edge(Edge(source=a.id, target=topic_id, relation=RelationType.MENTIONS, evidence_artifact_ids=[a.id]))
    graph.save()

    for ans, art in zip(session.answers, artifacts):
        ans.artifact_id = art.id
    session.status = InterviewStatus.COMPLETED
    session.completed_at = datetime.now(timezone.utc)
    interview_store.save(session)

    risk_store.invalidate()
    after = risk_store.get_risk(session.risk_id)
    log.info("interview %s approved: %d artifacts, risk %s -> %s", session.id, len(artifacts),
             before.risk_score if before else None, after.risk_score if after else None)
    return ApproveResponse(
        session=session, risk_before=before, risk_after=after,
        artifacts_published=len(artifacts), chunks_indexed=chunks,
    )
