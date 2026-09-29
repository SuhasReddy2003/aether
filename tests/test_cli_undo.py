from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aether import Aether
from aether.cli import main as cli_main
from aether.core.side_effects import SideEffectType

runner = CliRunner()


@pytest.fixture()
def cli_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "clidata2"
    d.mkdir()
    monkeypatch.setattr(cli_main, "DEFAULT_DATA_DIR", d)
    return d


def _seed_crm_run(data_dir: Path, run_id: str = "run_undo_cli") -> None:
    a = Aether(data_dir=data_dir)

    @a.tool(name="crm.update", side_effects=[SideEffectType.UPDATE], reversible=True, compensation="crm.restore_previous")
    def crm_update(customer_id: str, note: str) -> dict:
        from aether.state.world import WorldState

        world = WorldState(data_dir=data_dir, run_id=run_id)
        data = world.load()
        previous = data.get(customer_id)
        data[customer_id] = {"note": note}
        world.save(data)
        return {"customer_id": customer_id, "previous": previous}

    with a.run(agent="tester", goal="cli undo test", run_id=run_id):
        crm_update("cust_1", "first note")
        crm_update("cust_1", "second note")


def test_cli_checkpoint_and_checkout(cli_data_dir: Path) -> None:
    _seed_crm_run(cli_data_dir)
    result = runner.invoke(cli_main.app, ["checkpoint", "run_undo_cli", "--label", "after_run"])
    assert result.exit_code == 0
    assert "Checkpoint saved" in result.stdout

    result2 = runner.invoke(cli_main.app, ["checkout", "run_undo_cli", "--step", "2"])
    assert result2.exit_code == 0
    assert "content_hash" in result2.stdout


def test_cli_checkout_missing_returns_error(cli_data_dir: Path) -> None:
    result = runner.invoke(cli_main.app, ["checkout", "run_does_not_exist", "--step", "1"])
    assert result.exit_code == 1


def test_cli_rollback_restores_state(cli_data_dir: Path) -> None:
    from aether.state.world import WorldState

    _seed_crm_run(cli_data_dir)
    world = WorldState(data_dir=cli_data_dir, run_id="run_undo_cli")
    assert world.load()["cust_1"]["note"] == "second note"

    result = runner.invoke(cli_main.app, ["rollback", "run_undo_cli", "--to", "1"])
    assert result.exit_code == 0
    assert "compensated" in result.stdout

    world2 = WorldState(data_dir=cli_data_dir, run_id="run_undo_cli")
    assert world2.load()["cust_1"]["note"] == "first note"
