"""
Artifact generator: runbooks (T22) and handover packs (T23), always with citations.

Pipeline: gather SOURCES (published interview answers first, then conversations and
tickets) -> the LLM drafts sections as items that each list the source numbers they rest on
-> we drop any item that cites nothing real, renumber the citations in order of first use,
and render markdown. With the mock LLM, or if the LLM fails, a deterministic sentence
classifier builds the same structure from the same sources, so it works offline.
No citation, no claim: uncited items never reach the document.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field

from app.agents.gap_analyzer import collect_evidence
from app.agents.prompts import RUNBOOK_SYSTEM, build_runbook_user
from app.graph.store import GraphStore
from app.models import Citation, DocType, Entity, EntityType, GeneratedDoc, RelationType
from app.services.llm import LLMService

log = logging.getLogger(__name__)

SECTIONS: tuple[tuple[str, str], ...] = (
    ("symptoms", "Symptoms and alerts"),
    ("diagnosis", "Diagnosis"),
    ("fix", "Fix steps"),
    ("verification", "Verification"),
    ("escalation", "Escalation"),
    ("gotchas", "Known gotchas"),
)
ORDERED_SECTIONS = {"diagnosis", "fix"}   # rendered as numbered lists
MAX_ITEMS_PER_SECTION = 5
SOURCE_CHARS = 900
EVIDENCE_SOURCES = 6
MAX_ITEM_CHARS = 400


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    citation: Citation
    text: str              # what the generator reads (for interviews: the answer only)
    is_interview: bool


_ANSWER = re.compile(r"Answer \([^)]*\):\s*", re.DOTALL)


def _answer_text(snippet: str) -> str:
    m = _ANSWER.search(snippet)
    return snippet[m.end():].strip() if m else snippet


def gather_sources(
    entity: Entity, graph: GraphStore, search, *, include_interviews: bool = True
) -> list[Source]:
    """Published interview answers about the topic first, then other evidence."""
    sources: list[Source] = []
    if include_interviews:
        linked = {e.target for e in graph.edges(source=entity.id, relation=RelationType.DOCUMENTED_BY)
                  if (graph.artifact_node(e.target) or {}).get("type") == "interview"}
        if linked:
            query = " ".join(dict.fromkeys([entity.name, *entity.aliases[:3]]))
            hits = search.search(query, top_k=max(20, 4 * len(linked)), artifact_types=["interview"], snippet_chars=SOURCE_CHARS)
            # several chunks may match one answer; keep the best per artifact, in artifact-id order for stability
            best = {}
            for h in hits:
                if h.artifact_id in linked and h.artifact_id not in best:
                    best[h.artifact_id] = h
            sources += [Source(h, _answer_text(h.snippet), True) for _, h in sorted(best.items())]
    for c in collect_evidence(entity, graph, search, limit=EVIDENCE_SOURCES, snippet_chars=SOURCE_CHARS):
        sources.append(Source(c, c.snippet, False))
    return sources


# ---------------------------------------------------------------------------
# Draft structure shared by the LLM and rule paths
# ---------------------------------------------------------------------------


class DraftItem(BaseModel):
    text: str
    sources: list[int] = Field(default_factory=list)   # 1-based numbers into the source list


class RunbookDraft(BaseModel):
    sections: dict[str, list[DraftItem]] = Field(default_factory=dict)
    open_questions: list[str] = Field(default_factory=list)


_SPEAKER = re.compile(r"^\[[^\]]+\]\s*[^:\n]{1,30}:\s*")
_HEADER = re.compile(
    r"^(From|To|Date|Subject|Channel|Question|Severity|Description|Resolution|Comments|Files changed):|^[A-Z]{2,5}-\d+:\s",
    re.IGNORECASE,
)
_MENTION = re.compile(r"^(?:@\w+[,:]?\s+)+")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")

# First match wins, so the most specific intent is tested first.
_CLASSIFIERS: tuple[tuple[str, re.Pattern], ...] = (
    ("escalation", re.compile(r"\b(escalat\w*|call (?:the |daniel)|page the|contact|noc\b|on-call)\b", re.I)),
    ("gotchas", re.compile(r"\b(do not|don't|never|gotcha|careful|otherwise|double pa\w*|must not|make sure|remember to)\b", re.I)),
    ("verification", re.compile(r"\b(verify|confirm\w*|back to normal|recovered|should be above|above \d+%)", re.I)),
    ("fix", re.compile(r"\b(restart\w*|drain\w*|rerun|re-run|update\w*|lower\w*|raise\w*|flush\w*|re-pin|apply\w*|kubectl)\b|\brun\b", re.I)),
    ("diagnosis", re.compile(r"\b(check(?:s|ed|ing)?|look(?:s|ing)? at|pool-status|if stale|first thing|inspect\w*)\b", re.I)),
    ("symptoms", re.compile(r"\b(timeouts?|timing out|time out|504s?|fail(?:s|ed|ing|ure)?|stuck|outage|returning|mismatch)\b", re.I)),
)


def _sentences(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        raw = raw.strip().lstrip("- ").strip()
        if not raw or _HEADER.match(raw):
            continue
        for sent in _SENTENCE.split(_MENTION.sub("", _SPEAKER.sub("", raw))):
            sent = sent.strip()
            if len(sent) >= 25 and not sent.endswith("?") and len(sent) <= MAX_ITEM_CHARS:
                out.append(sent)
    return out


def classify(sentence: str) -> Optional[str]:
    return next((name for name, pat in _CLASSIFIERS if pat.search(sentence)), None)


def rule_draft(sources: list[Source]) -> RunbookDraft:
    """Deterministic fallback: route each source sentence to the section it reads like."""
    sections: dict[str, list[DraftItem]] = {k: [] for k, _ in SECTIONS}
    seen: set[str] = set()
    for n, src in enumerate(sources, 1):  # interviews come first in `sources`
        for sent in _sentences(src.text):
            key = re.sub(r"\W+", " ", sent.lower()).strip()
            section = classify(sent)
            if section is None or key in seen or len(sections[section]) >= MAX_ITEMS_PER_SECTION:
                continue
            seen.add(key)
            sections[section].append(DraftItem(text=sent, sources=[n]))
    return RunbookDraft(sections=sections)


# ---------------------------------------------------------------------------
# Validation, rendering
# ---------------------------------------------------------------------------


def _validate(draft: RunbookDraft, n_sources: int) -> dict[str, list[DraftItem]]:
    """Keep only known sections and items that cite at least one real source."""
    out: dict[str, list[DraftItem]] = {}
    for key, _ in SECTIONS:
        kept = []
        for item in draft.sections.get(key, []):
            text = " ".join(item.text.split())
            cites = [n for n in dict.fromkeys(item.sources) if 1 <= n <= n_sources]
            if text and cites and len(text) <= MAX_ITEM_CHARS * 2:
                kept.append(DraftItem(text=text, sources=cites))
        out[key] = kept[:MAX_ITEMS_PER_SECTION]
    return out


def _ref(nums: list[int]) -> str:
    return "".join(f"[{n}]" for n in nums)


@dataclass
class CItem:
    """One statement and the artifacts that back it."""

    text: str
    cites: list[Citation]


@dataclass
class Section:
    heading: str
    items: list[CItem]
    ordered: bool = False
    cited: bool = True        # False for pointers (e.g. links to other generated documents)


def render_cited(
    title: str, banner: str, overview: str, sections: list[Section], open_questions: list[str],
) -> tuple[str, list[Citation]]:
    """Markdown where every cited item ends in [n] refs; sources are numbered by first use
    (one number per artifact) and listed at the end. Returns the markdown and citations used."""
    number: dict[str, int] = {}
    cites: list[Citation] = []
    for sec in sections:
        if not sec.cited:
            continue
        for item in sec.items:
            for c in item.cites:
                if c.artifact_id not in number:
                    number[c.artifact_id] = len(number) + 1
                    cites.append(c)

    lines = [f"# {title}", "", banner, "", "## Overview", "", overview, ""]
    for sec in sections:
        if not sec.items:
            continue
        lines += [f"## {sec.heading}", ""]
        for i, item in enumerate(sec.items, 1):
            marker = f"{i}." if sec.ordered else "-"
            refs = _ref(list(dict.fromkeys(number[c.artifact_id] for c in item.cites))) if sec.cited else ""
            lines.append(f"{marker} {item.text}" + (f" {refs}" if refs else ""))
        lines.append("")
    if open_questions:
        lines += ["## Open questions", ""] + [f"- {q}" for q in open_questions] + [""]
    lines += ["## Sources", ""]
    for i, c in enumerate(cites, 1):
        where = f" — {c.url_or_path}" if c.url_or_path else ""
        lines.append(f"{i}. {c.title or c.artifact_id} ({c.artifact_type or 'artifact'}){where}")
    return "\n".join(lines).rstrip() + "\n", cites


def render_runbook(
    title: str, topic: str, sections: dict[str, list[DraftItem]], sources: list[Source],
    open_questions: list[str], n_interview_used: int,
) -> tuple[str, list[Citation]]:
    """Runbook markdown + the citations actually used, numbered by first use."""
    used: dict[int, None] = {}
    for key, _ in SECTIONS:
        for item in sections[key]:
            used.update(dict.fromkeys(item.sources))
    n_used = len({sources[n - 1].citation.artifact_id for n in used})
    banner = (
        f"> Built from {n_used} source(s), {n_interview_used} of them published interview answers."
        if n_interview_used else
        f"> DRAFT built from {n_used} source(s). No interview has been published for this topic yet, "
        "so the steps below are not yet confirmed by the person who knows them."
    )
    overview = (
        f"Operational runbook for **{topic}**: how to recognise, diagnose, fix and verify problems, "
        "and when to escalate. Every item cites its source."
    )
    secs = [
        Section(heading, [CItem(it.text, [sources[n - 1].citation for n in it.sources]) for it in sections[key]],
                ordered=key in ORDERED_SECTIONS)
        for key, heading in SECTIONS
    ]
    return render_cited(title, banner, overview, secs, open_questions)


def _open_questions(sections: dict[str, list[DraftItem]], topic: str, has_interview: bool, extra: list[str]) -> list[str]:
    qs = [] if has_interview else [f"Run an interview with the owner of {topic} to confirm and complete this draft."]
    qs += [q.strip() for q in extra if q.strip()]
    for key, heading in SECTIONS:
        if not sections[key]:
            qs.append(f"No source describes “{heading.lower()}” for {topic}. What should a successor know?")
    return list(dict.fromkeys(qs))


# ---------------------------------------------------------------------------
# Runbook entry point
# ---------------------------------------------------------------------------


def generate_runbook(
    entity: Entity, graph: GraphStore, search, llm: LLMService, *, include_interviews: bool = True,
) -> Optional[GeneratedDoc]:
    """A cited runbook for a topic, or None if there is nothing at all to build it from."""
    sources = gather_sources(entity, graph, search, include_interviews=include_interviews)
    if not sources:
        return None

    model, draft, validated = "rules", None, None
    try:
        user = build_runbook_user(entity.name, entity.type, [(s.citation.title or s.citation.artifact_id, s.text, s.is_interview) for s in sources])
        draft = llm.chat_json(RUNBOOK_SYSTEM, user, RunbookDraft)
        validated = _validate(draft, len(sources))
        if any(validated.values()):
            model = llm.model_name
    except Exception as exc:
        log.warning("runbook generation via LLM failed (%s); using rules", type(exc).__name__)
    if model == "rules":  # mock LLM, failure, or nothing usable
        draft = rule_draft(sources)
        validated = _validate(draft, len(sources))
        model = "mock" if llm.model_name == "mock" else "rules"

    title = f"Runbook: {entity.name}"
    has_interview = any(s.is_interview for s in sources)
    interviews_used = {n for items in validated.values() for it in items for n in it.sources if sources[n - 1].is_interview}
    questions = _open_questions(validated, entity.name, has_interview, draft.open_questions if model not in ("rules", "mock") else [])
    markdown, citations = render_runbook(title, entity.name, validated, sources, questions, len(interviews_used))
    if not citations:
        return None
    return GeneratedDoc(
        type=DocType.RUNBOOK, title=title, topic=entity.name, markdown=markdown,
        citations=citations, model=model, open_questions=questions,
    )


# ---------------------------------------------------------------------------
# Handover pack (T23): assembled from the graph, risks, interviews and runbooks
# ---------------------------------------------------------------------------

OWNER_MIN_SHARE = 0.15        # same threshold as the bus factor: this much share = "knows it"
PRIMARY_SHARE = 0.5
MAX_HANDOVER_TOPICS = 5
MAX_OWNED_LISTED = 8
MAX_INCIDENTS = 8
MAX_OPEN_QUESTIONS = 10
ANSWER_CHARS = 300
_RATIONALE = re.compile(
    r"\b(because|reason\w*|criteria|rejected|trade-?offs?|accepted|recommend\w*|replay|ordering|decid\w*|chose|wins?)\b",
    re.I,
)
_EVIDENCE_ORDER = (RelationType.RESOLVED, RelationType.DECIDED, RelationType.COMMITTED_TO,
                   RelationType.PARTICIPATED_IN, RelationType.DISCUSSED)


def citation_for(graph: GraphStore, artifact_id: str, snippet: str = "") -> Optional[Citation]:
    """A citation for an artifact node (title/type/link); None if the graph doesn't know it."""
    node = graph.artifact_node(artifact_id)
    if node is None:
        return None
    ts = node.get("timestamp")
    return Citation(
        artifact_id=artifact_id, snippet=snippet or f"{node['type']}: {node['title']}", title=node["title"],
        artifact_type=node["type"], url_or_path=node.get("url_or_path"),
        timestamp=datetime.fromisoformat(ts) if ts else None,
    )


