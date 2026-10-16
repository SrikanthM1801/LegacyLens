"""
LegacyLens shared contracts — THE SOURCE OF TRUTH for every module.

Everything that crosses a module boundary (ingestion -> search -> agents -> API ->
frontend) is one of the models below. Owners of individual modules should import
from here rather than inventing local shapes.

TEAM RULE: do not change a field name/type in this file without telling the team.
Adding a new OPTIONAL field is safe. Renaming or removing one breaks other modules.

Reading order:
  1. Enums                  — the controlled vocabularies
  2. Core data              — Person, Artifact, Chunk  (what we ingest)
  3. Knowledge graph        — Entity, Edge             (what we extract)
  4. Evidence               — Citation                 (how we stay grounded)
  5. Analysis               — RiskItem                 (gap analyzer output)
  6. Interviews             — InterviewSession/Question/Answer
  7. Generation             — GeneratedDoc
  8. API request/response   — the HTTP surface

Graph node ids: a graph node is identified by either an `Entity.id` or an
`Artifact.id`. People live in the graph as `Entity(type=person)` whose `id` is
the same string as the corresponding `Person.id`, so `Edge.source`/`Edge.target`
can reference people, systems, incidents, decisions, processes and artifacts
with one flat id space.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


def new_id(prefix: str) -> str:
    """Short, readable, unique id — e.g. ``art_9f3c1a2b``."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(BaseModel):
    """Shared config: enums serialize as their string values, extras rejected."""

    model_config = ConfigDict(
        use_enum_values=True,
        validate_default=True,  # so enum defaults normalize to strings too
        extra="forbid",
    )


# ---------------------------------------------------------------------------
# 1. Enums
# ---------------------------------------------------------------------------


class ArtifactType(str, Enum):
    EMAIL = "email"
    CHAT = "chat"
    TICKET = "ticket"
    DOC = "doc"
    COMMIT = "commit"
    INTERVIEW = "interview"  # answers fed back in by the interviewer agent


#: Artifact types that count as documentation for `coverage_score`.
DOC_ARTIFACT_TYPES: tuple[str, ...] = (
    ArtifactType.DOC.value,
    ArtifactType.INTERVIEW.value,  # approved interview answers count as documentation
)


class EntityType(str, Enum):
    SYSTEM = "system"
    INCIDENT = "incident"
    DECISION = "decision"
    PROCESS = "process"
    PERSON = "person"


class RelationType(str, Enum):
    RESOLVED = "RESOLVED"            # person -> incident
    AUTHORED = "AUTHORED"            # person -> artifact
    AFFECTS = "AFFECTS"              # incident/decision -> system/process
    DECIDED = "DECIDED"              # person -> decision
    OWNS = "OWNS"                    # person -> system/process
    DOCUMENTED_BY = "DOCUMENTED_BY"  # entity -> artifact (doc/wiki)
    MENTIONS = "MENTIONS"            # artifact -> entity
    PARTICIPATED_IN = "PARTICIPATED_IN"  # person -> incident/decision/process/system (ticket work)
    COMMITTED_TO = "COMMITTED_TO"    # person -> system/process (code commits touching it)
    DISCUSSED = "DISCUSSED"          # person -> system/process/decision (spoke about it in chat/email)


class DocType(str, Enum):
    RUNBOOK = "runbook"
    HANDOVER = "handover"
    DECISION_LOG = "decision_log"


class InterviewStatus(str, Enum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


# ---------------------------------------------------------------------------
# 2. Core data — what we ingest
# ---------------------------------------------------------------------------


class Person(Base):
    """A team member. `id` doubles as the person's graph node id."""

    id: str = Field(default_factory=lambda: new_id("person"))
    name: str
    role: str = ""
    email: Optional[str] = None
    teams_handle: Optional[str] = None
    aliases: list[str] = Field(default_factory=list)
    is_departing: bool = False                     # drives the x1.5 risk boost
    departure_date: Optional[datetime] = None


class Artifact(Base):
    """
    One unit of captured project knowledge: an email, a chat thread, a ticket,
    a wiki page, a commit, or an interview answer we fed back in.

    `source_id` is the id in the system of origin (ticket key, commit sha,
    message id) and is what we de-duplicate on. `body` is the full plain text;
    chunking happens downstream in ingestion.
    """

    id: str = Field(default_factory=lambda: new_id("art"))
    type: ArtifactType
    source_id: str
    author: str                                    # Person.id
    participants: list[str] = Field(default_factory=list)  # Person.ids, excl. author
    timestamp: datetime
    title: str
    body: str
    url_or_path: Optional[str] = None              # for citations
    tags: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)  # type-specific extras


