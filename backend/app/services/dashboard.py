"""
Dashboard aggregation: org -> account -> project, computed from the audit trail and the consent register.

Everything here is a pure function over lists (events, consent records, project teams), so it is easy
to test and independent of where those lists come from (Azure blobs and tables, or memory offline).

Hierarchy
  * A *project* is a Teams team LegacyLens is installed in (ProjectTeam). Channels belong to the team
    they live in; auto-detected group chats belong to the project whose roster they overlap most
    (ConsentRecord.project_id). Conversations that match no project sit under "Unassigned conversations".
  * An *account* is the client account a project belongs to, from PROJECT_ACCOUNTS (project name or
    team id -> account). Unmapped projects fall under the default account.
  * The *org* is everything.

Attribution
  Events carry a conversation scope id, which maps to a project. Events with no conversation scope
  (interviews, generated documents, API questions, ingests, system events) belong to the shared
  knowledge base, which the backend keeps as one graph today, so they roll up to the org view only.

Source
  Only the audit trail and the consent register feed these numbers. Nothing here is read from the
  knowledge base, the sample dataset or any mock, so what you see is what was actually recorded.

Privacy
  Summaries are counts. The event view replaces names with a role and drops authors, member names and
  message ids. Only the consent register names the person who clicked Allow, because that is the
  accountability record.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.config import Settings
from app.models import (
    AuditEvent, CategoryCount, ConsentRecord, ConsentRegisterRow, ConversationActivity, DailyActivity,
    DashboardEvent, DashboardKpis, DashboardSummary, GovernanceItem, HierarchyNode, ProjectTeam, RiskImpact,
)

ORG_ID = "org"
UNASSIGNED_PROJECT_ID = "prj_unassigned"
UNASSIGNED_PROJECT_NAME = "Unassigned conversations"
TOP_CONVERSATIONS = 10
RECENT_FAILURES = 10
MAX_EVENTS = 1000

#: Detail keys that identify people or individual messages; never shown on the dashboard.
HIDDEN_DETAIL_KEYS = frozenset({"authors", "member_names", "message_ids", "question_id"})

GOVERNANCE: tuple[GovernanceItem, ...] = (
    GovernanceItem(
        what="Message text from conversations that clicked Allow (phone numbers, card numbers and external emails redacted first)",
        where="Azure AI Search index and the knowledge graph",
        contains_content=True,
        retention="Until purged. The original stays in Teams; nothing is read from declined, paused or pending conversations.",
    ),
    GovernanceItem(
        what="Interview answers and generated runbooks / handover packs",
        where="Knowledge base (search index, graph, generated documents)",
        contains_content=True,
        retention="Published only after the interviewee reviews and approves; every statement cites its source.",
    ),
    GovernanceItem(
        what="Consent decisions: which conversation, who allowed it, when",
        where="Azure Table Storage: ConsentRecords",
        contains_content=False,
        retention="Kept as the consent register.",
    ),
    GovernanceItem(
        what="Project roster: member ids of each Teams team",
        where="Azure Table Storage: SyncState",
        contains_content=False,
        retention="Refreshed whenever the team changes.",
    ),
    GovernanceItem(
        what="Audit trail: what LegacyLens read, answered and published (ids, names and counts only)",
        where="Azure Blob Storage: audit/events/YYYY/MM/DD/HH.jsonl (append-only)",
        contains_content=False,
        retention="Append-only; never holds message text, file contents or questions.",
    ),
)


# ---------------------------------------------------------------------------
# Hierarchy
# ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def project_node_id(team_id: str) -> str:
    return "prj_" + hashlib.sha1(team_id.encode()).hexdigest()[:10]


def account_node_id(name: str) -> str:
    return "acc_" + _slug(name)


@dataclass
class Org:
    root: HierarchyNode
    scope_project: dict[str, str] = field(default_factory=dict)    # conversation / team scope id -> project node id
    project_account: dict[str, str] = field(default_factory=dict)  # project node id -> account node id
    names: dict[str, str] = field(default_factory=dict)            # project / account node id -> display name

    def node(self, level: str, node_id: str) -> Optional[HierarchyNode]:
        if level == "org":
            return self.root if node_id == ORG_ID else None
        for acc in self.root.children:
            if level == "account" and acc.id == node_id:
                return acc
            for prj in acc.children:
                if level == "project" and prj.id == node_id:
                    return prj
        return None

    def projects_under(self, level: str, node_id: str) -> Optional[set[str]]:
        """Project node ids in scope; None means the whole org (including events with no conversation)."""
        if level == "org":
            return None
        if level == "project":
            return {node_id}
        return {p for p, a in self.project_account.items() if a == node_id}

    def project_of(self, scope_id: Optional[str]) -> Optional[str]:
        return self.scope_project.get(scope_id) if scope_id else None

    def account_name_of_project(self, project_id: str) -> str:
        return self.names.get(self.project_account.get(project_id, ""), "")


_TEAMS_ID = re.compile(r"^19:.+@thread\.(tacv2|v2|skype)$")


def conversation_name(rec: ConsentRecord, project_name: Optional[str] = None) -> str:
    """A readable name. Teams often gives group chats and channels no name, which would otherwise show as a raw
    id like 19:886f…@thread.v2."""
    name = (rec.display_name or "").strip()
    if name and not _TEAMS_ID.match(name):
        return name
    if rec.scope_type == "channel" and project_name:
        return f"{project_name} (channel)"
    tail = re.sub(r"[^A-Za-z0-9]", "", rec.scope_id.split("@")[0])[-6:]  # the unique part; every id ends @thread.*
    return f"Unnamed {'channel' if rec.scope_type == 'channel' else 'group chat'} …{tail}"


def _record_project(rec: ConsentRecord, projects: list[ProjectTeam]) -> Optional[ProjectTeam]:
    if rec.scope_type == "channel":
        for p in projects:
            if rec.team_id and rec.team_id == p.aad_group_id:
                return p
        for p in projects:
            if p.team_id == rec.scope_id:
                return p
    if rec.project_id:
        return next((p for p in projects if p.team_id == rec.project_id), None)
    if rec.scope_type == "chat" and rec.auto_installed and len(projects) == 1:
        # Detection only joins a chat with >= 2 members of a project, so with one project it can only be that one
        # (this also covers chats detected before project_id was recorded).
        return projects[0]
    return None


def build_org(
    records: Iterable[ConsentRecord], projects: Iterable[ProjectTeam], settings: Settings,
    mapping: Optional[dict[str, str]] = None,
) -> Org:
    records, projects = list(records), sorted(projects, key=lambda p: (p.name.lower(), p.team_id))
    mapping = settings.account_by_project if mapping is None else mapping

    by_project: dict[str, list[ConsentRecord]] = defaultdict(list)
    unassigned: list[ConsentRecord] = []
    scope_project: dict[str, str] = {}
    for p in projects:
        scope_project[p.team_id] = project_node_id(p.team_id)  # roster events use the team id as scope
    for rec in records:
        p = _record_project(rec, projects)
        if p is None:
            unassigned.append(rec)
        else:
            by_project[p.team_id].append(rec)
            scope_project[rec.scope_id] = project_node_id(p.team_id)
    for rec in unassigned:
        scope_project[rec.scope_id] = UNASSIGNED_PROJECT_ID

    names: dict[str, str] = {}
    project_account: dict[str, str] = {}
    accounts: dict[str, list[HierarchyNode]] = defaultdict(list)

    def add_project(pid: str, name: str, account: str, convs: int) -> None:
        aid = account_node_id(account)
        names[pid], names[aid] = name, account
        project_account[pid] = aid
        accounts[aid].append(HierarchyNode(level="project", id=pid, name=name, conversations=convs))

    for p in projects:
        account = mapping.get(p.team_id.lower()) or mapping.get(p.name.lower()) or settings.default_account_name
        add_project(project_node_id(p.team_id), p.name or "Unnamed project", account, len(by_project[p.team_id]))
    if unassigned:
        add_project(UNASSIGNED_PROJECT_ID, UNASSIGNED_PROJECT_NAME, settings.default_account_name, len(unassigned))

    account_nodes = [
        HierarchyNode(level="account", id=aid, name=names[aid], children=sorted(kids, key=lambda n: n.name.lower()),
                      conversations=sum(k.conversations for k in kids))
        for aid, kids in accounts.items()
    ]
    account_nodes.sort(key=lambda n: n.name.lower())
    root = HierarchyNode(level="org", id=ORG_ID, name=settings.org_name, children=account_nodes,
                         conversations=sum(a.conversations for a in account_nodes))
    return Org(root=root, scope_project=scope_project, project_account=project_account, names=names)


# ---------------------------------------------------------------------------
# Event helpers
# ---------------------------------------------------------------------------


def _int(x: Any) -> int:
    return int(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else 0


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def public_event(e: AuditEvent) -> DashboardEvent:
    """An audit event without people: a role instead of a name, no authors / member names / message ids."""
    if e.actor_name == "LegacyLens":
        role = "LegacyLens"
    elif e.actor_name or e.actor_aad_id:
        role = "member"
    else:
        role = "system"
    scope_name = e.scope_name
    details = {k: v for k, v in e.details.items() if k not in HIDDEN_DETAIL_KEYS}
    if e.action == "artifact.generated" and e.scope_type == "handover":
        # A handover pack is about a named person; saying who is handing over is not for a dashboard.
        scope_name = "a team member"
        details.pop("title", None)
    return DashboardEvent(
        id=e.id, timestamp=e.timestamp, action=e.action, outcome=e.outcome, source=e.source, actor_role=role,
        scope_type=e.scope_type, scope_name=scope_name, count=e.count, details=details,
    )


def in_scope(org: Org, level: str, node_id: str, scope_id: Optional[str]) -> bool:
    allowed = org.projects_under(level, node_id)
    return True if allowed is None else org.project_of(scope_id) in allowed


def filter_events(
    org: Org, level: str, node_id: str, events: Iterable[AuditEvent], since: datetime, until: datetime,
) -> list[AuditEvent]:
    since, until = _utc(since), _utc(until)
    return [e for e in events if since <= _utc(e.timestamp) <= until and in_scope(org, level, node_id, e.scope_id)]


def _question_kind(e: AuditEvent) -> Optional[str]:
    """answered | unanswered | failed | command, for ask.answered events; None for anything else."""
    if e.action != "ask.answered":
        return None
    if e.outcome == "failure":
        return "failed"
    reply = e.details.get("reply")
    if reply == "answer":
        return "answered"
    if reply == "no_answer":
        return "unanswered"
    return "command"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def summarize(
    org: Org, level: str, node_id: str, events: Iterable[AuditEvent], records: Iterable[ConsentRecord],
    since: datetime, until: datetime, *, source: str = "memory",
) -> DashboardSummary:
    node = org.node(level, node_id)
    if node is None:
        raise KeyError(f"unknown {level} '{node_id}'")
    since, until = _utc(since), _utc(until)
    allowed = org.projects_under(level, node_id)
    evs = filter_events(org, level, node_id, events, since, until)
    recs = [r for r in records if allowed is None or org.project_of(r.scope_id) in allowed]

    k = DashboardKpis(conversations_total=len(recs))
    for r in recs:
        setattr(k, f"conversations_{r.status}", getattr(k, f"conversations_{r.status}") + 1)

    redactions: Counter = Counter()
    funnel: Counter = Counter()
    by_cat: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    daily: dict[str, DailyActivity] = {}
    day = since.date()
    while day <= until.date():
        daily[day.isoformat()] = DailyActivity(date=day.isoformat())
        day += timedelta(days=1)
    per_conv: dict[str, dict[str, Any]] = defaultdict(lambda: {"captured": 0, "questions": 0, "last": None})
    confidences: list[float] = []
    citations: list[int] = []
    impacts: list[RiskImpact] = []

    for e in sorted(evs, key=lambda e: e.timestamp):
        d = daily.get(_utc(e.timestamp).date().isoformat())
        failed = e.outcome == "failure"
        cat = by_cat[e.category]
        cat[0] += 1
        cat[1] += failed
        k.failures += failed
        if d is not None:
            d.failures += failed
        if e.action.startswith("security."):
            k.security_events += 1

        conv = per_conv[e.scope_id] if e.scope_id and org.project_of(e.scope_id) else None
        if conv is not None:
            conv["last"] = _utc(e.timestamp)

        if e.action in ("capture.message", "capture.backfill"):
            captured = _int(e.count) if e.outcome == "success" else 0
            k.messages_captured += captured
            k.messages_read += _int(e.details.get("messages_read"))
            k.chunks_indexed += _int(e.details.get("chunks"))
            if d is not None:
                d.messages_captured += captured
            if conv is not None:
                conv["captured"] += captured
            for kind, n in (e.details.get("redactions") or {}).items():
                redactions[kind] += _int(n)
        elif e.action == "ingest.completed":
            k.chunks_indexed += _int(e.details.get("chunks"))
            for kind, n in (e.details.get("redactions") or {}).items():
                redactions[kind] += _int(n)
        elif e.action.startswith("consent."):
            verb = e.action.split(".", 1)[1]
            funnel[verb] += 1
            if verb in ("granted", "declined", "paused") and d is not None:
                d.consent_decisions += 1
        elif e.action == "detection.chat_invited":
            k.chats_detected += 1
        elif e.action == "interview.started":
            k.interviews_started += 1
        elif e.action == "interview.answered":
            k.interviews_answered += 1
        elif e.action == "interview.approved":
            k.interviews_approved += 1
            before, after = e.details.get("risk_before"), e.details.get("risk_after")
            if isinstance(before, (int, float)) and isinstance(after, (int, float)):
                impacts.append(RiskImpact(topic=e.scope_name or "Unknown topic", approved_at=_utc(e.timestamp),
                                          risk_before=float(before), risk_after=float(after)))
        elif e.action == "artifact.generated":
            k.documents_generated += 1
        else:
            kind = _question_kind(e)
            if kind:
                if kind == "command":
                    k.commands += 1
                elif kind == "failed":
                    k.questions_failed += 1
                else:
                    if kind == "answered":
                        k.questions_answered += 1
                        citations.append(len(e.item_ids))
                    else:
                        k.questions_unanswered += 1
                    if d is not None:
                        d.questions += 1
                    if conv is not None:
                        conv["questions"] += 1
                    c = e.details.get("confidence")
                    if isinstance(c, (int, float)):
                        confidences.append(float(c))

    k.duplicates_skipped = max(0, k.messages_read - k.messages_captured)
    asked = k.questions_answered + k.questions_unanswered
    k.answer_rate = round(k.questions_answered / asked, 3) if asked else None
    k.avg_confidence = round(sum(confidences) / len(confidences), 2) if confidences else None
    k.avg_citations = round(sum(citations) / len(citations), 2) if citations else None

    rec_by_scope = {r.scope_id: r for r in recs}
    conversations = []
    for scope_id, c in per_conv.items():
        rec = rec_by_scope.get(scope_id)
        if rec is None or not (c["captured"] or c["questions"]):
            continue
        pid = org.project_of(scope_id) or UNASSIGNED_PROJECT_ID
        conversations.append(ConversationActivity(
            scope_id=scope_id, name=conversation_name(rec, org.names.get(pid)), type=rec.scope_type, status=rec.status,
            project=org.names.get(pid, UNASSIGNED_PROJECT_NAME), messages_captured=c["captured"],
            questions=c["questions"], last_activity=c["last"],
        ))
    conversations.sort(key=lambda c: (-(c.messages_captured + c.questions), c.name))

    failures = [public_event(e) for e in sorted(evs, key=lambda e: e.timestamp, reverse=True) if e.outcome == "failure"]

    notes: list[str] = []
    if source == "demo":
        notes.append("Demo data: these events are generated, not read from Teams.")
    elif source == "memory":
        notes.append("Offline mode: only events recorded by this running server, kept in memory (latest 5,000), "
                     "gone on restart. Nothing is read from Azure.")
    allowed_events = funnel.get("granted", 0)
    if k.conversations_granted > allowed_events:
        notes.append("Some conversations were allowed before this period, or before auditing began. They are in the "
                     "consent register but not in this period's decision counts.")
    if level != "org":
        notes.append("Interviews, generated documents and API questions belong to the shared knowledge base, "
                     "so they appear in the organisation view only.")
    if not evs:
        notes.append("No audit events in this period.")

    return DashboardSummary(
        data_source=source,  # type: ignore[arg-type]
        scope=node.model_copy(update={"children": [c.model_copy() for c in node.children]}),
        since=since, until=until, kpis=k, redactions=dict(redactions),
        consent_funnel={**{v: funnel.get(v, 0) for v in ("requested", "granted", "declined", "paused")},
                        "approval_rate": (round(funnel["granted"] / (funnel["granted"] + funnel["declined"]), 3)
                                          if funnel["granted"] + funnel["declined"] else None)},
        activity=list(daily.values()),
        by_category=sorted((CategoryCount(category=c, count=v[0], failures=v[1]) for c, v in by_cat.items()),
                           key=lambda x: (-x.count, x.category)),
        conversations=conversations[:TOP_CONVERSATIONS],
        risk_impact=sorted(impacts, key=lambda r: r.approved_at) if level == "org" else [],
        recent_failures=failures[:RECENT_FAILURES],
        governance=list(GOVERNANCE), notes=notes,
    )


def consent_register(org: Org, level: str, node_id: str, records: Iterable[ConsentRecord]) -> list[ConsentRegisterRow]:
    allowed = org.projects_under(level, node_id)
    rows = []
    for r in records:
        pid = org.project_of(r.scope_id) or UNASSIGNED_PROJECT_ID
        if allowed is not None and pid not in allowed:
            continue
        rows.append(ConsentRegisterRow(
            scope_id=r.scope_id, name=conversation_name(r, org.names.get(pid)), type=r.scope_type, status=r.status,
            approved_by=r.granted_by, updated_at=r.updated_at, project=org.names.get(pid, UNASSIGNED_PROJECT_NAME),
            account=org.account_name_of_project(pid), auto_installed=r.auto_installed,
        ))
    return sorted(rows, key=lambda r: (r.account.lower(), r.project.lower(), r.name.lower()))
