"""
What the LegacyLens bot says back, as plain Markdown strings (Teams renders Markdown in messages).

Kept free of the Agents SDK so every reply is testable offline: `bot/agent.py` strips the
@mention, calls `reply_for(text)` and sends the result. Commands map onto existing services
(`/ask`, `/risks`); anything that is not a command is treated as a question for the Copilot.
"""

from __future__ import annotations

import re

from app.agents.copilot import answer_question
from app.graph import get_graph
from app.models import AskRequest, AskResponse, RiskItem
from app.services import risk_store
from app.services.llm import get_llm
from app.services.search import get_search

MAX_QUESTION_CHARS = 1000
MAX_RISKS_SHOWN = 5

WELCOME = (
    "👋 Hi, I'm **LegacyLens**. I find project knowledge that only one person holds and help "
    "capture it before it's lost.\n\n"
    "- **@LegacyLens** *your question* (or **ask** *question*) — answered from project knowledge, with citations\n"
    "- **@LegacyLens risks** — the topics most at risk right now\n"
    "- **@LegacyLens topics** — what I know about (systems, processes, decisions)\n"
    "- **@LegacyLens runbook** *topic* — a cited runbook for one topic\n"
    "- **@LegacyLens status** — whether I'm learning here, and what's in my knowledge\n"
    "- **@LegacyLens assign** *@Name* — team owners: onboard a member (they learn with me 1:1, then take a 2-question assessment)\n"
    "- **start** — in your 1:1 onboarding chat: begin your assessment\n"
    "- **@LegacyLens pause** / **resume** — stop learning from this conversation / ask again\n"
    "- **@LegacyLens help** — this message"
)

#: One-word commands answered without the LLM (no typing indicator needed).
COMMANDS = frozenset({"", "help", "hi", "hello", "risks", "risk", "top risks", "topics", "topic",
                      "runbook", "ask", "pause", "stop", "resume", "status", "start", "assign"})

MAX_TOPICS_SHOWN = 15
MAX_REPLY_CHARS = 20_000

PAUSED = (
    "⏸️ Paused. I'm not learning from this conversation any more. "
    "Type **@LegacyLens resume** if you want me to ask again."
)


def reply_for(text: str) -> str:
    """Route one user message (mention already removed) to a reply."""
    return respond(text)[0]


def respond(text: str) -> tuple[str, list[str], str]:
    """(reply, cited artifact ids, reply kind) — the ids and kind feed the audit trail."""
    text = " ".join((text or "").split())
    command = text.lower().strip(" ./!?")
    if command in ("", "help", "hi", "hello"):
        return WELCOME, [], "help"
    if command in ("risks", "risk", "top risks"):
        return format_risks(risk_store.get_risks().items[:MAX_RISKS_SHOWN]), [], "risks"
    if command in ("pause", "stop"):
        return PAUSED, [], "pause"
    if command in ("topics", "topic"):
        return format_topics(), [], "topics"
    if command == "runbook":
        return "Which topic? Try **@LegacyLens runbook** *topic name*. " + _topic_hint(), [], "help"
    if command.startswith("runbook "):
        return runbook(text.split(" ", 1)[1].strip(" ./!?\"'"))
    if command == "ask":
        return "What would you like to know? Ask me anything about the project, e.g. *@LegacyLens what is the release plan?*", [], "help"
    if command.startswith("ask "):
        text = text[4:]
    resp = ask(text)
    return format_answer(resp), [c.artifact_id for c in resp.citations], "answer" if resp.citations else "no_answer"


def ask(question: str) -> AskResponse:
    req = AskRequest(question=question[:MAX_QUESTION_CHARS])
    return answer_question(req, get_graph(), get_search(), get_llm(), risk_store.get_risks().items)


_CITE = re.compile(r"\[(\d+)\]")


