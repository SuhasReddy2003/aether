from __future__ import annotations

import pytest

from aether.core.errors import AetherReplayError
from aether.core.side_effects import SideEffectType
from aether.replay.comparator import diff_runs
from aether.replay.fork import fork_run
from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent
from aether.runtime.interceptor import Aether


def _build_tools(aether: Aether, state: dict, ledger: list):
    @aether.tool(name="crm.update", side_effects=[SideEffectType.UPDATE])
    def crm_update(note: str) -> dict:
        state["note"] = note
        return {"note": note}

    @aether.tool(name="payment.create", side_effects=[SideEffectType.FINANCIAL, SideEffectType.IRREVERSIBLE])
    def create_payment(amount: float) -> dict:
        ledger.append(amount)
        return {"amount": amount, "status": "simulated"}

    @aether.tool(name="payment.skip", side_effects=[SideEffectType.READ])
    def skip_payment() -> dict:
        return {"skipped": True}

    return {"crm.update": crm_update, "payment.create": create_payment, "payment.skip": skip_payment}


@pytest.fixture()
def parent_run(aether_instance: Aether):
    state: dict = {}
    ledger: list = []
    tools = _build_tools(aether_instance, state, ledger)
    scenario = Scenario(
        name="parent",
        steps=[
            ScenarioStep("crm.update", {"note": "reviewing refund"}),
            ScenarioStep("payment.create", {"amount": 2840.0}),
        ],
    )
    agent = ScriptedAgent(aether_instance, agent_id="parent_agent")
    run_id = agent.run_scenario(scenario, tools)
    return run_id, tools, state, ledger


def test_fork_creates_a_new_linked_run(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, ledger = parent_run
    result = fork_run(
        aether_instance, parent_run_id, at_step=1,
        continuation=[ScenarioStep("payment.skip", {})],
        tools=tools,
    )
    assert result.parent_run_id == parent_run_id
    assert result.forked_at_step == 1
    assert result.fork_run_id != parent_run_id

    fork_events = aether_instance.recorder.storage.get_events(result.fork_run_id)
    # at_step=1 means NO prefix is copied (nothing before step 1 exists);
    # the continuation supplies step 1 onward entirely.
    assert len(fork_events) == 1
    assert fork_events[0].action.tool == "payment.skip"
    # the fork's alternate path never touched the ledger
    assert ledger == [2840.0]  # only the ORIGINAL parent run's payment happened


def test_fork_preserves_verbatim_prefix_before_the_fork_point(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, ledger = parent_run
    result = fork_run(
        aether_instance, parent_run_id, at_step=2,
        continuation=[ScenarioStep("payment.skip", {})],
        tools=tools,
    )
    fork_events = aether_instance.recorder.storage.get_events(result.fork_run_id)
    # at_step=2: step 1 (crm.update) is copied verbatim from the parent;
    # the continuation replaces step 2 onward (the risky payment).
    assert len(fork_events) == 2
    assert fork_events[0].action.tool == "crm.update"
    assert fork_events[0].action.arguments == {"note": "reviewing refund"}
    assert fork_events[1].action.tool == "payment.skip"
    assert ledger == [2840.0]  # the fork never re-executed payment.create


def test_fork_out_of_range_step_raises(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, _ledger = parent_run
    with pytest.raises(AetherReplayError, match="out of range"):
        fork_run(aether_instance, parent_run_id, at_step=999, continuation=[], tools=tools)


def test_fork_nonexistent_parent_raises(aether_instance: Aether) -> None:
    with pytest.raises(AetherReplayError, match="no events"):
        fork_run(aether_instance, "does_not_exist", at_step=1, continuation=[], tools={})


def test_diff_runs_detects_first_divergence(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, _ledger = parent_run
    fork_result = fork_run(
        aether_instance, parent_run_id, at_step=2,
        continuation=[ScenarioStep("payment.skip", {})],
        tools=tools,
    )
    # fork's step 1 is a verbatim copy of parent's step 1 (crm.update), so
    # they diverge at step 2, where the fork substitutes payment.skip.
    diff = diff_runs(aether_instance.recorder, parent_run_id, fork_result.fork_run_id)
    assert diff.first_divergence is not None
    assert diff.first_divergence.step == 2
    assert diff.first_divergence.run_a_tool == "payment.create"
    assert diff.first_divergence.run_b_tool == "payment.skip"


def test_diff_runs_reports_high_risk_delta(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, _ledger = parent_run
    safe_scenario_steps = [ScenarioStep("crm.update", {"note": "reviewing refund"}), ScenarioStep("payment.skip", {})]
    agent = ScriptedAgent(aether_instance, agent_id="safe_agent")
    safe_run_id = agent.run_scenario(Scenario(name="safe", steps=safe_scenario_steps), tools)

    diff = diff_runs(aether_instance.recorder, parent_run_id, safe_run_id)
    assert diff.high_risk_a == 1  # the payment.create in the parent
    assert diff.high_risk_b == 0  # payment.skip has no financial/irreversible side effect
    assert diff.first_divergence.step == 2
    assert diff.first_divergence.reason == "tool or arguments differ"


def test_diff_identical_runs_has_no_divergence(aether_instance: Aether, parent_run) -> None:
    parent_run_id, tools, _state, _ledger = parent_run
    agent = ScriptedAgent(aether_instance, agent_id="parent_agent_2")
    twin_scenario = Scenario(
        name="twin",
        steps=[ScenarioStep("crm.update", {"note": "reviewing refund"}), ScenarioStep("payment.create", {"amount": 2840.0})],
    )
    twin_run_id = agent.run_scenario(twin_scenario, tools)

    diff = diff_runs(aether_instance.recorder, parent_run_id, twin_run_id)
    assert diff.first_divergence is None
    assert diff.tool_calls_a == diff.tool_calls_b == 2


def test_diff_detects_result_only_divergence(aether_instance: Aether) -> None:
    # Regression test for a real gap found during Phase 3's self-audit:
    # diff_runs previously only compared tool+arguments, so two runs
    # calling the same tool with the same arguments but getting a
    # DIFFERENT result (e.g. a nondeterministic tool, or different backing
    # state) were reported as "no divergence" even though they plainly
    # diverged. Must now be caught.
    call_count = {"n": 0}

    @aether_instance.tool(name="flaky.tool", side_effects=[SideEffectType.READ])
    def flaky_tool() -> dict:
        call_count["n"] += 1
        return {"result": "A" if call_count["n"] == 1 else "B"}

    agent = ScriptedAgent(aether_instance, agent_id="flaky_agent")
    tools = {"flaky.tool": flaky_tool}
    run_a = agent.run_scenario(Scenario(name="flaky_a", steps=[ScenarioStep("flaky.tool", {})]), tools)
    run_b = agent.run_scenario(Scenario(name="flaky_b", steps=[ScenarioStep("flaky.tool", {})]), tools)

    diff = diff_runs(aether_instance.recorder, run_a, run_b)
    assert diff.first_divergence is not None
    assert diff.first_divergence.reason == "same tool/arguments but result differs"
