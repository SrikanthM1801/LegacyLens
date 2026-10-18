"""Dashboard: SharePoint knowledge sources and onboarding assessments (aggregation, scoping, privacy)."""

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.models import AuditEvent, ConsentRecord, ProjectTeam
from app.services import dashboard
from app.services.audit_demo import DEMO_ACCOUNTS, build_demo

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=timezone.utc)
SINCE = NOW - timedelta(days=30)
CANDIDATES = ("Meera Iyer", "Sam Carter", "Nia Okafor", "Tom Becker", "Ida Lindqvist", "Raj Patel")
EDITORS = ("Priya Nair", "Ravi Kumar", "Arjun Mehta", "Meera Iyer", "Dana Whitfield")


@pytest.fixture(scope="module")
def demo():
    events, records, projects = build_demo(NOW)
    org = dashboard.build_org(records, projects, get_settings(), mapping=DEMO_ACCOUNTS)
    return org, events, records


def summary(demo, level="org", node_id="org"):
    org, events, records = demo
    return dashboard.summarize(org, level, node_id, events, records, SINCE, NOW, source="demo")


def node(demo, name):
    org = demo[0]
    stack = [org.root]
    while stack:
        n = stack.pop()
        if n.name == name:
            return n
        stack += n.children
    raise AssertionError(name)


def ev(action, minutes_ago=5, **kw):
    return AuditEvent(action=action, timestamp=NOW - timedelta(minutes=minutes_ago), **kw)


# --- a SharePoint site is a source, not a conversation -----------------------------------------------


def test_a_sharepoint_site_is_not_counted_as_a_conversation(demo):
    org, _, records = demo
    assert any(r.scope_type == "site" for r in records)
    assert org.root.conversations == 7                                    # 7 chats/channels, the site is not one of them
    s = summary(demo)
    assert s.kpis.conversations_total == 7 and s.kpis.conversations_granted == 4
    assert s.sources.sites == 1
    assert org.project_of("site-demo-payments") is None                  # not assigned to any project
    assert dashboard.UNASSIGNED_PROJECT_ID not in org.names              # and it does not create "Unassigned conversations"


def test_a_site_only_org_has_no_conversations_and_no_unassigned_project():
    site = ConsentRecord(scope_id="site1", scope_type="site", display_name="Intranet", status="granted", granted_by="admin configuration")
    org = dashboard.build_org([site], [], get_settings())
    assert org.root.conversations == 0 and org.root.children == []
    s = dashboard.summarize(org, "org", "org", [], [site], SINCE, NOW)
    assert s.kpis.conversations_total == 0 and s.sources.sites == 1


def test_admin_site_consent_is_not_a_persons_allow_decision(demo):
    s = summary(demo)
    assert s.consent_funnel["requested"] == 7, "the site's consent.granted by admin configuration is not in the funnel"
    assert s.consent_funnel["granted"] == 5 and s.consent_funnel["approval_rate"] == 0.833


# --- SharePoint aggregation --------------------------------------------------------------------------


def test_sources_count_each_file_once_however_often_it_was_loaded(demo):
    _, events, _ = demo
    s = summary(demo)
    src = s.sources
    files = [e for e in events if e.action == "capture.file"]
    ident = lambda e: e.item_ids[0] if e.item_ids else f"{e.details['path']}/{e.details['file']}"  # noqa: E731
    ok = [e for e in files if e.outcome == "success"]
    added = {ident(e) for e in ok if e.details["change"] == "added"}
    modified = {ident(e) for e in ok if e.details["change"] == "modified"}
    assert src.files_added == len(added) == 10
    assert src.files_modified == len(modified) and 0 < len(modified) <= 10
    assert sum(e.details["change"] == "modified" for e in ok) > len(modified), "events outnumber files: files are counted once"
    assert src.files_deleted == 1 and (src.files_failed, src.files_skipped) == (1, 1)
    syncs = [e for e in events if e.action == "sync.sharepoint"]
    assert src.sync_runs == len(syncs) and src.sync_failures == sum(e.outcome == "failure" for e in syncs) >= 2
    assert src.last_sync is not None and src.last_sync_outcome == "success"
    assert sum(src.by_type.values()) == len(added | modified)
    assert {"docx", "pdf", "xlsx", "pptx", "md", "txt", "html"} >= set(src.by_type)
    assert sum(d.files_synced for d in s.activity) >= len(added), "the chart counts each file once per day"


