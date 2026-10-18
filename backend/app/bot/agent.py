"""
Microsoft 365 Agents SDK wiring for the LegacyLens Teams bot.

Azure Bot Service posts every Teams activity to `POST /api/messages` (api/bot.py). This module
builds the SDK pieces once: an MSAL connection for the single-tenant bot identity (BOT_APP_ID /
BOT_APP_PASSWORD / BOT_APP_TENANT_ID), a CloudAdapter that sends replies back through Bot
Service, and an AgentApplication whose handlers stay thin:
  * bot added to a channel / group chat -> post the consent card (bot/consent.py); in a team,
    also record the project roster that chat detection uses (bot/chat_watch.py);
  * consent card click -> record the decision, then backfill or leave;
  * message that @mentions us (or 1:1) -> answer via `replies.py`; `pause` / `resume` manage consent;
  * any other message -> captured only if this conversation granted consent (bot/capture.py).

We use hosting-core directly with a ~20-line FastAPI shim (`process`) rather than
microsoft-agents-hosting-fastapi, because that package requires Starlette 1.x and our FastAPI
pin ships Starlette 0.41. With no BOT_APP_ID set (local dev, tests) the bot runs anonymously,
which only works for callers that read replies from the HTTP response (deliveryMode
"expectReplies") such as the Agents Playground or our tests.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Optional

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from microsoft_agents.authentication.msal import MsalConnectionManager
from microsoft_agents.hosting.core import (
    AgentApplication,
    AgentAuthConfiguration,
    HttpAdapterBase,
    MemoryStorage,
    TurnContext,
    TurnState,
)
from microsoft_agents.hosting.core.authorization.jwt import _authorize_request

from microsoft_agents.activity import Activity, Attachment

from app.bot import consent, departure, onboarding, replies
from app.bot.capture import LiveMessage, capture
from app.config import Settings, get_settings
from app.models import ConsentRecord, ProjectTeam
from app.services import audit
from app.services.consent_store import get_consent_store, is_granted
from app.services.graph_api import get_graph_api

log = logging.getLogger(__name__)


class _FastApiRequest:
    """Adapts a FastAPI Request to the SDK's HttpRequestProtocol."""

    def __init__(self, request: Request, claims_identity: Any):
        self._request = request
        self._claims = claims_identity

    @property
    def method(self) -> str:
        return self._request.method

    @property
    def headers(self):
        return self._request.headers

    async def json(self):
        return await self._request.json()

    def get_claims_identity(self):
        return self._claims

    def get_path_param(self, name: str) -> str:
        return self._request.path_params.get(name, "")


class CloudAdapter(HttpAdapterBase):
    """HttpAdapterBase bound to FastAPI request/response types."""


@dataclass
class Bot:
    app: AgentApplication
    adapter: CloudAdapter
    auth_config: AgentAuthConfiguration

    async def process(self, request: Request) -> Response:
        """Validate the Bot Service JWT, run the turn, return the SDK's HTTP response."""
        identity = await _authorize_request(request.headers.get("Authorization"), self.auth_config)
        if not hasattr(identity, "get_claim_value"):  # an HttpResponse describing the auth failure
            return JSONResponse(content=identity.body, status_code=identity.status_code)
        resp = await self.adapter.process_request(_FastApiRequest(request, identity), self.app)
        if resp.body is not None:
            return JSONResponse(content=resp.body, status_code=resp.status_code, headers=resp.headers)
        return Response(status_code=resp.status_code, headers=resp.headers)


def _sdk_config(s: Settings) -> dict:
    """The nested dict `load_configuration_from_env` would build from CONNECTIONS__... env vars."""
    if not s.bot_app_id:
        settings: dict[str, Any] = {"ANONYMOUS_ALLOWED": True}
    else:
        settings = {
            "AUTHTYPE": "ClientSecret",
            "CLIENTID": s.bot_app_id,
            "CLIENTSECRET": s.bot_app_password,
            "TENANTID": s.bot_app_tenant_id,
        }
    return {"CONNECTIONS": {"SERVICE_CONNECTION": {"SETTINGS": settings}}, "CONNECTIONSMAP": [], "AGENTAPPLICATION": {}}


#: Background work (backfill, capture) started from a turn; kept referenced until done.
_tasks: set[asyncio.Task] = set()


