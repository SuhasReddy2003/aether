"""Counterfactuals: "what if" scenarios, always executed via simulation
(a fresh tool registry / shadow world), never against anything real.

Built directly on `fork_run`: a counterfactual is a fork whose
continuation differs from the original in one specific, labeled way.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

from aether.replay.fork import ForkResult, fork_run
from aether.runtime.driver import ScenarioStep


class CounterfactualType(str, Enum):
    BLOCKED = "blocked"  # the risky step is skipped entirely
    DIFFERENT_TOOL_RESULT = "different_tool_result"
    COMPENSATION_TRIGGERED = "compensation_triggered"


@dataclass
class CounterfactualResult:
    counterfactual_type: CounterfactualType
    fork: ForkResult


def counterfactual_block(
    aether: Any,
    parent_run_id: str,
    at_step: int,
    continuation_without_risky_step: list[ScenarioStep],
    tools: dict[str, Callable[..., Any]],
    goal: str = "",
) -> CounterfactualResult:
    """Simulate: what if the action at `at_step` had been BLOCKED? The
    caller supplies the continuation with that step omitted — Aether has no
    autonomous agent to decide this itself (see fork_run's docstring)."""
    fork = fork_run(
        aether, parent_run_id, at_step, continuation_without_risky_step, tools, goal=goal,
        fork_run_id=f"{parent_run_id}.counterfactual_blocked_{at_step}",
        agent_id="counterfactual_agent",
    )
    return CounterfactualResult(counterfactual_type=CounterfactualType.BLOCKED, fork=fork)


def counterfactual_different_result(
    aether: Any,
    parent_run_id: str,
    at_step: int,
    continuation_with_alternate_result: list[ScenarioStep],
    tools: dict[str, Callable[..., Any]],
    goal: str = "",
) -> CounterfactualResult:
    """Simulate: what if a step had returned a different tool result (e.g.
    a stricter validation tool that rejects the amount)? The caller
    supplies the alternate continuation; Aether records and diffs it like
    any other fork."""
    fork = fork_run(
        aether, parent_run_id, at_step, continuation_with_alternate_result, tools, goal=goal,
        fork_run_id=f"{parent_run_id}.counterfactual_altresult_{at_step}",
        agent_id="counterfactual_agent",
    )
    return CounterfactualResult(counterfactual_type=CounterfactualType.DIFFERENT_TOOL_RESULT, fork=fork)