def test_a_rebuild_that_reloads_every_file_does_not_double_the_counts():
    org = dashboard.build_org([], [], get_settings())
    site = dict(scope_type="site", scope_id="s1", scope_name="Site")

    def load(minutes, name, chunks, change="added"):
        return ev("capture.file", minutes, source="sharepoint", count=chunks, item_ids=[f"art_sp_{name}"],
                  details={"change": change, "file": f"{name}.docx", "path": "Docs", "status": "loaded"}, **site)

    first = [load(300, "a", 5), load(299, "b", 7)]
    reload_all = [load(60, "a", 6), load(59, "b", 7)]            # the rebuild re-adds both files (chunks of 'a' changed)
    changed = [load(10, "a", 8, "modified")]
    src = dashboard.summarize(org, "org", "org", first + reload_all + changed, [], SINCE, NOW).sources
    assert (src.files_added, src.files_modified) == (2, 1)       # 2 distinct files, one of them also edited; not 4 and 1
    assert src.chunks_indexed == 8 + 7, "chunks come from each file's latest load, not from every load"
    assert src.by_type == {"docx": 2}


def test_deleted_files_leave_no_chunks_behind():
    org = dashboard.build_org([], [], get_settings())
    base = dict(source="sharepoint", scope_type="site", scope_id="s1")
    events = [
        ev("capture.file", 30, count=9, item_ids=["art_sp_x"], details={"change": "added", "file": "x.pdf", "path": "", "status": "loaded"}, **base),
        ev("capture.file", 5, count=0, item_ids=["art_sp_x"], details={"change": "deleted", "file": "x.pdf", "path": "", "status": "deleted"}, **base),
    ]
    src = dashboard.summarize(org, "org", "org", events, [], SINCE, NOW).sources
    assert (src.files_added, src.files_deleted, src.chunks_indexed) == (1, 1, 0)


def test_sources_recent_lists_are_newest_first_and_capped(demo):
    src = summary(demo).sources
    assert 0 < len(src.recent_syncs) <= dashboard.RECENT_SYNCS and len(src.recent_files) <= dashboard.RECENT_FILES
    for rows in (src.recent_syncs, src.recent_files):
        stamps = [r.timestamp for r in rows]
        assert stamps == sorted(stamps, reverse=True)
    assert [r.reason for r in src.rebuilds] == ["first start without sample data"]
    assert {f.change for f in src.recent_files} <= {"added", "modified", "deleted"}


def test_a_failed_sync_says_why(demo):
    runs = summary(demo).sources.recent_syncs
    failed = [r for r in runs if r.outcome == "failure"]
    assert any(r.error == "403 Forbidden" for r in failed), "the run that never reached SharePoint carries its reason"
    assert all(r.error is None for r in runs if r.outcome == "success")
    assert dashboard._sync_error({"errors": ["Corrupt export.pdf: could not read the PDF"]}).startswith("Corrupt export.pdf")
    assert dashboard._sync_error({"error": "x" * 500}) == "x" * 160 and dashboard._sync_error({"errors": []}) is None


def test_sources_are_org_level_only(demo):
    assert summary(demo).sources is not None
    pp = node(demo, "Payments Platform")
    acc = node(demo, "Contoso Payments")
    for level, n in (("project", pp), ("account", acc)):
        s = summary(demo, level, n.id)
        assert s.sources is None and all(d.files_synced == 0 for d in s.activity)
        assert any("SharePoint files" in note for note in s.notes)


def test_a_failed_sync_and_failed_file_are_visible_as_failures(demo):
    s = summary(demo)
    failed_actions = {e.action for e in s.recent_failures}
    assert {"capture.file", "sync.sharepoint"} & failed_actions or s.kpis.failures >= 6


def test_runbook_replies_are_counted_apart_from_commands():
    org = dashboard.build_org([], [], get_settings())
    events = [ev("ask.answered", details={"reply": r}) for r in ("runbook", "runbook", "help", "topics", "status", "answer")]
    k = dashboard.summarize(org, "org", "org", events, [], SINCE, NOW).kpis
    assert (k.runbooks_requested, k.commands, k.questions_answered) == (2, 3, 1)


# --- onboarding aggregation --------------------------------------------------------------------------


def test_onboarding_funnel_counts_sessions_not_events(demo):
    o = summary(demo).onboarding
    assert (o.assigned, o.started_learning, o.assessments_started, o.completed, o.results_posted) == (5, 5, 4, 4, 4)
    assert o.failed == 1
    assert o.learning_questions == 4 + 3 + 2 + 4 + 2 and set(o.learning_by_kind) == {"ask", "topics", "runbook", "risks"}
    assert o.learning_unanswered == 3, "the 'I could not find this' gaps in the knowledge base"