def _spawn(coro) -> None:
    task = asyncio.create_task(coro)
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def _channel_data(activity) -> dict:
    cd = activity.channel_data or {}
    return cd.model_dump(by_alias=True, exclude_none=True) if hasattr(cd, "model_dump") else dict(cd)


def scope_of(activity) -> tuple[str, str]:
    """(scope_id, scope_type) for the conversation; channel thread suffixes are dropped."""
    conv = activity.conversation
    kind = {"channel": "channel", "groupChat": "chat"}.get(conv.conversation_type or "", "personal")
    return conv.id.split(";")[0], kind


def _actor(activity) -> dict:
    who = activity.from_property
    return {"actor_name": who.name if who else None, "actor_aad_id": who.aad_object_id if who else None}


def _scope(rec_or_activity) -> dict:
    if isinstance(rec_or_activity, ConsentRecord):
        r = rec_or_activity
        return {"scope_type": r.scope_type, "scope_id": r.scope_id, "scope_name": r.display_name or None}
    scope_id, kind = scope_of(rec_or_activity)
    return {"scope_type": kind, "scope_id": scope_id, "scope_name": rec_or_activity.conversation.name}


def _card(card: dict) -> Activity:
    return Activity(type="message", attachments=[
        Attachment(content_type="application/vnd.microsoft.card.adaptive", content=card)])


async def fetch_team_member_ids(context: TurnContext, team_id: str) -> list[str]:
    """Project roster from the Bot Framework roster API (no Graph permission needed)."""
    connector = context.turn_state.get("ConnectorClient")
    members = await connector.conversations.get_conversation_members(team_id)
    # Teams' roster API names the Entra id `objectId`, which the SDK keeps as an extra field.
    ids = [m.aad_object_id or (m.model_extra or {}).get("objectId") for m in members]
    return [i for i in ids if i]


async def resolve_member_aad(context: TurnContext, team_id: str, member_id: str) -> Optional[str]:
    """Entra object id for a Teams user id (29:...) from the team roster."""
    connector = context.turn_state.get("ConnectorClient")
    for m in await connector.conversations.get_conversation_members(team_id):
        if m.id == member_id:
            return m.aad_object_id or (m.model_extra or {}).get("objectId")
    return None


