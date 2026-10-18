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
    DashboardEvent, DashboardKpis, DashboardSummary, FileEventView, GovernanceItem, HierarchyNode, OnboardingSummary,
    ProjectTeam, RebuildEvent, RiskImpact, SourcesSummary, SyncRun,
)

ORG_ID = "org"
UNASSIGNED_PROJECT_ID = "prj_unassigned"
UNASSIGNED_PROJECT_NAME = "Unassigned conversations"
TOP_CONVERSATIONS = 10
RECENT_FAILURES = 10
MAX_EVENTS = 1000

#: Scope types that are people talking. Anything else (a SharePoint site, a mailbox) is a knowledge *source*.
CONVERSATION_TYPES = ("channel", "chat")
SHARED_SOURCE_PROJECT = "Shared knowledge source"
#: Readiness spread and average are shown only for at least this many assessments, so one newcomer's result
#: can never be read off a chart.
MIN_GROUP = 3
RECENT_SYNCS = 8
RECENT_FILES = 12

#: Detail keys that identify people or individual messages; never shown on the dashboard.
HIDDEN_DETAIL_KEYS = frozenset({
    "authors", "member_names", "message_ids", "question_id", "modified_by", "created_by", "candidate",
})
#: Onboarding events describe one newcomer. The public view keeps only what is not about the person: no name, no
#: scores, no topics they studied, no readiness.
SAFE_ONBOARDING_DETAILS = frozenset({"kind", "answered", "focus", "question", "model", "error", "project"})

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
    if rec.scope_type == "site":
        return name or "Unnamed SharePoint site"
    if rec.scope_type == "mailbox":
        return name or "Unnamed mailbox"
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
    records = [r for r in records if r.scope_type in CONVERSATION_TYPES]  # sites and mailboxes are sources, not projects' chats
    projects = sorted(projects, key=lambda p: (p.name.lower(), p.team_id))
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
    """An audit event without people: a role instead of a name, no authors / member names / message ids,
    and nothing that describes one newcomer's assessment."""
    if e.actor_name == "LegacyLens":
        role = "LegacyLens"
    elif e.actor_name and e.actor_name.startswith("admin configuration"):
        role = "system"
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
    if e.action.startswith("onboarding."):
        scope_name = "a new joiner"  # the session is named after the candidate
        details = {k: v for k, v in details.items() if k in SAFE_ONBOARDING_DETAILS}
    return DashboardEvent(
        id=e.id, timestamp=e.timestamp, action=e.action, outcome=e.outcome, source=e.source, actor_role=role,
        scope_type=e.scope_type, scope_name=scope_name, count=e.count, details=details,
    )


def event_project(org: Org, e: AuditEvent) -> Optional[str]:
    """The project an event belongs to: by its conversation, or (onboarding) by the team id it records."""
    found = org.project_of(e.scope_id)
    if found is None:
        team = e.details.get("project_team_id")
        found = org.project_of(team) if isinstance(team, str) else None
    return found


def in_scope(org: Org, level: str, node_id: str, event: AuditEvent) -> bool:
    allowed = org.projects_under(level, node_id)
    return True if allowed is None else event_project(org, event) in allowed


def filter_events(
    org: Org, level: str, node_id: str, events: Iterable[AuditEvent], since: datetime, until: datetime,
) -> list[AuditEvent]:
    since, until = _utc(since), _utc(until)
    return [e for e in events if since <= _utc(e.timestamp) <= until and in_scope(org, level, node_id, e)]


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
    if reply == "runbook":
        return "runbook"
    return "command"


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _sync_error(details: dict[str, Any]) -> Optional[str]:
    """Why a sync failed: its own error, or the first per-file error of a run that finished with some."""
    errors = details.get("errors")
    text = details.get("error") or (errors[0] if isinstance(errors, list) and errors else None)
    return str(text)[:160] if text else None


