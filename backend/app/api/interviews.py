"""Interview endpoints: start a session for a risk, read sessions back."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Response

from app.agents.interviewer import follow_up_for, generate_questions, is_vague
from app.graph import get_graph
from app.ingestion.extractor import get_extractor
from app.ingestion.pipeline import ingest_lock
from app.services import audit
from app.models import (
    AnswerRequest, AnswerResponse, ApproveResponse, EntityType, InterviewAnswer, InterviewSession, InterviewStatus, RiskItem,
    StartInterviewRequest,
)
from app.services import interview_store, risk_store
from app.services.interview_publish import NothingToPublish, approve_session
from app.services.llm import get_llm
from app.services.search import get_search

log = logging.getLogger(__name__)
router = APIRouter(tags=["interviews"])

MAX_ANSWER_CHARS = 5000


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
    audit.record("interview.started", source="api", actor_name=person.name, scope_type="interview",
                 scope_id=session.id, scope_name=risk.topic, count=len(questions), details={"risk_id": risk.id})
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


@router.post("/interviews/{session_id}/answer", response_model=AnswerResponse)
def answer_question(session_id: str, req: AnswerRequest) -> AnswerResponse:
    """Record an answer as a DRAFT. Nothing is indexed or counted as documentation until the
    interviewee approves (POST .../approve): humans review before knowledge is published.
    Answering a question again replaces the draft. A vague answer gets one follow-up question."""
    text = req.text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Answer text must not be empty")
    if len(text) > MAX_ANSWER_CHARS:
        raise HTTPException(status_code=422, detail=f"Answer is longer than {MAX_ANSWER_CHARS} characters")
    graph = get_graph()

    with interview_store.lock():
        session = interview_store.get(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail=f"Unknown interview session '{session_id}'")
        if session.status == InterviewStatus.COMPLETED.value:
            raise HTTPException(status_code=409, detail="This interview is already approved and published")
        question = next((q for q in session.questions if q.id == req.question_id), None)
        if question is None:
            raise HTTPException(status_code=404, detail=f"Unknown question '{req.question_id}' in this session")
        who = req.answered_by or session.interviewee
        if graph.get_entity(who) is None or graph.get_entity(who).type != EntityType.PERSON.value:
            raise HTTPException(status_code=400, detail=f"Unknown person '{who}'")

        answer = InterviewAnswer(question_id=question.id, session_id=session.id, text=text, answered_by=who)
        session.answers = [a for a in session.answers if a.question_id != question.id] + [answer]
        question.answered = True

        # Follow-up bookkeeping: only for original questions, at most one each.
        follow_up = next((q for q in session.questions if q.follow_up_of == question.id), None)
        new_follow_up = None
        if question.follow_up_of is None:
            if is_vague(text) and follow_up is None:
                new_follow_up = follow_up_for(question, text)
                session.questions.append(new_follow_up)
            elif not is_vague(text) and follow_up is not None and not follow_up.answered:
                session.questions.remove(follow_up)  # the detail arrived; drop the nudge

        session.status = InterviewStatus.IN_PROGRESS
        interview_store.save(session)
        audit.record("interview.answered", source="api", actor_name=(graph.get_entity(who).name),
                     scope_type="interview", scope_id=session.id, scope_name=session.topic,
                     details={"question_id": question.id, "follow_up_asked": new_follow_up is not None})

    return AnswerResponse(
        session=session, stored_answer=answer, artifact_id=f"art_interview_{answer.id}",
        chunks_indexed=0, follow_up=new_follow_up,
    )


@router.post("/interviews/{session_id}/approve", response_model=ApproveResponse)
def approve_interview(session_id: str) -> ApproveResponse:
    """The interviewee reviewed their answers: publish them into the knowledge base.
    Returns the risk before and after so the client can show it drop."""
    if not ingest_lock.acquire(blocking=False):  # we change the shared index and graph
        raise HTTPException(status_code=409, detail="An ingest is running; try again in a moment")
    try:
        with interview_store.lock():
            session = interview_store.get(session_id)
            if session is None:
                raise HTTPException(status_code=404, detail=f"Unknown interview session '{session_id}'")
            if session.status == InterviewStatus.COMPLETED.value:
                raise HTTPException(status_code=409, detail="This interview is already approved and published")
            risk = risk_store.get_risk(session.risk_id)
            try:
                result = approve_session(
                    session, graph=get_graph(), search=get_search(), extractor=get_extractor(), llm=get_llm(),
                    topic_entity_id=risk.entity_id if risk else None,
                )
                person = get_graph().get_entity(session.interviewee)
                audit.record("interview.approved", source="api", actor_name=person.name if person else None,
                             scope_type="interview", scope_id=session.id, scope_name=session.topic,
                             count=result.artifacts_published, details={
                                 "chunks": result.chunks_indexed,
                                 "risk_before": result.risk_before.risk_score if result.risk_before else None,
                                 "risk_after": result.risk_after.risk_score if result.risk_after else None})
                return result
            except NothingToPublish as e:
                raise HTTPException(status_code=409, detail=str(e))
            except HTTPException:
                raise
            except Exception as e:  # Azure hiccup mid-publish: the session stays open, retry is safe
                log.exception("approve failed for %s", session_id)
                raise HTTPException(status_code=502, detail=f"Publishing failed ({type(e).__name__}); you can retry")
    finally:
        ingest_lock.release()
