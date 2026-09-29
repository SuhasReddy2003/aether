"""Generic state snapshot and structured diff.

A `StateSnapshot` is a content-addressed capture of some resource (a set of
DB tables, a filesystem tree, ...) as plain JSON-safe data. `diff_snapshots`
produces a structured before/after diff that both the Shadow World and the
undo/rollback machinery build on.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from aether.core.canonical import canonical_json


def content_hash(data: Any) -> str:
    return hashlib.sha256(canonical_json(data)).hexdigest()


@dataclass
class StateSnapshot:
    label: str
    data: dict[str, Any]
    content_hash_value: str = field(init=False)

    def __post_init__(self) -> None:
        self.content_hash_value = content_hash(self.data)

    def to_dict(self) -> dict[str, Any]:
        return {"label": self.label, "content_hash": self.content_hash_value, "data": self.data}


@dataclass
class StateDiff:
    added: dict[str, Any] = field(default_factory=dict)
    removed: dict[str, Any] = field(default_factory=dict)
    changed: dict[str, tuple[Any, Any]] = field(default_factory=dict)  # key -> (before, after)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.changed)

    def to_dict(self) -> dict[str, Any]:
        return {
            "added": self.added,
            "removed": self.removed,
            "changed": {k: {"before": v[0], "after": v[1]} for k, v in self.changed.items()},
        }


def diff_flat_maps(before: dict[str, Any], after: dict[str, Any]) -> StateDiff:
    """Diff two flat key->value maps (e.g. row_id -> row, or path -> content)."""
    diff = StateDiff()
    before_keys = set(before.keys())
    after_keys = set(after.keys())
    for key in after_keys - before_keys:
        diff.added[key] = after[key]
    for key in before_keys - after_keys:
        diff.removed[key] = before[key]
    for key in before_keys & after_keys:
        if canonical_json(before[key]) != canonical_json(after[key]):
            diff.changed[key] = (before[key], after[key])
    return diff


def restore_content_hash_matches(expected_hash: str, data: Any) -> bool:
    """Used by rollback fidelity checks: does `data` hash to `expected_hash`?"""
    return content_hash(data) == expected_hash