def ownership_evidence(graph: GraphStore, person_id: str, topic_id: str, limit: int = 3) -> list[Citation]:
    """Artifacts that show this person working on the topic (fixes first, then code, then talk)."""
    ids: list[str] = []
    for rel in _EVIDENCE_ORDER:
        if rel is RelationType.RESOLVED:
            for e in graph.edges(source=person_id, relation=rel):
                if graph.edges(source=e.target, target=topic_id, relation=RelationType.AFFECTS):
                    ids += e.evidence_artifact_ids
        else:
            for e in graph.edges(source=person_id, target=topic_id, relation=rel):
                ids += e.evidence_artifact_ids
    out, seen = [], set()
    for aid in ids:
        if aid in seen:
            continue
        seen.add(aid)
        if (c := citation_for(graph, aid)):
            out.append(c)
        if len(out) == limit:
            break
    return out


def _paragraphs(text: str) -> list[str]:
    """Substantial lines of an evidence snippet, without chat prefixes, headers or questions."""
    out = []
    for raw in text.splitlines():
        line = _MENTION.sub("", _SPEAKER.sub("", raw.strip().lstrip("- ").strip())).strip()
        if len(line) >= 40 and not _HEADER.match(line) and not line.endswith("?"):
            out.append(line)
    return out


def _pct(x: float) -> int:
    return round(100 * x)


