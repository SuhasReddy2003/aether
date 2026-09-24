"""SQLite-backed Storage implementation. WAL mode, simple schema."""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from aether.core.event import Event
from aether.storage.base import Storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY,
    agent_id TEXT NOT NULL,
    agent_version TEXT NOT NULL,
    goal TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    public_key_b64 TEXT,
    signature_b64 TEXT,
    head_hash TEXT
);

CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT UNIQUE NOT NULL,
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    parent_event_id TEXT,
    previous_hash TEXT NOT NULL,
    hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    event_json TEXT NOT NULL,
    FOREIGN KEY(run_id) REFERENCES runs(run_id)
);
CREATE INDEX IF NOT EXISTS idx_events_run_step ON events(run_id, step);
CREATE INDEX IF NOT EXISTS idx_events_run_seq ON events(run_id, seq);

CREATE TABLE IF NOT EXISTS tool_calls (
    event_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    tool TEXT NOT NULL,
    status TEXT NOT NULL,
    duration_ms REAL NOT NULL,
    FOREIGN KEY(event_id) REFERENCES events(event_id)
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_tool ON tool_calls(tool);

CREATE TABLE IF NOT EXISTS state_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    step INTEGER NOT NULL,
    label TEXT,
    content_hash TEXT NOT NULL,
    data_json TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


class SQLiteStorage(Storage):
    def __init__(self, db_path: str | Path) -> None:
        self.db_path = str(db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        # One connection per thread; SQLite connections are not safe to
        # share across threads without care, and this keeps concurrent
        # writers (tested in test_concurrency.py) from corrupting each
        # other's transactions.
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.row_factory = sqlite3.Row
            self._local.conn = conn
        return self._local.conn

    def create_run(self, run_id: str, agent_id: str, agent_version: str, goal: str = "") -> None:
        from aether.core.action import utcnow

        conn = self._connect()
        with self._init_lock:
            conn.execute(
                "INSERT OR IGNORE INTO runs (run_id, agent_id, agent_version, goal, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (run_id, agent_id, agent_version, goal, utcnow().isoformat()),
            )

    def append_event(self, event: Event) -> None:
        conn = self._connect()
        with self._init_lock:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO events (event_id, run_id, step, parent_event_id, previous_hash, "
                    "hash, created_at, schema_version, event_json) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        event.event_id,
                        event.run_id,
                        event.action.step,
                        event.parent_event_id,
                        event.previous_hash,
                        event.hash,
                        event.created_at,
                        event.schema_version,
                        event.model_dump_json(),
                    ),
                )
                conn.execute(
                    "INSERT INTO tool_calls (event_id, run_id, tool, status, duration_ms) "
                    "VALUES (?,?,?,?,?)",
                    (
                        event.event_id,
                        event.run_id,
                        event.action.tool,
                        event.action.status.value,
                        event.action.duration_ms,
                    ),
                )
                conn.execute("COMMIT")
            except Exception:
                conn.execute("ROLLBACK")
                raise

    def get_events(self, run_id: str) -> list[Event]:
        # Ordered by `seq` (true append/insertion order, monotonic and set by
        # SQLite itself), NOT by the caller-supplied `step`. The hash chain
        # is built in append order under the Recorder's lock; if two writers
        # for the same run interleave, their `step` values can end up out of
        # append order, and sorting by `step` would then make a perfectly
        # valid chain look tampered. `seq` is the source of truth for order.
        conn = self._connect()
        rows = conn.execute(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY seq ASC", (run_id,)
        ).fetchall()
        return [Event.model_validate_json(row["event_json"]) for row in rows]

    def get_last_event(self, run_id: str) -> Event | None:
        conn = self._connect()
        row = conn.execute(
            "SELECT event_json FROM events WHERE run_id = ? ORDER BY seq DESC LIMIT 1", (run_id,)
        ).fetchone()
        return Event.model_validate_json(row["event_json"]) if row else None

    def get_run_ids(self) -> list[str]:
        conn = self._connect()
        rows = conn.execute("SELECT run_id FROM runs ORDER BY created_at ASC").fetchall()
        return [r["run_id"] for r in rows]

    def get_run_meta(self, run_id: str) -> dict | None:
        conn = self._connect()
        row = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def set_run_signature(self, run_id: str, public_key_b64: str, signature_b64: str, head_hash: str) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE runs SET public_key_b64=?, signature_b64=?, head_hash=? WHERE run_id=?",
            (public_key_b64, signature_b64, head_hash, run_id),
        )

    def get_run_signature(self, run_id: str) -> dict | None:
        meta = self.get_run_meta(run_id)
        if not meta or not meta.get("signature_b64"):
            return None
        return {
            "public_key_b64": meta["public_key_b64"],
            "signature_b64": meta["signature_b64"],
            "head_hash": meta["head_hash"],
        }

    def raw_connection(self) -> sqlite3.Connection:
        """Escape hatch used only by tests to simulate tampering directly
        against the database file, and by `aether doctor` for integrity
        checks."""
        return self._connect()
