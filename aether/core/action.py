"""The Action model: the unit of everything Aether records."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from aether.core.side_effects import SideEffect


class ActionStatus(str, Enum):
    SUCCESS = "success"
    ERROR = "error"


class ActionSource(str, Enum):
    AGENT = "agent"
    SYSTEM = "system"
    ROLLBACK = "rollback"
    REPLAY = "replay"


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def utcnow() -> datetime:
    return datetime.now(UTC)


class Authorization(BaseModel):
    role: str
    agent_id: str | None = None
    scopes: list[str] = Field(default_factory=list)


class Action(BaseModel):
    """A single recorded invocation of a tool by an agent."""

    action_id: str = Field(default_factory=lambda: new_id("act"))
    run_id: str
    step: int
    agent_id: str
    agent_version: str = "v1"
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: Any = None
    error: str | None = None
    status: ActionStatus = ActionStatus.SUCCESS
    source: ActionSource = ActionSource.AGENT
    authorization: Authorization
    side_effects: list[SideEffect] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=utcnow)
    duration_ms: float = 0.0
    parent_action_id: str | None = None
    untrusted_output: bool = Field(
        default=False,
        description="True if this action's result is untrusted content (e.g. reading an "
        "email or web page) for taint-tracking purposes. Persisted as a real field so it "
        "is part of the recorded, hash-chained event, not an ephemeral in-memory flag.",
    )

    def to_record(self) -> dict[str, Any]:
        """JSON-safe dict for hashing/storage (datetimes -> ISO strings)."""
        data = self.model_dump(mode="json")
        return data
