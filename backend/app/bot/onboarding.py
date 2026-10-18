"""
Onboarding assessment in Teams (bot side). No scoring logic here; that lives in agents/assessor.py.

1. In the project channel a team OWNER types `@LegacyLens assign @Candidate`. We check the sender
   is an owner and the candidate is on the team, install LegacyLens for the candidate, open a 1:1
   chat and send a welcome card that says plainly what will be evaluated.
2. In that 1:1 chat the candidate learns with ask / topics / runbook / risks; each request is added
   to the session's learning log with the topics it touched.
3. `start` writes 2 questions (one studied topic, one unexplored) and sends them one at a time.
4. After the last answer we evaluate in the background, show the candidate their result first,
   then post the result card to the project channel. Every step is audited (category onboarding).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

from app.agents.assessor import evaluate, topics_for_artifacts, write_questions
from app.graph import get_graph
from app.models import LearningEntry, OnboardingSession
from app.services import audit
from app.services.graph_api import GraphError, get_graph_api
from app.services.llm import get_llm
from app.services.onboarding_store import active_for, get_onboarding_store
from app.services.search import get_search
from app.services.teams_connector import ConnectorError, card_activity, get_connector

log = logging.getLogger(__name__)

ANSWER_VERB = "legacylens_assess_answer"
MIN_ANSWER_CHARS = 3
_BADGE = {"ready": ("✅ Ready", "Good"), "almost": ("🟡 Almost ready", "Warning"),
          "needs_more_time": ("🔴 Needs more time", "Attention")}
_SCHEMA = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5"}


def _audit(action: str, s: OnboardingSession, outcome: str = "success", **fields) -> None:
    audit.record(f"onboarding.{action}", outcome=outcome, source="teams", scope_type="onboarding",
                 scope_id=s.id, scope_name=s.candidate_name, **fields)


# --- cards ---------------------------------------------------------------------------
def welcome_card(s: OnboardingSession) -> dict:
    return {**_SCHEMA, "body": [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True,
         "text": f"👋 Welcome to {s.project_name or 'the project'}, {s.candidate_name.split(',')[-1].strip()}!"},
        {"type": "TextBlock", "wrap": True,
         "text": f"{s.assigned_by_name or 'A project owner'} asked me to help you get up to speed. Learn about the "
                 "project here by asking me anything; my answers come from the project's own documents and conversations."},
        {"type": "FactSet", "facts": [
            {"title": "ask …", "value": "any question, e.g. *What is the release process?*"},
            {"title": "topics", "value": "what there is to learn"},
            {"title": "runbook …", "value": "a step-by-step guide for one topic"},
            {"title": "status", "value": "your progress"},
            {"title": "start", "value": "when you're ready: 2 questions"},
        ]},
        {"type": "TextBlock", "wrap": True, "isSubtle": True,
         "text": "ℹ️ How you'll be assessed: the questions you ask here and your answers to 2 questions are used to "
                 "write a readiness summary. You'll see it first, then it's shared in the project channel. "
                 "Grammar and writing style are not judged."},
    ]}


def question_card(s: OnboardingSession, index: int) -> dict:
    q = s.questions[index]
    return {**_SCHEMA, "body": [
        {"type": "TextBlock", "weight": "Bolder", "text": f"Question {index + 1} of {len(s.questions)} · {q.topic}", "wrap": True},
        {"type": "TextBlock", "text": q.text, "wrap": True},
        {"type": "Input.Text", "id": "answer", "isMultiline": True, "placeholder": "Your answer (a few sentences)"},
    ], "actions": [{"type": "Action.Submit", "title": "Submit answer",
                    "data": {"verb": ANSWER_VERB, "session_id": s.id, "question_id": q.id}}]}


def result_card(s: OnboardingSession, for_channel: bool) -> dict:
    ev = s.evaluation
    label, color = _BADGE[ev.readiness]
    title = (f"Onboarding assessment · {s.candidate_name}" if for_channel else "Your onboarding result")
    body = [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": title, "wrap": True},
        {"type": "ColumnSet", "columns": [
            {"type": "Column", "width": "auto", "items": [{"type": "TextBlock", "text": label, "color": color, "weight": "Bolder"}]},
            {"type": "Column", "width": "stretch", "items": [{"type": "TextBlock", "text": f"Overall {ev.overall}/100", "horizontalAlignment": "Right"}]},
        ]},
        {"type": "TextBlock", "text": ev.summary, "wrap": True},
        {"type": "FactSet", "facts": [
            {"title": "Learning journey", "value": f"{ev.journey_score}/5 · {len(s.learning_log)} questions · "
                                                   f"{len(ev.topics_explored)} of {ev.topics_total} topics"},
            *[{"title": f"Q{i + 1} · {q.topic[:40]}", "value": f"{sc.score}/5 · {sc.reason}"}
              for i, (q, sc) in enumerate(zip(s.questions, ev.answer_scores))],
        ]},
    ]
    if ev.strengths:
        body.append({"type": "TextBlock", "wrap": True, "text": "**Strengths:** " + "; ".join(ev.strengths)})
    if ev.gaps:
        body.append({"type": "TextBlock", "wrap": True, "text": "**To work on:** " + "; ".join(ev.gaps)})
    if ev.recommended_docs:
        docs = ", ".join(f"[{d.title}]({d.url.replace(' ', '%20')})" if d.url else d.title for d in ev.recommended_docs)
        body.append({"type": "TextBlock", "wrap": True, "text": "**Read next:** " + docs})
    body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "size": "Small",
                 "text": (f"Assigned by {s.assigned_by_name or 'a project owner'}. " if for_channel else "")
                 + "Scored by LegacyLens against the project's own documents; a project owner should review before acting on it."})
    return {**_SCHEMA, "body": body}


# --- 1. assign -------------------------------------------------------------------------
async def assign(*, owner_aad: Optional[str], owner_name: str, candidate_aad: Optional[str], candidate_name: str,
                 team: dict, channel_id: str, service_url: str, tenant_id: Optional[str]) -> str:
    """Returns the reply for the channel."""
    graph_api, store = get_graph_api(), get_onboarding_store()
    if not candidate_aad:
        return ("I couldn't tell who to assign. Mention a team member (**@LegacyLens assign @Name**), or for someone "
                "not on the team yet, type their email: **@LegacyLens assign name@company.com**.")
    try:
        owners = await graph_api.team_owner_ids(team.get("aadGroupId") or "")
    except GraphError as e:
        log.warning("owner check failed: %s", e)
        return "I couldn't check who owns this team (missing permission TeamMember.Read.All?)."
    if owner_aad not in owners:
        return "Only a team owner can assign an onboarding assessment."
    if (existing := await asyncio.to_thread(active_for, store, candidate_aad)):
        if existing.error and not existing.learning_log and existing.status == "learning":
            existing.status = "cancelled"  # never reached the candidate: replace it
            await asyncio.to_thread(store.save, existing)
        else:
            return f"{candidate_name} already has an onboarding session in progress ({existing.status})."
    s = OnboardingSession(
        project_team_id=team.get("id", ""), project_name=team.get("name") or "", channel_id=channel_id,
        service_url=service_url, tenant_id=tenant_id, candidate_aad_id=candidate_aad, candidate_name=candidate_name,
        assigned_by_aad_id=owner_aad, assigned_by_name=owner_name,
    )
    connector = get_connector()
    try:
        await graph_api.install_app_for_user(candidate_aad)
        s.conversation_id = await connector.open_personal_chat(candidate_aad, tenant_id or "", service_url)
        await connector.send(s.conversation_id, card_activity(welcome_card(s)), service_url)
    except (GraphError, ConnectorError) as e:
        s.error = str(e)
        await asyncio.to_thread(store.save, s)
        _audit("assigned", s, outcome="failure", actor_name=owner_name, actor_aad_id=owner_aad, details={"error": str(e)})
        return (f"I created the session but couldn't open a chat with {candidate_name} ({e}). "
                f"Ask them to open LegacyLens in Teams (Apps → LegacyLens) and say hi; I'll pick it up.")
    await asyncio.to_thread(store.save, s)
    _audit("assigned", s, actor_name=owner_name, actor_aad_id=owner_aad, details={"project": s.project_name})
    return (f"✅ Assigned an onboarding assessment to **{candidate_name}**. I've messaged them in a 1:1 chat; "
            "they'll learn there, then type **start** for 2 questions. The result will be posted here.")


# --- 2. learning -------------------------------------------------------------------------
def session_for(candidate_aad: Optional[str], conversation_id: str) -> Optional[OnboardingSession]:
    if not candidate_aad:
        return None
    s = active_for(get_onboarding_store(), candidate_aad)
    if s and s.conversation_id != conversation_id:  # they opened the 1:1 themselves first
        s.conversation_id = conversation_id
        get_onboarding_store().save(s)
    return s


def log_learning(s: OnboardingSession, question: str, kind: str, cited: list[str], answered: bool) -> None:
    topics = topics_for_artifacts(get_graph(), cited, question)
    s.learning_log.append(LearningEntry(question=question[:500], kind=kind, topics=topics,
                                        cited_artifact_ids=cited[:20], answered=answered))
    get_onboarding_store().save(s)
    _audit("question_asked", s, actor_name=s.candidate_name, actor_aad_id=s.candidate_aad_id, count=len(s.learning_log),
           details={"kind": kind, "topics": topics, "answered": answered})


def progress_text(s: OnboardingSession) -> str:
    explored = list(dict.fromkeys(t for e in s.learning_log for t in e.topics))
    lines = [f"**Your onboarding** · {s.project_name or 'project'}",
             f"- Questions asked: **{len(s.learning_log)}**",
             f"- Topics explored: **{len(explored)}**" + (f" ({', '.join(explored[:6])})" if explored else "")]
    if s.status == "learning":
        lines.append("Type **start** when you're ready for your 2 questions.")
    elif s.status == "assessing":
        done = sum(1 for q in s.questions if q.answer)
        lines.append(f"Assessment in progress: {done} of {len(s.questions)} answered.")
    else:
        lines.append(f"Assessment {s.status}.")
    return "\n".join(lines)


# --- 3. assessment -------------------------------------------------------------------------
def start(s: OnboardingSession) -> tuple[Optional[dict], str]:
    """Returns (first question card, text). Blocking (LLM): call in a thread."""
    if s.status != "learning":
        return None, "Your assessment has already started." if s.status == "assessing" else "Your assessment is complete."
    questions = write_questions(s, get_graph(), get_search(), get_llm())
    if len(questions) < 2:
        return None, "I don't know enough about this project yet to write your questions. Please try again later."
    s.questions, s.status = questions, "assessing"
    s.assessment_started_at = datetime.now(timezone.utc)
    get_onboarding_store().save(s)
    _audit("assessment_started", s, actor_name=s.candidate_name, actor_aad_id=s.candidate_aad_id,
           count=len(s.learning_log), details={"topics": [q.topic for q in questions], "focus": [q.focus for q in questions]})
    return question_card(s, 0), f"Here are your {len(questions)} questions. Take your time and answer in your own words."


def current_question(s: OnboardingSession) -> Optional[int]:
    return next((i for i, q in enumerate(s.questions) if not q.answer), None)


def answer(s: OnboardingSession, question_id: Optional[str], text: str) -> tuple[Optional[dict], str, bool]:
    """Store one answer. Returns (next card, reply text, finished)."""
    if s.status != "assessing":
        return None, "There's no question waiting for an answer.", False
    idx = current_question(s)
    if question_id:
        idx = next((i for i, q in enumerate(s.questions) if q.id == question_id), idx)
    if idx is None or s.questions[idx].answer:
        return None, "You've already answered that one.", False
    if len((text or "").strip()) < MIN_ANSWER_CHARS:
        return None, "Please type an answer before submitting.", False
    q = s.questions[idx]
    q.answer, q.answered_at = text.strip()[:4000], datetime.now(timezone.utc)
    nxt = current_question(s)
    if nxt is None:
        s.status = "evaluating"
    get_onboarding_store().save(s)
    _audit("answered", s, actor_name=s.candidate_name, actor_aad_id=s.candidate_aad_id,
           details={"question": idx + 1, "topic": q.topic, "focus": q.focus})
    if nxt is not None:
        return question_card(s, nxt), "Thanks! Next question:", False
    return None, "Thanks, that's both. I'm evaluating your answers now; it takes a few seconds.", True


# --- 4. evaluation and result ----------------------------------------------------------
async def finish(session_id: str) -> None:
    """Evaluate, show the candidate their result, then post it to the project channel."""
    store, connector = get_onboarding_store(), get_connector()
    s = await asyncio.to_thread(store.get, session_id)
    if s is None or s.status != "evaluating":
        return
    try:
        s.evaluation = await asyncio.to_thread(evaluate, s, get_graph(), get_llm())
    except Exception as e:  # keep the answers; an owner can re-run later
        log.exception("evaluation failed")
        s.error = f"evaluation failed: {type(e).__name__}"
        await asyncio.to_thread(store.save, s)
        _audit("evaluated", s, outcome="failure", details={"error": s.error})
        return
    s.status = "evaluated"
    await asyncio.to_thread(store.save, s)
    ev = s.evaluation
    _audit("evaluated", s, actor_name="LegacyLens", details={
        "readiness": ev.readiness, "overall": ev.overall, "journey_score": ev.journey_score,
        "answer_scores": [a.score for a in ev.answer_scores], "model": ev.model})
    try:  # the candidate sees it first
        if s.conversation_id:
            await connector.send(s.conversation_id, card_activity(result_card(s, for_channel=False)), s.service_url)
        await connector.post_to_channel(s.channel_id, card_activity(result_card(s, for_channel=True)), s.service_url)
    except ConnectorError as e:
        s.error = f"result not delivered: {e}"
        await asyncio.to_thread(store.save, s)
        _audit("result_posted", s, outcome="failure", details={"error": str(e)})
        return
    s.status, s.posted_at = "posted", datetime.now(timezone.utc)
    await asyncio.to_thread(store.save, s)
    _audit("result_posted", s, actor_name="LegacyLens", details={"channel_id": s.channel_id, "readiness": ev.readiness})