class Chunk(Base):
    """
    A retrievable slice of an Artifact. One search-index document == one Chunk.
    `embedding` is populated by services/llm.py and may be None before embedding.
    """

    id: str = Field(default_factory=lambda: new_id("chunk"))
    artifact_id: str
    artifact_type: ArtifactType
    ordinal: int = 0                               # position within the artifact
    text: str
    title: str = ""
    author: str = ""                               # denormalized for filtering
    timestamp: Optional[datetime] = None
    url_or_path: Optional[str] = None
    embedding: Optional[list[float]] = None


# ---------------------------------------------------------------------------
# 3. Knowledge graph
# ---------------------------------------------------------------------------


class Entity(Base):
    """A node in the knowledge graph: a system, incident, decision, process or person."""

    id: str = Field(default_factory=lambda: new_id("ent"))
    type: EntityType
    name: str
    aliases: list[str] = Field(default_factory=list)
    description: str = ""
    artifact_ids: list[str] = Field(default_factory=list)  # where we saw it
    attributes: dict[str, Any] = Field(default_factory=dict)


class Edge(Base):
    """
    A directed, weighted relation between two graph nodes (see module docstring
    on the id space). `evidence_artifact_ids` is what makes the edge citable.
    """

    source: str
    target: str
    relation: RelationType
    weight: float = 1.0
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)


class GraphStats(Base):
    """Cheap summary of the graph, for /health and the frontend."""

    node_count: int = 0
    edge_count: int = 0
    nodes_by_type: dict[str, int] = Field(default_factory=dict)
    edges_by_relation: dict[str, int] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# 4. Evidence
# ---------------------------------------------------------------------------


class Citation(Base):
    """
    A pointer back to the artifact that justifies a claim. Every user-facing
    statement LegacyLens makes (risk, question, generated doc, answer) carries
    these — no citation, no claim.
    """

    artifact_id: str
    snippet: str
    url_or_path: Optional[str] = None
    title: str = ""
    artifact_type: Optional[ArtifactType] = None
    author: Optional[str] = None
    timestamp: Optional[datetime] = None
    score: Optional[float] = None                  # retrieval score, if any


# ---------------------------------------------------------------------------
# 5. Analysis — gap analyzer output
# ---------------------------------------------------------------------------


class RiskItem(Base):
    """
    One knowledge-risk finding for a topic.

    Scores are all 0..1 except `bus_factor` (a count) and `risk_score`:
      activity_score  — how much the project does with this topic lately
      coverage_score  — how well it is documented (doc/wiki hits only)
      risk_score      = activity x (1 / max(bus_factor, 1)) x (1 - coverage_score)
    """

    id: str = Field(default_factory=lambda: new_id("risk"))
    topic: str
    entity_id: Optional[str] = None                # graph entity, when known
    entity_type: Optional[EntityType] = None
    summary: str = ""
    owner_candidates: list[str] = Field(default_factory=list)  # Person.ids, best first
    bus_factor: int = 1
    coverage_score: float = 0.0
    activity_score: float = 0.0
    risk_score: float = 0.0
    risk_level: Literal["high", "medium", "low"] = "low"  # high>=0.6, medium>=0.3
    missing_doc_types: list[DocType] = Field(default_factory=list)
    evidence: list[Citation] = Field(default_factory=list)
    computed_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 6. Interviews
# ---------------------------------------------------------------------------


class InterviewQuestion(Base):
    """A question grounded in evidence — `evidence` is quoted back to the human."""

    id: str = Field(default_factory=lambda: new_id("q"))
    text: str
    rationale: str = ""                            # why we are asking
    evidence: list[Citation] = Field(default_factory=list)
    target_entity_id: Optional[str] = None
    answered: bool = False


class InterviewAnswer(Base):
    """A human reply. Ingested back in as an Artifact(type=interview)."""

    id: str = Field(default_factory=lambda: new_id("ans"))
    question_id: str
    session_id: str
    text: str
    answered_by: str                               # Person.id
    answered_at: datetime = Field(default_factory=utcnow)
    artifact_id: Optional[str] = None              # set once re-ingested


