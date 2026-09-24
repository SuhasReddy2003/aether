"""Tamper-evident hash chain over Events.

hash(event_i) = SHA-256( previous_hash || canonical_json(event_i without .hash) )

Genesis event uses previous_hash = "0" * 64.
"""
from __future__ import annotations

import hashlib

from aether.core.canonical import canonical_json
from aether.core.event import Event

GENESIS_HASH = "0" * 64


def compute_event_hash(event: Event) -> str:
    payload = canonical_json(event.hashable_payload())
    h = hashlib.sha256()
    h.update(event.previous_hash.encode("utf-8"))
    h.update(payload)
    return h.hexdigest()


def seal_event(event: Event, previous_hash: str) -> Event:
    """Set previous_hash and compute+set hash on a copy of the event."""
    sealed = event.model_copy(update={"previous_hash": previous_hash})
    sealed = sealed.model_copy(update={"hash": compute_event_hash(sealed)})
    return sealed


def verify_chain(events: list[Event]) -> tuple[bool, str | None]:
    """Verify a full ordered chain of events.

    Returns (is_valid, first_bad_event_id). first_bad_event_id is None if
    valid, otherwise the id of the *first* event (in order) whose hash does
    not match, whose previous_hash link is broken, or which is out of order.
    """
    expected_previous = GENESIS_HASH
    for event in events:
        if event.previous_hash != expected_previous:
            return False, event.event_id
        recomputed = compute_event_hash(event)
        if recomputed != event.hash:
            return False, event.event_id
        expected_previous = event.hash
    return True, None
