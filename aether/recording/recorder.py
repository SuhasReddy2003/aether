"""Recorder: turns an Action into a hash-chained, signed Event."""
from __future__ import annotations

import threading
from pathlib import Path

from aether.core.action import Action
from aether.core.event import Event
from aether.recording.hashchain import GENESIS_HASH, seal_event
from aether.recording.redaction import Redactor
from aether.recording.signing import KeyPair
from aether.storage.base import Storage
from aether.storage.sqlite import SQLiteStorage

DEFAULT_DATA_DIR = Path.home() / ".aether"


class Recorder:
    def __init__(
        self,
        storage: Storage | None = None,
        data_dir: Path | None = None,
        redactor: Redactor | None = None,
    ) -> None:
        self.data_dir = Path(data_dir) if data_dir else DEFAULT_DATA_DIR
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage = storage or SQLiteStorage(self.data_dir / "aether.db")
        self.redactor = redactor or Redactor()
        self.keypair = KeyPair.load_or_create(self.data_dir / "signing_key")
        self._lock = threading.Lock()

    def start_run(self, run_id: str, agent_id: str, agent_version: str, goal: str = "") -> None:
        self.storage.create_run(run_id, agent_id, agent_version, goal)

    def record(self, action: Action, parent_event_id: str | None = None) -> Event:
        """Redact, seal (hash-chain), append to storage, and return the Event."""
        with self._lock:
            redacted_args = self.redactor.redact(action.arguments)
            redacted_result = self.redactor.redact(action.result) if isinstance(action.result, (dict, list)) else action.result
            redacted_action = action.model_copy(update={"arguments": redacted_args, "result": redacted_result})

            last = self.storage.get_last_event(action.run_id)
            previous_hash = last.hash if last else GENESIS_HASH
            resolved_parent = parent_event_id or (last.event_id if last else None)

            event = Event(
                run_id=action.run_id,
                parent_event_id=resolved_parent,
                action=redacted_action,
            )
            sealed = seal_event(event, previous_hash)
            self.storage.append_event(sealed)
            self._sign_head(action.run_id, sealed)
            return sealed

    def _sign_head(self, run_id: str, head_event: Event) -> None:
        signature = self.keypair.sign(head_event.hash.encode("utf-8"))
        self.storage.set_run_signature(
            run_id, self.keypair.public_key_b64(), signature, head_event.hash
        )
