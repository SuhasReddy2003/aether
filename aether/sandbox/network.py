"""Shadow network: NEVER makes a real network request. Represents what a
network-touching tool *would* do as a typed, declared simulated effect.
This module has no socket/HTTP code in it at all, by design — there is
nothing here that could accidentally reach a real host.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aether.core.side_effects import SideEffect, SideEffectType


@dataclass
class SimulatedNetworkEffect:
    kind: str  # "external_call" | "money_movement" | "communication"
    target: str
    reversible: bool
    description: str
    amount: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_side_effect(self) -> SideEffect:
        type_map = {
            "money_movement": SideEffectType.FINANCIAL,
            "communication": SideEffectType.COMMUNICATION,
            "external_call": SideEffectType.EXTERNAL_CALL,
        }
        effect_type = type_map.get(self.kind, SideEffectType.EXTERNAL_CALL)
        return SideEffect(
            type=effect_type,
            reversible=self.reversible,
            compensation="external.manual_reversal" if self.reversible else None,
            amount=self.amount,
            resource=self.target,
        )


class ShadowNetwork:
    """A registry of simulated network effects. Call `simulate(...)` instead
    of ever calling `requests`/`httpx`/sockets directly."""

    def __init__(self) -> None:
        self.calls: list[SimulatedNetworkEffect] = []

    def simulate_call(self, target: str, description: str, reversible: bool = False) -> SimulatedNetworkEffect:
        effect = SimulatedNetworkEffect(kind="external_call", target=target, reversible=reversible, description=description)
        self.calls.append(effect)
        return effect

    def simulate_payment(self, target: str, amount: float, description: str = "") -> SimulatedNetworkEffect:
        # Money movement to an external system is, by policy, never marked
        # reversible here: real payment reversal (refund/chargeback) is an
        # out-of-band human/financial process, not something Aether can
        # guarantee, and pretending otherwise would violate the "never fake
        # reversibility" rule.
        effect = SimulatedNetworkEffect(
            kind="money_movement", target=target, reversible=False, description=description, amount=amount
        )
        self.calls.append(effect)
        return effect

    def simulate_communication(self, target: str, description: str) -> SimulatedNetworkEffect:
        effect = SimulatedNetworkEffect(kind="communication", target=target, reversible=False, description=description)
        self.calls.append(effect)
        return effect
