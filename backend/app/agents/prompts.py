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
