"""Teams bot: replies (no SDK) and a full /api/messages turn through the Agents SDK, offline."""

import pytest
from fastapi.testclient import TestClient

from app.bot import replies
from app.main import app

BOT = {"id": "28:legacylens-bot", "name": "LegacyLens"}


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        c.post("/ingest")
        yield c


def activity(text: str, mention: bool = True, **extra) -> dict:
    """A Teams channel message as Bot Service would post it; expectReplies returns replies inline."""
    body = {
        "type": "message",
        "id": "1",
        "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/teams/",
        "from": {"id": "29:user", "name": "Test User"},
        "recipient": BOT,
        "conversation": {"id": "19:channel@thread.tacv2", "conversationType": "channel"},
        "deliveryMode": "expectReplies",
        "text": f"<at>LegacyLens</at> {text}" if mention else text,
        "entities": [{"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}] if mention else [],
        **extra,
    }
    return body


def bot_says(client, body: dict) -> list[str]:
    r = client.post("/api/messages", json=body)
    assert r.status_code == 200, r.text
    return [a.get("text", "") for a in r.json()["activities"] if a.get("type") != "typing"]


def test_help_and_empty_mention_show_welcome():
    assert replies.reply_for("") == replies.WELCOME
    assert replies.reply_for(" Help ") == replies.WELCOME


def test_risks_lists_topics_not_people(client):
    text = replies.reply_for("risks")
    assert text.startswith("**Top knowledge risks**")
    assert "1. 🔴" in text
    for person in ("Ravi", "Priya", "Arjun", "Meera"):
        assert person not in text


def test_pause_never_claims_to_store_anything():
    assert "not learning" in replies.reply_for("pause")


def test_question_reply_carries_sources(client):
    text = replies.reply_for("How did Ravi usually handle gateway timeouts?")
    assert "`gwctl pool-status`" in text
    assert "Sources" not in text and "[1]" in text  # markers stay, the list is gone


def test_api_messages_full_turn_strips_mention_and_answers(client):
    [reply] = bot_says(client, activity("risks"))
    assert reply.startswith("**Top knowledge risks**")


def test_api_messages_question_turn(client):
    [reply] = bot_says(client, activity("What do I do if the bank file is late?"))
    assert "--skip-recon" in reply and "Sources" not in reply and "[1]" in reply


def test_welcome_when_bot_is_added(client):
    body = activity("", mention=False, type="conversationUpdate", membersAdded=[BOT])
    body.pop("text")
    welcome, card = client.post("/api/messages", json=body).json()["activities"]
    assert welcome["text"] == replies.WELCOME
    assert card["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"


def test_probe_route(client):
    assert client.get("/api/messages").json() == {"status": "ok"}


def test_citation_markers_link_to_their_source_file():
    from app.models import AskResponse, Citation

    resp = AskResponse(answer="- Uses AKS [1][2]\n- Fixed price [2]", citations=[
        Citation(artifact_id="a", snippet="s", url_or_path="https://contoso.sharepoint.com/sites/p/Shared Documents/SAD.docx"),
        Citation(artifact_id="b", snippet="s", url_or_path="data/sample/emails.json"),
    ])
    text = replies.format_answer(resp)
    assert "[\\[1\\]](https://contoso.sharepoint.com/sites/p/Shared%20Documents/SAD.docx)" in text
    assert text.count("[2]") == 2 and "Sources" not in text  # no link for a local path, marker kept


def test_topics_and_runbook_commands(client):
    topics = replies.reply_for("topics")
    assert topics.startswith("**What I know about**") and "runbook" in topics
    text, cited, kind = replies.respond("runbook payment gateway")
    assert kind == "runbook" and cited and "## " not in text and "Sources" not in text
    assert "[1]" in text  # claims keep their markers
    assert replies.respond("runbook no-such-topic-xyz")[2] == "no_answer"
    assert "Which topic?" in replies.reply_for("runbook")


def test_status_command_reports_consent_and_knowledge(client):
    mention = {"type": "mention", "mentioned": BOT, "text": "<at>LegacyLens</at>"}
    [reply] = bot_says(client, activity("status", entities=[mention]))
    assert reply.startswith("**LegacyLens status**") and ("haven't asked" in reply or "Waiting" in reply)
    assert "SharePoint files" in reply and "topics" in reply


def test_help_lists_every_manifest_command():
    import json
    from pathlib import Path

    manifest = json.loads((Path(__file__).resolve().parents[2] / "bot/manifest/manifest.template.json").read_text())
    commands = {c["title"] for cl in manifest["bots"][0]["commandLists"] for c in cl["commands"]}
    assert {"ask", "risks", "topics", "runbook", "status", "pause", "resume", "help", "assign", "start",
            "handover", "away", "link"} == commands
    assert all(f"{c}**" in replies.WELCOME or f"**{c}" in replies.WELCOME for c in commands)  # help mentions each


def test_questions_show_typing_but_commands_do_not(client):
    types = lambda text: [a["type"] for a in client.post("/api/messages", json=activity(text)).json()["activities"]]
    assert types("What do I do if the bank file is late?")[0] == "typing"
    assert "typing" not in types("risks")


def test_admin_opens_the_dashboard_with_deep_links(client):
    card, fallback = replies.dashboard_reply("admin", "https://tunnel.example/")
    assert card["actions"][0] == {"type": "Action.OpenUrl", "title": "Open dashboard", "url": "https://tunnel.example/app/"}
    assert "https://tunnel.example/app/" in fallback
    card, _ = replies.dashboard_reply("admin handovers", "https://tunnel.example")
    assert card["actions"][0]["url"] == "https://tunnel.example/app/?tab=interviews"
    top = __import__("app.services.risk_store", fromlist=["x"]).get_risks().items[0]
    card, _ = replies.dashboard_reply(f"dashboard {top.topic.lower()}", "https://tunnel.example")
    assert card["actions"][0]["url"] == f"https://tunnel.example/app/?tab=risks&risk={top.id}"
    card, fallback = replies.dashboard_reply("admin no-such-topic-xyz", "")
    assert card["actions"][0]["url"] == "http://localhost:8000/app/" and "don't know a topic" in fallback
    text = str(card)
    for person in ("Ravi", "Priya", "Arjun", "Meera"):  # topics, never people
        assert person not in text


def test_admin_command_through_the_bot(client):
    r = client.post("/api/messages", json=activity("admin risks"))
    [msg] = [a for a in r.json()["activities"] if a.get("type") == "message"]
    card = msg["attachments"][0]["content"]
    assert card["actions"][0]["url"].endswith("/app/?tab=risks")
    assert "typing" not in [a["type"] for a in r.json()["activities"]]


def test_admin_card_never_waits_for_a_risk_recompute(client, monkeypatch):
    from app.services import risk_store

    monkeypatch.setattr(risk_store, "peek", lambda: None)  # cold cache
    monkeypatch.setattr(risk_store, "get_risks", lambda: (_ for _ in ()).throw(AssertionError("must not compute")))
    card, _ = replies.dashboard_reply("admin payment gateway", "https://tunnel.example")
    assert card["actions"][0]["url"] == "https://tunnel.example/app/"
    assert "being computed" in str(card)
