"""
Deterministic synthetic dataset for the fictional "Payments Platform" project.

    .venv/bin/python scripts/generate_dataset.py [--out data/sample]

Fixed seed, templated text, no LLM. Re-running produces byte-identical files.
Every record is built through the models in backend/app/models.py, so the
output is guaranteed to validate against the shared contracts.

PLANTED RISKS (the analyzer must rank these top 3 - see Plan.md section 13):
  1. Payment gateway outages   - Ravi resolved INC-142/155/171/188; no wiki page;
                                 the fix lives only in chat threads.
  2. Settlement batch job      - only Arjun touches it; workarounds only in chats.
  3. Kafka over RabbitMQ       - rationale only in Priya's emails; no decision record.
Well-documented contrast topics: release process, onboarding, API authentication.

Each build_* function below produces one source file. Do not weaken the planted
risks when editing: the demo depends on them.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.models import Artifact, ArtifactType, Person  # noqa: E402

SEED = 26
DEFAULT_OUT = Path(__file__).resolve().parents[1] / "data" / "sample"

RAVI, PRIYA, ARJUN, MEERA = "ravi", "priya", "arjun", "meera"


def ts(y: int, m: int, d: int, h: int = 10, mi: int = 0) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# People
# ---------------------------------------------------------------------------


def build_people() -> list[Person]:
    return [
        Person(
            id=RAVI, name="Ravi Kumar", role="Senior Backend Engineer",
            email="ravi.kumar@contoso-payments.com", teams_handle="@ravi",
            aliases=["Ravi", "ravi.k"], is_departing=True, departure_date=ts(2026, 11, 15, 0),
        ),
        Person(
            id=PRIYA, name="Priya Nair", role="Solution Architect",
            email="priya.nair@contoso-payments.com", teams_handle="@priya",
            aliases=["Priya", "priya.n"],
        ),
        Person(
            id=ARJUN, name="Arjun Mehta", role="Support Lead",
            email="arjun.mehta@contoso-payments.com", teams_handle="@arjun",
            aliases=["Arjun", "arjun.m"],
        ),
        Person(
            id=MEERA, name="Meera Iyer", role="Software Engineer (new joiner)",
            email="meera.iyer@contoso-payments.com", teams_handle="@meera",
            aliases=["Meera", "meera.i"],
        ),
    ]


# ---------------------------------------------------------------------------
# Tickets
# ---------------------------------------------------------------------------

TICKET_URL = "https://tracker.contoso-payments.example/browse/{key}"


def _ticket(
    key: str,
    title: str,
    opened: datetime,
    reporter: str,
    resolver: str | None,
    severity: str,
    description: str,
    resolution: str,
    comments: list[tuple[str, str, str]],
    tags: list[str],
    systems: list[str],
    hours_to_resolve: int = 3,
) -> Artifact:
    """Build a ticket Artifact. `comments` = (person_id, 'YYYY-MM-DD', text)."""
    lines = [f"{key}: {title}", f"Severity: {severity}", "", "Description:", description, ""]
    lines += ["Resolution:", resolution, ""]
    if comments:
        lines.append("Comments:")
        lines += [f"- [{when}] {who}: {text}" for who, when, text in comments]
    participants = sorted({w for w, _, _ in comments} | ({resolver} if resolver else set()))
    participants = [p for p in participants if p != reporter]
    resolved_at = opened + timedelta(hours=hours_to_resolve)
    return Artifact(
        id=f"art_ticket_{key.lower().replace('-', '_')}",
        type=ArtifactType.TICKET,
        source_id=key,
        author=reporter,
        participants=participants,
        timestamp=opened,
        title=f"{key} {title}",
        body="\n".join(lines),
        url_or_path=TICKET_URL.format(key=key),
        tags=tags,
        metadata={
            "key": key,
            "severity": severity,
            "status": "resolved" if resolver else "open",
            "resolved_by": resolver,
            "resolved_at": resolved_at.isoformat() if resolver else None,
            "systems": systems,
        },
    )


def build_tickets() -> list[Artifact]:
    gw, st = ["payment-gateway"], ["settlement-batch-job"]
    t: list[Artifact] = []

    # --- Planted risk 1: payment gateway outages, all resolved by Ravi -------
    t += [
        _ticket(
            "INC-142", "Payment gateway timeouts on card authorisations", ts(2025, 11, 3, 9, 20),
            ARJUN, RAVI, "P1",
            "Card authorisations through the payment gateway are timing out for ~30% of checkouts. "
            "Merchants report spinner then failure. Started after the 08:50 UTC traffic spike.",
            "Fixed by Ravi. Details in the team chat thread.",
            [(ARJUN, "2025-11-03", "Escalating to Ravi, nobody else has touched the gateway integration."),
             (RAVI, "2025-11-03", "On it. Looks like the usual one. Will write up later."),
             (RAVI, "2025-11-03", "Back to normal. See chat for what I did.")],
            ["incident", "payment-gateway"], gw, 3,
        ),
        _ticket(
            "INC-155", "Gateway returning 504 for refunds", ts(2026, 1, 19, 14, 5),
            ARJUN, RAVI, "P2",
            "Refund calls to the payment gateway intermittently return 504. Customer support has 40 open complaints.",
            "Resolved by Ravi, same cause as INC-142. See chat.",
            [(ARJUN, "2026-01-19", "Same symptoms as INC-142?"),
             (RAVI, "2026-01-19", "Yes. Applied the same fix, plus a stricter check on connection age.")],
            ["incident", "payment-gateway"], gw, 2,
        ),
        _ticket(
            "INC-171", "Gateway timeouts after certificate rotation", ts(2026, 4, 8, 7, 45),
            ARJUN, RAVI, "P1",
            "Timeouts to the payment gateway began right after the partner rotated their TLS certificate. "
            "Authorisation success rate dropped to 61%.",
            "Ravi fixed it. Notes are in the chat thread.",
            [(RAVI, "2026-04-08", "Different trigger, same failure mode. Had to flush the pool and re-pin the cert."),
             (MEERA, "2026-04-08", "Is there a doc I can read about this? Couldn't find one on the wiki.")],
            ["incident", "payment-gateway"], gw, 4,
        ),
        _ticket(
            "INC-188", "Payment gateway outage during flash sale", ts(2026, 7, 22, 18, 30),
            ARJUN, RAVI, "P1",
            "Gateway authorisations failing during the flash sale. Peak traffic 4x normal. Revenue impact.",
            "Ravi restored service. Fix described in chat.",
            [(ARJUN, "2026-07-22", "Paging Ravi again. This is the fourth gateway incident this year."),
             (RAVI, "2026-07-22", "Recovered. Third time I've done this by hand, we should automate it.")],
            ["incident", "payment-gateway"], gw, 2,
        ),
    ]

    # --- Planted risk 2: settlement batch job, handled only by Arjun ---------
    t += [
        _ticket(
            "INC-150", "Settlement batch job stuck at reconciliation", ts(2025, 12, 12, 6, 10),
            ARJUN, ARJUN, "P2",
            "Nightly settlement batch did not complete. Reconciliation step waiting on the bank file.",
            "Arjun re-ran the job manually.",
            [(ARJUN, "2025-12-12", "Bank file arrived late again. Re-ran it.")],
            ["incident", "settlement"], st, 2,
        ),
        _ticket(
            "INC-163", "Settlement totals mismatch for 2 merchants", ts(2026, 3, 2, 8, 0),
            ARJUN, ARJUN, "P2",
            "Settlement totals differ from the bank statement for two merchants. Finance flagged it.",
            "Arjun corrected the batch and re-ran settlement.",
            [(ARJUN, "2026-03-02", "Fixed the input and re-ran. Finance confirmed the totals match.")],
            ["incident", "settlement"], st, 5,
        ),
        _ticket(
            "INC-180", "Settlement batch failed after bank file format change", ts(2026, 6, 15, 5, 40),
            ARJUN, ARJUN, "P2",
            "Overnight settlement failed to parse the bank file after the bank changed a column.",
            "Arjun resolved this.",
            [(ARJUN, "2026-06-15", "Done, job completed. Raising a ticket to document this one day.")],
            ["incident", "settlement"], st, 3,
        ),
    ]

    # --- Planted risk 3: Kafka decision has tickets but no rationale ---------
    t += [
        _ticket(
            "PAY-118", "Select message broker for the payment event bus", ts(2025, 3, 10, 11, 0),
            PRIYA, PRIYA, "Task",
            "We need a message broker for payment events (authorised, captured, settled).",
            "Decision made: Kafka. Rationale discussed over email.",
            [(PRIYA, "2025-03-21", "Decision taken, moving ahead with Kafka.")],
            ["decision", "kafka", "architecture"], ["kafka-event-bus"], 24,
        ),
        _ticket(
            "PAY-204", "Implement Kafka consumer for settlement events", ts(2025, 6, 2, 10, 0),
            PRIYA, RAVI, "Task",
            "Consume payment.captured events from Kafka and feed the settlement batch input.",
            "Implemented and deployed.",
            [(RAVI, "2025-06-20", "Consumer merged. Topic config in the infra repo.")],
            ["kafka", "settlement"], ["kafka-event-bus", "settlement-batch-job"], 72,
        ),
    ]

    # --- Well-documented contrast topics (several people, wiki pages exist) ---
    api = ["api-authentication"]
    rel = ["release-process"]
    onb = ["onboarding"]
    t += [
        _ticket(
            "PAY-301", "Rotate API signing keys", ts(2026, 2, 9, 10, 0), PRIYA, RAVI, "Task",
            "Quarterly rotation of API signing keys per the API authentication guide.",
            "Done following the wiki procedure.",
            [(RAVI, "2026-02-10", "Followed the API auth wiki page, no surprises.")],
            ["api-auth"], api, 6,
        ),
        _ticket(
            "PAY-312", "Add OAuth scope for refunds endpoint", ts(2026, 4, 20, 10, 0), ARJUN, PRIYA, "Task",
            "Refund endpoint needs its own OAuth scope.",
            "Scope added, documented in the API authentication guide.",
            [(PRIYA, "2026-04-22", "Updated the wiki page with the new scope.")],
            ["api-auth"], api, 8,
        ),
        _ticket(
            "PAY-327", "Token expiry too short for batch clients", ts(2026, 6, 3, 10, 0), ARJUN, MEERA, "Bug",
            "Batch clients get 401 after 15 min. Increase expiry per the documented limits.",
            "Expiry made configurable, documented.",
            [(MEERA, "2026-06-04", "Used the API auth guide to find the config, easy fix.")],
            ["api-auth"], api, 5,
        ),
        _ticket(
            "PAY-290", "Fix release checklist step ordering", ts(2026, 1, 14, 10, 0), PRIYA, ARJUN, "Task",
            "Smoke test step was listed after the traffic switch in the release checklist.",
            "Checklist corrected.",
            [(ARJUN, "2026-01-15", "Updated the wiki release process page.")],
            ["release"], rel, 3,
        ),
        _ticket(
            "PAY-318", "Automate staging deploy in release pipeline", ts(2026, 5, 6, 10, 0), PRIYA, RAVI, "Task",
            "Automate the staging deploy as described in the release process doc.",
            "Pipeline updated.",
            [(RAVI, "2026-05-08", "Done. Release process page updated.")],
            ["release"], rel, 16,
        ),
        _ticket(
            "PAY-335", "Hotfix release for rounding bug", ts(2026, 7, 1, 10, 0), ARJUN, MEERA, "Bug",
            "Rounding bug on fee display. Needs hotfix release.",
            "Released using the documented hotfix flow.",
            [(MEERA, "2026-07-01", "First release I ran, the wiki made it painless.")],
            ["release"], rel, 4,
        ),
        _ticket(
            "PAY-340", "Update onboarding guide for new dev environment", ts(2026, 8, 3, 10, 0), PRIYA, MEERA, "Task",
            "Onboarding guide references the old docker-compose setup.",
            "Guide updated.",
            [(MEERA, "2026-08-04", "Fixed the steps I hit while setting up.")],
            ["onboarding"], onb, 6,
        ),
        _ticket(
            "PAY-341", "Add access checklist for new joiners", ts(2026, 8, 5, 10, 0), PRIYA, ARJUN, "Task",
            "Add a checklist of access requests to the onboarding guide.",
            "Added to the guide.",
            [(ARJUN, "2026-08-06", "Checklist added to the wiki.")],
            ["onboarding"], onb, 4,
        ),
        _ticket(
            "PAY-350", "Add latency dashboard for checkout API", ts(2026, 8, 18, 10, 0), PRIYA, MEERA, "Task",
            "Create a dashboard for p95 checkout latency.",
            "Dashboard shipped.",
            [(MEERA, "2026-08-20", "Dashboard live.")],
            ["monitoring"], ["checkout-api"], 20,
        ),
    ]
    return t


# ---------------------------------------------------------------------------
# Remaining sources (T04: emails + chats, T05: wiki + commits)
# ---------------------------------------------------------------------------


Msg = tuple[str, str, str]  # (person_id, "YYYY-MM-DD HH:MM", text)

_NAMES = {RAVI: "Ravi Kumar", PRIYA: "Priya Nair", ARJUN: "Arjun Mehta", MEERA: "Meera Iyer"}
_EMAILS = {
    RAVI: "ravi.kumar@contoso-payments.com",
    PRIYA: "priya.nair@contoso-payments.com",
    ARJUN: "arjun.mehta@contoso-payments.com",
    MEERA: "meera.iyer@contoso-payments.com",
}


def _when(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)


def _thread(
    kind: ArtifactType, source_id: str, title: str, msgs: list[Msg], tags: list[str], channel: str = ""
) -> Artifact:
    """One conversation (chat thread or email chain) as a single Artifact."""
    first_who, first_when, _ = msgs[0]
    blocks = []
    for who, when, text in msgs:
        if kind is ArtifactType.EMAIL:
            others = [_EMAILS[p] for p in sorted({m[0] for m in msgs}) if p != who]
            blocks.append(
                f"From: {_NAMES[who]} <{_EMAILS[who]}>\nTo: {', '.join(others)}\n"
                f"Date: {when} UTC\nSubject: {title}\n\n{text}"
            )
        else:
            blocks.append(f"[{when}] {_NAMES[who].split()[0]}: {text}")
    sep = "\n\n---\n\n" if kind is ArtifactType.EMAIL else "\n"
    head = f"Channel: {channel}\n" if channel else ""
    return Artifact(
        id=f"art_{kind.value}_{source_id.lower().replace('-', '_')}",
        type=kind,
        source_id=source_id,
        author=first_who,
        participants=sorted({m[0] for m in msgs} - {first_who}),
        timestamp=_when(first_when),
        title=title,
        body=head + sep.join(blocks),
        url_or_path=(
            f"mail://{source_id}" if kind is ArtifactType.EMAIL else f"teams://{channel.lstrip('#')}/{source_id}"
        ),
        tags=tags,
        metadata={"message_count": len(msgs), **({"channel": channel} if channel else {})},
    )


def build_emails() -> list[Artifact]:
    E = ArtifactType.EMAIL
    return [
        # --- Planted risk 3: why Kafka over RabbitMQ lives ONLY in these emails ---
        _thread(E, "MAIL-001", "Message broker for payment events: Kafka vs RabbitMQ", [
            (PRIYA, "2025-03-12 09:15",
             "Team, I have been comparing Kafka and RabbitMQ for the payment event bus (authorised, captured, "
             "settled). My recommendation is Kafka. Reasons: (1) event replay. Settlement reconciliation needs to "
             "re-process a day of events after a bank file fix, and Kafka's retained log gives us that for free, "
             "RabbitMQ deletes messages once acked. (2) Ordering per merchant. Keying by merchant id gives strict "
             "ordering per merchant on a partition, which RabbitMQ only gives with a single consumer. (3) Throughput "
             "headroom. Projected volume is about 10x in 18 months and Kafka scales by partitions."),
            (RAVI, "2025-03-12 11:40",
             "Makes sense for the consumers I own. One concern: RabbitMQ is simpler to run. Who operates the Kafka "
             "cluster?"),
            (PRIYA, "2025-03-12 13:05",
             "Fair. The platform team already runs a shared Kafka cluster, so we get managed ops and don't carry the "
             "pager for brokers. That was the deciding factor for me. We lose the simple per-message routing of "
             "RabbitMQ and accept more consumer-side code."),
            (PRIYA, "2025-03-13 08:50",
             "I also looked at Pulsar. Rejected: smaller ecosystem here and no platform-team support. Decision "
             "criteria were replay, per-merchant ordering, and who runs it. Kafka wins on all three."),
            (RAVI, "2025-03-13 09:30", "Agreed. I'll start on the consumer side once the topics are provisioned."),
        ], ["kafka", "architecture", "decision"]),
        _thread(E, "MAIL-002", "Re: Kafka operational risk", [
            (ARJUN, "2025-03-18 10:00",
             "Priya, support concern on Kafka. If the consumer lags or dies, settlement input is late and I get the "
             "call. How do we recover?"),
            (PRIYA, "2025-03-18 12:20",
             "Good question, and part of why replay mattered. If the consumer falls behind, we reset the consumer "
             "group offset to the last good checkpoint and replay. No data loss. We'll add a lag alert at 5 minutes. "
             "I accepted the extra operational complexity in exchange for exactly this recoverability."),
            (ARJUN, "2025-03-18 12:45", "OK, that helps. Please make sure the lag alert goes to the support channel."),
        ], ["kafka", "settlement", "decision"]),
        _thread(E, "MAIL-003", "Decision: Kafka selected as payment event bus", [
            (PRIYA, "2025-03-21 16:00",
             "FYI to all stakeholders: we are going ahead with Kafka for the payment event bus. Implementation "
             "tracked in PAY-118 and follow-ups. No further action needed from you."),
            (RAVI, "2025-03-21 16:10", "Ack."),
        ], ["kafka", "decision"]),
        _thread(E, "MAIL-004", "Architecture review notes: Sept 2025", [
            (PRIYA, "2025-09-08 17:00",
             "Notes from today's review. Kafka topics are now at 12 partitions per payment topic. The payment gateway "
             "adapter is stable but remains a single-owner component (Ravi). The settlement batch consumes from "
             "Kafka nightly at 02:00 UTC. Action: review ownership of gateway and settlement next quarter."),
            (ARJUN, "2025-09-08 17:20", "Noted. Settlement ownership is effectively me today."),
            (RAVI, "2025-09-09 09:00", "Same for the gateway adapter, effectively me."),
        ], ["architecture", "kafka", "payment-gateway", "settlement"]),
        _thread(E, "MAIL-005", "Q3 planning: backlog items", [
            (PRIYA, "2026-07-03 10:00",
             "Q3 backlog candidates: automate payment gateway pool recovery (Ravi to propose design), settlement "
             "runbook (Arjun), latency dashboard (Meera). We may not get to all of them."),
            (RAVI, "2026-07-03 10:30", "I'll write a short proposal for the gateway automation when I have time."),
        ], ["planning"]),
        _thread(E, "MAIL-006", "Why did we choose Kafka?", [
            (MEERA, "2026-08-25 11:00",
             "Hi Priya, I'm reading the event consumers. Is there a document explaining why we picked Kafka over "
             "RabbitMQ? I couldn't find a decision record."),
            (PRIYA, "2026-08-25 14:10",
             "There isn't a written record. It's buried in an email thread from March last year, I'll try to dig it "
             "out and write it up properly when I get a moment."),
        ], ["kafka", "decision"]),
        _thread(E, "MAIL-007", "Handover planning for Ravi", [
            (PRIYA, "2026-09-10 09:00",
             "Ravi, thank you for the notice. Your last day is 15 November. Please start preparing a handover for "
             "the gateway adapter and anything else only you know. Even a rough doc helps."),
            (RAVI, "2026-09-10 09:45",
             "Thanks Priya. Most of the gateway knowledge is in my head and in chat threads. I will try, but my "
             "time is tight with the Q3 deliverables."),
            (PRIYA, "2026-09-10 10:15", "Understood. We'll look at ways to capture it with less effort on your side."),
        ], ["handover", "payment-gateway"]),
        # --- Well-documented contrast topics ---
        _thread(E, "MAIL-008", "Release schedule for 2026", [
            (PRIYA, "2026-01-10 09:00",
             "Releases go out every second Tuesday following the release process on the wiki (Release Process page). "
             "Hotfixes follow the hotfix flow on the same page."),
            (ARJUN, "2026-01-10 09:20", "Thanks, support will align the maintenance notices with that."),
        ], ["release"]),
        _thread(E, "MAIL-009", "Quarterly API signing key rotation", [
            (PRIYA, "2026-02-05 09:00",
             "Reminder: rotate the API signing keys this week. Steps are in the API Authentication guide on the wiki."),
            (RAVI, "2026-02-05 09:40", "Will do, tracked as PAY-301."),
        ], ["api-auth"]),
        _thread(E, "MAIL-010", "Welcome Meera: onboarding plan", [
            (PRIYA, "2026-03-16 08:30",
             "Welcome to the team, Meera. Start with the Onboarding Guide on the wiki: environment setup, access "
             "checklist, and a first-week plan. Ask any of us if something is unclear."),
            (MEERA, "2026-03-16 08:50", "Thank you! Starting on the guide now."),
        ], ["onboarding"]),
    ]


def build_chats() -> list[Artifact]:
    C = ArtifactType.CHAT
    oncall, ops, dev = "#payments-oncall", "#settlement-ops", "#payments-dev"
    return [
        # --- Planted risk 1: how Ravi fixes gateway outages (lives ONLY here) ---
        _thread(C, "CHAT-101", "Gateway timeouts (INC-142)", [
            (ARJUN, "2025-11-03 09:25", "@ravi PG authorisations are timing out, 30% of checkouts. INC-142."),
            (RAVI, "2025-11-03 09:31", "Checking. Pulling up the pool stats on the gateway adapter."),
            (RAVI, "2025-11-03 09:40",
             "Yep, same thing as the last spike. The partner drops idle connections after 60s but our pool keeps "
             "them, so we send requests on dead sockets and they time out. First thing I always check: "
             "`gwctl pool-status` and look at the stale count. If stale > 20%, that's it."),
            (ARJUN, "2025-11-03 09:42", "What's the fix?"),
            (RAVI, "2025-11-03 09:48",
             "Drain then restart the adapter: `gwctl drain` then `kubectl rollout restart deploy/pg-adapter`. "
             "Do NOT restart between 02:00 and 03:00 UTC, the settlement batch reads through it."),
            (RAVI, "2025-11-03 10:05",
             "Auth success back to 99%. Verify on the 'PG Auth' Grafana dashboard, success rate should be above 98% "
             "within 5 minutes. If not, call Daniel at the acquirer NOC, +91 98450 12345."),
            (ARJUN, "2025-11-03 10:08", "Thanks. Will you document this?"),
        ], ["incident", "payment-gateway"], oncall),
        _thread(C, "CHAT-102", "Gateway 504 on refunds (INC-155)", [
            (ARJUN, "2026-01-19 14:07", "@ravi refunds through the payment gateway are returning 504s. INC-155."),
            (RAVI, "2026-01-19 14:15", "Same as INC-142. Stale connections again. Checking `gwctl pool-status`."),
            (RAVI, "2026-01-19 14:30",
             "Drained and restarted pg-adapter. This time I also lowered `POOL_MAX_IDLE_SECONDS` from 300 to 45 in "
             "the configmap so we recycle before the partner's 60s cut-off. Should help but it isn't a guarantee."),
            (ARJUN, "2026-01-19 14:36", "Refunds working again."),
            (RAVI, "2026-01-19 14:40", "Good. If it recurs the order is: pool-status, drain, restart, check Grafana."),
        ], ["incident", "payment-gateway"], oncall),
        _thread(C, "CHAT-103", "Gateway timeouts after cert rotation (INC-171)", [
            (ARJUN, "2026-04-08 07:48", "@ravi gateway timeouts again, started right after the partner cert rotation. INC-171"),
            (RAVI, "2026-04-08 07:55",
             "Pool-status looks fine this time, so it's not the stale connection problem. It's the cert."),
            (RAVI, "2026-04-08 08:20",
             "The adapter pins the partner's TLS cert. When they rotate, the pin is wrong and the handshake fails "
             "silently, which looks like a timeout. Fix: update `PARTNER_CERT_PIN` in the pg-adapter configmap with "
             "the new fingerprint (partner emails it, or `openssl s_client -connect` to get it), then restart."),
            (MEERA, "2026-04-08 08:25", "Is this written down anywhere? I couldn't find it on the wiki."),
            (RAVI, "2026-04-08 08:27", "Not yet, it's in my head. Sorry. I'll add it to the list."),
            (ARJUN, "2026-04-08 11:45", "Auth rate is back above 98%. Thanks Ravi."),
        ], ["incident", "payment-gateway"], oncall),
        _thread(C, "CHAT-104", "Gateway outage during flash sale (INC-188)", [
            (ARJUN, "2026-07-22 18:33", "@ravi PG down during the flash sale, INC-188. Traffic is 4x."),
            (RAVI, "2026-07-22 18:40",
             "Pool-status shows exhaustion, not stale connections. 50 connections max isn't enough at this volume."),
            (RAVI, "2026-07-22 18:52",
             "Temporary fix: raise `POOL_MAX_SIZE` from 50 to 200 and restart. Remember to put it back to 50 "
             "afterwards, 200 hits the partner's rate limit if kept for long."),
            (ARJUN, "2026-07-22 19:10", "Recovering. Success rate 97%... 99%."),
            (RAVI, "2026-07-22 20:30",
             "Third time I've fixed this by hand. Three different triggers (stale sockets, cert pin, pool size) but "
             "the same first step: always `gwctl pool-status` to tell which. We should automate it."),
            (ARJUN, "2026-07-22 20:35", "Agreed, and someone else should know this too."),
        ], ["incident", "payment-gateway"], oncall),
        # --- Planted risk 2: settlement workarounds live ONLY in Arjun's chats ---
        _thread(C, "CHAT-201", "Settlement batch stuck (INC-150)", [
            (PRIYA, "2025-12-12 06:12", "Is the settlement batch stuck? Merchants are asking about payouts."),
            (ARJUN, "2025-12-12 06:20",
             "Yes, it's waiting on the bank file at the reconciliation step. The bank SFTP drop is late again."),
            (ARJUN, "2025-12-12 06:25",
             "Workaround: rerun with `--skip-recon` if the bank file is late. Payouts go out on our ledger. Then "
             "once the file lands I run `settlement_batch.py --recon-only --date <yesterday>` to reconcile."),
            (PRIYA, "2025-12-12 06:30", "Is that safe?"),
            (ARJUN, "2025-12-12 06:33",
             "Yes as long as you pass the same `--idempotency-key` as the original run, otherwise it double pays. "
             "That's the gotcha."),
        ], ["incident", "settlement"], ops),
        _thread(C, "CHAT-202", "Settlement totals mismatch (INC-163)", [
            (ARJUN, "2026-03-02 08:02", "Finance says settlement totals are off for two merchants. Looking."),
            (ARJUN, "2026-03-02 09:10",
             "Cause: refunds posted after the 23:59 cut-off are counted in the next batch but the bank statement "
             "counted them same day. I fixed it by moving them with the SQL in `scripts/shift_refunds.sql` and "
             "re-running with `--recon-only`."),
            (RAVI, "2026-03-02 09:20", "Not my area, but do you want me to review the SQL?"),
            (ARJUN, "2026-03-02 09:22", "No, it's fine, I've done it before. Always take a DB snapshot first."),
            (ARJUN, "2026-03-02 13:05", "Finance confirmed totals match."),
        ], ["incident", "settlement"], ops),
        _thread(C, "CHAT-203", "Settlement parse failure after bank format change (INC-180)", [
            (ARJUN, "2026-06-15 05:44", "Settlement failed overnight. The bank added a column to the file."),
            (ARJUN, "2026-06-15 06:30",
             "Fix: update the column mapping in `config/bank_schema.yaml` (add the new column name, keep the old "
             "order), then rerun. The parser is strict so any new column breaks it."),
            (MEERA, "2026-06-15 07:00", "Should that be validated before the batch runs?"),
            (ARJUN, "2026-06-15 07:05", "Yes, good idea, but nobody has had time. For now it's just me watching it."),
            (ARJUN, "2026-06-15 08:40", "Batch completed."),
        ], ["incident", "settlement"], ops),
        _thread(C, "CHAT-204", "What do I do if the bank file is late?", [
            (MEERA, "2026-05-11 15:00", "Arjun, if I'm covering and the settlement bank file is late, what do I do?"),
            (ARJUN, "2026-05-11 15:06",
             "Rerun with `--skip-recon`, then `--recon-only` once the file arrives. Always the same idempotency key. "
             "The job runs at 02:00 UTC, the bank file normally lands by 01:30 but not always."),
            (MEERA, "2026-05-11 15:08", "Where's the doc for that?"),
            (ARJUN, "2026-05-11 15:10", "Honestly just me. I keep meaning to write it down."),
        ], ["settlement"], ops),
        # --- Well-documented contrast topics ---
        _thread(C, "CHAT-301", "How do I cut a release?", [
            (MEERA, "2026-05-08 10:00", "How do I do the staging deploy for the release?"),
            (PRIYA, "2026-05-08 10:04", "Follow the Release Process page on the wiki, step 3 onwards."),
            (MEERA, "2026-05-08 10:30", "Done, the checklist was clear. Thanks."),
        ], ["release"], dev),
        _thread(C, "CHAT-302", "API token 401s", [
            (MEERA, "2026-06-04 11:00", "Batch client gets 401 after 15 minutes. Is that expected?"),
            (ARJUN, "2026-06-04 11:05", "Yes, see the API Authentication guide on the wiki, section on token expiry."),
            (PRIYA, "2026-06-04 11:07", "The expiry is configurable, the guide shows how."),
        ], ["api-auth"], dev),
        _thread(C, "CHAT-303", "First week setup", [
            (MEERA, "2026-03-17 09:30", "Docker compose fails on my machine, any ideas?"),
            (ARJUN, "2026-03-17 09:40", "Check the Troubleshooting section of the Onboarding Guide."),
            (MEERA, "2026-03-17 10:05", "That fixed it, the guide is great."),
            (PRIYA, "2026-03-17 10:10", "Please add anything missing to the guide as you go."),
        ], ["onboarding"], dev),
        _thread(C, "CHAT-401", "Ravi's last day", [
            (ARJUN, "2026-09-02 12:00", "Ravi's last day is 15 Nov. Does anyone else know how to fix the gateway?"),
            (PRIYA, "2026-09-02 12:05", "Honestly no. That's the problem we need to solve."),
            (ARJUN, "2026-09-02 12:08", "Same for settlement tbh. We should both write things down."),
        ], ["handover", "payment-gateway", "settlement"], dev),
        # --- Noise ---
        _thread(C, "CHAT-901", "Standup", [
            (PRIYA, "2026-02-03 09:30", "Standup in 5. Anyone blocked?"),
            (MEERA, "2026-02-03 09:31", "Not blocked, working on the dashboard spike."),
            (RAVI, "2026-02-03 09:32", "Heads down on reviews today."),
        ], ["noise"], dev),
        _thread(C, "CHAT-902", "Lunch", [
            (ARJUN, "2026-08-14 12:00", "Lunch at the new place?"),
            (MEERA, "2026-08-14 12:01", "Yes please!"),
            (RAVI, "2026-08-14 12:03", "Give me 10 minutes."),
        ], ["noise"], dev),
    ]


def _doc(
    slug: str, title: str, author: str, when: str, body: str, contributors: list[str], tags: list[str]
) -> Artifact:
    return Artifact(
        id=f"art_doc_{slug}",
        type=ArtifactType.DOC,
        source_id=f"wiki/{slug}",
        author=author,
        participants=sorted(set(contributors) - {author}),
        timestamp=_when(when),
        title=title,
        body=f"# {title}\n\n{body.strip()}",
        url_or_path=f"https://wiki.contoso-payments.example/payments/{slug}",
        tags=tags,
        metadata={"space": "payments", "contributors": sorted(set(contributors) | {author})},
    )


def build_wiki() -> list[Artifact]:
    """Six wiki pages. Deliberately NONE for the gateway, settlement or Kafka decision."""
    return [
        _doc("release-process", "Release Process", PRIYA, "2025-06-10 10:00", """
