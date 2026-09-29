"""Side effect taxonomy.

Every tool call declares what kind of effect it has and whether that effect
is reversible. Reversibility is never assumed true by default — a tool that
does not explicitly declare itself reversible is treated as NOT reversible,
because a false "yes, this can be undone" is far more dangerous than an
honest "no, it cannot."
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SideEffectType(str, Enum):
    READ = "read"
    WRITE = "write"
    CREATE = "create"
    UPDATE = "update"
    DELETE = "delete"
    EXTERNAL_CALL = "external_call"
    FINANCIAL = "financial"
    COMMUNICATION = "communication"
    IRREVERSIBLE = "irreversible"


class SideEffect(BaseModel):
    type: SideEffectType
    reversible: bool = False
    compensation: str | None = Field(
        default=None,
        description="Name of the compensating action, if reversible=True.",
    )
    amount: float | None = None
    resource: str | None = None

    def model_post_init(self, __context: object, /) -> None:
        # Never allow a declared compensation without reversible=True, and
        # never allow reversible=True on an IRREVERSIBLE-typed effect: that
        # combination is exactly the kind of dishonest reversibility claim
        # this system exists to prevent.
        if self.type == SideEffectType.IRREVERSIBLE and self.reversible:
            raise ValueError("an IRREVERSIBLE side effect cannot declare reversible=True")
        if self.reversible and self.compensation is None:
            raise ValueError("reversible=True requires a compensation action name")
