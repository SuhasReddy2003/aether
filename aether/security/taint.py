"""Taint tracking: session taint + value matching.

Two conservative, explainable mechanisms — reported as evidence with a
confidence score, never as proof (see docs/limitations.md for documented
false positives and false negatives):

1. **Session taint**: once untrusted content enters a run's context (any
   action whose output is declared `untrusted_output=True`, e.g. reading an
   email), every subsequent sensitive-sink action in that run is marked
   `tainted_context` — a broad, weak signal.
2. **Value matching**: for each sink action, check whether its argument
   values appear — verbatim, or after light normalization (currency
   symbols/commas/whitespace stripped) — inside previously ingested
   untrusted content. A match becomes `tainted_value`, with the exact
   matched span, its source event, and a higher confidence than context
   taint alone.

You cannot track values through an LLM's reasoning; this is a deliberate,
documented approximation, not a claim of dataflow-accurate taint analysis.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aether.core.event import Event

DEFAULT_SENSITIVE_SINKS = {
    "payment.create",
    "email.send",
    "database.delete",
    "credential.write",
    "external.upload",
    "mcp.write_file",
    "mcp.move_file",
    "mcp.delete_file",
}

# Values shorter than this (after normalization) are excluded from value
# matching entirely: matching e.g. "1" or "a" against arbitrary untrusted
# text produces overwhelming false positives with zero evidentiary value.
MIN_MATCH_LENGTH = 3


class TrustLevel(str, Enum):
    TRUSTED = "trusted"
    USER = "user"
    INTERNAL = "internal"
    EXTERNAL_UNTRUSTED = "external_untrusted"
    SENSITIVE = "sensitive"


@dataclass
class TaintedSpan:
    source_event_id: str
    source_tool: str
    content: str


@dataclass
class MatchedSpan:
    argument_key: str
    matched_text: str
    source_event_id: str
    source_tool: str
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "argument_key": self.argument_key,
            "matched_text": self.matched_text,
            "source_event_id": self.source_event_id,
            "source_tool": self.source_tool,
            "confidence": self.confidence,
        }


@dataclass
class TaintFinding:
    action_id: str
    tool: str
    tainted_context: bool
    tainted_value: bool = False
    matched_spans: list[MatchedSpan] = field(default_factory=list)
    confidence: float = 0.0

    @property
    def is_tainted(self) -> bool:
        return self.tainted_context or self.tainted_value

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "tool": self.tool,
            "tainted_context": self.tainted_context,
            "tainted_value": self.tainted_value,
            "matched_spans": [s.to_dict() for s in self.matched_spans],
            "confidence": self.confidence,
        }


def _value_to_match_string(value: Any) -> str:
    """Render a value the way it would plausibly appear in prose, so a
    numeric argument like `2840.00` (which Python stringifies as `2840.0`)
    still matches the text "$2,840" in an email body. This is itself a
    narrow, documented heuristic — it does not attempt full numeral
    normalization (e.g. spelled-out numbers), see docs/limitations.md."""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        if value == int(value):
            return str(int(value))
        return str(value)
    return str(value)


# Invisible characters an attacker can splice into a value to break substring
# matching (found by the Phase 3 self-audit: a zero-width space defeated it).
_ZERO_WIDTH = dict.fromkeys(map(ord, "\u200b\u200c\u200d\u2060\ufeff\u00ad"))

# A SMALL table of common Cyrillic/Greek look-alikes -> Latin. This closes the
# specific homoglyph attack demonstrated in the audit; it is NOT a complete
# Unicode confusables implementation and says nothing about the many other
# look-alike characters (see docs/limitations.md).
_CONFUSABLES = str.maketrans({
    "\u0430": "a", "\u0435": "e", "\u043e": "o", "\u0440": "p", "\u0441": "c", "\u0445": "x",
    "\u0443": "y", "\u0456": "i", "\u0455": "s", "\u0458": "j", "\u0410": "a", "\u0412": "b",
    "\u0415": "e", "\u041a": "k", "\u041c": "m", "\u041d": "h", "\u041e": "o", "\u0420": "p",
    "\u0421": "c", "\u0422": "t", "\u0425": "x", "\u03bf": "o", "\u03bd": "v", "\u03b1": "a",
    "\u0391": "a", "\u0392": "b", "\u0395": "e", "\u039f": "o", "\u03a1": "p",
})


def _normalize_for_matching(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = text.translate(_ZERO_WIDTH)
    text = text.lower().translate(_CONFUSABLES)
    text = re.sub(r"[,$€£]", "", text)
    # Whitespace is removed entirely (not collapsed) so inserting spaces into a
    # value ("acct _991") no longer evades matching. Trade-off: slightly more
    # false positives from words joining across spaces.
    return re.sub(r"\s+", "", text)


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return " ".join(_stringify(v) for v in value.values())
    if isinstance(value, list):
        return " ".join(_stringify(v) for v in value)
    if value is None:
        return ""
    return str(value)


def _flatten_scalars(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    results: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            results.extend(_flatten_scalars(v, f"{prefix}.{k}" if prefix else str(k)))
    elif isinstance(value, list):
        for i, v in enumerate(value):
            results.extend(_flatten_scalars(v, f"{prefix}[{i}]"))
    elif value is not None and not isinstance(value, bool):
        results.append((prefix, value))
    return results


class TaintTracker:
    def __init__(self, sensitive_sinks: set[str] | None = None) -> None:
        self.sensitive_sinks = set(sensitive_sinks) if sensitive_sinks else set(DEFAULT_SENSITIVE_SINKS)
        self._tainted_spans: list[TaintedSpan] = []
        self._session_tainted = False

    def ingest(self, event: Event) -> None:
        """Feed one event (in run order) into the tracker. Registers
        untrusted output as tainted content and flips session taint on for
        the rest of this run."""
        if event.action.untrusted_output and event.action.status.value == "success":
            content = _stringify(event.action.result)
            if content:
                self._tainted_spans.append(
                    TaintedSpan(source_event_id=event.event_id, source_tool=event.action.tool, content=content)
                )
            self._session_tainted = True

    def check_sink(self, event: Event) -> TaintFinding | None:
        """Evaluate one action as a potential sink. Returns None if its
        tool isn't a configured sensitive sink. Call `ingest()` for every
        prior event in the run, in order, before calling this."""
        action = event.action
        if action.tool not in self.sensitive_sinks:
            return None

        finding = TaintFinding(action_id=action.action_id, tool=action.tool, tainted_context=self._session_tainted)

        for key, value in _flatten_scalars(action.arguments):
            normalized_value = _normalize_for_matching(_value_to_match_string(value))
            if len(normalized_value) < MIN_MATCH_LENGTH:
                continue
            for span in self._tainted_spans:
                normalized_content = _normalize_for_matching(span.content)
                if normalized_value in normalized_content:
                    finding.tainted_value = True
                    finding.matched_spans.append(
                        MatchedSpan(
                            argument_key=key,
                            matched_text=str(value),
                            source_event_id=span.source_event_id,
                            source_tool=span.source_tool,
                            confidence=0.9,
                        )
                    )

        if finding.matched_spans:
            finding.confidence = max(s.confidence for s in finding.matched_spans)
        elif finding.tainted_context:
            finding.confidence = 0.3  # broad/weak signal only

        return finding

    def analyze_run(self, events: list[Event]) -> list[TaintFinding]:
        """Convenience: ingest every event in order, checking sinks as it
        goes (a sink action can also itself be untrusted-producing, though
        none of the built-in demo tools are both)."""
        findings: list[TaintFinding] = []
        for event in events:
            finding = self.check_sink(event)
            if finding is not None:
                findings.append(finding)
            self.ingest(event)
        return findings