Releases go out every second Tuesday. Anyone on the team can run one by following this page.

## Steps
1. Freeze `main` on Monday 16:00 and confirm CI is green.
2. Cut the release branch `release/YYYY-MM-DD` and tag it.
3. Deploy to staging with the pipeline job `deploy-staging` (automated since PAY-318).
4. Run the smoke test suite and check the checkout latency dashboard.
5. Switch traffic gradually: 5%, 25%, 100% with ten minutes between steps.
6. Announce in #payments-dev and update the changelog.

## Hotfix flow
Branch from the last release tag, fix, run smoke tests, deploy straight to production with approval from one
other engineer, then merge back to `main`.

## Rollback
Re-deploy the previous tag with `deploy-prod --tag <previous>`. Rollbacks need no approval.
""", [PRIYA, ARJUN, RAVI, MEERA], ["release", "process"]),
        _doc("onboarding-guide", "Onboarding Guide", PRIYA, "2025-07-01 10:00", """
Welcome! This guide gets a new engineer productive in the first week.

## Day 1: access checklist
Request: GitHub org, the CI system, the tracker, wiki, Teams channels (#payments-dev, #payments-oncall),
Grafana, and the staging VPN. Your buddy approves the requests.

## Environment setup
1. Clone the monorepo and install Docker Desktop.
2. Run `make dev-up` to start the services with docker compose.
3. Run `make seed` to load sample data, then `make test`.

## Troubleshooting
- Docker compose fails: increase Docker memory to 8 GB and re-run `make dev-up`.
- Port 5432 busy: stop your local Postgres.

## First-week plan
Day 2 read the architecture overview; Day 3 pick a starter ticket; Day 5 ship your first change.
""", [PRIYA, MEERA, ARJUN], ["onboarding"]),
        _doc("api-authentication", "API Authentication Guide", PRIYA, "2025-05-05 10:00", """
All public APIs use OAuth 2.0 client credentials with signed JWT access tokens.

## Tokens
Tokens are valid for 15 minutes by default. Batch clients can request a longer lifetime via
`TOKEN_TTL_SECONDS` (maximum 4 hours) in their client config.

## Scopes
`payments:read`, `payments:write`, `refunds:write`, `settlements:read`. Request only what a client needs.

## Signing keys
Signing keys are rotated quarterly. To rotate: generate a new key pair in the key vault, publish the new public
key to the JWKS endpoint, switch the signer, wait 24 hours, then retire the old key.

## Troubleshooting 401s
Check token expiry first, then scope, then clock skew (more than 60 seconds breaks validation).
""", [PRIYA, RAVI, ARJUN, MEERA], ["api-auth", "security"]),
        _doc("coding-standards", "Coding Standards", PRIYA, "2025-04-01 10:00", """
- Python 3.11, type hints everywhere, `ruff` and `black` in CI.
- Every PR needs one reviewer and passing tests.
- Commit messages use the imperative mood and reference the ticket key.
- No secrets in code; use the key vault.
""", [PRIYA, RAVI], ["engineering"]),
        _doc("architecture-overview", "Architecture Overview", PRIYA, "2025-09-15 10:00", """
High-level view of the Payments Platform.

- **Checkout API** receives payment requests and publishes events.
- **Event bus**: payment events flow through Kafka.
- **Payment gateway adapter**: talks to the acquiring partner.
- **Settlement batch job**: runs nightly and pays out merchants.
- **Reporting**: dashboards in Grafana.

See the onboarding guide for how to run the system locally.
""", [PRIYA], ["architecture"]),
        _doc("support-basics", "Support Escalation Basics", ARJUN, "2025-08-12 10:00", """
## Severity levels
P1: payments failing for many merchants. P2: degraded or single-merchant impact. P3: questions.

## Escalation
Post in #payments-oncall with the ticket key and impact. P1 pages the on-call engineer. Update the status page
every 30 minutes during a P1 and write a short summary when it closes.

## Customer communication
Use the approved templates in the support folder; never promise a resolution time.
""", [ARJUN, PRIYA], ["support", "process"]),
    ]


_GW = "services/pg_adapter"
_ST = "services/settlement"


def build_commits() -> list[Artifact]:
    """~40 commits. Ravi -> gateway, Arjun -> settlement, others spread out."""
    rows: list[tuple[str, str, str, list[str], list[str]]] = [
        # (author, when, message, files, systems)
        (RAVI, "2025-04-14 10:10", "Add payment gateway adapter skeleton", [f"{_GW}/client.py", f"{_GW}/config.py"], ["payment-gateway"]),
        (RAVI, "2025-04-22 15:30", "Implement authorisation call with retries to gateway", [f"{_GW}/client.py"], ["payment-gateway"]),
        (RAVI, "2025-05-06 11:00", "Add connection pool to gateway adapter", [f"{_GW}/pool.py"], ["payment-gateway"]),
        (RAVI, "2025-06-20 16:45", "Add Kafka consumer for settlement events (PAY-204)", ["services/consumers/settlement_events.py"], ["kafka-event-bus", "settlement-batch-job"]),
        (RAVI, "2025-07-08 09:20", "Gateway adapter: map partner error codes", [f"{_GW}/errors.py"], ["payment-gateway"]),
        (RAVI, "2025-08-19 14:00", "Add gwctl CLI for pool inspection", [f"{_GW}/gwctl.py"], ["payment-gateway"]),
        (RAVI, "2025-11-03 10:40", "Gateway adapter: add gwctl drain command (INC-142)", [f"{_GW}/gwctl.py"], ["payment-gateway"]),
        (RAVI, "2026-01-19 14:50", "Lower POOL_MAX_IDLE_SECONDS to 45 for gateway pool (INC-155)", [f"{_GW}/config.py", "deploy/pg-adapter-configmap.yaml"], ["payment-gateway"]),
        (RAVI, "2026-02-10 11:15", "Rotate API signing keys (PAY-301)", ["services/auth/jwks.py"], ["api-authentication"]),
        (RAVI, "2026-04-08 12:00", "Make PARTNER_CERT_PIN configurable in gateway adapter (INC-171)", [f"{_GW}/tls.py", "deploy/pg-adapter-configmap.yaml"], ["payment-gateway"]),
        (RAVI, "2026-05-08 16:00", "Automate staging deploy job (PAY-318)", ["ci/deploy-staging.yml"], ["release-process"]),
        (RAVI, "2026-07-22 19:30", "Gateway pool: allow POOL_MAX_SIZE override (INC-188)", [f"{_GW}/pool.py", f"{_GW}/config.py"], ["payment-gateway"]),
        (RAVI, "2026-08-11 10:00", "Gateway adapter: log stale connection count", [f"{_GW}/pool.py"], ["payment-gateway"]),
        (ARJUN, "2025-04-30 09:00", "Add settlement batch job entrypoint", [f"{_ST}/settlement_batch.py"], ["settlement-batch-job"]),
        (ARJUN, "2025-05-20 13:30", "Settlement: add bank file SFTP fetcher", [f"{_ST}/bank_fetch.py"], ["settlement-batch-job"]),
        (ARJUN, "2025-06-25 10:10", "Settlement: reconciliation step against bank statement", [f"{_ST}/reconcile.py"], ["settlement-batch-job"]),
        (ARJUN, "2025-08-05 15:00", "Settlement: add --idempotency-key to prevent double payout", [f"{_ST}/settlement_batch.py"], ["settlement-batch-job"]),
        (ARJUN, "2025-12-12 06:40", "Settlement: add --skip-recon and --recon-only flags (INC-150)", [f"{_ST}/settlement_batch.py"], ["settlement-batch-job"]),
        (ARJUN, "2026-01-15 09:30", "Fix release checklist step order (PAY-290)", ["docs/release-checklist.md"], ["release-process"]),
        (ARJUN, "2026-03-02 09:30", "Settlement: add shift_refunds.sql for cut-off mismatches (INC-163)", [f"{_ST}/scripts/shift_refunds.sql"], ["settlement-batch-job"]),
        (ARJUN, "2026-04-22 11:00", "Add refunds OAuth scope to client registry (PAY-312)", ["services/auth/scopes.py"], ["api-authentication"]),
        (ARJUN, "2026-06-15 06:45", "Settlement: add new column to bank_schema.yaml (INC-180)", [f"{_ST}/config/bank_schema.yaml"], ["settlement-batch-job"]),
        (ARJUN, "2026-07-14 10:20", "Settlement: log row counts after each stage", [f"{_ST}/settlement_batch.py"], ["settlement-batch-job"]),
        (ARJUN, "2026-08-06 14:00", "Onboarding: add access checklist (PAY-341)", ["docs/onboarding.md"], ["onboarding"]),
        (PRIYA, "2025-03-25 10:00", "Provision Kafka topics for payment events (PAY-118)", ["infra/kafka/topics.tf"], ["kafka-event-bus"]),
        (PRIYA, "2025-05-05 12:00", "Add OAuth client credentials flow", ["services/auth/oauth.py"], ["api-authentication"]),
        (PRIYA, "2025-06-10 10:30", "Add release pipeline definition", ["ci/release.yml"], ["release-process"]),
        (PRIYA, "2025-09-15 11:00", "Add architecture overview diagram", ["docs/architecture.md"], ["architecture"]),
        (PRIYA, "2025-10-02 09:45", "Increase Kafka partitions to 12", ["infra/kafka/topics.tf"], ["kafka-event-bus"]),
        (PRIYA, "2026-04-22 11:30", "Document refunds scope in API auth guide (PAY-312)", ["docs/api-auth.md"], ["api-authentication"]),
        (PRIYA, "2026-08-04 09:00", "Update onboarding for new dev environment (PAY-340)", ["docs/onboarding.md"], ["onboarding"]),
        (MEERA, "2026-03-20 15:00", "Fix docker compose memory limits in onboarding", ["docker-compose.yml"], ["onboarding"]),
        (MEERA, "2026-04-14 10:00", "Add unit tests for checkout API validation", ["tests/test_checkout.py"], ["checkout-api"]),
        (MEERA, "2026-05-19 14:20", "Add retry to checkout idempotency lookup", ["services/checkout/idempotency.py"], ["checkout-api"]),
        (MEERA, "2026-06-04 12:00", "Make token TTL configurable (PAY-327)", ["services/auth/oauth.py"], ["api-authentication"]),
        (MEERA, "2026-07-01 16:00", "Fix rounding bug in fee display (PAY-335)", ["services/checkout/fees.py"], ["checkout-api"]),
        (MEERA, "2026-08-04 10:30", "Update onboarding steps I hit while setting up (PAY-340)", ["docs/onboarding.md"], ["onboarding"]),
        (MEERA, "2026-08-20 13:00", "Add checkout p95 latency dashboard (PAY-350)", ["dashboards/checkout_latency.json"], ["checkout-api"]),
        (MEERA, "2026-09-09 11:00", "Add tests for Kafka consumer offset handling", ["tests/test_consumers.py"], ["kafka-event-bus"]),
    ]
    out = []
    for i, (who, when, msg, files, systems) in enumerate(rows):
        sha = hashlib.sha1(f"{SEED}:{i}:{msg}".encode()).hexdigest()[:10]
        out.append(Artifact(
            id=f"art_commit_{sha}",
            type=ArtifactType.COMMIT,
            source_id=sha,
            author=who,
            timestamp=_when(when),
            title=msg,
            body=f"{msg}\n\nFiles changed:\n" + "\n".join(f"- {f}" for f in files),
            url_or_path=f"https://git.contoso-payments.example/payments/commit/{sha}",
            tags=["commit"],
            metadata={"sha": sha, "files": files, "systems": systems, "repo": "payments-platform"},
        ))
    return out


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _dump(records: list) -> str:
    data = [r.model_dump(mode="json") for r in records]
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


def generate(out: Path) -> dict[str, int]:
    random.seed(SEED)  # reserved for jitter in later generators; keeps runs reproducible
    out.mkdir(parents=True, exist_ok=True)
    sources = {
        "people.json": build_people(),
        "tickets.json": build_tickets(),
        "emails.json": build_emails(),
        "chats.json": build_chats(),
        "wiki.json": build_wiki(),
        "commits.json": build_commits(),
    }
    counts = {}
    for name, records in sources.items():
        # Sort artifacts chronologically (stable) so diffs stay readable.
        if records and isinstance(records[0], Artifact):
            records = sorted(records, key=lambda a: (a.timestamp, a.source_id))
        (out / name).write_text(_dump(records), encoding="utf-8")
        counts[name] = len(records)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()
    for name, n in generate(args.out).items():
        print(f"{name:14s} {n:3d}")


if __name__ == "__main__":
    main()
