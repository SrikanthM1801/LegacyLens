"""
Departure prevention: before someone goes away, capture what only they know.

Triggers (any of):
  * Outlook out-of-office turned on / scheduled for a project member (polled; MailboxSettings.Read)
  * the person says `@LegacyLens away until 3 Nov` (works for guests, who have no mailbox here)
  * a team owner says `@LegacyLens handover @Name`
Then:
  1. find the topics where this person holds most (>= 50%) of the knowledge (graph activity shares),
     including what they appear as in documents (person links: `@LegacyLens link @User as <name>`);
  2. post an alert card in the project channel;
  3. open a 1:1 chat and ask grounded interview questions about the top topics, one card at a time;
  4. after they review and approve, publish the answers (risk recomputed -> the topic turns green) and
     post the before/after card;
  5. write a cited handover pack and save it as a Word file in the project's SharePoint library
     (draft at the start, refreshed after approval).
Every step is audited (category `departure`).
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.agents.gap_analyzer import compute_topic_stats
from app.agents.generator import generate_handover
from app.agents.interviewer import generate_questions
from app.bot.capture import person_id
from app.config import get_settings
from app.graph import get_graph
from app.ingestion.extractor import get_extractor
from app.ingestion.pipeline import ingest_lock
from app.models import AtRiskTopic, Departure, InterviewAnswer, InterviewSession, InterviewStatus, PersonLink
from app.services import audit, doc_store, interview_store, risk_store
from app.services.consent_store import get_consent_store
from app.services.docx_export import markdown_to_docx
from app.services.graph_api import GraphError, get_graph_api
from app.services.interview_publish import NothingToPublish, approve_session
from app.services.json_store import JsonStore
from app.services.llm import get_llm
from app.services.search import get_search
from app.services.teams_connector import ConnectorError, card_activity, get_connector

log = logging.getLogger(__name__)

ANSWER_VERB = "legacylens_handover_answer"
APPROVE_VERB = "legacylens_handover_approve"
MIN_SHARE = 0.5
MAX_TOPICS_ALERT = 5
TOPICS_TO_INTERVIEW = 2
QUESTIONS_PER_TOPIC = 2
ACTIVE = ("alerted", "interviewing", "reviewing")
_SCHEMA = {"type": "AdaptiveCard", "$schema": "http://adaptivecards.io/schemas/adaptive-card.json", "version": "1.5"}
_BADGE = {"high": "🔴", "medium": "🟠", "low": "🟢"}

_departures: Optional[JsonStore[Departure]] = None
_links: Optional[JsonStore[PersonLink]] = None


def departures() -> JsonStore[Departure]:
    global _departures
    if _departures is None:
        _departures = JsonStore("departures", Departure)
    return _departures


def links() -> JsonStore[PersonLink]:
    global _links
    if _links is None:
        _links = JsonStore("people/links", PersonLink, key="aad_id")
    return _links


def _audit(action: str, d: Departure, outcome: str = "success", **fields) -> None:
    audit.record(f"departure.{action}", outcome=outcome, source=fields.pop("source", "teams"), scope_type="departure",
                 scope_id=d.id, scope_name=d.person_name, **fields)


# --- who is this person in the knowledge graph ---------------------------------------
def _tokens(name: str) -> frozenset[str]:
    return frozenset(t for t in re.split(r"[\s,.]+", name.lower()) if t)


def person_ids_for(aad_id: str, display_name: str) -> list[str]:
    graph = get_graph()
    ids = [person_id(aad_id)]
    link = links().get(aad_id)
    if link:
        ids += link.person_ids
    wanted = _tokens(display_name)
    for e in graph.entities("person"):  # a document persona with exactly the same name
        if e.id not in ids and wanted and _tokens(e.name) == wanted:
            ids.append(e.id)
    return list(dict.fromkeys(ids))


def link_person(aad_id: str, display_name: str, document_name: str, by: str) -> tuple[Optional[PersonLink], str]:
    graph = get_graph()
    wanted = _tokens(document_name)
    matches = [e for e in graph.entities("person") if wanted and (wanted == _tokens(e.name) or wanted <= _tokens(e.name))]
    if not matches:
        known = ", ".join(sorted(e.name for e in graph.entities("person") if e.id.startswith("person_doc_"))[:12])
        return None, f"I don't know anyone called *{document_name}* in the project documents. People I know: {known or 'none yet'}."
    link = links().get(aad_id) or PersonLink(aad_id=aad_id, display_name=display_name)
    link.person_ids = list(dict.fromkeys([*link.person_ids, *(m.id for m in matches)]))
    link.linked_by, link.linked_at = by, datetime.now(timezone.utc)
    links().save(link)
    audit.record("people.linked", source="teams", actor_name=by, scope_type="person", scope_id=aad_id,
                 scope_name=display_name, details={"document_names": [m.name for m in matches]})
    return link, f"🔗 Linked **{display_name}** to *{', '.join(m.name for m in matches)}* from the project documents."


def at_risk_topics(ids: list[str]) -> list[AtRiskTopic]:
    graph = get_graph()
    risks = {r.entity_id: r for r in risk_store.get_risks().items if r.entity_id}
    out = []
    for s in compute_topic_stats(graph):
        share = sum(s.shares.get(p, 0.0) for p in ids)
        r = risks.get(s.topic_id)
        if r and share >= MIN_SHARE:
            out.append(AtRiskTopic(risk_id=r.id, entity_id=s.topic_id, topic=r.topic, share=round(share, 2),
                                   risk_score=r.risk_score, risk_level=r.risk_level, coverage=r.coverage_score))
    return sorted(out, key=lambda t: (-t.risk_score, -t.share))[:MAX_TOPICS_ALERT]


# --- cards -------------------------------------------------------------------------------
def _when(d: Departure) -> str:
    if d.starts_at and d.ends_at:
        return f"away {d.starts_at:%d %b} – {d.ends_at:%d %b}"
    if d.starts_at:
        return f"away from {d.starts_at:%d %b}"
    return "leaving"


def alert_card(d: Departure) -> dict:
    first = d.person_name.split(",")[-1].strip().split()[0]
    why = {"out_of_office": "Outlook out-of-office is on", "self": f"{first} told me", "owner": f"requested by {d.triggered_by}"}[d.source]
    body = [{"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True,
             "text": f"⚠️ {d.person_name} is {_when(d)}"},
            {"type": "TextBlock", "isSubtle": True, "wrap": True, "text": f"Detected: {why}."}]
    if d.at_risk:
        body.append({"type": "TextBlock", "wrap": True, "text": f"Knowledge mostly held by {first}:"})
        body.append({"type": "FactSet", "facts": [
            {"title": f"{_BADGE[t.risk_level]} {t.topic[:45]}", "value": f"{t.share:.0%} of the know-how · {t.coverage:.0%} documented"}
            for t in d.at_risk]})
        body.append({"type": "TextBlock", "wrap": True, "text": (
            f"I've asked {first} a few questions in a 1:1 chat so this isn't lost"
            + (f", and drafted a [handover pack]({d.handover_url.replace(' ', '%20')})." if d.handover_url else "."))})
    else:
        body.append({"type": "TextBlock", "wrap": True,
                     "text": f"✅ No topic depends mostly on {first}: the project knowledge is shared or documented."})
    return {**_SCHEMA, "body": body}


def intro_card(d: Departure, n_questions: int) -> dict:
    first = d.person_name.split(",")[-1].strip().split()[0]
    return {**_SCHEMA, "body": [
        {"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True, "text": f"Hi {first}, before you're {_when(d)} 👋"},
        {"type": "TextBlock", "wrap": True, "text": (
            "You hold most of the team's know-how on: " + ", ".join(f"**{t.topic}**" for t in d.at_risk[:TOPICS_TO_INTERVIEW])
            + f". I have **{n_questions} short questions**, based on what's already in the project documents and chats, "
              "so the team can manage while you're away.")},
        {"type": "TextBlock", "wrap": True, "isSubtle": True,
         "text": "You'll review your answers before anything is published. Plain notes are fine."},
    ]}


def question_card(d: Departure, session: InterviewSession, q, index: int, total: int) -> dict:
    ev = q.evidence[0] if q.evidence else None
    body = [{"type": "TextBlock", "weight": "Bolder", "wrap": True, "text": f"Question {index} of {total} · {session.topic}"},
            {"type": "TextBlock", "wrap": True, "text": q.text}]
    if q.rationale:
        body.append({"type": "TextBlock", "wrap": True, "isSubtle": True, "text": q.rationale})
    if ev:
        body.append({"type": "TextBlock", "wrap": True, "size": "Small", "isSubtle": True,
                     "text": f"From *{ev.title or 'project record'}*: “{' '.join(ev.snippet.split())[:200]}…”"})
    body.append({"type": "Input.Text", "id": "answer", "isMultiline": True, "placeholder": "What would a colleague need to know?"})
    return {**_SCHEMA, "body": body, "actions": [{"type": "Action.Submit", "title": "Submit",
            "data": {"verb": ANSWER_VERB, "departure_id": d.id, "session_id": session.id, "question_id": q.id}}]}


def review_card(d: Departure, pairs: list[tuple[InterviewSession, object, str]]) -> dict:
    body = [{"type": "TextBlock", "size": "Medium", "weight": "Bolder", "text": "Review your answers", "wrap": True},
            {"type": "TextBlock", "wrap": True, "isSubtle": True,
             "text": "Approving publishes them to the project knowledge (with you as the source) and updates the handover pack."}]
    for session, q, answer in pairs:
        body += [{"type": "TextBlock", "wrap": True, "weight": "Bolder", "text": f"{session.topic}: {q.text}"},
                 {"type": "TextBlock", "wrap": True, "text": answer}]
    return {**_SCHEMA, "body": body, "actions": [
        {"type": "Action.Submit", "title": "Approve & publish", "style": "positive", "data": {"verb": APPROVE_VERB, "departure_id": d.id}}]}


def result_card(d: Departure) -> dict:
    first = d.person_name.split(",")[-1].strip().split()[0]
    facts = []
    for t in d.at_risk:
        if t.risk_after is not None:
            facts.append({"title": t.topic[:45], "value": f"{_BADGE[t.risk_level]} {t.risk_score:.2f} → {_BADGE[t.level_after or 'low']} {t.risk_after:.2f}"})
    body = [{"type": "TextBlock", "size": "Medium", "weight": "Bolder", "wrap": True, "text": f"✅ Knowledge captured from {first}"},
            {"type": "FactSet", "facts": facts}]
    if d.handover_url:
        body.append({"type": "TextBlock", "wrap": True, "text": f"📄 [Handover pack]({d.handover_url.replace(' ', '%20')}) updated in SharePoint."})
    body.append({"type": "TextBlock", "wrap": True, "isSubtle": True,
                 "text": f"Ask **@LegacyLens** about these topics; answers now cite {first}."})
    return {**_SCHEMA, "body": body}


# --- 1. start ------------------------------------------------------------------------------
def _project_channel() -> Optional[str]:
    chans = [r for r in get_consent_store().list() if r.scope_type == "channel" and r.status == "granted"]
    return chans[0].scope_id if chans else None


def active_for(aad_id: str) -> Optional[Departure]:
    open_ = [d for d in departures().list() if d.person_aad_id == aad_id and d.status in ACTIVE]
    return max(open_, key=lambda d: d.detected_at) if open_ else None


def _questions(d: Departure) -> list[tuple[InterviewSession, object]]:
    out = []
    for sid in d.session_ids:
        s = interview_store.get(sid)
        if s:
            out += [(s, q) for q in s.questions]
    return out


def _prepare(d: Departure) -> int:
    """Blocking: at-risk topics, interview sessions, handover draft. Returns number of questions."""
    graph = get_graph()
    d.at_risk = at_risk_topics(d.person_ids)
    if not d.at_risk:
        return 0
    stats = {s.topic_id: s for s in compute_topic_stats(graph)}
    top = stats[d.at_risk[0].entity_id]
    d.interviewee_id = max(d.person_ids, key=lambda p: top.shares.get(p, 0.0))
    risks = {r.id: r for r in risk_store.get_risks().items}
    for t in d.at_risk[:TOPICS_TO_INTERVIEW]:
        qs = generate_questions(risks[t.risk_id], graph, get_search(), get_llm(), interviewee_name=d.person_name,
                                max_questions=QUESTIONS_PER_TOPIC)[:QUESTIONS_PER_TOPIC]
        if qs:
            s = InterviewSession(risk_id=t.risk_id, topic=t.topic, interviewee=d.interviewee_id,
                                 status=InterviewStatus.PENDING, questions=qs)
            interview_store.save(s)
            d.session_ids.append(s.id)
    return sum(len(interview_store.get(sid).questions) for sid in d.session_ids)


async def _publish_handover(d: Departure, stage: str) -> None:
    """Write the cited handover pack and save it to SharePoint as .docx (best effort)."""
    graph = get_graph()
    person = graph.get_entity(d.interviewee_id or d.person_ids[0])
    if person is None:
        return
    doc = await asyncio.to_thread(
        generate_handover, person, graph, get_search(), risk_store.get_risks().items, interview_store.list_sessions(),
        doc_store.list_docs("runbook"), only_topics=[t.entity_id for t in d.at_risk])
    if doc is None:
        return
    doc = doc.model_copy(update={"title": f"Handover pack: {d.person_name}"})
    doc_store.save(doc)
    d.handover_doc_id = doc.id
    site = get_settings().sharepoint_site_url
    if not site:
        return
    name = re.sub(r"[^\w .-]", "", d.person_name).strip() or "person"
    path = f"LegacyLens Handover/Handover - {name} - {d.detected_at:%Y-%m-%d}.docx"
    try:
        data = await asyncio.to_thread(markdown_to_docx, doc.markdown, doc.title)
        d.handover_url = await get_graph_api().upload_to_site(site, path, data)
        _audit("handover_published", d, source="sharepoint", actor_name="LegacyLens",
               details={"stage": stage, "path": path, "web_url": d.handover_url, "citations": len(doc.citations)})
    except GraphError as e:
        d.error = f"handover not saved to SharePoint: {e}"
        _audit("handover_published", d, outcome="failure", source="sharepoint", details={"error": str(e)})


async def start(*, aad_id: str, name: str, source: str, starts_at: Optional[datetime] = None,
                ends_at: Optional[datetime] = None, triggered_by: Optional[str] = None,
                channel_id: Optional[str] = None, service_url: str = "", tenant_id: Optional[str] = None) -> tuple[Departure, str]:
    existing = active_for(aad_id)
    if existing:
        return existing, f"I'm already helping {name} hand over ({existing.status})."
    d = Departure(person_aad_id=aad_id, person_name=name, person_ids=person_ids_for(aad_id, name), source=source,
                  starts_at=starts_at, ends_at=ends_at, triggered_by=triggered_by, tenant_id=tenant_id,
                  channel_id=channel_id or _project_channel(), service_url=service_url)
    n = await asyncio.to_thread(_prepare, d)
    _audit("detected", d, actor_name=triggered_by or name, details={
        "trigger": source, "starts_at": starts_at.isoformat() if starts_at else None,
        "ends_at": ends_at.isoformat() if ends_at else None,
        "topics": [{"topic": t.topic, "share": t.share, "risk": t.risk_score} for t in d.at_risk]})
    connector = get_connector()
    if not d.at_risk:
        d.status = "no_risk"
    else:
        await _publish_handover(d, "draft")
        if n:
            try:
                await get_graph_api().install_app_for_user(aad_id)
                d.conversation_id = await connector.open_personal_chat(aad_id, tenant_id or "", service_url)
                await connector.send(d.conversation_id, card_activity(intro_card(d, n)), service_url)
                s, q = _questions(d)[0]
                await connector.send(d.conversation_id, card_activity(question_card(d, s, q, 1, n)), service_url)
                d.status = "interviewing"
                _audit("interview_started", d, actor_name="LegacyLens", count=n,
                       details={"topics": [t.topic for t in d.at_risk[:TOPICS_TO_INTERVIEW]]})
            except (GraphError, ConnectorError) as e:
                d.error = f"could not open a chat: {e}"
                _audit("interview_started", d, outcome="failure", details={"error": str(e)})
    if d.channel_id:
        try:
            await connector.post_to_channel(d.channel_id, card_activity(alert_card(d)), service_url)
            _audit("alert_posted", d, actor_name="LegacyLens", details={"topics": len(d.at_risk)})
        except ConnectorError as e:
            _audit("alert_posted", d, outcome="failure", details={"error": str(e)})
    departures().save(d)
    first = name.split(",")[-1].strip().split()[0]
    if not d.at_risk:
        return d, f"✅ {first} doesn't hold any topic alone, so nothing is at risk while they're away."
    return d, (f"⚠️ {len(d.at_risk)} topic(s) depend mostly on {first}. "
               + ("I've started a short handover interview with them." if d.status == "interviewing"
                  else f"I couldn't reach them in a chat ({d.error})."))


# --- 2. interview ------------------------------------------------------------------------
def answer(d: Departure, session_id: Optional[str], question_id: Optional[str], text: str) -> tuple[Optional[dict], str]:
    """Store one answer; returns (next card, reply). The last answer returns the review card."""
    if d.status != "interviewing":
        return None, "There's no handover question waiting."
    pairs = _questions(d)
    answered = {(s.id, a.question_id) for s, _ in pairs for a in s.answers}
    pending = [(s, q) for s, q in pairs if (s.id, q.id) not in answered]
    target = next(((s, q) for s, q in pending if q.id == question_id), pending[0] if pending else None)
    if target is None:
        return None, "You've answered everything. Use the review card to approve."
    if len((text or "").strip()) < 3:
        return None, "Please type an answer before submitting."
    s, q = target
    with interview_store.lock():
        s = interview_store.get(s.id)
        s.answers.append(InterviewAnswer(question_id=q.id, session_id=s.id, text=text.strip()[:4000],
                                         answered_by=d.interviewee_id or d.person_ids[0]))
        q_live = next(x for x in s.questions if x.id == q.id)
        q_live.answered = True
        s.status = InterviewStatus.IN_PROGRESS
        interview_store.save(s)
    _audit("answered", d, actor_name=d.person_name, actor_aad_id=d.person_aad_id, details={"topic": s.topic})
    pending = [(x, y) for x, y in pending if not (x.id == s.id and y.id == q.id)]
    total = len(pairs)
    if pending:
        ns, nq = pending[0]
        return question_card(d, ns, nq, total - len(pending) + 1, total), "Thanks! Next one:"
    d.status = "reviewing"
    departures().save(d)
    review = [(x, y, next(a.text for a in interview_store.get(x.id).answers if a.question_id == y.id))
              for x, y in _questions(d)]
    return review_card(d, review), "That's all. Please check your answers:"


async def approve(d: Departure) -> str:
    if d.status != "reviewing":
        return "There's nothing waiting for your approval."

    def publish() -> list:
        results = []
        with ingest_lock:
            for sid in d.session_ids:
                s = interview_store.get(sid)
                risk = risk_store.get_risk(s.risk_id)
                try:
                    results.append(approve_session(s, graph=get_graph(), search=get_search(), extractor=get_extractor(),
                                                   llm=get_llm(), topic_entity_id=risk.entity_id if risk else None))
                except NothingToPublish:
                    continue
        return results

    results = await asyncio.to_thread(publish)
    after = {r.session.risk_id: r.risk_after for r in results if r.risk_after}
    for t in d.at_risk:
        if t.risk_id in after:
            t.risk_after, t.level_after = after[t.risk_id].risk_score, after[t.risk_id].risk_level
    d.status = "completed"
    await _publish_handover(d, "final")
    departures().save(d)
    _audit("knowledge_published", d, actor_name=d.person_name, actor_aad_id=d.person_aad_id,
           count=sum(r.artifacts_published for r in results),
           details={"risk": {t.topic: [t.risk_score, t.risk_after] for t in d.at_risk if t.risk_after is not None}})
    if d.channel_id:
        try:
            await get_connector().post_to_channel(d.channel_id, card_activity(result_card(d)), d.service_url)
        except ConnectorError as e:
            _audit("result_posted", d, outcome="failure", details={"error": str(e)})
    return "✅ Published. Thank you, the team can now find this while you're away."


# --- out-of-office detection ----------------------------------------------------------------
def _graph_time(v: Optional[dict]) -> Optional[datetime]:
    if not v or not v.get("dateTime"):
        return None
    t = datetime.fromisoformat(v["dateTime"][:19])
    return t.replace(tzinfo=timezone.utc) if (v.get("timeZone") or "UTC").upper() == "UTC" else t.replace(tzinfo=timezone.utc)


def ooo_window(setting: Optional[dict], now: datetime, lookahead_days: int) -> Optional[tuple[datetime, Optional[datetime]]]:
    """(start, end) when the automatic replies mean 'away now or soon', else None."""
    if not setting:
        return None
    status = (setting.get("status") or "").lower()
    if status == "alwaysenabled":
        return now, None
    if status == "scheduled":
        start, end = _graph_time(setting.get("scheduledStartDateTime")), _graph_time(setting.get("scheduledEndDateTime"))
        if end and end < now:
            return None
        if start and start > now + timedelta(days=lookahead_days):
            return None
        return start or now, end
    return None


async def check_out_of_office(member_ids: list[str], names: dict[str, str]) -> list[Departure]:
    """Start a departure for every project member whose out-of-office says they're away now or soon."""
    s, now, started = get_settings(), datetime.now(timezone.utc), []
    for aad in member_ids:
        if active_for(aad):
            continue
        try:
            window = ooo_window(await get_graph_api().automatic_replies(aad), now, s.departure_lookahead_days)
        except GraphError as e:
            log.warning("out-of-office check failed: %s", e)
            continue
        if not window:
            continue
        recent = [d for d in departures().list() if d.person_aad_id == aad and d.source == "out_of_office"
                  and d.starts_at and window[0] and abs((d.starts_at - window[0]).total_seconds()) < 3600]
        if recent:  # already handled this absence
            continue
        d, _ = await start(aad_id=aad, name=names.get(aad) or aad, source="out_of_office", starts_at=window[0],
                           ends_at=window[1], service_url="")
        started.append(d)
    return started


