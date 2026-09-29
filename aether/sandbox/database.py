"""Shadow database: a real SQLite clone that absorbs writes so the real
database is never touched. Used both to *simulate* an action before it runs
for real, and (in the demo agents) as the actual backing store for a
tool that is declared reversible, so rollback has something concrete to
restore.

Design choice, documented: table snapshots are captured as full row dumps
(list of dict per table). This is correct and simple for the small,
demo-scale databases this project targets; it is NOT suitable for
multi-GB production tables, and that limitation is stated in
docs/limitations.md rather than hidden.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

from aether.core.errors import AetherSimulationError
from aether.state.snapshot import StateDiff, diff_flat_maps

_TABLE_RE = re.compile(
    r"^\s*(INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+[\"'`\[]?([A-Za-z_][A-Za-z0-9_]*)[\"'`\]]?",
    re.IGNORECASE,
)


@dataclass
class DatabaseEffect:
    tool_sql: str
    table: str | None
    operation: str  # INSERT | UPDATE | DELETE | SELECT | OTHER
    rows_read: int = 0
    rows_created: int = 0
    rows_modified: int = 0
    rows_deleted: int = 0
    dependent_tables_affected: list[str] = field(default_factory=list)
    row_diff: StateDiff | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "table": self.table,
            "operation": self.operation,
            "rows_read": self.rows_read,
            "rows_created": self.rows_created,
            "rows_modified": self.rows_modified,
            "rows_deleted": self.rows_deleted,
            "dependent_tables_affected": self.dependent_tables_affected,
            "row_diff": self.row_diff.to_dict() if self.row_diff else None,
        }


class ShadowDatabase:
    """Clones `source_path` into a private temp file. All `execute()` calls
    run against the clone. The source file is opened read-only to compute
    the initial clone and is never written to."""

    def __init__(self, source_path: str | Path) -> None:
        self.source_path = Path(source_path)
        fd, tmp_name = tempfile.mkstemp(suffix=".shadow.db")
        os.close(fd)
        self.shadow_path = Path(tmp_name)
        if self.source_path.exists():
            shutil.copyfile(self.source_path, self.shadow_path)
        self.conn = sqlite3.connect(self.shadow_path)
        self.conn.row_factory = sqlite3.Row

    def close(self) -> None:
        self.conn.close()
        try:
            self.shadow_path.unlink(missing_ok=True)
        except OSError:
            pass

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- introspection --------------------------------------------------

    def list_tables(self) -> list[str]:
        rows = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        return [r["name"] for r in rows]

    def primary_key_columns(self, table: str) -> list[str]:
        rows = self.conn.execute(f"PRAGMA table_info({_quote_ident(table)})").fetchall()
        pk_cols = [r["name"] for r in rows if r["pk"] > 0]
        return pk_cols or ["rowid"]

    def snapshot_table(self, table: str) -> dict[str, dict[str, Any]]:
        """Return {row_key -> row_dict} for the whole table, row_key being the
        primary key value(s) joined, or rowid if no PK is declared."""
        pk_cols = self.primary_key_columns(table)
        use_rowid = pk_cols == ["rowid"]
        select_cols = "rowid, *" if use_rowid else "*"
        rows = self.conn.execute(f"SELECT {select_cols} FROM {_quote_ident(table)}").fetchall()
        result: dict[str, dict[str, Any]] = {}
        for row in rows:
            d = dict(row)
            if use_rowid:
                key = str(d["rowid"])
            else:
                key = "|".join(str(d[c]) for c in pk_cols)
            result[key] = d
        return result

    def dependent_tables(self, table: str) -> list[str]:
        """Tables with a FOREIGN KEY referencing `table`."""
        dependents = []
        for other in self.list_tables():
            if other == table:
                continue
            fks = self.conn.execute(f"PRAGMA foreign_key_list({_quote_ident(other)})").fetchall()
            if any(fk["table"] == table for fk in fks):
                dependents.append(other)
        return dependents

    # -- simulated writes -------------------------------------------------

    def execute(self, sql: str, params: tuple | dict = ()) -> DatabaseEffect:
        """Execute `sql` against the shadow clone (never the real DB) and
        return a structured effect describing what changed."""
        match = _TABLE_RE.match(sql)
        table = match.group(2) if match else None
        operation = (match.group(1).split()[0].upper() if match else "OTHER")

        if operation == "OTHER" and sql.strip().upper().startswith("SELECT"):
            operation = "SELECT"

        before = self.snapshot_table(table) if table and table in self.list_tables() else {}
        cursor = self.conn.execute(sql, params)
        self.conn.commit()

        if operation == "SELECT":
            rows = cursor.fetchall()
            return DatabaseEffect(tool_sql=sql, table=table, operation="SELECT", rows_read=len(rows))

        after = self.snapshot_table(table) if table else {}
        row_diff = diff_flat_maps(before, after)
        dependents = self.dependent_tables(table) if table else []

        return DatabaseEffect(
            tool_sql=sql,
            table=table,
            operation=operation,
            rows_created=len(row_diff.added),
            rows_modified=len(row_diff.changed),
            rows_deleted=len(row_diff.removed),
            dependent_tables_affected=dependents,
            row_diff=row_diff,
        )

    def full_state(self) -> dict[str, dict[str, dict[str, Any]]]:
        return {t: self.snapshot_table(t) for t in self.list_tables()}


def _quote_ident(name: str) -> str:
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", name):
        raise AetherSimulationError(f"unsafe identifier rejected: {name!r}")
    return f'"{name}"'