def _onboarding_summary(events: list[AuditEvent]) -> OnboardingSummary:
    """Counts by session. Per-person detail (names, scores, topics studied) is never read here, and the readiness
    spread and average appear only for a group of at least MIN_GROUP evaluations."""
    o = OnboardingSummary(min_group=MIN_GROUP)
    sessions: dict[str, set[str]] = defaultdict(set)
    last_eval: dict[str, AuditEvent] = {}
    for e in sorted(events, key=lambda e: e.timestamp):
        verb, sid = e.action.split(".", 1)[1], e.scope_id or e.id
        if e.outcome == "failure":
            o.failed += 1
            continue
        sessions[verb].add(sid)
        if verb == "question_asked":
            o.learning_questions += 1
            kind = str(e.details.get("kind") or "ask")
            o.learning_by_kind[kind] = o.learning_by_kind.get(kind, 0) + 1
            o.learning_unanswered += e.details.get("answered") is False
        elif verb == "evaluated":
            last_eval[sid] = e
    o.assigned, o.started_learning = len(sessions["assigned"]), len(sessions["question_asked"])
    o.assessments_started, o.completed = len(sessions["assessment_started"]), len(sessions["evaluated"])
    o.results_posted = len(sessions["result_posted"])
    if o.completed >= MIN_GROUP:
        spread = Counter(str(e.details.get("readiness")) for e in last_eval.values())
        o.readiness = {r: spread.get(r, 0) for r in ("ready", "almost", "needs_more_time")}
        scores = [float(e.details["overall"]) for e in last_eval.values() if isinstance(e.details.get("overall"), (int, float))]
        o.average_overall = round(sum(scores) / len(scores)) if scores else None
    elif o.completed:
        o.suppressed = True
    return o


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
    records = list(records)
    # Only chats and channels are "conversations". A SharePoint site has a consent record too, but it is a knowledge
    # source: counting it as a conversation would inflate the numbers and put it under a project it does not belong to.
    recs = [r for r in records if r.scope_type in CONVERSATION_TYPES and (allowed is None or org.project_of(r.scope_id) in allowed)]
    site_recs = [r for r in records if r.scope_type == "site"]

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
    src = SourcesSummary(sites=len(site_recs))
    # Files are counted once each, however many times they were (re)loaded: a rebuild reloads every file, and
    # counting events would report each of them twice. Chunks come from each file's latest load.
    file_sets: dict[str, set[str]] = defaultdict(set)
    day_files: dict[str, set[str]] = defaultdict(set)
    latest_load: dict[str, tuple[str, int]] = {}
    file_names: dict[str, str] = {}
    onboarding_events: list[AuditEvent] = []

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
        elif e.action.startswith("consent.") and e.scope_type in ("site", "mailbox"):
            pass  # an admin configured a source; that is not a person's Allow / Not now
        elif e.action.startswith("consent."):
            verb = e.action.split(".", 1)[1]
            funnel[verb] += 1
            if verb in ("granted", "declined", "paused") and d is not None:
                d.consent_decisions += 1
        elif e.action == "capture.file":
            change, status = str(e.details.get("change", "")), str(e.details.get("status", ""))
            name = str(e.details.get("file", ""))
            ident = e.item_ids[0] if e.item_ids else f"{e.details.get('path', '')}/{name}"
            if e.outcome == "failure":
                file_sets["failed"].add(ident)
            elif e.outcome == "skipped":
                file_sets["skipped"].add(ident)
            elif change == "deleted":
                file_sets["deleted"].add(ident)
                latest_load[ident] = ("deleted", 0)
            else:
                file_sets["modified" if change == "modified" else "added"].add(ident)
                latest_load[ident] = (change, _int(e.count))
                file_names[ident] = name
                if d is not None:
                    day_files[d.date].add(ident)
            src.recent_files.append(FileEventView(
                timestamp=_utc(e.timestamp), change=change, file=name, path=str(e.details.get("path", "")),
                status=status, chunks=_int(e.count), outcome=e.outcome,
                error=str(e.details["error"])[:160] if e.details.get("error") else None))
        elif e.action == "sync.sharepoint":
            src.sync_runs += 1
            src.sync_failures += e.outcome == "failure"
            src.last_sync, src.last_sync_outcome = _utc(e.timestamp), e.outcome
            dd = e.details
            src.recent_syncs.append(SyncRun(
                timestamp=_utc(e.timestamp), outcome=e.outcome, added=_int(dd.get("added")), modified=_int(dd.get("modified")),
                deleted=_int(dd.get("deleted")), unchanged=_int(dd.get("unchanged")), failed=_int(dd.get("failed")),
                unsupported=_int(dd.get("unsupported")), chunks_indexed=_int(dd.get("chunks_indexed")),
                seconds=float(dd.get("duration_seconds") or 0.0), full=bool(dd.get("full")),
                error=_sync_error(dd)))
        elif e.action == "knowledge.rebuilt":
            src.rebuilds.append(RebuildEvent(
                timestamp=_utc(e.timestamp), reason=str(e.details.get("reason", "")),
                sharepoint_files=_int(e.details.get("sharepoint_files")), teams_messages=_int(e.details.get("teams_messages"))))
        elif e.action.startswith("onboarding."):
            onboarding_events.append(e)
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
                elif kind == "runbook":
                    k.runbooks_requested += 1
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
    onb = _onboarding_summary(onboarding_events)
    src.files_added, src.files_modified = len(file_sets["added"]), len(file_sets["modified"])
    src.files_deleted, src.files_failed, src.files_skipped = len(file_sets["deleted"]), len(file_sets["failed"]), len(file_sets["skipped"])
    src.chunks_indexed = sum(chunks for change, chunks in latest_load.values() if change != "deleted")
    types: Counter = Counter()
    for ident in file_sets["added"] | file_sets["modified"]:
        name = file_names.get(ident, "")
        types[name.rsplit(".", 1)[-1].lower() if "." in name else "other"] += 1
    src.by_type = dict(types.most_common())
    for date, idents in day_files.items():
        daily[date].files_synced = len(idents)
    newest = lambda x: x.timestamp  # noqa: E731
    src.recent_syncs = sorted(src.recent_syncs, key=newest, reverse=True)[:RECENT_SYNCS]
    src.recent_files = sorted(src.recent_files, key=newest, reverse=True)[:RECENT_FILES]
    src.rebuilds = sorted(src.rebuilds, key=newest, reverse=True)[:5]

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
        notes.append("Interviews, generated documents, SharePoint files and API questions belong to the shared knowledge "
                     "base, so they appear in the organisation view only.")
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
        sources=src if level == "org" else None,
        onboarding=onb,
        recent_failures=failures[:RECENT_FAILURES],
        governance=list(GOVERNANCE), notes=notes,
    )


