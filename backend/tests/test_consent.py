"""Per-conversation consent, project-chat detection and live capture — all offline (mock Graph, memory store)."""

import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.bot import agent, consent
from app.bot.capture import html_to_text
from app.bot.chat_watch import chat_ids_from_notification, handle_new_chat
from app.config import get_settings
from app.graph import get_graph
from app.main import app
from app.models import ProjectTeam
from app.services.consent_store import get_consent_store
from app.services.graph_api import ChatInfo, GraphMessage, get_graph_api

BOT = {"id": "28:legacylens-bot", "name": "LegacyLens"}
TEAM = {"id": "19:team@thread.tacv2", "aadGroupId": "group-1", "name": "LegacyLensProject"}
ALICE = {"id": "29:alice", "name": "Alice", "aadObjectId": "aad-alice"}
BOB = {"id": "29:bob", "name": "Bob", "aadObjectId": "aad-bob"}
CHANNEL = {"id": "19:general@thread.tacv2", "conversationType": "channel", "name": "General"}
CHAT = {"id": "19:groupchat@thread.v2", "conversationType": "groupChat", "name": "Settlement fix"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/ingest")
        yield c


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    store, graph = get_consent_store(), get_graph_api()
    store.clear()
    graph.reset()
    spawned = []
    monkeypatch.setattr(agent, "_spawn", spawned.append)  # run background work explicitly below

    async def roster(_context, _team_id):
        return ["aad-alice", "aad-bob", "aad-carol"]

    monkeypatch.setattr(agent, "fetch_team_member_ids", roster)
    yield spawned


def run_spawned(spawned):
    while spawned:
        asyncio.run(spawned.pop(0))


def turn(client, conversation, **fields) -> list[dict]:
    body = {
        "type": "message", "id": fields.pop("id", "m1"), "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "from": fields.pop("frm", ALICE), "recipient": BOT, "conversation": conversation,
        "channelData": {"team": TEAM} if conversation["conversationType"] == "channel" else {},
        "deliveryMode": "expectReplies", **fields,
    }
    r = client.post("/api/messages", json=body)
    assert r.status_code == 200, r.text
    return r.json()["activities"]


def card_actions(activities) -> list[str]:
    cards = [a["attachments"][0]["content"] for a in activities if a.get("attachments")]
    return [x["data"]["verb"] for c in cards for x in c.get("actions", [])]


def install(client, conversation):
    return turn(client, conversation, type="conversationUpdate", membersAdded=[BOT])


def click(client, conversation, verb, frm=BOB):
    return turn(client, conversation, value={"verb": verb, "scope_id": conversation["id"]}, frm=frm, id="m-click")


def say(client, conversation, text, mid):
    return turn(client, conversation, text=text, id=mid, timestamp=datetime.now(timezone.utc).isoformat())


def test_install_in_channel_records_roster_and_asks_consent(client):
    acts = install(client, CHANNEL)
    assert card_actions(acts) == [consent.ALLOW, consent.DECLINE]
    rec = get_consent_store().get(CHANNEL["id"])
    assert rec.status == "pending" and rec.scope_type == "channel" and rec.team_id == "group-1"
    [project] = get_consent_store().projects()
    assert project.member_aad_ids == ["aad-alice", "aad-bob", "aad-carol"]


def test_nothing_is_captured_before_allow(client, fresh):
    install(client, CHANNEL)
    assert say(client, CHANNEL, "The settlement job needs --skip-recon when the bank file is late", "m-pre") == []
    assert fresh == []  # no capture task was even started


def test_allow_backfills_and_then_captures_live_messages(client, fresh):
    graph_api = get_graph_api()
    graph_api.messages[CHANNEL["id"]] = [GraphMessage(
        id="old-1", created=datetime(2026, 10, 1, tzinfo=timezone.utc), author_aad_id="aad-carol",
        author_name="Carol", html="<p>Rotate the <b>PG adapter</b> cert pin before Friday's release</p>")]
    install(client, CHANNEL)
    acts = click(client, CHANNEL, consent.ALLOW)
    assert "allowed LegacyLens" in acts[-1]["attachments"][0]["content"]["body"][0]["text"]
    assert get_consent_store().get(CHANNEL["id"]).granted_by == "Bob"
    run_spawned(fresh)  # backfill
    before = get_graph().stats().node_count
    say(client, CHANNEL, "Reminder: settlement batch restarts need the recon flag", "live-1")
    run_spawned(fresh)  # live capture
    assert get_graph().stats().node_count > before
    from app.bot.capture import LiveMessage, to_artifact
    old = to_artifact(LiveMessage(CHANNEL["id"], "channel", "", "old-1", "aad-carol", "Carol",
                                  datetime(2026, 10, 1, tzinfo=timezone.utc), "x"))
    assert get_graph().has_node(old.id)


def test_pause_stops_capture_and_resume_asks_again(client, fresh):
    install(client, CHANNEL)
    click(client, CHANNEL, consent.ALLOW)
    run_spawned(fresh)  # finish the (empty) backfill
    mention = {"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}
    [reply] = turn(client, CHANNEL, text="<at>LegacyLens</at> pause", entities=[mention], id="m-pause")
    assert "Paused" in reply["text"] and get_consent_store().get(CHANNEL["id"]).status == "paused"
    say(client, CHANNEL, "this should not be stored", "m-after-pause")
    assert fresh == []
    acts = turn(client, CHANNEL, text="<at>LegacyLens</at> resume", entities=[mention], id="m-resume")
    assert card_actions(acts) == [consent.ALLOW, consent.DECLINE]


def test_project_group_chat_is_detected_and_invited():
    store, graph = get_consent_store(), get_graph_api()
    store.save_project(ProjectTeam(team_id=TEAM["id"], member_aad_ids=["aad-alice", "aad-bob"]))
    graph.chats["c-project"] = ChatInfo("c-project", "group", "Settlement fix", ["aad-alice", "aad-bob", "aad-zed"])
    graph.chats["c-other"] = ChatInfo("c-other", "group", "Lunch", ["aad-alice", "aad-zed", "aad-yan"])
    graph.chats["c-1on1"] = ChatInfo("c-1on1", "oneOnOne", "", ["aad-alice", "aad-bob"])

    assert asyncio.run(handle_new_chat("c-project", store, graph)) == "invited"
    assert asyncio.run(handle_new_chat("c-other", store, graph)) == "not-project"
    assert asyncio.run(handle_new_chat("c-1on1", store, graph)) == "not-group"
    assert asyncio.run(handle_new_chat("c-project", store, graph)) == "known"
    assert graph.installed == {"c-project"}
    rec = store.get("c-project")
    assert rec.status == "pending" and rec.auto_installed


def test_declining_an_auto_installed_chat_removes_the_app(client, fresh):
    store, graph = get_consent_store(), get_graph_api()
    store.save_project(ProjectTeam(team_id=TEAM["id"], member_aad_ids=["aad-alice", "aad-bob"]))
    graph.chats[CHAT["id"]] = ChatInfo(CHAT["id"], "group", "Settlement fix", ["aad-alice", "aad-bob"])
    asyncio.run(handle_new_chat(CHAT["id"], store, graph))
    acts = install(client, CHAT)  # Teams tells the bot it was added -> card
    card = acts[0]["attachments"][0]["content"]
    assert "members of the project" in card["body"][1]["text"]
    click(client, CHAT, consent.DECLINE)
    run_spawned(fresh)
    assert store.get(CHAT["id"]).status == "declined" and CHAT["id"] in graph.uninstalled


def test_webhook_validation_and_client_state(client, monkeypatch):
    r = client.post("/webhooks/graph?validationToken=abc%20123")
    assert r.status_code == 200 and r.text == "abc 123" and r.headers["content-type"].startswith("text/plain")
    monkeypatch.setattr(get_settings(), "graph_webhook_client_state", "s3cret")
    good = {"clientState": "s3cret", "changeType": "created", "resource": "chats('19:x@thread.v2')", "resourceData": {"id": "19:x@thread.v2"}}
    bad = {**good, "clientState": "nope"}
    assert chat_ids_from_notification({"value": [good, bad]}, "s3cret") == ["19:x@thread.v2"]
    assert chat_ids_from_notification({"value": [good]}, "") == []  # no secret configured -> trust nothing
    assert client.post("/webhooks/graph", json={"value": [bad]}).status_code == 202


def test_html_to_text_strips_teams_markup():
    assert html_to_text("<p>Hi <at id='0'>LegacyLens</at>, use <b>gwctl</b>&nbsp;pool-status</p><p>then restart</p>") == \
        "Hi , use gwctl\xa0pool-status\nthen restart"


def test_scan_existing_chats_uses_the_same_rules():
    from app.bot.chat_watch import scan_existing_chats
    store, graph = get_consent_store(), get_graph_api()
    store.save_project(ProjectTeam(team_id=TEAM["id"], member_aad_ids=["aad-alice", "aad-bob"]))
    graph.chats["old-project"] = ChatInfo("old-project", "group", "", ["aad-alice", "aad-bob", "aad-x"])
    graph.chats["old-other"] = ChatInfo("old-other", "group", "", ["aad-alice", "aad-x", "aad-y"])
    assert asyncio.run(scan_existing_chats(store, graph)) == {"old-project": "invited", "old-other": "not-project"}
    assert asyncio.run(scan_existing_chats(store, graph))["old-project"] == "known"
