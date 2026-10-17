"""POST /ask: the Project Copilot. Answers carry citations or say they cannot answer."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.agents.copilot import answer_question
from app.graph import get_graph
from app.models import AskRequest, AskResponse
from app.services import audit, risk_store
from app.services.llm import get_llm
from app.services.search import get_search

router = APIRouter(tags=["ask"])

MAX_QUESTION_CHARS = 1000


@router.post("/ask", response_model=AskResponse)
def ask(req: AskRequest) -> AskResponse:
    question = req.question.strip()
    if not question:
        raise HTTPException(status_code=422, detail="Question must not be empty")
    if len(question) > MAX_QUESTION_CHARS:
        raise HTTPException(status_code=422, detail=f"Question is longer than {MAX_QUESTION_CHARS} characters")
    req = req.model_copy(update={"question": question})
    resp = answer_question(req, get_graph(), get_search(), get_llm(), risk_store.get_risks().items)
    audit.record("ask.answered", source="api", item_ids=[c.artifact_id for c in resp.citations],
                 details={"reply": "answer" if resp.citations else "no_answer", "confidence": round(resp.confidence, 2)})
    return resp
