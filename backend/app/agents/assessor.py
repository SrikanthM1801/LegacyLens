"""
Onboarding assessment: pick topics, write two grounded questions, evaluate journey + answers.

Topics: one the candidate STUDIED (most-touched in their learning log) to check it stuck, and one
important topic they did NOT explore. Each question is written from the project's own documents
and carries hidden expected points. Evaluation scores each answer against those points and the
learning journey (breadth, depth, relevance); the LLM writes reasons, strengths, gaps and a short
summary. The overall score and readiness level are computed here, not by the LLM, so they are
explainable: overall = 65% answers + 35% journey; ready >= 75, almost >= 50.
With the mock LLM, or if the LLM fails, deterministic rules do the same job.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from statistics import mean
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.prompts import (
    ASSESSMENT_EVAL_SYSTEM, ASSESSMENT_QUESTION_SYSTEM, build_assessment_eval_user, build_assessment_question_user,
)
from app.graph.queries import TOPIC_TYPES
from app.graph.store import GraphStore
from app.models import (
    AnswerScore, AssessmentEvaluation, AssessmentQuestion, Citation, Entity, OnboardingSession, RecommendedDoc,
)
from app.services.llm import LLMService, tokenize

log = logging.getLogger(__name__)

SOURCES_PER_QUESTION = 4
SOURCE_CHARS = 900
ANSWER_WEIGHT = 0.65
READY, ALMOST = 75, 50
_STOP = frozenset("the a an and or of to in on for is are was were be it this that with as by at from what how why "
                  "does do which who when your you their its our".split())


def project_topics(graph: GraphStore) -> list[Entity]:
    """Systems, processes and decisions, most-evidenced first."""
    found = [e for t in TOPIC_TYPES for e in graph.entities(t)]
    return sorted(found, key=lambda e: (-len(e.artifact_ids), e.name.lower()))


def topics_for_artifacts(graph: GraphStore, artifact_ids: list[str], question: str = "") -> list[str]:
    """Topics a learning question touched: those the cited artifacts mention AND the question is
    about (shared words with the topic name or aliases). Falls back to the 2 most-cited when the
    question names none, so one broad question doesn't count as studying every topic in a document."""
    kinds = {getattr(t, "value", t) for t in TOPIC_TYPES}
    found: dict[str, Entity] = {}
    for aid in artifact_ids:
        for e in graph.edges(source=aid):
            ent = graph.get_entity(e.target)
            if ent and getattr(ent.type, "value", ent.type) in kinds:
                found.setdefault(ent.name, ent)
    if not question:
        return list(found)
    words = _content_tokens(question)
    about = [n for n, ent in found.items() if words & _content_tokens(" ".join([n, *ent.aliases]))]
    return about or list(found)[:2]


def pick_topics(session: OnboardingSession, graph: GraphStore) -> list[tuple[Entity, str]]:
    topics = project_topics(graph)
    by_name = {t.name: t for t in topics}
    touched = Counter(n for entry in session.learning_log for n in entry.topics if n in by_name)
    chosen: list[tuple[Entity, str]] = []
    if touched:
        chosen.append((by_name[touched.most_common(1)[0][0]], "studied"))
    for t in topics:
        if len(chosen) == 2:
            break
        if t.name not in touched and all(t.id != c.id for c, _ in chosen):
            chosen.append((t, "unexplored"))
    for t in topics:  # tiny project: everything was studied
        if len(chosen) == 2:
            break
        if all(t.id != c.id for c, _ in chosen):
            chosen.append((t, "studied"))
    return chosen


class _QuestionOut(BaseModel):
    question: str = ""
    expected_points: list[str] = Field(default_factory=list)
    sources: list[int] = Field(default_factory=list)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n", text) if len(s.strip()) > 30]


def write_question(topic: Entity, focus: str, search, llm: LLMService) -> Optional[AssessmentQuestion]:
    query = " ".join(dict.fromkeys([topic.name, *topic.aliases[:2]]))
    hits: list[Citation] = search.search(query, top_k=SOURCES_PER_QUESTION, snippet_chars=SOURCE_CHARS)
    if not hits:
        return None
    out: Optional[_QuestionOut] = None
    if llm.model_name != "mock":
        try:
            out = llm.chat_json(ASSESSMENT_QUESTION_SYSTEM,
                                build_assessment_question_user(topic.name, focus, [(h.title, h.snippet) for h in hits]),
                                _QuestionOut)
            if not out.question.strip().endswith("?") or len(out.expected_points) < 2:
                out = None
        except Exception as e:  # fall back to the rule question below
            log.warning("assessment question generation failed: %s", type(e).__name__)
    if out is None:
        points = [s for h in hits for s in _sentences(h.snippet)][:4]
        out = _QuestionOut(
            question=f"In your own words, what is {topic.name} on this project, and what are the key details a new team member should know about it?",
            expected_points=points or [topic.description or topic.name], sources=list(range(1, len(hits) + 1)))
    used = [hits[i - 1] for i in out.sources if 1 <= i <= len(hits)] or hits
    return AssessmentQuestion(
        text=out.question.strip(), topic=topic.name, focus=focus, expected_points=out.expected_points[:5],
        source_artifact_ids=list(dict.fromkeys(h.artifact_id for h in used)),
        source_titles=list(dict.fromkeys(h.title for h in used)),
    )


