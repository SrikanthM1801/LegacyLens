"""
Project Copilot: answer a question from the project's own knowledge, with citations.

1. Retrieve: hybrid search for the question, then expand through the graph. Topics and
   people named in the question pull in the published interview answers about the topic and
   the artifacts that show that person working on it ("How did Ravi usually handle X?").
2. Answer: the LLM answers only from the numbered sources and cites every sentence; we drop any
   sentence without a valid citation. With the mock LLM, or on failure, an extractive fallback
   quotes the best-matching sentences instead.
3. If the sources do not contain the answer, say so and suggest an interview. Never guess.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.generator import (
    SOURCE_CHARS, Source, _RATIONALE, _answer_text, _clip, _paragraphs, _sentences, classify, gather_sources,
    ownership_evidence,
)
from app.agents.prompts import ASK_SYSTEM, build_ask_user
from app.graph.queries import TOPIC_TYPES
from app.graph.store import GraphStore
from app.ingestion.extractor import alias_regex
from app.models import AskRequest, AskResponse, Citation, Entity, EntityType, RelationType, RiskItem
from app.services.llm import LLMService, tokenize

log = logging.getLogger(__name__)

MAX_SOURCES_EXTRA = 2          # sources beyond top_k that graph expansion may add
MAX_SOURCE_TEXT = SOURCE_CHARS
MIN_SENTENCE_SCORE = 0.3
MAX_ANSWER_SENTENCES = 4
_CITE = re.compile(r"\[(\d+)\]")
_STOP = frozenset(
    "a an and are as at be by can could did do does for from had has have how i if in into is it its me my of "
    "on or our should so than that the their them then there these they this to us was we were what when where "
    "which who will with would you your usually typically normally often generally over about".split()
)

NOT_FOUND = "I couldn't find this in the project knowledge base."


class LLMAnswer(BaseModel):
    sufficient: bool = False
    answer: str = ""
    confidence: float = 0.0
    followups: list[str] = Field(default_factory=list)


def content_words(text: str) -> set[str]:
    return {t for t in tokenize(text) if t not in _STOP and len(t) > 1}


# ---------------------------------------------------------------------------
# Entity detection
# ---------------------------------------------------------------------------


def detect_topics(question: str, graph: GraphStore) -> list[Entity]:
    """Topic entities named in the question by name or alias (catalog and LLM-found alike)."""
    found = []
    for t in TOPIC_TYPES:
        for e in graph.entities(t):
            if any(alias_regex(a).search(question) for a in dict.fromkeys([e.name, *e.aliases]) if a.strip()):
                found.append(e)
    return found


def detect_person(question: str, graph: GraphStore) -> Optional[Entity]:
    for p in graph.entities(EntityType.PERSON):
        names = [p.name, *p.aliases]
        if any(alias_regex(n).search(question) for n in names if n.strip()):
            return p
    return None


# ---------------------------------------------------------------------------
# Retrieval
# ---------------------------------------------------------------------------


def _type_ok(c: Citation, types: Optional[list[str]]) -> bool:
    return not types or str(c.artifact_type) in types


def retrieve(
    req: AskRequest, graph: GraphStore, search, topics: list[Entity], person: Optional[Entity],
) -> tuple[list[Source], set[str]]:
    """Sources for a question, best first, plus the ids of those the graph links to the asked topic
    (published interviews and wiki pages about it), which need no further topical check."""
    types = [t.value if hasattr(t, "value") else str(t) for t in req.artifact_types] if req.artifact_types else None
    pool: dict[str, Source] = {}

    def add(c: Citation, interview: Optional[bool] = None) -> None:
        if c.artifact_id in pool or not _type_ok(c, types):
            return
        is_int = (c.artifact_type == "interview") if interview is None else interview
        pool[c.artifact_id] = Source(c, _answer_text(c.snippet) if is_int else c.snippet, is_int)

    base = search.search(req.question, top_k=req.top_k, artifact_types=types, author=req.person_filter, snippet_chars=MAX_SOURCE_TEXT)
    for h in base:
        add(h)

    interviews: list[Source] = []
    docs: list[Source] = []
    person_src: list[Source] = []
    for topic in topics[:2]:
        interviews += [s for s in gather_sources(topic, graph, search) if s.is_interview]  # published interviews
        doc_ids = {e.target for e in graph.edges(source=topic.id, relation=RelationType.DOCUMENTED_BY)
                   if (graph.artifact_node(e.target) or {}).get("type") == "doc"}
        if doc_ids:  # wiki pages that are about the topic
            query = " ".join(dict.fromkeys([topic.name, *topic.aliases[:3]]))
            docs += [Source(h, h.snippet, False) for h in
                     search.search(query, top_k=10, artifact_types=["doc"], snippet_chars=MAX_SOURCE_TEXT)
                     if h.artifact_id in doc_ids]
        if person:  # what this person did / said about this topic (chats count, not just fixes)
            ids = {c.artifact_id for c in ownership_evidence(graph, person.id, topic.id, limit=30)}
            if ids:
                query = " ".join(dict.fromkeys([topic.name, *topic.aliases[:3]]))
                person_src += [Source(h, h.snippet, h.artifact_type == "interview")
                               for h in search.search(query, top_k=30, artifact_types=types, snippet_chars=MAX_SOURCE_TEXT)
                               if h.artifact_id in ids]

    # Interviews are the most trustworthy, then what the named person did, then the general hits.
    ordered: dict[str, Source] = {}
    for src in [*interviews, *docs, *person_src, *pool.values()]:
        if src.citation.artifact_id not in ordered and _type_ok(src.citation, types):
            ordered[src.citation.artifact_id] = src
    ranked = sorted(ordered.values(), key=lambda s: not s.is_interview)
    linked = {s.citation.artifact_id for s in [*interviews, *docs]}
    return ranked[: req.top_k + (MAX_SOURCES_EXTRA + (3 if person else 0))], linked


# ---------------------------------------------------------------------------
# Answering
# ---------------------------------------------------------------------------


def _followups(topics: list[Entity], question: str) -> list[str]:
    if not topics:
        return []
    t = topics[0].name
    pool = [f"What should I check first when {t} has a problem?", f"Who else knows about {t}?",
            f"What are the known gotchas with {t}?", f"What is the escalation path for {t}?"]
    return [q for q in pool if q.lower() != question.strip().lower()][:3]


def _insufficient(topics: list[Entity], person: Optional[Entity], risks: list[RiskItem], model: str) -> AskResponse:
    suggestion = ""
    risk = next((r for r in risks if topics and r.entity_id == topics[0].id), None)
    if risk:
        owner = person.name if person else None
        owner = owner or next(iter(risk.owner_candidates), None)
        suggestion = (f" The topic “{risk.topic}” is a recorded knowledge risk: start an interview"
                      f"{' with ' + owner if owner else ''} to capture it (risk id: {risk.id}).")
    elif topics:
        suggestion = f" If someone on the team knows about {topics[0].name}, an interview can capture it."
    else:
        suggestion = " If someone on the team knows the answer, an interview can capture it."
    return AskResponse(
        answer=NOT_FOUND + suggestion, citations=[], confidence=0.0,
        followup_questions=_followups(topics, ""), model=model,
    )


_CHAT_LINE = re.compile(r"^(?:- )?\[[^\]]+\]\s*([^:\n]{1,30}):\s*(.*)$")
_TIMEOUT = re.compile(r"\b(time[sd]? out|timing out)\b", re.I)
_WHY_WORDS = frozenset({"why", "reason", "rationale", "choose", "chose", "chosen", "pick", "picked", "select", "selected", "decide", "decided"})
_HOW_WORDS = frozenset({"handle", "handled", "handling", "deal", "resolve", "resolved", "fix", "fixed", "respond", "recover", "steps", "procedure"})
_ACTION_SECTIONS = {"fix", "diagnosis", "escalation", "verification"}
_HOW_Q = re.compile(r"\b(what (?:do|should|would) (?:i|we|you)|how (?:do|can|should|would) (?:i|we|you)|how to)\b", re.I)
_WHY_BOOST = 0.6
_HOW_BOOST = 0.3
_SPEAKER_BOOST = 0.3
_INTERVIEW_BOOST = 0.15
MAX_PER_SOURCE = 2


def _norm(text: str) -> set[str]:
    """Content words, lightly normalised: 'time out'/'timing out' -> 'timeout'; plural 's' stripped."""
    text = _TIMEOUT.sub("timeout", text)
    out = set()
    for w in content_words(text):
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if len(w) > 6 and w.endswith("ing"):
            w = w[:-3]
        out.add(w)
    return out


def _attributed(src: Source, whole_lines: bool = False) -> list[tuple[Optional[str], str]]:
    """(speaker first name or None, unit) for the usable text of a source.

    Units are sentences, or whole lines for "why" questions (a rationale only makes sense in full)."""
    out: list[tuple[Optional[str], str]] = []
    default = (src.citation.author or "").lower() if src.is_interview else None
    for raw in src.text.splitlines():
        m = _CHAT_LINE.match(raw.strip())
        speaker, body = (m.group(1).strip().split()[0].lower(), m.group(2)) if m else (default, raw)
        units = [_clip(p, 320) for p in _paragraphs(body)] if whole_lines else _sentences(body)
        out += [(speaker, u) for u in units]
    return out


def _extractive(question: str, sources: list[Source], topics: list[Entity], person: Optional[Entity], linked: frozenset | set = frozenset()):
    """Fallback: quote the units that best answer the question. Returns (text, source numbers, score) or None.

    Topic words only decide whether a source is *about* the topic; the rest of the question decides
    which units answer it. "Why" questions favour rationale (whole lines), "how/what do I do" favours
    actions, and "what is X" favours documents about X. When a person is named, their own words win.
    """
    q_words = _norm(question)
    anchors: set[str] = set()
    topic_patterns = []  # the alias phrases the question used; a source must contain one of them
    for t in topics:
        for alias in dict.fromkeys([t.name, *t.aliases]):
            if alias.strip() and alias_regex(alias).search(question):
                anchors |= _norm(alias)
                topic_patterns.append(alias_regex(alias))
    person_words = _norm(" ".join([person.name, *person.aliases])) if person else set()
    why = bool(q_words & _WHY_WORDS)
    how = bool(q_words & _HOW_WORDS) or bool(_HOW_Q.search(question))
    rest = q_words - anchors - person_words - _WHY_WORDS - _HOW_WORDS
    overview = not rest and not why and not how and bool(anchors)
    person_first = person.name.split()[0].lower() if person else None

    scored = []
    for n, src in enumerate(sources, 1):
        if topic_patterns and src.citation.artifact_id not in linked and not any(p.search(src.text) for p in topic_patterns):
            continue  # not about the topic asked
        for pos, (speaker, unit) in enumerate(_attributed(src, whole_lines=why)):
            words = _norm(unit)
            if overview:
                about = src.is_interview or src.citation.artifact_type == "doc"  # a page about the topic explains it
                score = (0.45 if about else 0.0) + 0.4 * len(anchors & words) / len(anchors)
            else:
                lexical = len(rest & words) / len(rest) if rest else 0.0
                cues = {m.lower() for m in _RATIONALE.findall(unit)} if why else set()
                cue = (_WHY_BOOST + min(0.3, 0.1 * len(cues))) if cues else _HOW_BOOST if how and classify(unit) in _ACTION_SECTIONS else 0.0
                if cue and not lexical:
                    # A cue alone ("it contains the word run") proves nothing: the unit must also relate to the
                    # question (the named person's words, a source about the topic, or the topic itself).
                    related = (person_first and speaker == person_first) or src.citation.artifact_id in linked or (anchors & words)
                    if not related:
                        continue
                score = 0.5 * lexical + cue if (why or how) else lexical
            if score <= 0:
                continue
            score += _INTERVIEW_BOOST if src.is_interview else 0.0
            scored.append((score, n, pos, unit, bool(person_first and speaker == person_first)))

    scored = [x for x in scored if x[0] >= MIN_SENTENCE_SCORE]
    mine = [x for x in scored if x[4]]
    if len(mine) >= 2:  # "how did Ravi...": answer from Ravi's own words, not from people reporting on him
        scored = mine
    scored.sort(key=lambda x: (-round(x[0], 3), x[1], x[2]))  # ties: source order, then position in the source

    chosen, seen, per = [], set(), {}
    for score, n, _, unit, _ in scored:
        if unit in seen or per.get(n, 0) >= MAX_PER_SOURCE:
            continue
        seen.add(unit)
        per[n] = per.get(n, 0) + 1
        chosen.append((score, n, unit))
        if len(chosen) == MAX_ANSWER_SENTENCES:
            break
    if not chosen:
        return None
    best = chosen[0][0]
    chosen.sort(key=lambda x: (x[1], -x[0]))  # interviews (lowest numbers) first
    return "\n".join(f"- {unit} [{n}]" for _, n, unit in chosen), [n for _, n, _ in chosen], min(1.0, best)


def _finish(lines_text: str, sources: list[Source], model: str, confidence: float, followups: list[str], lead: str = "") -> AskResponse:
    """Renumber citations by first use and attach the cited sources."""
    order: dict[int, int] = {}
    for m in _CITE.finditer(lines_text):
        order.setdefault(int(m.group(1)), len(order) + 1)
    text = _CITE.sub(lambda m: f"[{order[int(m.group(1))]}]", lines_text)
    cites = [sources[n - 1].citation for n in order]
    return AskResponse(
        answer=(lead + "\n" + text).strip() if lead else text, citations=cites,
        confidence=round(max(0.0, min(1.0, confidence)), 2), followup_questions=followups, model=model,
    )


def _validate_llm(text: str, n_sources: int) -> str:
    """Keep only lines whose every cited number is a real source, and which cite at least one."""
    kept = []
    for line in text.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        nums = [int(n) for n in _CITE.findall(line)]
        if nums and all(1 <= n <= n_sources for n in nums):
            kept.append(line)
        else:
            log.info("dropping uncited/invalid answer line: %r", line[:80])
    return "\n".join(kept)


def answer_question(
    req: AskRequest, graph: GraphStore, search, llm: LLMService, risks: list[RiskItem]
) -> AskResponse:
    topics = detect_topics(req.question, graph)
    person = (graph.get_entity(req.person_filter) if req.person_filter else None) or detect_person(req.question, graph)
    sources, linked = retrieve(req, graph, search, topics, person)
    follow = _followups(topics, req.question)
    if not sources:
        return _insufficient(topics, person, risks, llm.model_name)

    try:
        user = build_ask_user(req.question, [(s.citation.title or s.citation.artifact_id, s.text, s.is_interview) for s in sources])
        out = llm.chat_json(ASK_SYSTEM, user, LLMAnswer)
        if out.sufficient and out.answer.strip():
            text = _validate_llm(out.answer, len(sources))
            if text:
                used = {int(n) for n in _CITE.findall(text)}
                conf = min(out.confidence, 0.95) * (1.0 if any(sources[n - 1].is_interview for n in used) else 0.85)
                return _finish(text, sources, llm.model_name, conf, [q for q in out.followups if q.strip()][:3] or follow)
        elif llm.model_name != "mock" and not out.sufficient:
            return _insufficient(topics, person, risks, llm.model_name)
    except Exception as exc:
        log.warning("ask via LLM failed (%s); using extractive fallback", type(exc).__name__)

    model = "mock" if llm.model_name == "mock" else "rules"
    ext = _extractive(req.question, sources, topics, person, linked)
    if ext is None:
        return _insufficient(topics, person, risks, model)
    text, used, best = ext
    quality = 1.0 if any(sources[n - 1].is_interview for n in used) else 0.8
    return _finish(text, sources, model, best * quality * 0.8, follow, lead="From the project records:")
