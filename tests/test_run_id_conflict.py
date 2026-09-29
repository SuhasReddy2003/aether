from __future__ import annotations

import pytest

from aether.core.errors import AetherRunConflictError
from aether.core.side_effects import SideEffectType
from aether.runtime.interceptor import Aether


def test_reusing_run_id_for_a_fresh_run_raises_instead_of_duplicating(aether_instance: Aether) -> None:
    # Regression test for a real bug found while testing Phase 3's
    # fork_demo.py against a persistent data directory: running a script
    # twice with a fixed run_id used to silently APPEND duplicate,
    # colliding step numbers to the same run_id instead of failing. That
    # must now raise a clear, structured error instead.
    a = aether_instance

    @a.tool(name="test.tool", side_effects=[SideEffectType.READ])
    def tool() -> dict:
        return {"ok": True}

    with a.run(agent="tester", goal="first", run_id="fixed_run_id"):
        tool()

    events_after_first = a.recorder.storage.get_events("fixed_run_id")
    assert len(events_after_first) == 1

    with pytest.raises(AetherRunConflictError, match="already has 1 recorded event"), a.run(agent="tester", goal="second", run_id="fixed_run_id"):
        tool()

    # critically: the existing history must be UNCHANGED, not corrupted
    # with a partial second attempt
    events_after_conflict = a.recorder.storage.get_events("fixed_run_id")
    assert events_after_conflict == events_after_first


def test_fork_run_legitimate_resume_pattern_is_not_affected(aether_instance: Aether) -> None:
    # fork_run's own resume pattern (copy prefix events, then continue with
    # starting_step matching exactly) must continue to work after the fix.
    from aether.replay.fork import fork_run
    from aether.runtime.driver import Scenario, ScenarioStep, ScriptedAgent

    a = aether_instance

    @a.tool(name="a", side_effects=[SideEffectType.READ])
    def a_tool() -> dict:
        return {"x": 1}

    @a.tool(name="b", side_effects=[SideEffectType.READ])
    def b_tool() -> dict:
        return {"x": 2}

    tools = {"a": a_tool, "b": b_tool}
    scenario = Scenario(name="s", steps=[ScenarioStep("a", {}), ScenarioStep("b", {})])
    agent = ScriptedAgent(a, agent_id="agent1")
    run_id = agent.run_scenario(scenario, tools, run_id="parent_for_resume_test")

    # this must NOT raise, since fork_run sets starting_step to match the
    # copied prefix exactly
    result = fork_run(a, run_id, at_step=2, continuation=[ScenarioStep("b", {})], tools=tools)
    fork_events = a.recorder.storage.get_events(result.fork_run_id)
    assert len(fork_events) == 2  # 1 copied prefix event + 1 continuation event