def write_questions(session: OnboardingSession, graph: GraphStore, search, llm: LLMService) -> list[AssessmentQuestion]:
    qs = [q for t, focus in pick_topics(session, graph) if (q := write_question(t, focus, search, llm))]
    return qs[:2]


class _EvalOut(BaseModel):
    answer_scores: list[AnswerScore] = Field(default_factory=list)
    journey_score: int = Field(default=0, ge=0, le=5)
    journey_summary: str = ""
    strengths: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    summary: str = ""


def _content_tokens(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _STOP and len(t) > 2}


def _rule_answer_score(q: AssessmentQuestion) -> AnswerScore:
    answer = _content_tokens(q.answer or "")
    hit = [p for p in q.expected_points if (pt := _content_tokens(p)) and len(pt & answer) / len(pt) >= 0.4]
    score = round(5 * len(hit) / max(1, len(q.expected_points))) if answer else 0
    return AnswerScore(question_id=q.id, score=score,
                       reason=f"Covered {len(hit)} of {len(q.expected_points)} key points from the project documents.")


def _rule_journey(session: OnboardingSession, explored: list[str]) -> tuple[int, str]:
    asked = len(session.learning_log)
    follow_ups = sum(1 for _, n in Counter(t for e in session.learning_log for t in e.topics).items() if n > 1)
    score = min(5, len(explored) + min(1, follow_ups) + (1 if asked >= 5 else 0)) if asked else 0
    return score, f"Asked {asked} question(s) covering {len(explored)} topic(s), with follow-ups on {follow_ups}."


def _recommended(session: OnboardingSession, scores: dict[str, int], graph: GraphStore, explored: list[str]) -> list[RecommendedDoc]:
    docs: list[RecommendedDoc] = []
    for q in session.questions:
        if scores.get(q.id, 0) <= 3 or q.focus == "unexplored":
            for aid, title in zip(q.source_artifact_ids, q.source_titles):
                url = (graph.artifact_node(aid) or {}).get("url_or_path")
                if title not in {d.title for d in docs}:
                    docs.append(RecommendedDoc(title=title, url=url if url and url.startswith("http") else None))
    return docs[:5]


def evaluate(session: OnboardingSession, graph: GraphStore, llm: LLMService) -> AssessmentEvaluation:
    topics = project_topics(graph)
    explored = list(dict.fromkeys(t for e in session.learning_log for t in e.topics))
    out: Optional[_EvalOut] = None
    if llm.model_name != "mock":
        try:
            out = llm.chat_json(ASSESSMENT_EVAL_SYSTEM, build_assessment_eval_user(
                session.candidate_name, [t.name for t in topics[:30]],
                [(e.question, e.topics, e.answered) for e in session.learning_log[-40:]],
                [(q.id, q.focus, q.text, q.expected_points, q.answer or "") for q in session.questions],
            ), _EvalOut)
            if {s.question_id for s in out.answer_scores} != {q.id for q in session.questions}:
                out = None
        except Exception as e:
            log.warning("assessment evaluation failed, using rules: %s", type(e).__name__)
    if out is None:
        j_score, j_summary = _rule_journey(session, explored)
        scores = [_rule_answer_score(q) for q in session.questions]
        weak = [q.topic for q, s in zip(session.questions, scores) if s.score <= 2]
        strong = [q.topic for q, s in zip(session.questions, scores) if s.score >= 4]
        out = _EvalOut(
            answer_scores=scores, journey_score=j_score, journey_summary=j_summary,
            strengths=[f"Explains {t} well" for t in strong] + ([f"Explored {len(explored)} project topics"] if len(explored) >= 3 else []),
            gaps=[f"Needs a clearer understanding of {t}" for t in weak] or (["Ask more questions before the next assessment"] if not explored else []),
            summary=(f"{session.candidate_name} explored {len(explored)} of {len(topics)} project topics and answered "
                     f"{sum(s.score >= 3 for s in scores)} of {len(scores)} assessment questions well."),
        )
    by_q = {s.question_id: s.score for s in out.answer_scores}
    answers = mean(by_q.get(q.id, 0) for q in session.questions) if session.questions else 0
    overall = round(100 * (ANSWER_WEIGHT * answers / 5 + (1 - ANSWER_WEIGHT) * out.journey_score / 5))
    readiness = "ready" if overall >= READY else "almost" if overall >= ALMOST else "needs_more_time"
    return AssessmentEvaluation(
        readiness=readiness, overall=overall, journey_score=out.journey_score, journey_summary=out.journey_summary,
        answer_scores=[s for q in session.questions for s in out.answer_scores if s.question_id == q.id],
        strengths=out.strengths[:3], gaps=out.gaps[:3], summary=out.summary,
        recommended_docs=_recommended(session, by_q, graph, explored),
        topics_explored=explored, topics_total=len(topics), model=llm.model_name,
    )
