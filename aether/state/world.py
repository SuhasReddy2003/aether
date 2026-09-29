"""WorldState: a tiny JSON-file-backed store for demo/example "external
system" state (e.g. a CRM). Deliberately boring — a single JSON file per
run — so that a rollback invoked from a *separate* CLI process later can
genuinely read and mutate the same state a demo script wrote earlier. This
is what makes `aether rollback` a real cross-process operation rather than
something that only works inside one Python process.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aether.state.snapshot import content_hash


class WorldState:
    def __init__(self, data_dir: Path, run_id: str) -> None:
        self.path = Path(data_dir) / "worlds" / f"{run_id}.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text("{}")

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text())

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, indent=2, sort_keys=True))

    def content_hash(self) -> str:
        return content_hash(self.load())
