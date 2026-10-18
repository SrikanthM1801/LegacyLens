"""Dashboard: hierarchy, aggregation, privacy, demo data, and the /dashboard API."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import dashboard as dash_api
from app.bot.chat_watch import matching_project
from app.config import get_settings
from app.main import app
from app.models import AuditEvent, ConsentRecord, ProjectTeam
from app.services import audit, dashboard
from app.services.audit import BlobAuditLog
from app.services.audit_demo import DEMO_ACCOUNTS, build_demo, seed_demo
from app.services.consent_store import MemoryConsentStore, TableConsentStore, get_consent_store

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=30)
NAMES = ("Priya Nair", "Arjun Mehta", "Meera Iyer", "Dana Whitfield", "Luis Ortega", "Ravi Kumar")
CANDIDATES = ("Meera Iyer", "Sam Carter", "Nia Okafor", "Tom Becker", "Ida Lindqvist", "Raj Patel")   # onboarding newcomers
EDITORS = ("Priya Nair", "Ravi Kumar", "Arjun Mehta", "Meera Iyer", "Dana Whitfield")                # people who edited files


@pytest.fixture(scope="module")
def demo():
    events, records, projects = build_demo(NOW)
    org = dashboard.build_org(records, projects, get_settings(), mapping=DEMO_ACCOUNTS)
    return org, events, records


def summary(demo, level="org", node_id="org", **kw):
    org, events, records = demo
    return dashboard.summarize(org, level, node_id, events, records, SINCE, NOW, **kw)


def ev(action, minutes_ago=5, **kw):
    return AuditEvent(action=action, timestamp=NOW - timedelta(minutes=minutes_ago), **kw)


# --- hierarchy -------------------------------------------------------------------------


def test_hierarchy_org_account_project(demo):
    org = demo[0]
    assert org.root.level == "org" and org.root.conversations == 7
    assert [(a.name, [p.name for p in a.children]) for a in org.root.children] == [
        ("Contoso Payments", ["Merchant Onboarding", "Payments Platform"]),
        ("Northwind Retail", ["Storefront Revamp"]),
    ]
    prj = {p.name: p for a in org.root.children for p in a.children}
    assert prj["Payments Platform"].conversations == 3  # a channel plus two auto-detected chats
    assert org.root.children[0].conversations == 5
    assert org.node("project", prj["Storefront Revamp"].id) is prj["Storefront Revamp"] and org.node("project", "nope") is None


def test_chats_and_team_scopes_resolve_to_their_project(demo):
    org = demo[0]
    pp = next(p for a in org.root.children for p in a.children if p.name == "Payments Platform")
    for scope in ("19:pp-general@thread.tacv2", "19:gw-bridge@thread.v2", "19:settle-ops@thread.v2"):
        assert org.project_of(scope) == pp.id
    assert org.project_of("19:somewhere-else@thread.v2") is None and org.project_of(None) is None


def test_unmatched_conversations_go_to_an_unassigned_project():
    rec = ConsentRecord(scope_id="19:lost@thread.v2", scope_type="chat", display_name="Lost chat", status="granted")
    org = dashboard.build_org([rec], [], get_settings())
    assert [(a.name, [p.name for p in a.children]) for a in org.root.children] == [
        (get_settings().default_account_name, [dashboard.UNASSIGNED_PROJECT_NAME])]
    assert org.project_of("19:lost@thread.v2") == dashboard.UNASSIGNED_PROJECT_ID


def test_project_accounts_setting_maps_by_name_or_team_id(monkeypatch):
    team = ProjectTeam(team_id="19:x@thread.tacv2", name="Alpha", member_aad_ids=[])
    s = get_settings()
    monkeypatch.setattr(s, "project_accounts", json.dumps({"alpha": "Acme"}))
    assert dashboard.build_org([], [team], s).root.children[0].name == "Acme"
    monkeypatch.setattr(s, "project_accounts", json.dumps({"19:X@thread.tacv2": "Globex"}))
    assert dashboard.build_org([], [team], s).root.children[0].name == "Globex"
    monkeypatch.setattr(s, "project_accounts", "{not json")
    assert dashboard.build_org([], [team], s).root.children[0].name == s.default_account_name  # never crashes


# --- aggregation -----------------------------------------------------------------------


def test_kpis_are_computed_from_events(demo):
    org, _, _ = demo
    rec = ConsentRecord(scope_id="c1", scope_type="channel", display_name="Chan", status="granted", team_id="g1")
    team = ProjectTeam(team_id="t1", aad_group_id="g1", name="P", member_aad_ids=[])
    o = dashboard.build_org([rec], [team], get_settings())
    c = dict(scope_type="channel", scope_id="c1", scope_name="Chan", source="teams")
    events = [
        ev("consent.requested", 50, **c), ev("consent.granted", 49, **c), ev("consent.paused", 10, **c),
        ev("capture.backfill", 48, count=5, details={"messages_read": 6, "chunks": 5, "redactions": {"phone": 2}}, **c),
        ev("capture.message", 40, count=2, details={"messages_read": 2, "chunks": 2, "redactions": {"email": 1}}, **c),
        ev("capture.message", 30, outcome="skipped", count=0, details={"messages_read": 3}, **c),
        ev("ask.answered", 20, item_ids=["a", "b", "c"], details={"reply": "answer"}, **c),
        ev("ask.answered", 19, item_ids=["a"], details={"reply": "answer"}, **c),
        ev("ask.answered", 18, details={"reply": "no_answer"}, **c),
        ev("ask.answered", 17, details={"reply": "help"}, **c),
        ev("ask.answered", 16, outcome="failure", **c),
        ev("ask.answered", 15, source="api", item_ids=["z"], details={"reply": "answer", "confidence": 0.8}),  # shared KB
        ev("interview.started", 14, source="api"), ev("interview.answered", 13, source="api"),
        ev("interview.approved", 12, source="api", scope_name="Gateway", details={"risk_before": 1.0, "risk_after": 0.1}),
        ev("artifact.generated", 11, source="api"), ev("security.webhook_rejected", 9, outcome="failure", source="graph"),
        ev("detection.chat_invited", 8, source="graph", scope_type="chat", scope_id="c1"),
        ev("capture.message", 60 * 24 * 40, count=99, **c),  # outside the window
    ]
    k = dashboard.summarize(o, "org", "org", events, [rec], SINCE, NOW).kpis
    assert (k.conversations_total, k.conversations_granted) == (1, 1)
    assert (k.messages_captured, k.messages_read, k.duplicates_skipped, k.chunks_indexed) == (7, 11, 4, 7)
    assert (k.questions_answered, k.questions_unanswered, k.questions_failed, k.commands) == (3, 1, 1, 1)
    assert k.answer_rate == 0.75 and k.avg_confidence == 0.8 and k.avg_citations == 1.67
    assert (k.interviews_started, k.interviews_answered, k.interviews_approved, k.documents_generated) == (1, 1, 1, 1)
    assert (k.failures, k.security_events, k.chats_detected) == (2, 1, 1)
    s = dashboard.summarize(o, "org", "org", events, [rec], SINCE, NOW)
    assert s.redactions == {"phone": 2, "email": 1}
    assert s.consent_funnel == {"requested": 1, "granted": 1, "declined": 0, "paused": 1, "approval_rate": 1.0}
    assert [(r.topic, r.risk_before, r.risk_after) for r in s.risk_impact] == [("Gateway", 1.0, 0.1)]
    assert s.by_category[0].category == "ask" and {c.category: c.failures for c in s.by_category}["security"] == 1


def test_activity_is_zero_filled_daily_and_sums_match(demo):
    s = summary(demo)
    assert len(s.activity) == 31 and s.activity[-1].date == NOW.date().isoformat()
    assert sum(d.messages_captured for d in s.activity) == s.kpis.messages_captured
    assert sum(d.questions for d in s.activity) == s.kpis.questions_answered + s.kpis.questions_unanswered
    assert sum(d.failures for d in s.activity) == s.kpis.failures
    assert [d.date for d in s.activity] == sorted(d.date for d in s.activity)


def test_levels_add_up(demo):
    org, _, _ = demo
    top = summary(demo)
    accounts = [summary(demo, "account", a.id) for a in org.root.children]
    projects = [summary(demo, "project", p.id) for a in org.root.children for p in a.children]
    for field in ("messages_captured", "messages_read", "conversations_total", "chunks_indexed"):
        by_account = sum(getattr(a.kpis, field) for a in accounts)
        by_project = sum(getattr(p.kpis, field) for p in projects)
        assert by_account == by_project, field
    assert sum(a.kpis.messages_captured for a in accounts) == top.kpis.messages_captured
    assert sum(a.kpis.conversations_total for a in accounts) == top.kpis.conversations_total == 7
    # Teams questions roll up exactly; the org also counts API questions that belong to no conversation.
    assert sum(p.kpis.questions_answered for p in projects) < top.kpis.questions_answered
    assert top.kpis.interviews_approved == 2 and all(p.kpis.interviews_approved == 0 for p in projects)


def test_project_view_is_conversation_scoped(demo):
    org, _, _ = demo
    pp = next(p for a in org.root.children for p in a.children if p.name == "Payments Platform")
    s = summary(demo, "project", pp.id)
    assert s.scope.name == "Payments Platform" and s.risk_impact == []
    assert s.kpis.conversations_total == 3 and (s.kpis.conversations_granted, s.kpis.conversations_paused) == (2, 1)
    assert {c.project for c in s.conversations} == {"Payments Platform"}
    assert any("organisation view only" in n for n in s.notes)
    assert s.kpis.documents_generated == 0


def test_top_conversations_are_sorted_and_named(demo):
    s = summary(demo)
    assert 1 <= len(s.conversations) <= dashboard.TOP_CONVERSATIONS
    scores = [c.messages_captured + c.questions for c in s.conversations]
    assert scores == sorted(scores, reverse=True)
    assert all(c.name and c.project and c.status for c in s.conversations)


def test_consent_funnel_and_conversation_states_in_demo(demo):
    s = summary(demo)
    k = s.kpis
    assert (k.conversations_granted, k.conversations_paused, k.conversations_declined, k.conversations_pending) == (4, 1, 1, 1)
    assert s.consent_funnel["requested"] == 7 and s.consent_funnel["declined"] == 1 and s.consent_funnel["approval_rate"] == 0.833


def test_a_note_explains_conversations_allowed_before_the_audit_trail_began(demo):
    org, _, records = demo
    quiet = dashboard.summarize(org, "org", "org", [], records, SINCE, NOW)  # 4 allowed, but no decision events at all
    assert any("allowed before this period" in n for n in quiet.notes)
    assert not any("allowed before this period" in n for n in summary(demo).notes)  # demo: every allow is in the trail


def test_unknown_node_raises(demo):
    with pytest.raises(KeyError):
        summary(demo, "project", "prj_nope")


def test_empty_period_is_explained(demo):
    org, _, records = demo
    s = dashboard.summarize(org, "org", "org", [], records, SINCE, NOW)
    assert s.kpis.messages_captured == 0 and s.kpis.answer_rate is None and "No audit events in this period." in s.notes


# --- privacy ---------------------------------------------------------------------------


def test_summary_and_events_contain_no_people(demo):
    org, events, _ = demo
    blob = summary(demo).model_dump_json() + json.dumps([dashboard.public_event(e).model_dump(mode="json") for e in events])
    for name in NAMES:
        assert name not in blob, name
    assert "authors" not in blob and "member_names" not in blob and "message_ids" not in blob and "aad-demo" not in blob


def test_public_event_roles():
    assert dashboard.public_event(ev("consent.granted", actor_name="Bob", actor_aad_id="x")).actor_role == "member"
    assert dashboard.public_event(ev("capture.message", actor_name="LegacyLens")).actor_role == "LegacyLens"
    assert dashboard.public_event(ev("system.started")).actor_role == "system"
    e = dashboard.public_event(ev("capture.message", details={"authors": ["Bob"], "message_ids": ["m"], "chunks": 3}))
    assert e.details == {"chunks": 3}
    h = dashboard.public_event(ev("artifact.generated", scope_type="handover", scope_name="Ravi Kumar",
                                  details={"title": "Handover pack: Ravi Kumar", "model": "rules"}))
    assert h.scope_name == "a team member" and h.details == {"model": "rules"}
    r = dashboard.public_event(ev("artifact.generated", scope_type="runbook", scope_name="Payment Gateway",
                                  details={"title": "Runbook: Payment Gateway"}))
    assert r.scope_name == "Payment Gateway" and r.details["title"] == "Runbook: Payment Gateway"  # topics stay visible


def test_consent_register_names_the_approver_and_scopes(demo):
    org, _, records = demo
    rows = dashboard.consent_register(org, "org", "org", records)
    assert len(rows) == 8 and {r.status for r in rows} == {"granted", "paused", "declined", "pending"}
    assert {r.type for r in rows} == {"channel", "chat", "site"}
    site = next(r for r in rows if r.type == "site")
    assert site.project == dashboard.SHARED_SOURCE_PROJECT and site.account == "" and "admin configuration" in site.approved_by
    by_name = {r.name: r for r in rows}
    assert by_name["Gateway incident bridge"].approved_by == "Arjun Mehta" and by_name["Gateway incident bridge"].auto_installed
    assert by_name["Release planning"].approved_by is None and by_name["Release planning"].account == "Northwind Retail"
    northwind = next(a for a in org.root.children if a.name == "Northwind Retail")
    assert {r.name for r in dashboard.consent_register(org, "account", northwind.id, records)} == {"Storefront Revamp", "Release planning"}


# --- demo data -------------------------------------------------------------------------


def test_demo_data_is_deterministic_and_realistic():
    a, b = build_demo(NOW), build_demo(NOW)
    assert [e.model_dump() for e in a[0]] == [e.model_dump() for e in b[0]]
    events = a[0]
    assert 500 < len(events) < 4000 and len({e.id for e in events}) == len(events)
    assert all(e.timestamp <= NOW for e in events)
    assert {"consent.granted", "capture.message", "ask.answered", "interview.approved", "security.webhook_rejected"} <= {e.action for e in events}
    assert sum(e.outcome == "failure" for e in events) >= 4


def test_demo_seed_never_touches_real_storage():
    with pytest.raises(RuntimeError, match="in-memory"):
        seed_demo(object(), MemoryConsentStore())
    log = audit.MemoryAuditLog()
    store = MemoryConsentStore()
    n = seed_demo(log, store, NOW)
    assert n == len(log.query(NOW - timedelta(days=40), NOW)) and len(store.list()) == 8 and len(store.projects()) == 3


# --- names and project assignment of real-world records -------------------------------------------


def test_unnamed_conversations_get_readable_names_not_raw_teams_ids():
    chat = ConsentRecord(scope_id="19:886f14549adb42a8889095d445b8310d@thread.v2", scope_type="chat", status="granted")
    named = ConsentRecord(scope_id="19:x@thread.v2", scope_type="chat", display_name="Gateway bridge", status="granted")
    id_as_name = ConsentRecord(scope_id="19:abc123@thread.v2", scope_type="chat", display_name="19:abc123@thread.v2", status="granted")
    chan = ConsentRecord(scope_id="19:Gyfd_neqG0@thread.tacv2", scope_type="channel", status="granted")
    assert dashboard.conversation_name(chat) == "Unnamed group chat …b8310d"
    assert dashboard.conversation_name(named) == "Gateway bridge"
    assert dashboard.conversation_name(id_as_name) == "Unnamed group chat …abc123"
    assert dashboard.conversation_name(chan, "Payments") == "Payments (channel)"
    assert dashboard.conversation_name(chan) == "Unnamed channel …dneqG0" and "@thread" not in dashboard.conversation_name(chan)
    other = ConsentRecord(scope_id="19:886f14549adb42a8889095d445b8999z@thread.v2", scope_type="chat", status="granted")
    assert dashboard.conversation_name(other) != dashboard.conversation_name(chat), "two unnamed chats stay distinguishable"
    org = dashboard.build_org([chat], [], get_settings())
    assert [r.name for r in dashboard.consent_register(org, "org", "org", [chat])] == ["Unnamed group chat …b8310d"]


def test_detected_chats_belong_to_the_only_project_but_manual_ones_stay_unassigned():
    team = ProjectTeam(team_id="19:t@thread.tacv2", aad_group_id="g", name="Only project", member_aad_ids=[])
    detected = ConsentRecord(scope_id="19:d@thread.v2", scope_type="chat", status="granted", auto_installed=True)
    manual = ConsentRecord(scope_id="19:m@thread.v2", scope_type="chat", status="granted")
    org = dashboard.build_org([detected, manual], [team], get_settings())
    assert org.project_of("19:d@thread.v2") == dashboard.project_node_id(team.team_id)
    assert org.project_of("19:m@thread.v2") == dashboard.UNASSIGNED_PROJECT_ID
    other = ProjectTeam(team_id="19:u@thread.tacv2", aad_group_id="h", name="Another", member_aad_ids=[])
    two = dashboard.build_org([detected], [team, other], get_settings())   # ambiguous with two projects: do not guess
    assert two.project_of("19:d@thread.v2") == dashboard.UNASSIGNED_PROJECT_ID


# --- project matching for detected chats -----------------------------------------------


def test_matching_project_picks_the_biggest_overlap():
    store = MemoryConsentStore()
    store.save_project(ProjectTeam(team_id="t-a", name="A", member_aad_ids=["1", "2", "3"]))
    store.save_project(ProjectTeam(team_id="t-b", name="B", member_aad_ids=["3", "4", "5", "6"]))
    assert matching_project(store, ["3", "4", "5"]) == "t-b"
    assert matching_project(store, ["1", "2", "9"]) == "t-a"
    assert matching_project(store, ["9"]) is None and matching_project(MemoryConsentStore(), ["1"]) is None


# --- API -------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    mp = pytest.MonkeyPatch()
    mp.setattr(get_settings(), "dashboard_demo", True)
    audit.get_audit_log().clear()
    get_consent_store().clear()
    with TestClient(app) as c:  # the lifespan loads the demo data
        dash_api.clear_cache()
        yield c
    mp.undo()
    audit.get_audit_log().clear()
    get_consent_store().clear()
    dash_api.clear_cache()


def node_id(tree, name):
    stack = [tree]
    while stack:
        n = stack.pop()
        if n["name"] == name:
            return n["id"]
        stack += n["children"]
    raise AssertionError(name)


def test_api_hierarchy_and_org_summary(client):
    tree = client.get("/dashboard/hierarchy").json()
    assert tree["level"] == "org" and [a["name"] for a in tree["children"]] == ["Contoso Payments", "Northwind Retail"]
    s = client.get("/dashboard/summary").json()
    assert s["scope"]["id"] == "org" and s["kpis"]["messages_captured"] > 100 and s["kpis"]["conversations_total"] == 7
    assert any(n.startswith("Demo data") for n in s["notes"])
    assert 30 <= len(s["activity"]) <= 32
    assert s["data_source"] == "demo" and "knowledge_base" not in s, "the page is audit + consent only: no sample-dataset numbers"
    assert [r["topic"] for r in s["risk_impact"]] == ["Payment Gateway", "Settlement Batch Job"]
    assert len(s["governance"]) >= 5 and any(g["contains_content"] is False for g in s["governance"])


def test_api_account_and_project_summaries(client):
    tree = client.get("/dashboard/hierarchy").json()
    acc = client.get("/dashboard/summary", params={"level": "account", "id": node_id(tree, "Contoso Payments")}).json()
    assert acc["scope"]["name"] == "Contoso Payments" and len(acc["scope"]["children"]) == 2 and acc["data_source"] == "demo"
    prj = client.get("/dashboard/summary", params={"level": "project", "id": node_id(tree, "Storefront Revamp")}).json()
    assert prj["kpis"]["conversations_total"] == 2 and prj["kpis"]["conversations_pending"] == 1
    assert prj["kpis"]["messages_captured"] < acc["kpis"]["messages_captured"]


def test_api_validation_and_404(client):
    assert client.get("/dashboard/summary", params={"level": "project", "id": "prj_nope"}).status_code == 404
    assert client.get("/dashboard/summary", params={"level": "galaxy"}).status_code == 422
    assert client.get("/dashboard/summary", params={"since": "2026-10-10T00:00:00Z", "until": "2026-10-01T00:00:00Z"}).status_code == 422
    assert client.get("/dashboard/events", params={"limit": 0}).status_code == 422
    assert client.get("/dashboard/events", params={"outcome": "meh"}).status_code == 422


def test_api_events_are_anonymised_and_filterable(client):
    body = client.get("/dashboard/events", params={"limit": 1000}).json()
    assert body["total"] >= 500 and "consent.granted" in body["actions"]
    text = json.dumps(body)
    for name in NAMES:
        assert name not in text
    assert {i["actor_role"] for i in body["items"]} == {"member", "LegacyLens", "system"}
    ts = [i["timestamp"] for i in body["items"]]
    assert ts == sorted(ts, reverse=True)
    consent = client.get("/dashboard/events", params={"category": "consent", "limit": 1000}).json()
    assert consent["total"] > 0 and {i["action"].split(".")[0] for i in consent["items"]} == {"consent"}
    fails = client.get("/dashboard/events", params={"outcome": "failure"}).json()
    assert fails["total"] >= 4 and {i["outcome"] for i in fails["items"]} == {"failure"}
    assert len(client.get("/dashboard/events", params={"limit": 5}).json()["items"]) == 5
    tree = client.get("/dashboard/hierarchy").json()
    prj = client.get("/dashboard/events", params={"level": "project", "id": node_id(tree, "Merchant Onboarding"), "limit": 1000}).json()
    assert 0 < prj["total"] < body["total"] and all(i["scope_type"] in ("channel", "chat", "team", "onboarding") for i in prj["items"])


def test_api_consent_register(client):
    rows = client.get("/dashboard/consents").json()
    assert len(rows) == 8
    tree = client.get("/dashboard/hierarchy").json()
    one = client.get("/dashboard/consents", params={"level": "project", "id": node_id(tree, "Payments Platform")}).json()
    assert {r["name"] for r in one} == {"Payments Platform", "Gateway incident bridge", "Settlement ops"}
    assert next(r for r in one if r["name"] == "Settlement ops")["status"] == "paused"


def test_api_requires_the_admin_key_when_configured(client, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "admin_api_key", "k3y")
    for path in ("/dashboard/hierarchy", "/dashboard/summary", "/dashboard/events", "/dashboard/consents"):
        assert client.get(path).status_code == 401
        assert client.get(path, headers={"X-Admin-Key": "wrong"}).status_code == 401
        assert client.get(path, headers={"X-Admin-Key": "k3y"}).status_code == 200
    monkeypatch.setattr(s, "admin_api_key", "")
    monkeypatch.setattr(s, "live_m365", True)
    assert client.get("/dashboard/summary").status_code == 403  # live data is never served without a key


def test_api_shares_one_audit_read_between_identical_requests(client, monkeypatch):
    calls = []
    real = audit.get_audit_log().query
    monkeypatch.setattr(audit.get_audit_log(), "query", lambda *a: calls.append(1) or real(*a))
    dash_api.clear_cache()
    for _ in range(3):
        client.get("/dashboard/summary")
    client.get("/dashboard/events")
    assert len(calls) == 1


# --- data source: audit + consent only, never mock in live mode ----------------------------------


class FakeBlobAudit(BlobAuditLog):
    """Looks like the real Azure-backed trail to the API, but serves canned events and never touches Azure."""

    def __init__(self, events, fail=False):  # noqa: skip the real __init__ (it would open an Azure client)
        self._events, self._fail = events, fail
        self.queries = 0

    def query(self, since, until):
        self.queries += 1
        if self._fail:
            raise RuntimeError("AADSTS700082: secret-looking detail that must not leak")
        return [e for e in self._events if since <= e.timestamp <= until]

    def write(self, event):  # pragma: no cover - the dashboard must never write
        raise AssertionError("the dashboard must not write audit events")

    def flush(self):
        pass


class FakeTableConsents(TableConsentStore):
    def __init__(self, records, projects, fail=False):  # noqa: skip the real __init__
        self._records, self._projects, self._fail = records, projects, fail

    def list(self):
        if self._fail:
            raise RuntimeError("table read failed")
        return self._records

    def projects(self):
        return self._projects


def _live_admin(client, monkeypatch):
    """Live mode only serves callers with the admin key, so the test authenticates like a real caller."""
    monkeypatch.setattr(get_settings(), "admin_api_key", "k3y")
    monkeypatch.setitem(client.headers, "X-Admin-Key", "k3y")


def _live_stores(client, monkeypatch, *, fail_events=False, fail_tables=False):
    _live_admin(client, monkeypatch)
    now = datetime.now(timezone.utc)
    team = ProjectTeam(team_id="19:real@thread.tacv2", aad_group_id="aad-real", name="Real Team", member_aad_ids=["a", "b"])
    rec = ConsentRecord(scope_id="19:real@thread.tacv2", scope_type="channel", display_name="Real channel", status="granted",
                        team_id="aad-real", granted_by="Someone")
    scope = dict(scope_type="channel", scope_id=rec.scope_id, scope_name="Real channel")
    events = [
        AuditEvent(action="consent.granted", timestamp=now - timedelta(hours=3), source="teams", actor_name="Someone", **scope),
        AuditEvent(action="capture.message", timestamp=now - timedelta(hours=2), source="teams", actor_name="LegacyLens", count=3,
                   details={"messages_read": 3, "chunks": 3, "redactions": {"phone": 1}}, **scope),
        AuditEvent(action="ask.answered", timestamp=now - timedelta(hours=1), source="teams", actor_name="Someone",
                   item_ids=["a1", "a2"], details={"reply": "answer"}, **scope),
    ]
    audit_log = FakeBlobAudit(events, fail=fail_events)
    monkeypatch.setattr(audit, "get_audit_log", lambda: audit_log)
    monkeypatch.setattr(dash_api, "get_consent_store", lambda: FakeTableConsents([rec], [team], fail=fail_tables))
    monkeypatch.setattr(get_settings(), "live_m365", True)
    dash_api.clear_cache()
    return audit_log


def test_live_mode_shows_only_what_the_azure_audit_trail_and_consent_tables_hold(client, monkeypatch):
    # DASHBOARD_DEMO is on and the in-memory stores are full of demo data: none of it may appear.
    assert get_settings().dashboard_demo is True
    _live_stores(client, monkeypatch)
    s = client.get("/dashboard/summary").json()
    assert s["data_source"] == "azure" and not any("Demo" in n or "Offline" in n for n in s["notes"])
    k = s["kpis"]
    assert (k["conversations_total"], k["conversations_granted"]) == (1, 1)
    assert (k["messages_captured"], k["questions_answered"], k["interviews_approved"]) == (3, 1, 0)
    assert s["redactions"] == {"phone": 1} and s["consent_funnel"]["granted"] == 1
    assert [c["name"] for c in s["conversations"]] == ["Real channel"] and s["risk_impact"] == []
    tree = client.get("/dashboard/hierarchy").json()
    assert [(a["name"], [p["name"] for p in a["children"]]) for a in tree["children"]] == [("Default account", ["Real Team"])]
    assert client.get("/dashboard/events").json()["total"] == 3
    assert [r["name"] for r in client.get("/dashboard/consents").json()] == ["Real channel"]
    blob = json.dumps(s) + json.dumps(tree)
    for demo_name in ("Payments Platform", "Contoso Payments", "Northwind Retail", "Storefront Revamp", "Gateway incident bridge"):
        assert demo_name not in blob


@pytest.mark.parametrize("fail_events,fail_tables,what", [(True, False, "audit trail"), (False, True, "consent register")])
def test_live_mode_unreadable_storage_is_a_clear_503_never_a_fallback(client, monkeypatch, fail_events, fail_tables, what):
    _live_stores(client, monkeypatch, fail_events=fail_events, fail_tables=fail_tables)
    for path in ("/dashboard/summary", "/dashboard/events", "/dashboard/hierarchy", "/dashboard/consents"):
        r = client.get(path)
        if what == "audit trail" and path in ("/dashboard/hierarchy", "/dashboard/consents"):
            assert r.status_code == 200  # these two only need the consent tables
            continue
        assert r.status_code == 503, path
        detail = r.json()["detail"]
        assert what in detail and "az login" in detail and "RuntimeError" in detail
        assert "secret-looking" not in detail, "the underlying error text must not leak"


def test_live_mode_without_azure_storage_configured_refuses_instead_of_using_memory(client, monkeypatch):
    _live_admin(client, monkeypatch)
    monkeypatch.setattr(get_settings(), "live_m365", True)  # but the stores are still the in-memory ones
    dash_api.clear_cache()
    r = client.get("/dashboard/summary")
    assert r.status_code == 503 and "AZURE_STORAGE_ACCOUNT_URL" in r.json()["detail"]
    audit_log = FakeBlobAudit([])
    monkeypatch.setattr(audit, "get_audit_log", lambda: audit_log)  # audit is real now, consent register is not
    r = client.get("/dashboard/summary")
    assert r.status_code == 503 and "AZURE_TABLES_ACCOUNT_URL" in r.json()["detail"]
    assert audit_log.queries == 0, "nothing is read before the configuration is proven real"


def test_failed_reads_are_not_cached(client, monkeypatch):
    audit_log = _live_stores(client, monkeypatch, fail_events=True)
    assert client.get("/dashboard/summary").status_code == 503
    audit_log._fail = False
    assert client.get("/dashboard/summary").status_code == 200
    assert audit_log.queries == 2


def test_data_source_label_offline(client, monkeypatch):
    assert client.get("/dashboard/summary").json()["data_source"] == "demo"
    monkeypatch.setattr(get_settings(), "dashboard_demo", False)
    dash_api.clear_cache()
    s = client.get("/dashboard/summary").json()
    assert s["data_source"] == "memory" and any("Nothing is read from Azure" in n for n in s["notes"])


def test_the_dashboard_never_writes_to_the_audit_trail(client, monkeypatch):
    audit_log = _live_stores(client, monkeypatch)  # its write() raises if called
    for path in ("/dashboard/hierarchy", "/dashboard/summary", "/dashboard/events", "/dashboard/consents"):
        assert client.get(path).status_code == 200
    assert audit_log.queries >= 1
