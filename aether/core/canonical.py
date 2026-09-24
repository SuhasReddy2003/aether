"""Deterministic canonical JSON.

The hash chain is only tamper-evident if identical logical content always
produces identical bytes. Python dict ordering, float formatting, and
non-ASCII handling can all silently break that. This module is the single
place that turns a Python value into the exact bytes that get hashed.
"""
from __future__ import annotations

import json
import math
from typing import Any


def _normalize(value: Any) -> Any:
    """Recursively normalize a value into JSON-safe, order-stable data.

    - dict keys are sorted so key insertion order never affects output
    - floats that are NaN/Infinity are rejected (not valid canonical JSON)
    - tuples are normalized to lists (JSON has no tuple type)
    """
    if isinstance(value, dict):
        return {str(k): _normalize(value[k]) for k in sorted(value.keys(), key=str)}
    if isinstance(value, (list, tuple)):
        return [_normalize(v) for v in value]
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            raise ValueError("canonical_json does not support NaN/Infinity floats")
        return value
    return value


def canonical_json(value: Any) -> bytes:
    """Serialize `value` to canonical, deterministic UTF-8 JSON bytes.

    Guarantees for any two calls with logically-equal input (same keys/values
    regardless of dict insertion order):
      1. byte-identical output
      2. sorted object keys
      3. no insignificant whitespace
      4. ensure_ascii=False with UTF-8 encoding (stable across locales)
    """
    normalized = _normalize(value)
    text = json.dumps(
        normalized,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return text.encode("utf-8")