def _clip(text: str, n: int = ANSWER_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def generate_handover(
    person: Entity,
    graph: GraphStore,
    search,
    risks: list,
    sessions: list,
    runbooks: list[GeneratedDoc],
    *,
    only_topics: Optional[list[str]] = None,
) -> Optional[GeneratedDoc]:
    """A cited handover pack for one person, or None if they hold no knowledge in the graph.

    `only_topics` (entity ids) narrows the risk/decision/interview sections; by default the
    pack covers the person's riskiest topics.
    """
    from app.agents.gap_analyzer import compute_topic_stats

    first = person.name.split()[0]
    stats = [s for s in compute_topic_stats(graph) if s.shares.get(person.id, 0) >= OWNER_MIN_SHARE]
    if not stats:
        return None
    stats.sort(key=lambda s: (-s.shares[person.id], s.topic_id))
    risk_by = {r.entity_id: r for r in risks}

    if only_topics:
        selected = [s for s in stats if s.topic_id in set(only_topics)]
    else:
        rank = {"high": 0, "medium": 1, "low": 2}
        ranked = sorted(stats, key=lambda s: (rank.get(getattr(risk_by.get(s.topic_id), "risk_level", "low"), 2),
                                              -getattr(risk_by.get(s.topic_id), "risk_score", 0), s.topic_id))
        selected = [s for s in ranked if getattr(risk_by.get(s.topic_id), "risk_level", "low") != "low"
                    or s.shares[person.id] >= PRIMARY_SHARE][:MAX_HANDOVER_TOPICS] or ranked[:3]
    if not selected:
        return None
    names = {e.id: e for e in graph.entities()}
    sections: list[Section] = []

    # 1. where the knowledge sits
    items = []
    for s in stats[:MAX_OWNED_LISTED]:
        r = risk_by.get(s.topic_id)
        role = "primary owner" if s.shares[person.id] >= PRIMARY_SHARE else "contributor"
        detail = f", {r.risk_level} knowledge risk, documentation coverage {_pct(r.coverage_score)}%" if r else ""
        cites = ownership_evidence(graph, person.id, s.topic_id)
        if cites:
            items.append(CItem(f"**{s.name}**: {role}, {_pct(s.shares[person.id])}% of the activity{detail}.", cites))
    sections.append(Section(f"Where {first}'s knowledge sits", items))

    # 2. incidents handled
    inc_items = []
    resolved = [e for e in graph.edges(source=person.id, relation=RelationType.RESOLVED)]
    resolved.sort(key=lambda e: (names[e.target].attributes.get("opened_at") or "", e.target))
    for e in resolved[:MAX_INCIDENTS]:
        inc = names[e.target]
        topics = [names[t].name for t in graph.neighbors(inc.id, relation=RelationType.AFFECTS, direction="out")]
        when = (inc.attributes.get("opened_at") or "")[:10]
        cites = [c for aid in e.evidence_artifact_ids if (c := citation_for(graph, aid))]
        if cites:
            inc_items.append(CItem(f"{inc.name}: {inc.description} ({when}), affected {', '.join(topics) or 'n/a'}.", cites))
    sections.append(Section("Incidents handled", inc_items))

    # 3. open risks in the selected areas
    risk_items = []
    for s in selected:
        r = risk_by.get(s.topic_id)
        if r is None or not r.evidence:
            continue
        missing = f" Still missing: {', '.join(str(m).replace('_', ' ') for m in r.missing_doc_types)}." if r.missing_doc_types else ""
        risk_items.append(CItem(f"{r.summary}{missing}", r.evidence[:2]))
    sections.append(Section("Open knowledge risks", risk_items))

    # 4. decisions they made, with rationale
    dec_items = []
    for e in graph.edges(source=person.id, relation=RelationType.DECIDED):
        if e.target not in {s.topic_id for s in selected} and only_topics:
            continue
        decision = names[e.target]
        count = 0
        for src in gather_sources(decision, graph, search):
            if not src.is_interview and src.citation.author != person.id:
                continue
            # Whole paragraphs, not sentences: "Reasons: (1) replay. (2) ordering..." makes no sense in pieces.
            for para in _paragraphs(src.text):
                if _RATIONALE.search(para) and count < 4:
                    dec_items.append(CItem(f"{decision.name}: {_clip(para, 450)}", [src.citation]))
                    count += 1
    sections.append(Section("Key decisions and their rationale", dec_items))

    # 5. what they told us (published interviews)
    told, open_qs = [], []
    mine = sorted((x for x in sessions if x.interviewee == person.id), key=lambda x: (x.created_at, x.id))
    for sess in mine:
        if sess.status == "completed":
            for a in sess.answers:
                c = a.artifact_id and citation_for(graph, a.artifact_id, snippet=_clip(a.text))
                if c:
                    told.append(CItem(f"{sess.topic}: {_clip(a.text)}", [c]))
    sections.append(Section(f"What {first} told us in interviews", told))

    # 6. who else to ask
    who = []
    people = {e.id: e for e in graph.entities(EntityType.PERSON)}
    for s in selected:
        for other, share in s.shares.items():
            if other != person.id and share >= OWNER_MIN_SHARE and other in people:
                cites = ownership_evidence(graph, other, s.topic_id, limit=1)
                if cites:
                    role = people[other].attributes.get("role") or "team member"
                    who.append(CItem(f"{s.name}: {people[other].name} ({role}, {_pct(share)}%).", cites))
    sections.append(Section("Who else to ask", who))

    # 7. pointers to generated documents (no claims, so no citations)
    pointers = []
    for s in selected:
        doc = next((d for d in runbooks if d.topic == s.name), None)
        pointers.append(CItem(f"{s.name}: " + (f"runbook “{doc.title}” (document {doc.id})" if doc else "no runbook generated yet"), []))
    sections.append(Section("Runbooks", pointers, cited=False))

    # open questions
    completed_topics = {x.topic for x in mine if x.status == "completed"}
    for s in selected:
        if not any(o != person.id and sh >= OWNER_MIN_SHARE for o, sh in s.shares.items()):
            open_qs.append(f"Nobody else holds {s.name} knowledge: choose and brief a successor.")
        if s.name not in completed_topics:
            open_qs.append(f"No interview has been published for {s.name}: run one with {person.name}.")
        doc = next((d for d in runbooks if d.topic == s.name), None)
        if doc is None:
            open_qs.append(f"There is no runbook for {s.name} yet.")
        else:
            open_qs += doc.open_questions
    for sess in mine:
        pending = sum(1 for q in sess.questions if not q.answered)
        if sess.status != "completed" and pending:
            open_qs.append(f"{pending} interview question(s) about {sess.topic} are still unanswered.")
    open_qs = list(dict.fromkeys(open_qs))[:MAX_OPEN_QUESTIONS]

    areas = ", ".join(s.name for s in selected)
    departing = person.attributes.get("is_departing")
    date = (person.attributes.get("departure_date") or "")[:10]
    markdown, citations = render_cited(
        title=f"Handover pack: {person.name}",
        banner=f"> {person.name}, {person.attributes.get('role') or 'team member'}"
               + (f", departing{' on ' + date if date else ''}" if departing else "")
               + ". Every statement below cites the artifact it comes from.",
        overview=f"What {first} knows about the areas most at risk ({areas}), where that knowledge is written "
                 "down, who else can help, and what is still missing.",
        sections=sections, open_questions=open_qs,
    )
    if not citations:
        return None
    return GeneratedDoc(
        type=DocType.HANDOVER, title=f"Handover pack: {person.name}", subject_person=person.id,
        markdown=markdown, citations=citations, model="rules", open_questions=open_qs,
    )
