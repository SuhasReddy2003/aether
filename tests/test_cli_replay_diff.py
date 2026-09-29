from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from aether import Aether
from aether.cli import main as cli_main
from aether.core.side_effects import SideEffectType
from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent

runner = CliRunner()


@pytest.fixture()
def cli_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    d = tmp_path / "clidata3"
    d.mkdir()
    monkeypatch.setattr(cli_main, "DEFAULT_DATA_DIR", d)
    return d


def _seed_two_runs(data_dir: Path) -> tuple[str, str]:
    a = Aether(data_dir=data_dir)
    ledger: list = []

    @a.tool(name="payment.create", side_effects=[SideEffectType.FINANCIAL, SideEffectType.IRREVERSIBLE])
    def create_payment(amount: float) -> dict:
        ledger.append(amount)
        return {"amount": amount}

    @a.tool(name="payment.skip", side_effects=[SideEffectType.READ])
    def skip_payment() -> dict:
        return {"skipped": True}

    tools = {"payment.create": create_payment, "payment.skip": skip_payment}
    agent = ScriptedAgent(a, agent_id="tester")
    run_a = agent.run_scenario(Scenario(name="a", steps=[ScenarioStep("payment.create", {"amount": 100.0})]), tools)
    run_b = agent.run_scenario(Scenario(name="b", steps=[ScenarioStep("payment.skip", {})]), tools)
    return run_a, run_b


def test_cli_replay_shows_strict_reconstruction(cli_data_dir: Path) -> None:
    run_a, _ = _seed_two_runs(cli_data_dir)
    result = runner.invoke(cli_main.app, ["replay", run_a])
    assert result.exit_code == 0
    assert "payment.create" in result.stdout
    assert "strict mode" in result.stdout


def test_cli_replay_missing_run_fails(cli_data_dir: Path) -> None:
    result = runner.invoke(cli_main.app, ["replay", "does_not_exist"])
    assert result.exit_code == 1


def test_cli_diff_shows_first_divergence(cli_data_dir: Path) -> None:
    run_a, run_b = _seed_two_runs(cli_data_dir)
    result = runner.invoke(cli_main.app, ["diff", run_a, run_b])
    assert result.exit_code == 0
    assert "First divergence" in result.stdout
    assert "payment.create" in result.stdout
    assert "payment.skip" in result.stdout


def test_cli_diff_identical_runs_reports_no_divergence(cli_data_dir: Path) -> None:
    run_a, _ = _seed_two_runs(cli_data_dir)
    a = Aether(data_dir=cli_data_dir)

    @a.tool(name="payment.create", side_effects=[SideEffectType.FINANCIAL, SideEffectType.IRREVERSIBLE])
    def create_payment(amount: float) -> dict:
        return {"amount": amount}

    agent = ScriptedAgent(a, agent_id="tester2")
    twin = agent.run_scenario(Scenario(name="twin", steps=[ScenarioStep("payment.create", {"amount": 100.0})]),
                               {"payment.create": create_payment})

    result = runner.invoke(cli_main.app, ["diff", run_a, twin])
    assert result.exit_code == 0
    assert "No divergence" in result.stdout
