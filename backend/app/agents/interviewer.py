"""
Interview agent, step 1: write the questions.

`generate_questions` turns a RiskItem into 3-5 evidence-grounded questions aimed at
tacit knowledge (triggers, first checks, workarounds, gotchas, escalation, decision
criteria). The LLM writes them from numbered evidence and must cite which evidence
each question uses; questions that cite nothing real are dropped. If the LLM is the
mock, fails, or returns nothing usable, deterministic templates built from the same
evidence take over, so interviews always work offline.
"""

from __future__ import annotations

import logging
import re
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.agents.gap_analyzer import collect_evidence
from app.agents.prompts import INTERVIEW_SYSTEM, build_interview_user
from app.graph.store import GraphStore
from app.models import Artifact, ArtifactType, Citation, EntityType, InterviewQuestion, RiskItem
from app.services.llm import LLMService

log = logging.getLogger(__name__)

EVIDENCE_FOR_INTERVIEW = 6
#: Commits are too terse to ask good questions from; conversations and tickets carry the reasoning.
EVIDENCE_TYPES = ["chat", "email", "ticket"]
EVIDENCE_SNIPPET_CHARS = 900     # a full chunk: the LLM needs the actual commands and names
MAX_QUESTION_CHARS = 500
QUOTE_CHARS = 180
SPEAKER_BOOST = 6  # roughly three backticked commands' worth: the owner's words beat a stranger's

Category = Literal[
    "trigger", "first_check", "workaround", "gotcha", "escalation", "decision_criteria", "alternatives"
]


class GeneratedQuestion(BaseModel):
    text: str
    rationale: str = ""
    category: str = ""
    evidence: list[int] = Field(default_factory=list)  # 1-based numbers from the prompt


