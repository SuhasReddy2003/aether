from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aether.cli import main as cli_main
from aether.core.action import Action, Authorization
from aether.recording.recorder import Recorder
from aether.storage.sqlite import SQLiteStorage

runner = CliRunner()


@pytest.fixture()
def cli_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "clidata"
    d.mkdir()
    monkeypatch.setattr(cli_main, "DEFAULT_DATA_DIR", d)
    return d


def _seed_run(data_dir: Path, run_id: str = "run_cli_test", n: int = 4) -> None:
    recorder = Recorder(data_dir=data_dir)
    recorder.start_run(run_id, "tester", "v1", goal="cli test")
    for i in range(1, n + 1):
        recorder.record(
            Action(
                run_id=run_id, step=i, agent_id="tester", tool="test.tool",
                arguments={"n": i}, result={"ok": True}, authorization=Authorization(role="agent"),
            )
        )


def test_cli_inspect_shows_table(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["inspect", "run_cli_test"])
    assert result.exit_code == 0
    assert "test.tool" in result.stdout
    assert "Step" in result.stdout


def test_cli_inspect_missing_run_fails(cli_data_dir: Path) -> None:
    result = runner.invoke(cli_main.app, ["inspect", "run_does_not_exist"])
    assert result.exit_code == 1


def test_cli_verify_valid_chain(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["verify", "run_cli_test"])
    assert result.exit_code == 0
    assert "VALID" in result.stdout


def test_cli_verify_detects_tampering_and_names_event(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    storage = SQLiteStorage(cli_data_dir / "aether.db")
    events = storage.get_events("run_cli_test")
    target = events[2]

    conn = storage.raw_connection()
    row = conn.execute("SELECT event_json FROM events WHERE event_id=?", (target.event_id,)).fetchone()
    data = json.loads(row["event_json"])
    data["action"]["arguments"]["n"] = -999
    conn.execute("UPDATE events SET event_json=? WHERE event_id=?", (json.dumps(data), target.event_id))

    result = runner.invoke(cli_main.app, ["verify", "run_cli_test"])
    assert result.exit_code == 1
    assert "INVALID" in result.stdout
    assert target.event_id in result.stdout


def test_cli_export_then_import_roundtrip(cli_data_dir: Path, tmp_path: Path) -> None:
    _seed_run(cli_data_dir)
    out_file = tmp_path / "out.aether"
    result = runner.invoke(cli_main.app, ["export", "run_cli_test", "-o", str(out_file)])
    assert result.exit_code == 0
    assert out_file.exists()

    other_dir = tmp_path / "other_clidata"
    other_dir.mkdir()

    import aether.cli.main as cli_main_mod

    original = cli_main_mod.DEFAULT_DATA_DIR
    cli_main_mod.DEFAULT_DATA_DIR = other_dir
    try:
        result2 = runner.invoke(cli_main.app, ["import", str(out_file)])
        assert result2.exit_code == 0
        assert "Imported" in result2.stdout
    finally:
        cli_main_mod.DEFAULT_DATA_DIR = original


def test_cli_doctor_runs(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["doctor"])
    assert result.exit_code == 0
    assert "Python" in result.stdout


def test_cli_tools_lists_recorded_tools(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["tools"])
    assert result.exit_code == 0
    assert "test.tool" in result.stdout


def test_cli_events_shows_detail(cli_data_dir: Path) -> None:
    _seed_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["events", "run_cli_test", "--step", "2"])
    assert result.exit_code == 0
    assert "test.tool" in result.stdout
