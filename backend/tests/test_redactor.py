from collections import Counter
from pathlib import Path

import pytest

from app.ingestion.loaders import load_dataset
from app.ingestion.redactor import (
    CARD_PLACEHOLDER, EMAIL_PLACEHOLDER, PHONE_PLACEHOLDER, redact_artifact, redact_text,
)

ROOT = Path(__file__).resolve().parents[2]
ORG = ["contoso-payments.com"]


def r(text: str) -> str:
    return redact_text(text, ORG)[0]


def test_external_emails_redacted_org_emails_kept():
    out = r("mail jane.doe@gmail.com or ravi.kumar@contoso-payments.com or x@Partner.IO")
    assert out == f"mail {EMAIL_PLACEHOLDER} or ravi.kumar@contoso-payments.com or {EMAIL_PLACEHOLDER}"


@pytest.mark.parametrize("phone", ["+91 98450 12345", "+1 (415) 555-0132", "98450-12345", "(080) 2345 6789"])
def test_phone_numbers_redacted(phone):
    assert r(f"call {phone} now") == f"call {PHONE_PLACEHOLDER} now"


@pytest.mark.parametrize("card", ["4111 1111 1111 1111", "4111-1111-1111-1111", "5500005555555559"])
def test_valid_cards_redacted(card):
    assert r(f"card {card}.") == f"card {CARD_PLACEHOLDER}."


@pytest.mark.parametrize("safe", [
    "4111 1111 1111 1112",              # fails Luhn
    "2026-03-02 09:10", "2025-11-03T09:25:00Z", "[2026-01-19 14:07] Arjun:",
    "INC-142 and PAY-118", "commit 7cc6388a1f", "p95 latency 1200 ms", "12 partitions", "1234567890123456789012",
])
def test_lookalikes_are_untouched(safe):
    assert r(safe) == safe


def test_idempotent_and_counts():
    text = "a@b.com 4111111111111111 +91 98450 12345"
    once, counts = redact_text(text, ORG)
    assert counts == Counter(email=1, card=1, phone=1)
    assert redact_text(once, ORG)[0] == once


def test_redact_artifact_returns_same_object_when_clean():
    art = load_dataset(ROOT / "data" / "sample").artifacts[0]
    out, counts = redact_artifact(art, ORG)
    assert out is art and not counts


def test_sample_dataset_only_loses_the_planted_phone_number():
    total: Counter = Counter()
    for a in load_dataset(ROOT / "data" / "sample").artifacts:
        red, c = redact_artifact(a, ORG)
        total += c
        if c:
            assert PHONE_PLACEHOLDER in red.body and "98450" not in red.body
            assert a.body.replace("+91 98450 12345", PHONE_PLACEHOLDER) == red.body  # nothing else changed
    assert total == Counter(phone=1)
