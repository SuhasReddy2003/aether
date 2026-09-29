from __future__ import annotations

import pytest

from aether.core.side_effects import SideEffectType
from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent
from aether.runtime.interceptor import Aether


def _build_tools(aether: Aether, state: dict) -> dict:
    @aether.tool(name="counter.increment", side_effects=[SideEffectType.UPDATE])
    def increment(by: int) -> dict:
        state["value"] = state.get("value", 0) + by
        return {"value": state["value"]}

    @aether.tool(name="counter.boom", side_effects=[SideEffectType.READ])
    def boom() -> None:
        raise RuntimeError("scenario failure")

    return {"counter.increment": increment, "counter.boom": boom}


def test_scripted_agent_executes_steps_in_order(aether_instance: Aether) -> None:
    state: dict = {}
    tools = _build_tools(aether_instance, state)
    scenario = Scenario(
        name="increments",
        goal="count up",
        steps=[ScenarioStep("counter.increment", {"by": 1}), ScenarioStep("counter.increment", {"by": 5})],
    )
    agent = ScriptedAgent(aether_instance, agent_id="scripted_agent")
    run_id = agent.run_scenario(scenario, tools)

    assert state["value"] == 6
    events = aether_instance.recorder.storage.get_events(run_id)
    assert [e.action.tool for e in events] == ["counter.increment", "counter.increment"]
    assert events[0].action.agent_id == "scripted_agent"


def test_scripted_agent_same_scenario_is_deterministic(aether_instance: Aether) -> None:
    state1: dict = {}
    tools1 = _build_tools(aether_instance, state1)
    scenario = Scenario(name="det", steps=[ScenarioStep("counter.increment", {"by": 3})])
    agent = ScriptedAgent(aether_instance, agent_id="scripted_agent")
    run_id_1 = agent.run_scenario(scenario, tools1)

    state2: dict = {}
    tools2 = _build_tools(aether_instance, state2)
    run_id_2 = agent.run_scenario(scenario, tools2)

    events_1 = aether_instance.recorder.storage.get_events(run_id_1)
    events_2 = aether_instance.recorder.storage.get_events(run_id_2)
    assert [e.action.tool for e in events_1] == [e.action.tool for e in events_2]
    assert [e.action.arguments for e in events_1] == [e.action.arguments for e in events_2]
    assert [e.action.result for e in events_1] == [e.action.result for e in events_2]


def test_scripted_agent_undeclared_tool_raises(aether_instance: Aether) -> None:
    state: dict = {}
    tools = _build_tools(aether_instance, state)
    scenario = Scenario(name="bad", steps=[ScenarioStep("does.not.exist", {})])
    agent = ScriptedAgent(aether_instance, agent_id="scripted_agent")
    with pytest.raises(KeyError, match="does.not.exist"):
        agent.run_scenario(scenario, tools)


def test_scripted_agent_exception_propagates_and_is_recorded(aether_instance: Aether) -> None:
    state: dict = {}
    tools = _build_tools(aether_instance, state)
    scenario = Scenario(name="boom", steps=[ScenarioStep("counter.increment", {"by": 1}), ScenarioStep("counter.boom", {})])
    agent = ScriptedAgent(aether_instance, agent_id="scripted_agent")

    with pytest.raises(RuntimeError, match="scenario failure"):
        agent.run_scenario(scenario, tools)

    run_ids = aether_instance.recorder.storage.get_run_ids()
    events = aether_instance.recorder.storage.get_events(run_ids[-1])
    assert len(events) == 2
    assert events[1].action.status.value == "error"
