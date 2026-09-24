"""Storage interface. SQLite is the only implementation in Phase 1, but every
caller depends on this interface, not on SQLite directly, so the backend is
replaceable per the engineering rules."""
from __future__ import annotations

from abc import ABC, abstractmethod

from aether.core.event import Event


class Storage(ABC):
    @abstractmethod
    def create_run(self, run_id: str, agent_id: str, agent_version: str, goal: str = "") -> None: ...

    @abstractmethod
    def append_event(self, event: Event) -> None: ...

    @abstractmethod
    def get_events(self, run_id: str) -> list[Event]: ...

    @abstractmethod
    def get_last_event(self, run_id: str) -> Event | None: ...

    @abstractmethod
    def get_run_ids(self) -> list[str]: ...

    @abstractmethod
    def get_run_meta(self, run_id: str) -> dict | None: ...

    @abstractmethod
    def set_run_signature(self, run_id: str, public_key_b64: str, signature_b64: str, head_hash: str) -> None: ...

    @abstractmethod
    def get_run_signature(self, run_id: str) -> dict | None: ...
