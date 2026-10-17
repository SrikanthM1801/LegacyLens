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
    return [a.get("text", "") for a in r.json()["activities"]]


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
    assert "**Sources**" in text and "1. **" in text


def test_api_messages_full_turn_strips_mention_and_answers(client):
    [reply] = bot_says(client, activity("risks"))
    assert reply.startswith("**Top knowledge risks**")


def test_api_messages_question_turn(client):
    [reply] = bot_says(client, activity("What do I do if the bank file is late?"))
    assert "--skip-recon" in reply and "**Sources**" in reply


def test_welcome_when_bot_is_added(client):
    body = activity("", mention=False, type="conversationUpdate", membersAdded=[BOT])
    body.pop("text")
    assert bot_says(client, body) == [replies.WELCOME]


def test_probe_route(client):
    assert client.get("/api/messages").json() == {"status": "ok"}