async def ooo_loop() -> None:
    from app.services.consent_store import project_member_ids

    minutes = max(1, get_settings().departure_check_minutes)
    await asyncio.sleep(20)
    while True:
        try:
            members = sorted(await asyncio.to_thread(project_member_ids, get_consent_store()))
            names = {}
            for aad in members:
                found = await get_graph_api().find_user(aad) if members else None
                if found:
                    names[aad] = found[1]
            await check_out_of_office(members, names)
        except Exception:
            log.exception("out-of-office check crashed")
        await asyncio.sleep(minutes * 60)


# --- dates in commands -------------------------------------------------------------------
_DATE_FORMATS = ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y", "%d %b", "%d %B", "%b %d", "%B %d")


def parse_until(text: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """'away until 3 Nov' / 'till 2026-11-03' / 'to 03/11/2026' -> date (next occurrence if no year)."""
    now = now or datetime.now(timezone.utc)
    m = re.search(r"\b(?:until|till|til|to|through)\s+(.+)$", text.strip(), re.IGNORECASE)
    raw = (m.group(1) if m else "").strip(" .!?").replace(",", "")
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        if "%Y" not in fmt:
            dt = dt.replace(year=now.year)
            if dt.replace(tzinfo=timezone.utc) < now - timedelta(days=1):
                dt = dt.replace(year=now.year + 1)
        return dt.replace(tzinfo=timezone.utc)
    return None