def format_answer(resp: AskResponse) -> str:
    """The answer only, no sources list. Every claim keeps its [n] marker (no citation, no claim);
    markers whose source has a link (SharePoint files) become clickable and open that file."""
    links = {i: c.url_or_path for i, c in enumerate(resp.citations, 1)
             if c.url_or_path and c.url_or_path.startswith(("https://", "http://"))}

    def link(m: re.Match) -> str:
        url = links.get(int(m.group(1)))
        return f"[\\[{m.group(1)}\\]]({url.replace(' ', '%20').replace(')', '%29')})" if url else m.group(0)

    lines = [_CITE.sub(link, resp.answer.strip())]
    if resp.followup_questions:
        lines.append("\n*You could also ask:* " + " · ".join(resp.followup_questions[:3]))
    return "\n".join(lines)


def _topics() -> list:
    from app.graph.queries import TOPIC_TYPES

    graph = get_graph()
    found = [e for t in TOPIC_TYPES for e in graph.entities(t)]
    return sorted(found, key=lambda e: (-len(e.artifact_ids), e.name.lower()))


def _topic_hint() -> str:
    names = [e.name for e in _topics()[:5]]
    return ("Topics I know: " + ", ".join(f"*{n}*" for n in names) + ".") if names else "I don't know any topics yet."


def format_topics() -> str:
    topics = _topics()
    if not topics:
        return "I don't know any topics yet. Add documents to the project SharePoint site and I'll learn them."
    kind = {"system": "🖥️", "process": "🔁", "decision": "⚖️"}
    lines = [f"**What I know about** ({len(topics)} topics)"]
    for e in topics[:MAX_TOPICS_SHOWN]:
        t = getattr(e.type, "value", e.type)
        lines.append(f"- {kind.get(t, '•')} **{e.name}** ({t}, {len(e.artifact_ids)} sources)")
    if len(topics) > MAX_TOPICS_SHOWN:
        lines.append(f"…and {len(topics) - MAX_TOPICS_SHOWN} more.")
    lines.append("\nTry **@LegacyLens runbook** *topic* for any of these.")
    return "\n".join(lines)


def _find_topic(name: str):
    from app.graph.queries import TOPIC_TYPES

    graph = get_graph()
    for t in TOPIC_TYPES:
        if (e := graph.find_entity(name, t)):
            return e
    wanted = name.lower()
    partial = [e for e in _topics() if wanted in e.name.lower() or any(wanted in a.lower() for a in e.aliases)]
    return partial[0] if partial else None


def runbook(topic: str) -> tuple[str, list[str], str]:
    from app.agents.generator import generate_runbook
    from app.services import audit, doc_store

    entity = _find_topic(topic)
    if entity is None:
        return f"I don't know a topic called *{topic}*. " + _topic_hint(), [], "no_answer"
    doc = generate_runbook(entity, get_graph(), get_search(), get_llm())
    if doc is None:
        return f"I don't have enough material about *{entity.name}* to write a runbook yet.", [], "no_answer"
    doc = doc_store.save(doc)
    cited = sorted({c.artifact_id for c in doc.citations})
    audit.record("artifact.generated", source="teams", scope_type="runbook", scope_id=doc.id, scope_name=entity.name,
                 item_ids=cited, details={"title": doc.title, "model": doc.model})
    body = doc.markdown.split("\n## Sources")[0]  # sources stay as linked [n] markers, not a list
    body = re.sub(r"^#{1,6}\s*(.+)$", r"**\1**", body, flags=re.MULTILINE)  # Teams has no headings
    body = format_answer(AskResponse(answer=body, citations=doc.citations))
    if len(body) > MAX_REPLY_CHARS:
        body = body[:MAX_REPLY_CHARS].rsplit("\n", 1)[0] + "\n\n…(truncated)"
    return body, cited, "runbook"


def format_risks(items: list[RiskItem]) -> str:
    """Topics only, never people: risk describes knowledge concentration, not individual performance."""
    if not items:
        return "No knowledge risks found yet. Ingest some project history first."
    badge = {"high": "🔴", "medium": "🟠", "low": "🟢"}
    lines = ["**Top knowledge risks**"]
    for i, r in enumerate(items, 1):
        lines.append(
            f"{i}. {badge.get(r.risk_level, '')} **{r.topic}** — risk {r.risk_score:.2f}, "
            f"{r.bus_factor} {'person knows' if r.bus_factor == 1 else 'people know'} it, "
            f"{r.coverage_score:.0%} documented"
        )
    return "\n".join(lines)
