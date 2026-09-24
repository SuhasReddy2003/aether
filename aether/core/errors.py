"""Structured error hierarchy for Aether.

Every failure mode in the system raises one of these, never a bare
Exception, and exceptions from wrapped tools are always re-raised
(never swallowed) after being recorded.
"""
from __future__ import annotations


class AetherError(Exception):
    """Base class for all Aether errors."""


class AetherPolicyError(AetherError):
    """Raised by the policy engine (Phase 4)."""


class AetherReplayError(AetherError):
    """Raised when a cassette cannot be replayed deterministically."""


class AetherRollbackError(AetherError):
    """Raised when a compensation/rollback cannot be completed safely."""


class AetherIntegrityError(AetherError):
    """Raised when the hash chain or signature fails verification.

    Carries the id of the first event found to be invalid, if known,
    so callers (and the CLI) can point directly at the tampered event.
    """

    def __init__(self, message: str, first_bad_event_id: str | None = None) -> None:
        super().__init__(message)
        self.first_bad_event_id = first_bad_event_id


class AetherSimulationError(AetherError):
    """Raised by the shadow-world simulators (Phase 2)."""


class AetherCassetteError(AetherError):
    """Raised on malformed, oversized, or unsafe cassette import."""