_EMAIL = re.compile(r"[A-Za-z0-9._%+'-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_ORGID = re.compile(r"8:orgid:([0-9a-fA-F-]{36})")


def _identifiers(activity) -> list[str]:
    """Emails / UPNs / Entra ids anywhere in the message: typed text, a shared contact card, entities."""
    import json

    blob = " ".join([
        activity.text or "",
        json.dumps([a.model_dump(mode="json", by_alias=True) for a in activity.attachments or []]),
        json.dumps([e.model_dump(mode="json", by_alias=True) for e in activity.entities or []]),
    ])
    found = [m.group(1) for m in _ORGID.finditer(blob)] + _EMAIL.findall(blob)
    bot_upn = (activity.recipient.name or "").lower() if activity.recipient else ""
    return [i for i in dict.fromkeys(f.lower() for f in found) if i != bot_upn]


async def resolve_target(context: TurnContext) -> tuple[Optional[str], str]:
    """The person a command is about: an @mentioned team member, else an email / UPN / contact card."""
    activity = context.activity
    team = _channel_data(activity).get("team") or {}
    bot_id = activity.recipient.id if activity.recipient else None
    target = next((m.mentioned for m in TurnContext.get_mentions(activity)
                   if m.mentioned and m.mentioned.id != bot_id), None)
    aad, name = None, (target.name if target else "") or ""
    if target and team.get("id"):
        try:
            aad = await resolve_member_aad(context, team["id"], target.id)
        except Exception as e:
            log.warning("could not resolve the mentioned member: %s", type(e).__name__)
    if not aad:
        for ident in _identifiers(activity):
            try:
                found = await get_graph_api().find_user(ident)
            except Exception as e:
                log.warning("user lookup failed: %s", type(e).__name__)
                found = None
            if found:
                return found
    return aad, name


async def _is_owner(activity) -> bool:
    team = _channel_data(activity).get("team") or {}
    who = activity.from_property
    try:
        return bool(who and who.aad_object_id in await get_graph_api().team_owner_ids(team.get("aadGroupId") or ""))
    except Exception as e:
        log.warning("owner check failed: %s", type(e).__name__)
        return False


async def handle_departure_command(context: TurnContext, command: str, text: str) -> None:
    """away until <date> (anyone, for themselves) / handover @Name and link @Name as <document name> (owners)."""
    activity = context.activity
    who = activity.from_property
    scope_id, kind = scope_of(activity)
    channel_id = scope_id if kind == "channel" else None
    if command.startswith("away"):
        until = departure.parse_until(text)
        try:
            await context.send_activity(Activity(type="typing"))
        except Exception:
            pass
        _, reply = await departure.start(
            aad_id=who.aad_object_id, name=who.name or "", source="self", starts_at=datetime.now(timezone.utc),
            ends_at=until, triggered_by=who.name, channel_id=channel_id, service_url=activity.service_url or "",
            tenant_id=activity.conversation.tenant_id)
        await context.send_activity(reply)
        return
    if not await _is_owner(activity):
        await context.send_activity("Only a team owner can do that.")
        return
    aad, name = await resolve_target(context)
    if not aad:
        await context.send_activity("Mention the person (or type their email), e.g. **@LegacyLens handover @Name**.")
        return
    if command.startswith("link"):
        m = re.search(r"\bas\s+(.+)$", TurnContext.remove_recipient_mention(activity) or "", re.IGNORECASE)
        doc_name = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""
        if not doc_name:
            await context.send_activity("Say who they are in the documents, e.g. **@LegacyLens link @Name as Dinesh Udhyakumar**.")
            return
        _, reply = await asyncio.to_thread(departure.link_person, aad, name, doc_name, who.name or "")
        await context.send_activity(reply)
        return
    try:
        await context.send_activity(Activity(type="typing"))
    except Exception:
        pass
    _, reply = await departure.start(
        aad_id=aad, name=name, source="owner", starts_at=datetime.now(timezone.utc), ends_at=departure.parse_until(text),
        triggered_by=who.name, channel_id=channel_id, service_url=activity.service_url or "",
        tenant_id=activity.conversation.tenant_id)
    await context.send_activity(reply)


async def handle_personal_departure(context: TurnContext, command: str, text: str) -> bool:
    """Handover interview answers and approval in the departing person's 1:1 chat."""
    activity = context.activity
    value = activity.value if isinstance(activity.value, dict) else {}
    verb = value.get("verb")
    d = await asyncio.to_thread(departure.active_for, activity.from_property.aad_object_id or "")
    if d is None and verb not in (departure.ANSWER_VERB, departure.APPROVE_VERB):
        return False
    if d is None:
        await context.send_activity("This handover is no longer open.")
        return True
    if verb == departure.APPROVE_VERB:
        try:
            await context.send_activity(Activity(type="typing"))
        except Exception:
            pass
        await context.send_activity(await departure.approve(d))
        return True
    if verb == departure.ANSWER_VERB or (d.status == "interviewing" and command not in replies.COMMANDS
                                         and not command.startswith(("ask ", "runbook "))):
        card, reply = await asyncio.to_thread(departure.answer, d, value.get("session_id"), value.get("question_id"),
                                              value.get("answer") if value else text)
        await context.send_activity(reply)
        if card:
            await context.send_activity(_card(card))
        return True
    return False


async def handle_assign(context: TurnContext) -> None:
    activity = context.activity
    team = _channel_data(activity).get("team") or {}
    candidate_aad, candidate_name = await resolve_target(context)
    who = activity.from_property
    reply = await onboarding.assign(
        owner_aad=who.aad_object_id if who else None, owner_name=(who.name if who else "") or "",
        candidate_aad=candidate_aad, candidate_name=candidate_name or "the candidate",
        team=team, channel_id=scope_of(activity)[0], service_url=activity.service_url or "",
        tenant_id=activity.conversation.tenant_id,
    )
    await context.send_activity(reply)


async def handle_personal_onboarding(context: TurnContext, command: str, text: str) -> bool:
    """Onboarding steps in the candidate's 1:1 chat. Returns True when handled."""
    activity = context.activity
    who = activity.from_property
    value = activity.value if isinstance(activity.value, dict) else {}
    s = await asyncio.to_thread(onboarding.session_for, who.aad_object_id if who else None, activity.conversation.id)
    if value.get("verb") == onboarding.ANSWER_VERB or (s and s.status == "assessing" and command not in replies.COMMANDS
                                                        and not command.startswith(("ask ", "runbook "))):
        if s is None:
            await context.send_activity("This assessment is no longer open.")
            return True
        card, reply, finished = await asyncio.to_thread(onboarding.answer, s, value.get("question_id"),
                                                        value.get("answer") if value else text)
        await context.send_activity(reply)
        if card:
            await context.send_activity(_card(card))
        if finished:
            _spawn(onboarding.finish(s.id))
        return True
    if command == "start":
        if s is None:
            await context.send_activity("You don't have an onboarding assessment yet. A project owner can assign one with "
                                        "**@LegacyLens assign @you** in the project channel.")
            return True
        try:
            await context.send_activity(Activity(type="typing"))
        except Exception:
            pass
        card, reply = await asyncio.to_thread(onboarding.start, s)
        await context.send_activity(reply)
        if card:
            await context.send_activity(_card(card))
        return True
    if command == "status" and s:
        await context.send_activity(await asyncio.to_thread(onboarding.progress_text, s))
        return True
    return False


async def refresh_project(context: TurnContext) -> None:
    team = _channel_data(context.activity).get("team") or {}
    if not team.get("id"):
        return
    try:
        member_ids = await fetch_team_member_ids(context, team["id"])
    except Exception as e:  # roster is best effort; keep the last known one
        log.warning("project roster refresh failed: %s", type(e).__name__)
        return
    store = get_consent_store()
    before = next((p for p in await asyncio.to_thread(store.projects) if p.team_id == team["id"]), None)
    # Teams puts the team name on install / member-change activities but not on ordinary messages, so a
    # missing name must never overwrite one we already know.
    name = team.get("name") or (before.name if before else "")
    await asyncio.to_thread(store.save_project, ProjectTeam(
        team_id=team["id"], aad_group_id=team.get("aadGroupId") or (before.aad_group_id if before else None),
        name=name, member_aad_ids=member_ids,
    ))
    if before is None or set(before.member_aad_ids) != set(member_ids):
        log.info("project roster updated: %d members", len(member_ids))
        audit.record("roster.updated", source="teams", scope_type="team", scope_id=team["id"],
                     scope_name=team.get("name"), count=len(member_ids),
                     details={"added": len(set(member_ids) - set(before.member_aad_ids if before else [])),
                              "removed": len(set(before.member_aad_ids if before else []) - set(member_ids))})


async def ask_consent(context: TurnContext, trigger: str = "install") -> None:
    store = get_consent_store()
    scope_id, kind = scope_of(context.activity)
    if kind == "channel":
        await refresh_project(context)  # also covers `resume` in a team installed before roster tracking
    rec = await asyncio.to_thread(store.get, scope_id)
    if rec and rec.status == "granted":
        await context.send_activity(
            f"✅ I'm already learning from this conversation (allowed by {rec.granted_by or 'a member'}). "
            "Type **@LegacyLens pause** to stop.")
        return
    if rec is None or rec.status in ("declined",):
        team = _channel_data(context.activity).get("team") or {}
        rec = ConsentRecord(
            scope_id=scope_id, scope_type=kind,
            display_name=(team.get("name") if kind == "channel" else context.activity.conversation.name) or "",
            team_id=team.get("aadGroupId") if kind == "channel" else None,
            channel_id=scope_id if kind == "channel" else None,
            auto_installed=bool(rec and rec.auto_installed),
            tenant_id=context.activity.conversation.tenant_id,
        )
    rec = await asyncio.to_thread(store.save, rec.model_copy(update={"status": "pending"}))
    await context.send_activity(_card(consent.consent_card(rec)))
    audit.record("consent.requested", source="teams", **_actor(context.activity), **_scope(rec),
                 details={"trigger": trigger, "auto_installed": rec.auto_installed})


async def on_consent_click(context: TurnContext, value: dict) -> None:
    store, who = get_consent_store(), context.activity.from_property
    scope_id = value.get("scope_id") or scope_of(context.activity)[0]
    allow = value.get("verb") == consent.ALLOW
    rec = await asyncio.to_thread(consent.decide, store, scope_id, allow, who.name or "", who.aad_object_id)
    if rec is None:
        await context.send_activity("That request has expired. Type **@LegacyLens resume** to ask again.")
        return
    audit.record(f"consent.{rec.status}", source="teams", **_actor(context.activity), **_scope(rec),
                 details={"auto_installed": rec.auto_installed})
    if context.activity.reply_to_id:  # replace the buttons with the decision
        try:
            done = _card(consent.decided_card(rec))
            done.id = context.activity.reply_to_id
            await context.update_activity(done)
        except Exception as e:
            log.warning("could not update consent card: %s", type(e).__name__)
            await context.send_activity(_card(consent.decided_card(rec)))
    else:
        await context.send_activity(_card(consent.decided_card(rec)))
    if rec.status == "granted":
        _spawn(consent.backfill(rec, get_graph_api()))
    else:
        _spawn(consent.leave_if_auto_installed(rec, get_graph_api()))


def _live_message(activity, scope_id: str, kind: str) -> LiveMessage:
    who = activity.from_property
    return LiveMessage(
        scope_id=scope_id, scope_type=kind, scope_name=activity.conversation.name or "",
        message_id=activity.id or "", author_aad_id=who.aad_object_id or who.id, author_name=who.name or "",
        timestamp=activity.timestamp or datetime.now(timezone.utc), text=activity.text or "",
    )


def status_text(scope_id: str, kind: str) -> str:
    """Consent for this conversation plus what the knowledge base holds (blocking)."""
    from app.graph import get_graph
    from app.services.source_registry import get_source_registry

    store = get_consent_store()
    lines = ["**LegacyLens status**"]
    if kind != "personal":
        rec = store.get(scope_id)
        here = {
            None: "❔ I haven't asked to learn from this conversation. Type **@LegacyLens resume** to ask.",
            "pending": "⏳ Waiting for someone to click **Allow** on my request.",
            "granted": f"✅ Learning from this conversation (allowed by {rec.granted_by if rec else ''}).",
            "declined": "🚫 Not learning here: someone chose *Not now*.",
            "paused": "⏸️ Paused here. Type **@LegacyLens resume** to ask again.",
        }[rec.status if rec else None]
        lines.append(here)
    files = get_source_registry().list()
    loaded = [f for f in files if f.status == "loaded"]
    sites = sorted({f.site_name for f in loaded if f.site_name})
    last = get_source_registry().last_sync()
    convs = [r for r in store.list() if r.scope_type in ("chat", "channel") and r.status == "granted"]
    stats = get_graph().stats().nodes_by_type
    topics = sum(stats.get(t, 0) for t in ("system", "process", "decision"))
    lines.append(f"- 📄 **{len(loaded)}** SharePoint files" + (f" from {', '.join(sites)}" if sites else "")
                 + (f" (last checked {last:%d %b %H:%M} UTC)" if last else ""))
    failed = len([f for f in files if f.status == "failed"])
    if failed:
        lines.append(f"- ⚠️ {failed} files could not be read")
    lines.append(f"- 💬 **{len(convs)}** Teams conversations allowed")
    lines.append(f"- 🧠 **{topics}** topics, **{stats.get('person', 0)}** people in the knowledge graph")
    return "\n".join(lines)


def _mentions_bot(activity) -> bool:
    bot_id = activity.recipient.id if activity.recipient else None
    return any(m.mentioned and m.mentioned.id == bot_id for m in TurnContext.get_mentions(activity))


def build_bot(s: Optional[Settings] = None) -> Bot:
    s = s or get_settings()
    config = _sdk_config(s)
    connections = MsalConnectionManager(**config)
    storage = MemoryStorage()
    app = AgentApplication[TurnState](storage=storage, connection_manager=connections, **config)

    @app.conversation_update("membersAdded")
    async def on_members_added(context: TurnContext, _state: TurnState):
        bot_id = context.activity.recipient.id if context.activity.recipient else None
        _, kind = scope_of(context.activity)
        if kind == "channel":
            await refresh_project(context)  # new teammates join the project roster
        if not any(m.id == bot_id for m in context.activity.members_added or []):
            return
        if kind == "personal":
            await context.send_activity(replies.WELCOME)
            return
        if kind == "channel":
            await context.send_activity(replies.WELCOME)
        await ask_consent(context)

    @app.conversation_update("membersRemoved")
    async def on_members_removed(context: TurnContext, _state: TurnState):
        if scope_of(context.activity)[1] == "channel":
            await refresh_project(context)

    @app.activity("message")
    async def on_message(context: TurnContext, _state: TurnState):
        activity = context.activity
        if isinstance(activity.value, dict) and activity.value.get("verb") in (consent.ALLOW, consent.DECLINE):
            await on_consent_click(context, activity.value)
            return
        scope_id, kind = scope_of(activity)
        if kind == "personal" and isinstance(activity.value, dict) and activity.value.get("verb") == onboarding.ANSWER_VERB:
            await handle_personal_onboarding(context, "", "")
            return
        if kind == "personal" and isinstance(activity.value, dict) and activity.value.get("verb") in (
                departure.ANSWER_VERB, departure.APPROVE_VERB):
            await handle_personal_departure(context, "", "")
            return
        if kind != "personal" and not _mentions_bot(activity):
            # Not addressed to us: learn from it only if this conversation said yes.
            if await asyncio.to_thread(is_granted, get_consent_store(), scope_id):
                _spawn(asyncio.to_thread(capture, [_live_message(activity, scope_id, kind)], "live"))
            return
        if kind == "channel":
            await refresh_project(context)  # keep the project roster current (needs the live turn)
        text = TurnContext.remove_recipient_mention(activity) or ""
        command = " ".join(text.lower().split()).strip(" ./!?")
        if kind == "channel" and command.startswith("assign"):
            await handle_assign(context)
            return
        if command.startswith("away") or (kind == "channel" and command.startswith(("handover", "link "))):
            await handle_departure_command(context, command, text)
            return
        if kind == "personal" and await handle_personal_onboarding(context, command, text):
            return
        if kind == "personal" and await handle_personal_departure(context, command, text):
            return
        if kind != "personal" and command in ("pause", "stop"):
            store = get_consent_store()
            rec = await asyncio.to_thread(store.get, scope_id)
            if rec:
                await asyncio.to_thread(store.save, rec.model_copy(update={"status": "paused"}))
                audit.record("consent.paused", source="teams", **_actor(activity), **_scope(rec))
            await context.send_activity(replies.PAUSED)
            return
        if command == "status":
            await context.send_activity(await asyncio.to_thread(status_text, scope_id, kind))
            audit.record("ask.answered", source="teams", **_actor(activity), **_scope(activity), details={"reply": "status"})
            return
        if kind != "personal" and command in ("resume", "consent", "allow"):
            await ask_consent(context, trigger="resume")
            return
        if command.startswith("runbook ") or command not in replies.COMMANDS:
            try:  # questions and runbooks call the LLM (several seconds): show "typing…"
                await context.send_activity(Activity(type="typing"))
            except Exception:
                pass
        try:
            reply, cited, kind_of_reply = await asyncio.to_thread(replies.respond, text)
            audit.record("ask.answered", source="teams", **_actor(activity), **_scope(activity),
                         item_ids=cited, details={"reply": kind_of_reply})
            if kind == "personal" and kind_of_reply in ("answer", "no_answer", "runbook", "topics", "risks"):
                s = await asyncio.to_thread(onboarding.session_for, activity.from_property.aad_object_id,
                                            activity.conversation.id)
                if s and s.status == "learning":  # part of the candidate's learning journey
                    learn_kind = {"answer": "ask", "no_answer": "ask"}.get(kind_of_reply, kind_of_reply)
                    await asyncio.to_thread(onboarding.log_learning, s, text.strip(), learn_kind, cited,
                                            kind_of_reply != "no_answer")
        except Exception:  # never leave the user without an answer; details stay in our logs
            log.exception("bot reply failed")
            audit.record("ask.answered", outcome="failure", source="teams", **_actor(activity), **_scope(activity))
            reply = "Sorry, something went wrong while answering. Please try again in a moment."
        await context.send_activity(reply)

    @app.error
    async def on_error(context: TurnContext, error: Exception):
        log.error("bot turn error: %s", type(error).__name__)

    adapter = CloudAdapter(connection_manager=connections)
    return Bot(app=app, adapter=adapter, auth_config=connections.get_default_connection_configuration())


@lru_cache
def get_bot() -> Bot:
    return build_bot()