class QuestionSet(BaseModel):
    questions: list[GeneratedQuestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic templates (mock mode and fallback)
# ---------------------------------------------------------------------------

_SYSTEM_PLAN: tuple[Category, ...] = ("first_check", "workaround", "gotcha", "escalation", "trigger")
_DECISION_PLAN: tuple[Category, ...] = ("decision_criteria", "alternatives", "gotcha", "escalation")

_TEMPLATES: dict[str, tuple[str, str]] = {
    "first_check": (
        "In “{title}” the record says: “{quote}” When {topic} goes wrong, what do you look at first, "
        "and how do you tell which kind of problem it is?",
        "Surfaces the diagnostic order that experts apply without thinking.",
    ),
    "workaround": (
        "“{title}” mentions: “{quote}” What are the exact steps you follow when the normal path "
        "fails, and in what order?",
        "Turns a one-line fix into a repeatable procedure.",
    ),
    "gotcha": (
        "Looking at “{title}” (“{quote}”): what goes wrong if someone new follows these steps naively "
        "for {topic}?",
        "Captures the traps and exceptions that are only learned by getting burned.",
    ),
    "escalation": (
        "In “{title}” (“{quote}”), if the usual fix for {topic} does not work, who or what do you "
        "escalate to, and after how long?",
        "Records the escalation path and the point at which to use it.",
    ),
    "trigger": (
        "From “{title}” (“{quote}”): what are the early warning signs that {topic} is about to have "
        "this problem again?",
        "Captures triggers so the problem can be caught before it becomes an incident.",
    ),
    "decision_criteria": (
        "In “{title}” you wrote: “{quote}” What were the deciding criteria for {topic}, and which one "
        "mattered most?",
        "Preserves the reasoning behind the decision, not just its outcome.",
    ),
    "alternatives": (
        "“{title}” says: “{quote}” Besides the chosen option, what else was seriously considered for "
        "{topic}, and why was it rejected?",
        "Records rejected alternatives so they are not re-litigated blindly.",
    ),
}
# Decisions reuse the shared templates, but "gotcha"/"escalation" read differently for them.
_DECISION_OVERRIDES: dict[str, tuple[str, str]] = {
    "gotcha": (
        "Given “{title}” (“{quote}”): what trade-offs did you accept with {topic}, and what would make "
        "you revisit it?",
        "Captures the accepted downsides and the conditions for revisiting the decision.",
    ),
    "escalation": (
        "Who else needs to be consulted before {topic} is changed, and what should they know first?",
        "Records who has a stake in the decision.",
    ),
}

_SPEAKER_PREFIX = re.compile(r"^\[[^\]]+\]\s*[^:\n]{1,30}:\s*")
_HEADER_LINE = re.compile(r"^(From|To|Date|Subject|Channel):", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def best_quote(snippet: str, limit: int = QUOTE_CHARS, speaker: Optional[str] = None) -> str:
    """The most informative sentence in an evidence snippet.

    Prefers sentences spoken by `speaker` (the interviewee), then ones with commands,
    flags and numbers. Chat speaker prefixes and email headers are stripped.
    """
    who = speaker.split()[0].lower() if speaker else None
    candidates: list[tuple[int, int, str]] = []
    for i, raw in enumerate(snippet.splitlines()):
        raw = raw.strip().lstrip("- ").strip()
        if not raw or _HEADER_LINE.match(raw):
            continue
        prefix = _SPEAKER_PREFIX.match(raw)
        spoken_by_them = bool(who and prefix and prefix.group().rstrip(": ").split("]")[-1].strip().lower() == who)
        line = _SPEAKER_PREFIX.sub("", raw)
        for sent in _SENTENCE.split(line):
            sent = sent.strip()
            if len(sent) < 25:
                continue
            score = 2 * sent.count("`") + sum(ch.isdigit() for ch in sent[:80]) // 3 + (1 if "--" in sent else 0)
            candidates.append((-(score + (SPEAKER_BOOST if spoken_by_them else 0)), i, sent))
    if not candidates:
        return " ".join(snippet.split())[:limit].rstrip()
    text = min(candidates)[2]
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def template_questions(
    risk: RiskItem, evidence: list[Citation], max_questions: int, speaker: Optional[str] = None
) -> list[InterviewQuestion]:
    if not evidence:
        return []
    decision = risk.entity_type == EntityType.DECISION.value
    plan = _DECISION_PLAN if decision else _SYSTEM_PLAN
    out = []
    for i, category in enumerate(plan[:max_questions]):
        ev = evidence[i % len(evidence)]
        text_t, why = (_DECISION_OVERRIDES if decision else {}).get(category) or _TEMPLATES[category]
        out.append(InterviewQuestion(
            text=text_t.format(title=ev.title or ev.artifact_id, quote=best_quote(ev.snippet, speaker=speaker), topic=risk.topic),
            rationale=why,
            evidence=[ev],
            target_entity_id=risk.entity_id,
        ))
    return out


# ---------------------------------------------------------------------------
# LLM path
# ---------------------------------------------------------------------------


def _from_llm(qs: QuestionSet, evidence: list[Citation], risk: RiskItem, max_questions: int) -> list[InterviewQuestion]:
    out, seen = [], set()
    for q in qs.questions:
        text = " ".join(q.text.split())
        cited = [evidence[n - 1] for n in dict.fromkeys(q.evidence) if 1 <= n <= len(evidence)]
        if not text or "?" not in text or len(text) > MAX_QUESTION_CHARS or not cited:
            log.info("dropping ungrounded/invalid generated question: %r", text[:80])
            continue
        if text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(InterviewQuestion(
            text=text, rationale=q.rationale.strip(), evidence=cited, target_entity_id=risk.entity_id
        ))
        if len(out) == max_questions:
            break
    return out


def generate_questions(
    risk: RiskItem,
    graph: GraphStore,
    search,
    llm: LLMService,
    *,
    interviewee_name: Optional[str] = None,
    max_questions: int = 4,
) -> list[InterviewQuestion]:
    """Grounded interview questions for a risk. Empty only if the topic has no evidence at all."""
    entity = graph.get_entity(risk.entity_id) if risk.entity_id else None
    if entity is None:
        return []
    evidence = collect_evidence(
        entity, graph, search, limit=EVIDENCE_FOR_INTERVIEW, snippet_chars=EVIDENCE_SNIPPET_CHARS,
        types=EVIDENCE_TYPES,
    )
    if not evidence:
        return []
    # Conversations carry the reasoning; tickets are terse. Keep relevance order within each group.
    evidence.sort(key=lambda c: c.artifact_type == "ticket")

    if interviewee_name is None and risk.owner_candidates:
        owner = graph.get_entity(risk.owner_candidates[0])
        interviewee_name = owner.name if owner else None

    system = INTERVIEW_SYSTEM.format(max_questions=max_questions)
    user = build_interview_user(
        risk.topic, risk.entity_type or "topic", risk.summary,
        interviewee_name or "the topic owner", [(c.title or c.artifact_id, c.snippet) for c in evidence],
    )
    try:
        questions = _from_llm(llm.chat_json(system, user, QuestionSet), evidence, risk, max_questions)
    except Exception as exc:  # LLM down, rate-limited, or invalid JSON twice
        log.warning("question generation via LLM failed (%s); using templates", type(exc).__name__)
        questions = []
    return questions or template_questions(risk, evidence, max_questions, speaker=interviewee_name)


# ---------------------------------------------------------------------------
# Answers: vagueness check, follow-ups, and the artifact an approved answer becomes
# ---------------------------------------------------------------------------

_VAGUE_PHRASES = (
    "not sure", "don't remember", "dont remember", "i forget", "it depends", "no idea", "idk",
    "same as before", "as usual", "figure it out", "n/a", "just restart", "it just works",
)
_SPECIFIC = re.compile(r"`|--|\d|[A-Z][A-Z_]{3,}|/|\.(?:py|yaml|yml|sql|sh)\b|\b[A-Z]{2,5}-\d+\b")
MIN_SPECIFIC_WORDS = 12


def is_vague(answer: str) -> bool:
    """Heuristic: a short answer with no concrete command, name, value or id, or an evasive one."""
    text = answer.strip()
    words = text.split()
    low = text.lower()
    if any(p in low for p in _VAGUE_PHRASES) and len(words) < 40:
        return True
    return len(words) < MIN_SPECIFIC_WORDS and not _SPECIFIC.search(text)


def follow_up_for(question: InterviewQuestion, answer_text: str) -> InterviewQuestion:
    """One short, specific follow-up to a vague answer. Never chained."""
    said = " ".join(answer_text.split())
    said = said if len(said) <= 80 else said[:79].rstrip() + "…"
    return InterviewQuestion(
        text=(f"You said “{said}”. Could you be more specific: what exact command, setting, value or "
              "name is involved, and what do you see when it has worked?"),
        rationale="The first answer was brief; a concrete detail is what a successor can actually follow.",
        evidence=question.evidence,
        target_entity_id=question.target_entity_id,
        follow_up_of=question.id,
    )


def build_answer_artifact(session, question: InterviewQuestion, answer, person_name: str) -> Artifact:
    """The Artifact(type=interview) an approved answer becomes (deterministic id from the answer id)."""
    from datetime import timezone

    return Artifact(
        id=f"art_interview_{answer.id}",
        type=ArtifactType.INTERVIEW,
        source_id=f"{session.id}/{answer.id}",
        author=answer.answered_by,
        timestamp=answer.answered_at.astimezone(timezone.utc),
        title=f"Interview: {session.topic}",
        body=f"Question: {question.text}\n\nAnswer ({person_name}): {answer.text}",
        url_or_path=f"interview://{session.id}/{answer.id}",
        tags=["interview", "tacit-knowledge"],
        metadata={"session_id": session.id, "risk_id": session.risk_id, "question_id": question.id,
                  "topic": session.topic, "interviewee": session.interviewee},
    )
