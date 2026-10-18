"""
Deterministic demo data for the dashboard: an organisation with two client accounts and three
projects, three weeks of consent decisions, captures, questions, interviews and the odd failure.

Used only offline (LIVE_M365=false) when DASHBOARD_DEMO=true, and by tests. It writes to the
in-memory audit log and consent store and refuses to touch real storage. Fixed seed, so the same
`now` always yields the same data.
"""

from __future__ import annotations

import random
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.models import AuditEvent, ConsentRecord, ProjectTeam
from app.services.audit import MemoryAuditLog
from app.services.consent_store import MemoryConsentStore

SEED = 26
DAYS = 21

#: project name -> client account, used when PROJECT_ACCOUNTS is blank in demo mode.
DEMO_ACCOUNTS = {
    "payments platform": "Contoso Payments",
    "merchant onboarding": "Contoso Payments",
    "storefront revamp": "Northwind Retail",
}

_PROJECTS = [
    ("19:pp-general@thread.tacv2", "aad-pp", "Payments Platform"),
    ("19:mo-general@thread.tacv2", "aad-mo", "Merchant Onboarding"),
    ("19:sf-general@thread.tacv2", "aad-sf", "Storefront Revamp"),
]

#: (scope id, type, name, project index, status, days ago it started, auto-installed, approved by)
_CONVERSATIONS = [
    ("19:pp-general@thread.tacv2", "channel", "Payments Platform", 0, "granted", 20, False, "Priya Nair"),
    ("19:gw-bridge@thread.v2", "chat", "Gateway incident bridge", 0, "granted", 14, True, "Arjun Mehta"),
    ("19:settle-ops@thread.v2", "chat", "Settlement ops", 0, "paused", 9, True, "Arjun Mehta"),
    ("19:mo-general@thread.tacv2", "channel", "Merchant Onboarding", 1, "granted", 18, False, "Dana Whitfield"),
    ("19:kyc-qs@thread.v2", "chat", "KYC questions", 1, "declined", 12, True, "Dana Whitfield"),
    ("19:sf-general@thread.tacv2", "channel", "Storefront Revamp", 2, "granted", 10, False, "Luis Ortega"),
    ("19:sf-release@thread.v2", "chat", "Release planning", 2, "pending", 3, True, None),
]

_ASKERS = ["Priya Nair", "Meera Iyer", "Arjun Mehta", "Dana Whitfield", "Luis Ortega"]
_INTERVIEWS = [  # topic, days ago, risk before, risk after
    ("Payment Gateway", 4, 1.0, 0.0),
    ("Settlement Batch Job", 2, 0.7, 0.19),
]


_SITE = ("site-demo-payments", "Payments Platform SharePoint")
_FILES = [  # file, folder, size (kB), editor (a person: must never reach the dashboard)
    ("Release process.docx", "Engineering/Process", 84, "Priya Nair"), ("Gateway recovery notes.docx", "Engineering/Runbooks", 41, "Ravi Kumar"),
    ("Settlement batch guide.pdf", "Operations", 312, "Arjun Mehta"), ("Onboarding checklist.xlsx", "People", 27, "Meera Iyer"),
    ("Architecture overview.pptx", "Engineering/Design", 905, "Priya Nair"), ("API authentication.md", "Engineering/Docs", 9, "Priya Nair"),
    ("Kafka decision record.txt", "Engineering/Design", 6, "Priya Nair"), ("Support escalation.html", "Operations", 18, "Arjun Mehta"),
    ("On-call handbook.docx", "Operations", 120, "Arjun Mehta"), ("Merchant KYC steps.pdf", "Onboarding", 220, "Dana Whitfield"),
]
#: project index, candidate, readiness, overall, journey, answer scores, learning questions (kind, answered)
_ONBOARDING = [
    (0, "Meera Iyer", "ready", 82, 4, [4, 4], [("ask", True), ("topics", True), ("runbook", True), ("ask", True)]),
    (0, "Sam Carter", "almost", 64, 3, [3, 3], [("ask", True), ("ask", False), ("risks", True)]),
    (0, "Nia Okafor", "needs_more_time", 38, 2, [2, 1], [("ask", False), ("ask", True)]),
    (1, "Tom Becker", "almost", 71, 4, [3, 3], [("ask", True), ("runbook", True), ("ask", True), ("ask", False)]),
    (2, "Ida Lindqvist", None, 0, 0, [], [("ask", True), ("topics", True)]),   # still learning, not assessed
]