def test_readiness_spread_and_average_appear_only_for_a_group_of_three_or_more(demo):
    org = summary(demo).onboarding
    assert org.readiness == {"ready": 1, "almost": 2, "needs_more_time": 1} and org.average_overall == 64 and not org.suppressed
    pp = summary(demo, "project", node(demo, "Payments Platform").id).onboarding
    assert pp.completed == 3 and pp.readiness == {"ready": 1, "almost": 1, "needs_more_time": 1} and pp.average_overall == 61
    mo = summary(demo, "project", node(demo, "Merchant Onboarding").id).onboarding
    assert mo.completed == 1 and mo.suppressed is True and mo.readiness is None and mo.average_overall is None
    sf = summary(demo, "project", node(demo, "Storefront Revamp").id).onboarding
    assert (sf.assigned, sf.completed, sf.suppressed, sf.readiness) == (1, 0, False, None)
    contoso = summary(demo, "account", node(demo, "Contoso Payments").id).onboarding
    assert contoso.completed == 4 and contoso.readiness is not None, "an account is a big enough group again"


def test_a_suppressed_group_leaks_nothing_in_the_serialised_summary(demo):
    mo = summary(demo, "project", node(demo, "Merchant Onboarding").id)
    blob = mo.onboarding.model_dump_json()
    assert '"readiness":null' in blob and '"average_overall":null' in blob
    for value in ("almost", "needs_more_time", "ready", "71", "Tom Becker"):
        assert f'"{value}"' not in blob and f": {value}," not in blob


def test_onboarding_rolls_up_by_the_project_it_records_not_by_name(demo):
    org, events, _ = demo
    pp = node(demo, "Payments Platform")
    mine = dashboard.filter_events(org, "project", pp.id, events, SINCE, NOW)
    onboarding = [e for e in mine if e.action.startswith("onboarding.")]
    assert onboarding and {e.details["project_team_id"] for e in onboarding} == {"19:pp-general@thread.tacv2"}
    old = ev("onboarding.assigned", scope_type="onboarding", scope_id="onb_old", details={"project": "Payments Platform"})  # no team id
    assert dashboard.filter_events(org, "project", pp.id, [old], SINCE, NOW) == []
    assert dashboard.filter_events(org, "org", "org", [old], SINCE, NOW) == [old], "still counted in the organisation"


def test_onboarding_failures_count_but_do_not_count_as_progress():
    org = dashboard.build_org([], [], get_settings())
    events = [ev("onboarding.assigned", scope_id="a"), ev("onboarding.assigned", scope_id="b", outcome="failure"),
              ev("onboarding.evaluated", scope_id="a", outcome="failure", details={"error": "evaluation failed: X"})]
    o = dashboard.summarize(org, "org", "org", events, [], SINCE, NOW).onboarding
    assert (o.assigned, o.completed, o.failed) == (1, 0, 2)


# --- privacy -----------------------------------------------------------------------------------------


def test_public_events_hide_newcomers_scores_topics_and_file_editors(demo):
    _, events, _ = demo
    public = [dashboard.public_event(e) for e in events if e.action.startswith(("onboarding.", "capture.file"))]
    assert len(public) > 40
    text = json.dumps([p.model_dump(mode="json") for p in public])
    for name in CANDIDATES + EDITORS:
        assert name not in text, name
    keys = set().union(*(p.details.keys() for p in public))   # keys, not values: "kind": "topics" is a harmless value
    assert not keys & {"readiness", "overall", "journey_score", "answer_scores", "topics", "topic", "channel_id",
                       "project_team_id", "modified_by"}, keys
    assert {p.scope_name for p in public if p.action.startswith("onboarding.")} == {"a new joiner"}
    assert {p.action.split(".")[1] for p in public if p.action.startswith("onboarding.")} >= {"assigned", "question_asked", "answered", "evaluated"}


def test_the_whole_summary_names_no_candidate_and_no_editor(demo):
    org, events, records = demo
    for level, n in (("org", org.root), ("account", node(demo, "Contoso Payments")), ("project", node(demo, "Payments Platform"))):
        text = summary(demo, level, n.id).model_dump_json()
        for name in CANDIDATES + EDITORS:
            assert name not in text, (level, name)


def test_admin_configuration_is_a_system_actor_not_a_member():
    e = dashboard.public_event(ev("consent.granted", actor_name="admin configuration", scope_type="site"))
    assert e.actor_role == "system"
    assert dashboard.public_event(ev("consent.granted", actor_name="Alice", actor_aad_id="x")).actor_role == "member"


def test_onboarding_event_details_keep_only_what_is_not_about_the_person():
    e = dashboard.public_event(ev("onboarding.evaluated", scope_name="Meera Iyer", details={
        "project_team_id": "19:x", "readiness": "ready", "overall": 91, "journey_score": 5, "answer_scores": [5, 5],
        "model": "gpt-4o", "error": "evaluation failed: TimeoutError"}))
    assert e.scope_name == "a new joiner" and e.details == {"model": "gpt-4o", "error": "evaluation failed: TimeoutError"}
    q = dashboard.public_event(ev("onboarding.question_asked", scope_name="Meera Iyer", details={"kind": "ask", "answered": False, "topics": ["Payment Gateway"]}))
    assert q.details == {"kind": "ask", "answered": False}
