"""Generated documents: POST /artifacts/runbook, plus read-back of everything generated."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from app.agents.generator import generate_handover, generate_runbook
from app.graph import get_graph
from app.graph.queries import TOPIC_TYPES
from app.models import Entity, EntityType, GeneratedDoc, HandoverRequest, RunbookRequest
from app.services import doc_store, interview_store, risk_store
from app.services.llm import get_llm
from app.services.search import get_search

router = APIRouter(tags=["artifacts"])


def resolve_topic(topic: str, risk_id: Optional[str]) -> Entity:
    """A topic entity by risk id (preferred) or by name / alias."""
    graph = get_graph()
    if risk_id:
        risk = risk_store.get_risk(risk_id)
        if risk is None or not risk.entity_id or graph.get_entity(risk.entity_id) is None:
            raise HTTPException(status_code=404, detail=f"Unknown risk '{risk_id}'")
        return graph.get_entity(risk.entity_id)
    for t in TOPIC_TYPES:
        found = graph.find_entity(topic, t)
        if found:
            return found
    raise HTTPException(status_code=404, detail=f"Unknown topic '{topic}'")


@router.post("/artifacts/runbook", response_model=GeneratedDoc)
def runbook(req: RunbookRequest) -> GeneratedDoc:
    entity = resolve_topic(req.topic, req.risk_id)
    doc = generate_runbook(entity, get_graph(), get_search(), get_llm(), include_interviews=req.include_interviews)
    if doc is None:
        raise HTTPException(status_code=409, detail=f"No source material found for '{entity.name}'")
    return doc_store.save(doc)


@router.post("/artifacts/handover", response_model=GeneratedDoc)
def handover(req: HandoverRequest) -> GeneratedDoc:
    graph = get_graph()
    person = graph.get_entity(req.person_id)
    if person is None or person.type != EntityType.PERSON.value:
        raise HTTPException(status_code=404, detail=f"Unknown person '{req.person_id}'")
    only = None
    if req.topics:
        only = [resolve_topic(t, None).id for t in req.topics]
    doc = generate_handover(
        person, graph, get_search(), risk_store.get_risks().items, interview_store.list_sessions(),
        doc_store.list_docs("runbook"), only_topics=only,
    )
    if doc is None:
        raise HTTPException(status_code=409, detail=f"No captured knowledge found for '{person.name}'")
    return doc_store.save(doc)


@router.get("/artifacts", response_model=list[GeneratedDoc])
def list_artifacts(type: Optional[str] = None) -> list[GeneratedDoc]:
    return doc_store.list_docs(type)


@router.get("/artifacts/{doc_id}", response_model=GeneratedDoc)
def get_artifact(doc_id: str) -> GeneratedDoc:
    doc = doc_store.get(doc_id)
    if doc is None:
        raise HTTPException(status_code=404, detail=f"Unknown document '{doc_id}'")
    return doc
