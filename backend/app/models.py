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
    follow_up_of: Optional[str] = None             # id of the question this one digs deeper into


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
    force_new: bool = False                        # start a fresh session even if one is still open


class AnswerRequest(Base):
    """POST /interviews/{session_id}/answer"""

    question_id: str
    text: str
    answered_by: Optional[str] = None              # defaults to session.interviewee


class AnswerResponse(Base):
    session: InterviewSession
    stored_answer: InterviewAnswer
    artifact_id: str                               # id the Artifact(type=interview) will have once published
    chunks_indexed: int = 0                        # 0 until the interviewee approves (see T21)
    follow_up: Optional[InterviewQuestion] = None  # set when the answer was vague


class ApproveResponse(Base):
    """POST /interviews/{id}/approve: what was published and how the risk moved."""

    session: InterviewSession
    risk_before: Optional[RiskItem] = None
    risk_after: Optional[RiskItem] = None
    artifacts_published: int = 0
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


# ---------------------------------------------------------------------------
# 9. Live Microsoft 365: consent and project roster (Plan §11.6, §15)
# ---------------------------------------------------------------------------


class ConsentRecord(Base):
    """
    Whether LegacyLens may learn from one conversation (or mailbox / site).
    Nothing from a scope is stored unless its record is `granted`.
    """

    scope_id: str                                  # Teams conversation id, mailbox, site id
    scope_type: Literal["channel", "chat", "mailbox", "site"]
    display_name: str = ""
    status: Literal["pending", "granted", "declined", "paused"] = "pending"
    team_id: Optional[str] = None                  # Graph group id, for channels
    channel_id: Optional[str] = None               # Graph channel id, for channels
    project_id: Optional[str] = None               # ProjectTeam.team_id this chat was matched to (dashboard roll-up)
    auto_installed: bool = False                   # we installed the app after detecting a project chat
    granted_by: Optional[str] = None               # display name of who clicked Allow / Not now
    granted_by_aad_id: Optional[str] = None
    updated_at: datetime = Field(default_factory=utcnow)
    tenant_id: Optional[str] = None


class ProjectTeam(Base):
    """The Teams team LegacyLens is installed in; its members define 'the project'."""

    team_id: str                                   # Bot Framework team id (19:...@thread.tacv2)
    aad_group_id: Optional[str] = None             # Graph /teams/{id}
    name: str = ""
    member_aad_ids: list[str] = Field(default_factory=list)
    updated_at: datetime = Field(default_factory=utcnow)


# ---------------------------------------------------------------------------
# 10. Audit trail (admin page): what LegacyLens read, learned, asked and did
# ---------------------------------------------------------------------------


class AuditEvent(Base):
    """
    One audit record. Ids, names and counts only: never message text, file contents or
    question text, so the audit trail itself holds no project knowledge.
    `action` is "<category>.<verb>", e.g. consent.granted, capture.message, detection.chat_invited.
    """

    id: str = Field(default_factory=lambda: new_id("audit"))
    timestamp: datetime = Field(default_factory=utcnow)
    action: str
    outcome: Literal["success", "failure", "skipped"] = "success"
    source: Literal["teams", "graph", "sharepoint", "mailbox", "api", "system"] = "system"
    actor_name: Optional[str] = None               # who did it (person) or "LegacyLens"
    actor_aad_id: Optional[str] = None
    scope_type: Optional[str] = None               # channel | chat | mailbox | site | topic | interview
    scope_id: Optional[str] = None
    scope_name: Optional[str] = None
    count: Optional[int] = None                    # messages / files / chunks affected
    item_ids: list[str] = Field(default_factory=list)  # message, file or artifact ids (capped)
    details: dict[str, Any] = Field(default_factory=dict)

    @property
    def category(self) -> str:
        return self.action.split(".", 1)[0]


class AuditListResponse(Base):
    items: list[AuditEvent] = Field(default_factory=list)
    total: int = 0
    actions: list[str] = Field(default_factory=list)  # distinct actions in the window, for filters


