from __future__ import annotations

from aether.core.side_effects import SideEffectType
from aether.replay.player import assert_replay_matches, replay_live_sim, replay_strict
from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent
from aether.runtime.interceptor import Aether


def _tools_factory(aether: Aether, state: dict):
    def factory():
        @aether.tool(name="counter.increment", side_effects=[SideEffectType.UPDATE])
        def increment(by: int) -> dict:
            state["value"] = state.get("value", 0) + by
            return {"value": state["value"]}

        return {"counter.increment": increment}

    return factory


def test_replay_strict_reconstructs_recorded_steps(aether_instance: Aether) -> None:
    state: dict = {}
    tools = _tools_factory(aether_instance, state)()
    scenario = Scenario(name="s1", steps=[ScenarioStep("counter.increment", {"by": 4})])
    agent = ScriptedAgent(aether_instance, agent_id="a1")
    run_id = agent.run_scenario(scenario, tools)

    result = replay_strict(aether_instance.recorder, run_id)
    assert result.mode == "strict"
    assert len(result.steps) == 1
    assert result.steps[0].tool == "counter.increment"
    assert result.steps[0].arguments == {"by": 4}
    assert result.steps[0].result == {"value": 4}


def test_replay_strict_is_byte_identical_across_100_calls(aether_instance: Aether) -> None:
    # Acceptance-checklist item: "replaying a cassette in strict mode 100
    # times yields byte-identical results."
    state: dict = {}
    tools = _tools_factory(aether_instance, state)()
    scenario = Scenario(name="s2", steps=[ScenarioStep("counter.increment", {"by": 7})])
    agent = ScriptedAgent(aether_instance, agent_id="a1")
    run_id = agent.run_scenario(scenario, tools)

    first = replay_strict(aether_instance.recorder, run_id)
    for _ in range(100):
        again = replay_strict(aether_instance.recorder, run_id)
        assert again.steps == first.steps


def test_replay_live_sim_reproduces_deterministic_scenario(aether_instance: Aether) -> None:
    state: dict = {}
    scenario = Scenario(name="s3", steps=[ScenarioStep("counter.increment", {"by": 2}), ScenarioStep("counter.increment", {"by": 3})])
    agent = ScriptedAgent(aether_instance, agent_id="a1")
    original_run_id = agent.run_scenario(scenario, _tools_factory(aether_instance, state)())
    original = replay_strict(aether_instance.recorder, original_run_id)

    # fresh state each time via the factory closure
    fresh_state: dict = {}
    replayed = replay_live_sim(aether_instance, scenario, _tools_factory(aether_instance, fresh_state))

    divergences = assert_replay_matches(original, replayed)
    assert divergences == []


def test_replay_live_sim_reports_real_divergence_when_state_not_reset(aether_instance: Aether) -> None:
    # If the caller's tools_factory does NOT reset external state (a bug in
    # the caller, not in Aether), live-sim replay must REPORT the resulting
    # divergence, not silently hide it.
    scenario = Scenario(name="s4", steps=[ScenarioStep("counter.increment", {"by": 1})])
    agent = ScriptedAgent(aether_instance, agent_id="a1")

    shared_state: dict = {"value": 100}  # already polluted before the "original" run
    original_run_id = agent.run_scenario(scenario, _tools_factory(aether_instance, shared_state)())
    original = replay_strict(aether_instance.recorder, original_run_id)

    def bad_factory():
        # returns tools bound to the SAME already-mutated `shared_state`
        return _tools_factory(aether_instance, shared_state)()

    replayed = replay_live_sim(aether_instance, scenario, bad_factory)
    divergences = assert_replay_matches(original, replayed)
    assert len(divergences) == 1
    assert "result differs" in divergences[0]
