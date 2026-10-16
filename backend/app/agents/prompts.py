"""
ALL LLM prompts live here (team rule). Builders return plain strings; the callers
in ingestion/ and agents/ never inline prompt text.
"""

from __future__ import annotations

from app.models import Artifact, Entity

# ---------------------------------------------------------------------------
# Ingestion: entity extraction
# ---------------------------------------------------------------------------

EXTRACTION_SYSTEM = """\
You extract knowledge-graph entities from one project artifact (email, chat, ticket, wiki page).

Entity types:
- system: a service, job, platform or component (e.g. a payment gateway adapter, a batch job)
- process: a repeatable procedure (e.g. release process, on-call rotation, an escalation procedure)
- decision: a deliberate choice between options, with or without a recorded rationale
- incident: an outage or production problem with an identifier (e.g. INC-142)

Rules:
- Only report entities the artifact actually discusses. Never invent.
- You are given KNOWN entities. If the artifact refers to one (by name, alias or abbreviation, such as
  "PG" for the payment gateway), reuse its exact known name. Only add a new entity when it is not known.
- For a decision, set decided_by to the person's name only if the artifact shows that person deciding
  or recommending; otherwise leave it null.
- Keep descriptions to one short sentence grounded in the artifact.
- Do not report people, tools used incidentally, or generic words.

Return JSON: {"entities": [{"name": str, "type": "system|process|decision|incident",
"aliases": [str], "description": str, "decided_by": str|null}]}. Return {"entities": []} if none.
"""

_MAX_ARTIFACT_CHARS = 6000


def build_extraction_user(artifact: Artifact, known: list[Entity]) -> str:
    known_lines = "\n".join(
        f"- {e.name} ({e.type})" + (f" aka {', '.join(e.aliases)}" if e.aliases else "")
        for e in known
        if e.type != "person"
    )
    body = artifact.body[:_MAX_ARTIFACT_CHARS]
    return (
        f"KNOWN ENTITIES:\n{known_lines or '(none)'}\n\n"
        f"ARTIFACT ({artifact.type}): {artifact.title}\n"
        f"{body}"
    )


# ---------------------------------------------------------------------------
# Interviews: grounded question generation
# ---------------------------------------------------------------------------

INTERVIEW_SYSTEM = """\
You are preparing a short knowledge-capture interview. A person is about to leave or rotate off a
project and holds knowledge about ONE topic that is barely documented. Your job is to write the
questions that extract what is NOT written down.

You are given numbered EVIDENCE: real tickets, chats, emails and commits about the topic.

Rules:
- Write between 3 and {max_questions} questions, each ending with a question mark.
- Every question must be GROUNDED: it quotes or points to a specific detail from the evidence
  (an incident id, a command, a config name, a stated workaround) and lists the evidence numbers it uses.
- Target tacit knowledge, one per question, using these categories:
  trigger (what tells you it is happening), first_check (what you look at first), workaround
  (what you do when the normal path fails), gotcha (what goes wrong if someone does it naively),
  escalation (who or what to involve and when), decision_criteria (why this option over the others),
  alternatives (what else was considered and why rejected).
- Do not ask about things the evidence already fully answers. Ask for the reasoning behind them,
  the exceptions, and the order of steps.
- One question, one thing. Plain language, under 60 words. Address the person as "you".
- The rationale is one sentence shown to the interviewee: say why the question matters, speak to them
  as "you", and name the source by its title or incident id. Never write "evidence [1]" or similar
  numbers in the question or the rationale (numbers belong only in the "evidence" list).
- Never invent facts or evidence numbers.

Return JSON: {{"questions": [{{"text": str, "rationale": str, "category": str, "evidence": [int]}}]}}
"""


def build_interview_user(topic: str, topic_type: str, summary: str, interviewee: str, evidence: list[tuple[str, str]]) -> str:
    """`evidence` is a list of (title, text); it is numbered from 1 in the prompt."""
    lines = [f"TOPIC: {topic} ({topic_type})", f"WHY IT IS AT RISK: {summary}", f"INTERVIEWEE: {interviewee}", "", "EVIDENCE:"]
    for i, (title, text) in enumerate(evidence, 1):
        lines.append(f"[{i}] {title}\n{text}\n")
    return "\n".join(lines)
