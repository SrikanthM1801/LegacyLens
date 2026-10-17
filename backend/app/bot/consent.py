"""
Per-conversation consent, asked in the conversation itself (Plan §11.6, §15).

When LegacyLens arrives in a channel or group chat it posts a card: "May LegacyLens learn from
this conversation?" [Allow] [Not now]. Until someone clicks Allow, nothing from that
conversation is stored. Allow -> record `granted`, backfill recent messages, capture new ones.
Not now -> record `declined`; if we installed ourselves (auto-detected project chat) we leave.
`@LegacyLens pause` -> `paused`, capture stops. This module builds the cards and applies the
decisions; bot/agent.py wires them to Teams activities.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from app.bot.capture import LiveMessage, capture
from app.config import get_settings
from app.models import ConsentRecord
from app.services.consent_store import ConsentStore
from app.services.graph_api import GraphError, GraphService

log = logging.getLogger(__name__)

ALLOW = "legacylens_consent_allow"
DECLINE = "legacylens_consent_decline"


def consent_card(record: ConsentRecord) -> dict:
    where = "this channel" if record.scope_type == "channel" else "this group chat"
    why = (
        "I noticed this chat is between members of the project, so I joined to ask first."
        if record.auto_installed else
        "I was just added here."
    )
    return {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.5",
        "body": [
            {"type": "TextBlock", "text": f"May LegacyLens learn from {where}?", "weight": "Bolder", "size": "Medium", "wrap": True},
            {"type": "TextBlock", "text": why, "wrap": True, "isSubtle": True},
            {"type": "TextBlock", "wrap": True, "text": (
                "If you allow it, I'll read messages here (including the last few) to build cited project "
                "runbooks and answer questions. Phone numbers, card numbers and external emails are redacted "
                "before anything is stored, nothing is published without review, and anyone can type "
                "**@LegacyLens pause** to stop."
            )},
        ],
        "actions": [
            {"type": "Action.Submit", "title": "Allow", "style": "positive",
             "data": {"verb": ALLOW, "scope_id": record.scope_id}},
            {"type": "Action.Submit", "title": "Not now",
             "data": {"verb": DECLINE, "scope_id": record.scope_id}},
        ],
    }


def decided_card(record: ConsentRecord) -> dict:
    text = {
        "granted": f"✅ {record.granted_by or 'Someone'} allowed LegacyLens to learn from this conversation. "
                   "Type **@LegacyLens pause** any time to stop.",
        "declined": f"🚫 {record.granted_by or 'Someone'} chose not now. LegacyLens won't read this conversation.",
        "paused": "⏸️ LegacyLens is paused here and isn't learning from this conversation.",
    }.get(record.status, "LegacyLens is waiting for a decision.")
    return {
        "type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5",
        "body": [{"type": "TextBlock", "text": text, "wrap": True}],
    }


def decide(store: ConsentStore, scope_id: str, allow: bool, by_name: str, by_aad_id: Optional[str]) -> Optional[ConsentRecord]:
    rec = store.get(scope_id)
    if rec is None:
        return None
    return store.save(rec.model_copy(update={
        "status": "granted" if allow else "declined", "granted_by": by_name, "granted_by_aad_id": by_aad_id,
    }))


async def backfill(record: ConsentRecord, graph: GraphService) -> int:
    """Pull recent history once consent is granted. Failures are logged, never raised to the user."""
    top = get_settings().backfill_messages
    try:
        if record.scope_type == "chat":
            msgs = await graph.chat_messages(record.scope_id, top)
        elif record.team_id and record.channel_id:
            msgs = await graph.channel_messages(record.team_id, record.channel_id, top)
        else:
            return 0
    except GraphError as e:
        log.warning("backfill skipped for a %s: %s", record.scope_type, e)
        return 0
    live = [
        LiveMessage(scope_id=record.scope_id, scope_type=record.scope_type, scope_name=record.display_name,
                    message_id=m.id, author_aad_id=m.author_aad_id, author_name=m.author_name,
                    timestamp=m.created, text=m.html)
        for m in msgs
    ]
    return await asyncio.to_thread(capture, live)


async def leave_if_auto_installed(record: ConsentRecord, graph: GraphService) -> None:
    if record.scope_type == "chat" and record.auto_installed:
        try:
            await graph.uninstall_app_from_chat(record.scope_id)
        except GraphError as e:
            log.warning("could not leave declined chat: %s", e)
