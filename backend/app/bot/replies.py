"""
What the LegacyLens bot says back, as plain Markdown strings (Teams renders Markdown in messages).

Kept free of the Agents SDK so every reply is testable offline: `bot/agent.py` strips the
@mention, calls `reply_for(text)` and sends the result. Commands map onto existing services
(`/ask`, `/risks`); anything that is not a command is treated as a question for the Copilot.
"""

from __future__ import annotations

from app.agents.copilot import answer_question
from app.graph import get_graph
from app.models import AskRequest, AskResponse, RiskItem
from app.services import risk_store
from app.services.llm import get_llm
from app.services.search import get_search

MAX_QUESTION_CHARS = 1000
MAX_RISKS_SHOWN = 5
MAX_CITATIONS_SHOWN = 5
SNIPPET_CHARS = 160

WELCOME = (
    "👋 Hi, I'm **LegacyLens**. I find project knowledge that only one person holds and help "
    "capture it before it's lost.\n\n"
    "- **@LegacyLens** *your question* — answered from project history, with citations\n"
    "- **@LegacyLens risks** — the topics most at risk right now\n"
    "- **@LegacyLens pause** — stop me learning from this conversation (**resume** to ask again)\n"
    "- **@LegacyLens help** — this message"
)

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
    if command == "ask":
        return "What would you like to know? Try: *@LegacyLens how do we recover from a payment gateway outage?*", [], "help"
    if command.startswith("ask "):
        text = text[4:]
    resp = ask(text)
    return format_answer(resp), [c.artifact_id for c in resp.citations], "answer" if resp.citations else "no_answer"


def ask(question: str) -> AskResponse:
    req = AskRequest(question=question[:MAX_QUESTION_CHARS])
    return answer_question(req, get_graph(), get_search(), get_llm(), risk_store.get_risks().items)


def format_answer(resp: AskResponse) -> str:
    lines = [resp.answer.strip()]
    if resp.citations:
        lines.append("\n**Sources**")
        for i, c in enumerate(resp.citations[:MAX_CITATIONS_SHOWN], 1):
            label = c.title or c.artifact_id
            meta = ", ".join(x for x in (c.author, c.timestamp.date().isoformat() if c.timestamp else None) if x)
            snippet = " ".join(c.snippet.split())
            if len(snippet) > SNIPPET_CHARS:
                snippet = snippet[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"
            lines.append(f"{i}. **{label}**" + (f" ({meta})" if meta else "") + (f" — “{snippet}”" if snippet else ""))
    if resp.followup_questions:
        lines.append("\n*You could also ask:* " + " · ".join(resp.followup_questions[:3]))
    return "\n".join(lines)


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
