from __future__ import annotations

from aether.core.side_effects import SideEffectType
from aether.replay.counterfactual import CounterfactualType, counterfactual_block
from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent
from aether.runtime.interceptor import Aether


def _build_tools(aether: Aether, ledger: list):
    @aether.tool(name="payment.create", side_effects=[SideEffectType.FINANCIAL, SideEffectType.IRREVERSIBLE])
    def create_payment(amount: float) -> dict:
        ledger.append(amount)
        return {"amount": amount, "status": "simulated"}

    @aether.tool(name="payment.skip", side_effects=[SideEffectType.READ])
    def skip_payment(reason: str) -> dict:
        return {"skipped": True, "reason": reason}

    return {"payment.create": create_payment, "payment.skip": skip_payment}


def test_counterfactual_block_never_touches_the_real_ledger(aether_instance: Aether) -> None:
    ledger: list = []
    tools = _build_tools(aether_instance, ledger)
    agent = ScriptedAgent(aether_instance, agent_id="agent")
    scenario = Scenario(name="risky", steps=[ScenarioStep("payment.create", {"amount": 2840.0})])
    run_id = agent.run_scenario(scenario, tools)
    assert ledger == [2840.0]  # the real (baseline) run's payment did happen

    result = counterfactual_block(
        aether_instance, run_id, at_step=1,
        continuation_without_risky_step=[ScenarioStep("payment.skip", {"reason": "blocked by counterfactual"})],
        tools=tools,
    )

    assert result.counterfactual_type == CounterfactualType.BLOCKED
    assert ledger == [2840.0]  # UNCHANGED — the counterfactual never called payment.create again

    fork_events = aether_instance.recorder.storage.get_events(result.fork.fork_run_id)
    assert fork_events[0].action.tool == "payment.skip"
    assert not any(e.action.tool == "payment.create" for e in fork_events)


def test_counterfactual_fork_run_id_follows_naming_convention(aether_instance: Aether) -> None:
    ledger: list = []
    tools = _build_tools(aether_instance, ledger)
    agent = ScriptedAgent(aether_instance, agent_id="agent")
    scenario = Scenario(name="risky2", steps=[ScenarioStep("payment.create", {"amount": 10.0})])
    run_id = agent.run_scenario(scenario, tools)

    result = counterfactual_block(
        aether_instance, run_id, at_step=1,
        continuation_without_risky_step=[ScenarioStep("payment.skip", {"reason": "blocked"})],
        tools=tools,
    )
    assert result.fork.fork_run_id == f"{run_id}.counterfactual_blocked_1"