class InterviewSession(Base):
    """A short, targeted interview with the person who holds the knowledge."""

    id: str = Field(default_factory=lambda: new_id("sess"))
    risk_id: str
    topic: str
    interviewee: str                               # Person.id
    status: InterviewStatus = InterviewStatus.PENDING
    questions: list[InterviewQuestion] = Field(default_factory=list)
    answers: list[InterviewAnswer] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utcnow)
    completed_at: Optional[datetime] = None


# ---------------------------------------------------------------------------
# 7. Generation
# ---------------------------------------------------------------------------


class GeneratedDoc(Base):
    """A runbook / handover pack / decision log, in markdown, with citations."""

    id: str = Field(default_factory=lambda: new_id("doc"))
    type: DocType
    title: str
    topic: Optional[str] = None                    # for runbooks & decision logs
    subject_person: Optional[str] = None           # Person.id, for handovers
    markdown: str
    citations: list[Citation] = Field(default_factory=list)
    generated_at: datetime = Field(default_factory=utcnow)
    model: str = "mock"                            # which LLM produced it
    open_questions: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# 8. API request / response
# ---------------------------------------------------------------------------


class IngestRequest(Base):
    """POST /ingest — re-run ingestion. Omit everything for the default sample run."""

    source_dir: Optional[str] = None               # defaults to settings.data_dir
    reset: bool = True                             # wipe index + graph first


class IngestResponse(Base):
    artifacts_ingested: int
    chunks_indexed: int
    entities_extracted: int
    edges_created: int
    graph: GraphStats = Field(default_factory=GraphStats)
    duration_seconds: float = 0.0
    warnings: list[str] = Field(default_factory=list)
    redactions: dict[str, int] = Field(default_factory=dict)  # PII redacted, by kind


class StartInterviewRequest(Base):
    """POST /interviews/{risk_id}/start"""

    interviewee: Optional[str] = None              # override the suggested person
    max_questions: int = Field(default=4, ge=1, le=10)


class AnswerRequest(Base):
    """POST /interviews/{session_id}/answer"""

    question_id: str
    text: str
    answered_by: Optional[str] = None              # defaults to session.interviewee


class AnswerResponse(Base):
    session: InterviewSession
    stored_answer: InterviewAnswer
    artifact_id: str                               # the new Artifact(type=interview)
    chunks_indexed: int = 0


class RunbookRequest(Base):
    """POST /artifacts/runbook"""

    topic: str
    risk_id: Optional[str] = None
    include_interviews: bool = True


class HandoverRequest(Base):
    """POST /artifacts/handover"""

    person_id: str
    topics: Optional[list[str]] = None             # defaults to their top risk topics


class AskRequest(Base):
    """POST /ask"""

    question: str
    top_k: int = Field(default=6, ge=1, le=20)
    artifact_types: Optional[list[ArtifactType]] = None
    person_filter: Optional[str] = None            # Person.id


class AskResponse(Base):
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    confidence: float = 0.0                        # 0..1, heuristic in mock mode
    followup_questions: list[str] = Field(default_factory=list)
    model: str = "mock"


class GraphResponse(Base):
    """GET /graph: nodes are Entity dumps (kind='entity') or artifact stubs (kind='artifact')."""

    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    stats: GraphStats = Field(default_factory=GraphStats)


class PersonTopic(Base):
    entity_id: str
    name: str
    type: EntityType
    activity: float                                # weighted interactions with the topic
    share: float                                   # 0..1, this person's share of the topic's activity


class PersonSummary(Base):
    """Knowledge concentration for one person. Describes topics, never performance."""

    id: str
    name: str
    role: str = ""
    is_departing: bool = False
    topics: list[PersonTopic] = Field(default_factory=list)
    concentrated_topics: list[str] = Field(default_factory=list)  # topic names where share >= 0.7


class RiskListResponse(Base):
    items: list[RiskItem] = Field(default_factory=list)
    total: int = 0
    computed_at: datetime = Field(default_factory=utcnow)


class HealthResponse(Base):
    status: Literal["ok"] = "ok"
    use_mocks: bool
    version: str
    artifacts_indexed: int = 0
    graph: GraphStats = Field(default_factory=GraphStats)
