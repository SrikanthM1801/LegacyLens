"""
Seed vocabulary of topics (systems, processes, decisions) the extractor knows.

It serves two purposes: the offline rule-based extractor matches these aliases,
and the LLM extractor receives them as KNOWN entities so "PG", "gateway" and
"payment gateway" all resolve to one node. The LLM may add new entities beyond
this list; it never has to.

`slug` equals the names used in ticket/commit `metadata["systems"]`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import EntityType


@dataclass(frozen=True)
class Topic:
    id: str
    slug: str
    type: EntityType
    name: str
    description: str
    aliases: tuple[str, ...]
    #: words that, in a speaker's own message, mean they made/drove this decision
    decision_cues: tuple[str, ...] = field(default=())
    #: for decisions: id of the system the decision concerns
    affects: str = ""


TOPICS: tuple[Topic, ...] = (
    Topic("sys_payment_gateway", "payment-gateway", EntityType.SYSTEM, "Payment Gateway",
          "Adapter that sends card authorisations and refunds to the acquiring partner.",
          ("payment gateway", "gateway", "pg-adapter", "pg adapter", "pg_adapter", "gwctl", "PG")),
    Topic("sys_settlement_batch_job", "settlement-batch-job", EntityType.SYSTEM, "Settlement Batch Job",
          "Nightly job that reconciles with the bank file and pays out merchants.",
          ("settlement", "settlement batch", "settlement_batch", "reconciliation", "recon", "bank file", "bank_schema")),
    Topic("sys_kafka_event_bus", "kafka-event-bus", EntityType.SYSTEM, "Kafka Event Bus",
          "Kafka topics carrying payment events (authorised, captured, settled).",
          ("kafka",)),
    Topic("sys_checkout_api", "checkout-api", EntityType.SYSTEM, "Checkout API",
          "Receives payment requests and publishes payment events.",
          ("checkout api", "checkout")),
    Topic("sys_api_authentication", "api-authentication", EntityType.SYSTEM, "API Authentication",
          "OAuth 2.0 client-credentials flow, scopes and signing keys for public APIs.",
          ("api authentication", "api auth", "oauth", "jwt", "jwks", "signing key", "signing keys", "token", "tokens")),
    Topic("proc_release_process", "release-process", EntityType.PROCESS, "Release Process",
          "Fortnightly release and hotfix procedure.",
          ("release process", "release", "releases", "hotfix", "staging deploy", "deploy-staging")),
    Topic("proc_onboarding", "onboarding", EntityType.PROCESS, "Onboarding",
          "How new engineers get access and set up their environment.",
          ("onboarding", "new joiner", "first week", "first-week")),
    Topic("dec_kafka_over_rabbitmq", "kafka-over-rabbitmq", EntityType.DECISION, "Kafka over RabbitMQ",
          "Decision to use Kafka rather than RabbitMQ as the payment message broker.",
          ("rabbitmq", "message broker", "event bus", "kafka vs rabbitmq", "kafka over rabbitmq", "broker"),
          decision_cues=("recommend", "decision made", "decision taken", "decided", "going ahead", "selected", "we chose"),
          affects="sys_kafka_event_bus"),
)

BY_SLUG: dict[str, Topic] = {t.slug: t for t in TOPICS}
