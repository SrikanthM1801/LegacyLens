"""
Basic PII redaction, applied before anything is chunked, embedded or indexed.

Redacts (in this order): external email addresses, payment card numbers
(Luhn-validated, so order ids are not eaten), and phone numbers. Org email
addresses are kept because they identify the people the knowledge graph needs.
This is intentionally a small, explainable pass, not a full DLP pipeline.
All functions are pure and idempotent.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Iterable, Optional

from app.config import get_settings
from app.models import Artifact

EMAIL_PLACEHOLDER = "[REDACTED_EMAIL]"
CARD_PLACEHOLDER = "[REDACTED_CARD]"
PHONE_PLACEHOLDER = "[REDACTED_PHONE]"

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@([A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)+)")
_CARD = re.compile(r"(?<![\w-])(?:\d[ -]?){12,18}\d(?![\w-])")
# A run of digits with common phone separators; validated further in code.
_PHONE = re.compile(r"(?<![\w])\+?\(?\d[\d \-().]{8,}\d(?![\w])")
# Things that look like long digit runs but are dates / times.
_DATELIKE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2:
            d = d * 2 - 9 if d * 2 > 9 else d * 2
        total += d
    return total % 10 == 0


def _redact_cards(text: str, counts: Counter) -> str:
    def repl(m: re.Match) -> str:
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn_ok(digits):
            counts["card"] += 1
            return CARD_PLACEHOLDER
        return m.group()

    return _CARD.sub(repl, text)


def _redact_phones(text: str, counts: Counter) -> str:
    def repl(m: re.Match) -> str:
        cand = m.group()
        digits = re.sub(r"\D", "", cand)
        if not 10 <= len(digits) <= 15 or _DATELIKE.search(cand):
            return cand
        counts["phone"] += 1
        return PHONE_PLACEHOLDER

    return _PHONE.sub(repl, text)


def redact_text(text: str, org_domains: Optional[Iterable[str]] = None) -> tuple[str, Counter]:
    """Return (redacted_text, counts_by_kind). `org_domains` defaults to settings."""
    keep = frozenset(d.lower() for d in org_domains) if org_domains is not None else get_settings().org_domains
    counts: Counter = Counter()

    def email_repl(m: re.Match) -> str:
        if m.group(1).lower() in keep:
            return m.group()
        counts["email"] += 1
        return EMAIL_PLACEHOLDER

    out = _EMAIL.sub(email_repl, text)
    out = _redact_cards(out, counts)
    out = _redact_phones(out, counts)
    return out, counts


def redact_artifact(a: Artifact, org_domains: Optional[Iterable[str]] = None) -> tuple[Artifact, Counter]:
    """Redact title and body. Returns the (possibly new) artifact and counts."""
    body, c1 = redact_text(a.body, org_domains)
    title, c2 = redact_text(a.title, org_domains)
    counts = c1 + c2
    if not counts:
        return a, counts
    return a.model_copy(update={"body": body, "title": title}), counts
