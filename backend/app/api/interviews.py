"""Interview endpoints: start a session for a risk, read sessions back."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from app.agents.interviewer import generate_questions
from app.graph import get_graph
from app.models import EntityType, InterviewSession, InterviewStatus, RiskItem, StartInterviewRequest
from app.services import interview_store, risk_store
from app.services.llm import get_llm
from app.services.search import get_search

router = APIRouter(tags=["interviews"])


def pick_interviewee(risk: RiskItem, graph, override: Optional[str] = None) -> str:
    """The person to interview: the override, else the best owner, preferring a departing one."""
    people = {e.id: e for e in graph.entities(EntityType.PERSON)}
    if override:
        if override not in people:
            raise HTTPException(status_code=400, detail=f"Unknown interviewee '{override}'")
        return override
    candidates = [p for p in risk.owner_candidates if p in people]
    if not candidates:
        raise HTTPException(status_code=409, detail=f"No known owner for '{risk.topic}'")
    departing = [p for p in candidates if people[p].attributes.get("is_departing")]
    return (departing or candidates)[0]  # candidates are already best-first


@router.post("/interviews/{risk_id}/start", response_model=InterviewSession)
def start_interview(risk_id: str, response: Response, req: Optional[StartInterviewRequest] = None) -> InterviewSession:
    req = req or StartInterviewRequest()
    risk = risk_store.get_risk(risk_id)
    if risk is None:
        raise HTTPException(status_code=404, detail=f"Unknown risk '{risk_id}'")
    graph = get_graph()
    interviewee = pick_interviewee(risk, graph, req.interviewee)

    if not req.force_new:
        existing = interview_store.find_open(risk_id, interviewee)
        if existing:  # resume instead of duplicating; 200 tells the client nothing new was created
            return existing

    person = graph.get_entity(interviewee)
    questions = generate_questions(
        risk, graph, get_search(), get_llm(), interviewee_name=person.name, max_questions=req.max_questions
    )
    if not questions:
        raise HTTPException(status_code=409, detail=f"No evidence found to ground questions about '{risk.topic}'")
    session = InterviewSession(
        risk_id=risk.id, topic=risk.topic, interviewee=interviewee,
        status=InterviewStatus.PENDING, questions=questions,
    )
    interview_store.save(session)
    response.status_code = 201
    return session


@router.get("/interviews", response_model=list[InterviewSession])
def list_interviews(risk_id: Optional[str] = None) -> list[InterviewSession]:
    return interview_store.list_sessions(risk_id)


@router.get("/interviews/{session_id}", response_model=InterviewSession)
def get_interview(session_id: str) -> InterviewSession:
    session = interview_store.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail=f"Unknown interview session '{session_id}'")
    return session
