from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from aether.core.errors import AetherSimulationError
from aether.sandbox.database import ShadowDatabase


@pytest.fixture()
def real_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "real.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE customers (id INTEGER PRIMARY KEY, name TEXT, balance REAL);
        CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL,
            FOREIGN KEY(customer_id) REFERENCES customers(id));
        INSERT INTO customers (id, name, balance) VALUES (1, 'Jordan', 84.0);
        INSERT INTO orders (id, customer_id, amount) VALUES (100, 1, 20.0);
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_shadow_clone_does_not_touch_real_db(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        shadow.execute("UPDATE customers SET balance = 999.0 WHERE id = 1")

    # real DB must be untouched
    conn = sqlite3.connect(real_db)
    row = conn.execute("SELECT balance FROM customers WHERE id=1").fetchone()
    conn.close()
    assert row[0] == 84.0


def test_update_effect_reports_rows_modified(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        effect = shadow.execute("UPDATE customers SET balance = 100.0 WHERE id = 1")
        assert effect.operation == "UPDATE"
        assert effect.table == "customers"
        assert effect.rows_modified == 1
        assert effect.rows_created == 0
        assert effect.rows_deleted == 0
        assert effect.row_diff is not None
        before, after = effect.row_diff.changed["1"]
        assert before["balance"] == 84.0
        assert after["balance"] == 100.0


def test_insert_effect_reports_rows_created(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        effect = shadow.execute("INSERT INTO customers (id, name, balance) VALUES (2, 'Alex', 50.0)")
        assert effect.operation == "INSERT"
        assert effect.rows_created == 1


def test_delete_effect_reports_rows_deleted_and_dependents(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        effect = shadow.execute("DELETE FROM customers WHERE id = 1")
        assert effect.operation == "DELETE"
        assert effect.rows_deleted == 1
        assert "orders" in effect.dependent_tables_affected


def test_select_effect_reports_rows_read_and_does_not_mutate(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        effect = shadow.execute("SELECT * FROM customers")
        assert effect.operation == "SELECT"
        assert effect.rows_read == 1
        # confirm nothing changed
        state = shadow.full_state()
        assert len(state["customers"]) == 1


def test_full_state_snapshot(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow:
        state = shadow.full_state()
        assert set(state.keys()) == {"customers", "orders"}
        assert len(state["customers"]) == 1
        assert len(state["orders"]) == 1


def test_unsafe_identifier_rejected(real_db: Path) -> None:
    with ShadowDatabase(real_db) as shadow, pytest.raises(AetherSimulationError):
        shadow.snapshot_table("customers; DROP TABLE customers;--")


def test_shadow_db_cleans_up_temp_file(real_db: Path) -> None:
    shadow = ShadowDatabase(real_db)
    shadow_path = shadow.shadow_path
    assert shadow_path.exists()
    shadow.close()
    assert not shadow_path.exists()


def test_shadow_db_starts_from_nonexistent_source(tmp_path: Path) -> None:
    # simulate against a DB that doesn't exist yet — should start empty, not crash
    missing = tmp_path / "does_not_exist.db"
    with ShadowDatabase(missing) as shadow:
        assert shadow.list_tables() == []