def build_demo(now: Optional[datetime] = None) -> tuple[list[AuditEvent], list[ConsentRecord], list[ProjectTeam]]:
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    rnd = random.Random(SEED)
    events: list[AuditEvent] = []

    def add(action: str, when: datetime, **fields) -> None:
        events.append(AuditEvent(id=f"audit_demo_{len(events):05d}", timestamp=min(when, now), action=action, **fields))

    def at(days_ago: float, hour: int = 9) -> datetime:
        day = (now - timedelta(days=days_ago)).replace(hour=hour, minute=rnd.randint(0, 59), second=rnd.randint(0, 59), microsecond=0)
        return day

    projects = [ProjectTeam(team_id=t, aad_group_id=g, name=n, member_aad_ids=[f"aad-{g}-{i}" for i in range(6)])
                for t, g, n in _PROJECTS]
    records = []
    for scope_id, kind, name, pi, status, started, auto, approver in _CONVERSATIONS:
        records.append(ConsentRecord(
            scope_id=scope_id, scope_type=kind, display_name=name, status=status, auto_installed=auto,
            team_id=_PROJECTS[pi][1] if kind == "channel" else None,
            channel_id=scope_id if kind == "channel" else None,
            project_id=_PROJECTS[pi][0] if kind == "chat" else None, granted_by=approver,
        ))

    for t, _g, n in _PROJECTS:
        add("roster.updated", at(DAYS - 1), source="teams", scope_type="team", scope_id=t, scope_name=n, count=6,
            details={"added": 6, "removed": 0})

    for scope_id, kind, name, pi, status, started, auto, approver in _CONVERSATIONS:
        scope = dict(scope_type=kind, scope_id=scope_id, scope_name=name)
        if auto:
            add("detection.chat_invited", at(started, 8), source="graph", actor_name="LegacyLens", count=5,
                details={"members": 5, "project_members": 3}, **scope)
        add("consent.requested", at(started, 8), source="teams", actor_name="LegacyLens", **scope,
            details={"trigger": "detection" if auto else "install", "auto_installed": auto})
        if status == "pending":
            continue
        verb = "granted" if status in ("granted", "paused") else "declined"
        add(f"consent.{verb}", at(started, 9), source="teams", actor_name=approver, actor_aad_id="aad-demo", **scope,
            details={"auto_installed": auto})
        if verb == "declined":
            add("detection.app_removed", at(started, 9), source="graph", actor_name="LegacyLens", **scope,
                details={"reason": "consent declined"})
            continue

        backfilled = rnd.randint(18, 50)
        add("capture.backfill", at(started, 9), source="teams", actor_name="LegacyLens", count=backfilled,
            details={"messages_read": backfilled + rnd.randint(0, 4), "chunks": backfilled,
                     "redactions": {"phone": rnd.randint(0, 2), "email": rnd.randint(0, 3)},
                     "authors": ["(hidden on the dashboard)"]}, **scope)
        stop = 5 if status == "paused" else 0           # a paused conversation stops being captured
        if status == "paused":
            add("consent.paused", at(stop, 15), source="teams", actor_name="Arjun Mehta", actor_aad_id="aad-demo",
                **scope, details={"auto_installed": auto})
        for ago in range(int(started) - 1, stop - 1, -1):
            weekday = (now - timedelta(days=ago)).weekday() < 5
            for _ in range(rnd.randint(3, 8) if weekday else rnd.randint(0, 2)):
                n = rnd.randint(1, 7)
                red = {"phone": 1} if rnd.random() < 0.08 else ({"email": 1} if rnd.random() < 0.06 else {})
                add("capture.message", at(ago, rnd.randint(9, 17)), source="teams", actor_name="LegacyLens",
                    count=n, details={"messages_read": n + rnd.randint(0, 1), "chunks": n, "redactions": red}, **scope)
            for _ in range(rnd.randint(2, 7) if weekday else rnd.randint(0, 1)):
                r = rnd.random()
                reply = "answer" if r < 0.68 else "no_answer" if r < 0.86 else rnd.choice(["help", "risks"])
                add("ask.answered", at(ago, rnd.randint(9, 18)), source="teams", actor_name=rnd.choice(_ASKERS),
                    actor_aad_id="aad-demo", item_ids=[f"art_demo_{rnd.randint(1, 90)}" for _ in range(rnd.randint(2, 4))]
                    if reply == "answer" else [], details={"reply": reply}, **scope)
    # A few things that went wrong, so the failure panel has something honest to show.
    add("capture.backfill", at(14, 10), outcome="failure", source="graph", actor_name="LegacyLens", scope_type="chat",
        scope_id="19:gw-bridge@thread.v2", scope_name="Gateway incident bridge", details={"error": "HTTP 429"})
    add("detection.install_failed", at(7, 11), outcome="failure", source="graph", actor_name="LegacyLens",
        scope_type="chat", scope_id="19:kyc-qs@thread.v2", scope_name="KYC questions", details={"error": "HTTP 403"})
    add("security.webhook_rejected", at(6, 3), outcome="failure", source="graph", details={"reason": "clientState mismatch"})
    add("system.subscription_failed", at(11, 4), outcome="failure", source="graph", details={"resource": "/chats", "error": "HTTP 503"})

    # Shared knowledge base: interviews, documents, API questions, ingests, restarts.
    for ago in range(DAYS - 1, -1, -3):
        add("system.started", at(ago, 7), source="system", details={"version": "0.1.0", "live_m365": False})
    add("ingest.completed", at(DAYS - 1, 7), source="api", count=87,
        details={"chunks": 92, "entities": 19, "edges": 280, "redactions": {"phone": 1}, "reset": True, "seconds": 0.4})
    for topic, ago, before, after in _INTERVIEWS:
        sid = f"sess_demo_{topic.split()[0].lower()}"
        add("interview.started", at(ago + 0.2, 10), source="api", actor_name="Priya Nair", scope_type="interview",
            scope_id=sid, scope_name=topic, count=4, details={})
        for q in range(4):
            add("interview.answered", at(ago + 0.1, 11 + q), source="api", actor_name="Ravi Kumar", scope_type="interview",
                scope_id=sid, scope_name=topic, details={"follow_up_asked": q == 1})
        add("interview.approved", at(ago, 16), source="api", actor_name="Ravi Kumar", scope_type="interview", scope_id=sid,
            scope_name=topic, count=4, details={"chunks": 6, "risk_before": before, "risk_after": after})
        add("artifact.generated", at(ago - 0.5, 10), source="api", scope_type="runbook", scope_id=f"doc_{sid}",
            scope_name=topic, item_ids=[f"art_demo_{i}" for i in range(5)], details={"title": f"Runbook: {topic}", "model": "gpt-4o"})
    add("artifact.generated", at(1, 12), source="api", scope_type="handover", scope_id="doc_demo_handover",
        scope_name="Ravi Kumar", item_ids=[f"art_demo_{i}" for i in range(8)], details={"title": "Handover pack: Ravi Kumar", "model": "rules"})
    for ago in range(DAYS - 1, -1, -1):
        for _ in range(rnd.randint(1, 5)):
            ok = rnd.random() < 0.8
            add("ask.answered", at(ago, rnd.randint(9, 18)), source="api", item_ids=[f"art_demo_{i}" for i in range(rnd.randint(2, 4))] if ok else [],
                details={"reply": "answer" if ok else "no_answer", "confidence": round(rnd.uniform(0.55, 0.9), 2) if ok else 0.0})

    # --- SharePoint knowledge source (org level): one consent record, a first full sync, then daily deltas ---------
    site_id, site_name = _SITE
    records.append(ConsentRecord(scope_id=site_id, scope_type="site", display_name=site_name, status="granted",
                                 granted_by="admin configuration (SHAREPOINT_SITE_URL)"))
    site = dict(scope_type="site", scope_id=site_id, scope_name=site_name)
    add("consent.granted", at(DAYS - 1, 7), source="system", actor_name="admin configuration", **site,
        details={"site_url": "https://contoso.sharepoint.example/sites/payments"})
    add("knowledge.rebuilt", at(DAYS - 1, 7), source="system", actor_name="LegacyLens",
        details={"reason": "first start without sample data", "sharepoint_files": len(_FILES), "sharepoint_failed": 0,
                 "teams_messages": 0, "sharepoint_configured": True})

    def file_event(when, name, folder, kb, editor, change, status="loaded", outcome="success", error=None):
        chunks = 0 if status != "loaded" else max(1, kb // 12)
        add("capture.file", when, outcome=outcome, source="sharepoint", actor_name="LegacyLens", count=chunks, item_ids=[f"art_sp_{zlib.crc32(name.encode()) % 99999}"],
            details={"change": change, "file": name, "path": folder, "web_url": f"https://contoso.sharepoint.example/{folder}/{name}",
                     "size": kb * 1024, "modified_by": editor, "modified_at": when.isoformat(), "status": status,
                     "characters": kb * 900, **({"error": error} if error else {})}, **site)

    for name, folder, kb, editor in _FILES:                                   # the first full sync
        file_event(at(DAYS - 1, 7), name, folder, kb, editor, "added")
    add("sync.sharepoint", at(DAYS - 1, 7), source="sharepoint", count=len(_FILES), **site,
        details={"site_name": site_name, "full": True, "added": len(_FILES), "modified": 0, "deleted": 0, "unchanged": 0,
                 "failed": 0, "unsupported": 0, "chunks_indexed": 95, "duration_seconds": 41.2, "errors": []})
    for ago in range(DAYS - 2, -1, -1):                                       # then a delta sync every day
        changed = rnd.randint(0, 3) if (now - timedelta(days=ago)).weekday() < 5 else 0
        modified = deleted = failed = skipped = 0
        for _ in range(changed):
            name, folder, kb, editor = rnd.choice(_FILES)
            file_event(at(ago, rnd.randint(9, 17)), name, folder, kb + rnd.randint(0, 5), editor, "modified")
            modified += 1
        if ago == 12:
            file_event(at(ago, 11), "Team photo.png", "People", 640, "Dana Whitfield", "added", status="unsupported", outcome="skipped")
            skipped += 1
        if ago == 8:
            file_event(at(ago, 14), "Corrupt export.pdf", "Operations", 80, "Arjun Mehta", "added", status="failed", outcome="failure", error="could not read the PDF")
            failed += 1
        if ago == 5:
            file_event(at(ago, 15), "Kafka decision record.txt", "Engineering/Design", 6, "Priya Nair", "deleted", status="deleted")
            deleted += 1
        outcome = "failure" if failed else "success"
        add("sync.sharepoint", at(ago, 18), outcome=outcome, source="sharepoint", count=modified, **site,
            details={"site_name": site_name, "full": False, "added": 0, "modified": modified, "deleted": deleted,
                     "unchanged": len(_FILES) - modified, "failed": failed, "unsupported": skipped,
                     "chunks_indexed": modified * 4, "duration_seconds": round(rnd.uniform(0.8, 3.5), 1), "errors": []})
    add("sync.sharepoint", at(3, 4), outcome="failure", source="sharepoint",
        details={"error": "403 Forbidden", "hint": "check Sites.Selected grant / SHAREPOINT_SITE_URL"})

    # --- onboarding assessments: counted per project, never per person --------------------------------------------
    for n, (pi, who, readiness, overall, journey, scores, learning) in enumerate(_ONBOARDING):
        team_id, _g, project = _PROJECTS[pi]
        sid = f"onb_demo_{n}"
        base = 12 - 2 * n
        on = dict(scope_type="onboarding", scope_id=sid, scope_name=who)

        def onb(action, hours, outcome="success", actor=who, **details):
            add(f"onboarding.{action}", at(base - hours / 24, 9 + hours % 8), outcome=outcome, source="teams",
                actor_name=actor, actor_aad_id="aad-demo", **on, details={"project_team_id": team_id, **details})

        onb("assigned", 0, actor="Priya Nair", project=project)
        for i, (kind, answered) in enumerate(learning):
            onb("question_asked", 2 + i, count=i + 1, kind=kind, topics=["Payment Gateway", "Release Process"][: 1 + i % 2], answered=answered)
        if readiness is None:
            continue
        onb("assessment_started", 12, topics=["Payment Gateway", "Settlement Batch Job"], focus=["studied", "unexplored"])
        for q in (1, 2):
            onb("answered", 13 + q, question=q, topic="Payment Gateway", focus="studied" if q == 1 else "unexplored")
        onb("evaluated", 15, actor="LegacyLens", readiness=readiness, overall=overall, journey_score=journey, answer_scores=scores, model="gpt-4o")
        onb("result_posted", 16, actor="LegacyLens", channel_id="19:pp-general@thread.tacv2", readiness=readiness)
    on = dict(scope_type="onboarding", scope_id="onb_demo_fail", scope_name="Raj Patel")
    add("onboarding.evaluated", at(2, 10), outcome="failure", source="teams", **on,
        details={"project_team_id": _PROJECTS[0][0], "error": "evaluation failed: TimeoutError"})
    return events, records, projects


def seed_demo(audit_log, consent_store, now: Optional[datetime] = None) -> int:
    """Fill the in-memory audit log and consent store. Returns the number of events written."""
    if not isinstance(audit_log, MemoryAuditLog) or not isinstance(consent_store, MemoryConsentStore):
        raise RuntimeError("demo data is only ever written to the in-memory stores, never to real storage")
    events, records, projects = build_demo(now)
    for e in events:
        audit_log.write(e)
    for r in records:
        consent_store.save(r)
    for p in projects:
        consent_store.save_project(p)
    return len(events)
