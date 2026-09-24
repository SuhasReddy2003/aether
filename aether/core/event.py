"""Event: an Action plus its position in the tamper-evident chain."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from aether.core.action import Action, new_id, utcnow


class Event(BaseModel):
    event_id: str = Field(default_factory=lambda: new_id("evt"))
    run_id: str
    parent_event_id: str | None = None
    action: Action
    previous_hash: str = ""
    hash: str = ""
    created_at: str = Field(default_factory=lambda: utcnow().isoformat())
    schema_version: int = 1

    def hashable_payload(self) -> dict[str, Any]:
        """The exact fields that go into the hash. Deliberately excludes
        `hash` itself (obviously) but includes everything else so that any
        mutation to any field is detectable."""
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "parent_event_id": self.parent_event_id,
            "action": self.action.to_record(),
            "previous_hash": self.previous_hash,
            "created_at": self.created_at,
            "schema_version": self.schema_version,
        }