# ---------------------------------------------------------------------------
# 11. Dashboard: org -> account -> project, aggregated from the audit trail
# ---------------------------------------------------------------------------


class HierarchyNode(Base):
    """A node of org -> account -> project. Conversations (channels, group chats) belong to a project."""

    level: Literal["org", "account", "project"]
    id: str
    name: str
    conversations: int = 0                         # consent-tracked conversations at or below this node
    children: list["HierarchyNode"] = Field(default_factory=list)


class DashboardKpis(Base):
    conversations_total: int = 0
    conversations_granted: int = 0
    conversations_pending: int = 0
    conversations_declined: int = 0
    conversations_paused: int = 0
    messages_read: int = 0
    messages_captured: int = 0
    duplicates_skipped: int = 0
    chunks_indexed: int = 0
    questions_answered: int = 0
    questions_unanswered: int = 0
    questions_failed: int = 0
    answer_rate: Optional[float] = None            # answered / (answered + unanswered)
    avg_confidence: Optional[float] = None         # API questions only; the bot does not log it
    avg_citations: Optional[float] = None
    commands: int = 0                              # help / risks / pause replies
    interviews_started: int = 0
    interviews_answered: int = 0
    interviews_approved: int = 0
    documents_generated: int = 0
    chats_detected: int = 0
    failures: int = 0
    security_events: int = 0


class DailyActivity(Base):
    date: str                                      # YYYY-MM-DD (UTC)
    messages_captured: int = 0
    questions: int = 0
    consent_decisions: int = 0
    failures: int = 0


class CategoryCount(Base):
    category: str
    count: int
    failures: int = 0


class ConversationActivity(Base):
    scope_id: str
    name: str
    type: str
    status: str
    project: str
    messages_captured: int = 0
    questions: int = 0
    last_activity: Optional[datetime] = None


class RiskImpact(Base):
    """What one approved interview did to a risk score (org-level: the knowledge base is shared)."""

    topic: str
    approved_at: datetime
    risk_before: float
    risk_after: float


class DashboardEvent(Base):
    """An audit event with people removed: a role instead of a name, no authors, no message ids."""

    id: str
    timestamp: datetime
    action: str
    outcome: str
    source: str
    actor_role: Literal["member", "LegacyLens", "system"] = "system"
    scope_type: Optional[str] = None
    scope_name: Optional[str] = None
    count: Optional[int] = None
    details: dict[str, Any] = Field(default_factory=dict)


class GovernanceItem(Base):
    """One thing LegacyLens keeps: what, where, whether it can contain project content, for how long."""

    what: str
    where: str
    contains_content: bool
    retention: str


class DashboardSummary(Base):
    #: Where the numbers come from. "azure" = the audit blobs and consent tables; "memory" = events recorded
    #: by this process only (offline); "demo" = generated demo data (offline only). Never mixed.
    data_source: Literal["azure", "memory", "demo"] = "memory"
    scope: HierarchyNode                           # the selected node (children = drill-down)
    since: datetime
    until: datetime
    generated_at: datetime = Field(default_factory=utcnow)
    kpis: DashboardKpis = Field(default_factory=DashboardKpis)
    redactions: dict[str, int] = Field(default_factory=dict)
    consent_funnel: dict[str, Any] = Field(default_factory=dict)
    activity: list[DailyActivity] = Field(default_factory=list)
    by_category: list[CategoryCount] = Field(default_factory=list)
    conversations: list[ConversationActivity] = Field(default_factory=list)
    risk_impact: list[RiskImpact] = Field(default_factory=list)
    recent_failures: list[DashboardEvent] = Field(default_factory=list)
    governance: list[GovernanceItem] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class DashboardEventsResponse(Base):
    items: list[DashboardEvent] = Field(default_factory=list)
    total: int = 0
    actions: list[str] = Field(default_factory=list)


class ConsentRegisterRow(Base):
    """One conversation's consent: the register an admin shows to prove nothing is read without 'Allow'."""

    scope_id: str
    name: str
    type: str
    status: str
    approved_by: Optional[str] = None
    updated_at: datetime
    project: str
    account: str
    auto_installed: bool = False
