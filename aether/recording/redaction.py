"""Redaction of sensitive fields, applied BEFORE hashing and storage.

Because redaction happens before hashing, a redacted field is redacted for
good: the original value is never in the chain, never in the database, and
cannot be recovered by verifying the signature harder. This is a real
constraint (you cannot un-redact for a legitimate audit) and is documented
as such in docs/limitations.md.
"""
from __future__ import annotations

import re
from typing import Any

DEFAULT_REDACT_KEYS = {
    "password",
    "api_key",
    "apikey",
    "secret",
    "token",
    "ssn",
    "card_number",
    "credit_card",
    "cvv",
}

EMAIL_RE = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
CARD_RE = re.compile(r"\b(?:\d[ -]*?){13,19}\b")

REDACTED_MARKER = "[REDACTED]"


class Redactor:
    def __init__(
        self,
        redact_keys: set[str] | None = None,
        redact_emails: bool = False,
        redact_card_numbers: bool = True,
    ) -> None:
        self.redact_keys = {k.lower() for k in (redact_keys or DEFAULT_REDACT_KEYS)}
        self.redact_emails = redact_emails
        self.redact_card_numbers = redact_card_numbers

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            return {k: self._redact_value(k, v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        return value

    def _redact_value(self, key: str, value: Any) -> Any:
        if key.lower() in self.redact_keys:
            return REDACTED_MARKER
        if isinstance(value, dict):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact(v) for v in value]
        if isinstance(value, str):
            return self._redact_string(value)
        return value

    def _redact_string(self, text: str) -> str:
        if self.redact_emails:
            text = EMAIL_RE.sub(REDACTED_MARKER, text)
        if self.redact_card_numbers:
            text = CARD_RE.sub(REDACTED_MARKER, text)
        return text