def consent_register(org: Org, level: str, node_id: str, records: Iterable[ConsentRecord]) -> list[ConsentRegisterRow]:
    allowed = org.projects_under(level, node_id)
    rows = []
    for r in records:
        if r.scope_type not in CONVERSATION_TYPES:  # a SharePoint site / mailbox: shared, listed at org level only
            if allowed is None:
                rows.append(ConsentRegisterRow(
                    scope_id=r.scope_id, name=conversation_name(r), type=r.scope_type, status=r.status,
                    approved_by=r.granted_by, updated_at=r.updated_at, project=SHARED_SOURCE_PROJECT, account="",
                    auto_installed=False))
            continue
        pid = org.project_of(r.scope_id) or UNASSIGNED_PROJECT_ID
        if allowed is not None and pid not in allowed:
            continue
        rows.append(ConsentRegisterRow(
            scope_id=r.scope_id, name=conversation_name(r, org.names.get(pid)), type=r.scope_type, status=r.status,
            approved_by=r.granted_by, updated_at=r.updated_at, project=org.names.get(pid, UNASSIGNED_PROJECT_NAME),
            account=org.account_name_of_project(pid), auto_installed=r.auto_installed,
        ))
    return sorted(rows, key=lambda r: (r.account.lower(), r.project.lower(), r.name.lower()))
