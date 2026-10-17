"""
Live capture: turn Teams messages from a consented conversation into chat Artifacts and run
them through the normal pipeline (redact -> chunk -> embed -> index -> extract -> graph).

Called only after `consent_store.is_granted(scope_id)`. Each message becomes its own Artifact
with a deterministic id, so a message seen twice (live + backfill) is indexed once. The
conversation id travels in `metadata.scope_id` so answers can later be limited to the asker's
conversations. Bodies arrive as Teams HTML and are reduced to plain text first.
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from dataclasses import dataclass
from datetime import datetime

from app.graph import get_graph
from app.ingestion.extractor import get_extractor
from app.ingestion.pipeline import index_artifacts, ingest_lock
from app.models import Artifact, ArtifactType, Person
from app.services import audit, risk_store
from app.services.interview_publish import people_from_graph
from app.services.llm import get_llm
from app.services.search import get_search

log = logging.getLogger(__name__)

MIN_CHARS = 3
_TAG = re.compile(r"<[^>]+>")
_AT = re.compile(r"<at[^>]*>.*?</at>", re.IGNORECASE | re.DOTALL)
_BREAK = re.compile(r"<\s*(br|/p|/div|/li)\s*/?>", re.IGNORECASE)


@dataclass
class LiveMessage:
    scope_id: str                 # conversation id the consent is recorded against
    scope_type: str               # channel | chat
    scope_name: str
    message_id: str
    author_aad_id: str
    author_name: str
    timestamp: datetime
    text: str                     # plain text or Teams HTML


def html_to_text(body: str) -> str:
    body = _AT.sub("", body or "")
    body = _BREAK.sub("\n", body)
    body = html.unescape(_TAG.sub("", body))
    return "\n".join(line.strip() for line in body.splitlines() if line.strip())


def person_id(aad_id: str) -> str:
    return "person_aad_" + aad_id.replace("-", "")[:12]


def to_artifact(m: LiveMessage) -> Artifact:
    text = html_to_text(m.text)
    digest = hashlib.sha1(f"{m.scope_id}|{m.message_id}".encode()).hexdigest()[:16]
    first_line = text.splitlines()[0] if text else ""
    return Artifact(
        id=f"art_teams_{digest}",
        type=ArtifactType.CHAT,
        source_id=f"teams:{m.scope_id}:{m.message_id}",
        author=person_id(m.author_aad_id),
        timestamp=m.timestamp,
        title=f"{m.scope_name or 'Teams chat'}: {first_line[:60]}",
        body=text,
        metadata={
            "source": "teams", "scope_id": m.scope_id, "scope_type": m.scope_type,
            "author_aad_id": m.author_aad_id, "live": True,
        },
    )


def capture(messages: list[LiveMessage], reason: str = "live") -> int:
    """Index new messages; returns how many artifacts were added. Blocking — call via a thread.
    `reason` (live | backfill) names the audit event: capture.message / capture.backfill."""
    graph = get_graph()
    arts = [to_artifact(m) for m in messages]
    fresh = [a for a in arts if len(a.body) >= MIN_CHARS and not graph.has_node(a.id)]
    if not fresh:
        if reason == "backfill":
            _audit(messages, [], reason)  # still worth knowing the history was read
        return 0
    people = {p.id: p for p in people_from_graph(graph)}
    for m in messages:
        pid = person_id(m.author_aad_id)
        if pid not in people:
            people[pid] = Person(id=pid, name=m.author_name or "Unknown", teams_handle=m.author_aad_id)
    with ingest_lock:
        resp, redactions = index_artifacts(
            fresh, list(people.values()),
            search=get_search(), graph=graph, extractor=get_extractor(), llm=get_llm(),
        )
        graph.save()
    risk_store.invalidate()
    log.info("live capture: %d messages -> %d chunks (%d redactions)",
             len(fresh), resp.chunks_indexed, sum(redactions.values()))
    _audit(messages, [a.id for a in fresh], reason, chunks=resp.chunks_indexed, redactions=dict(redactions))
    return len(fresh)


def _audit(messages: list[LiveMessage], new_artifact_ids: list[str], reason: str, **details) -> None:
    """One audit event per conversation: which messages were read, by whom, and what was indexed."""
    by_scope: dict[str, list[LiveMessage]] = {}
    for m in messages:
        by_scope.setdefault(m.scope_id, []).append(m)
    for scope_id, msgs in by_scope.items():
        audit.record(
            "capture.backfill" if reason == "backfill" else "capture.message",
            outcome="success" if new_artifact_ids else "skipped", source="teams", actor_name="LegacyLens",
            scope_type=msgs[0].scope_type, scope_id=scope_id, scope_name=msgs[0].scope_name or None,
            item_ids=new_artifact_ids, count=len(new_artifact_ids),
            details={
                "messages_read": len(msgs),
                "message_ids": [m.message_id for m in msgs][:50],
                "authors": sorted({m.author_name for m in msgs if m.author_name}),
                **details,
            },
        )
