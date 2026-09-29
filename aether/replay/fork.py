"""Fork: branch a run before a given step and continue with a different
scenario, recorded as a new run linked to its parent.

`at_step` is the first step the fork's `continuation` is responsible for:
steps `1 .. at_step - 1` are copied verbatim from the parent (same tool,
arguments, result, side effects — new `run_id`/`event_id`/hash chain,
since they belong to a different chain), and `continuation` supplies step
`at_step` onward. This means forking "at" the risky action lets the
continuation genuinely replace that action (e.g. block it), while
everything that led up to it — including whatever made it look risky, like
having read a tainted email — is preserved unchanged. A later `diff`
between the fork and its parent then shows a true first divergence
exactly at `at_step`, not an artifact of the fork's own step numbering
starting over.

Aether has no live, autonomous agent to "re-decide" what happens after the
fork point — that's a genuine scope boundary, not hidden here. The
continuation is supplied explicitly by the caller (e.g. a scripted
continuation that skips the risky step, or one that uses a different tool
result). This is exactly the mechanism a future policy engine (Phase 4)
will plug into to make forks decide their own continuation.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from aether.core.errors import AetherReplayError
from aether.runtime.driver import ScenarioStep


@dataclass
class ForkResult:
    parent_run_id: str
    fork_run_id: str
    forked_at_step: int


def fork_run(
    aether: Any,
    parent_run_id: str,
    at_step: int,
    continuation: list[ScenarioStep],
    tools: dict[str, Callable[..., Any]],
    goal: str = "",
    fork_run_id: str | None = None,
    agent_id: str | None = None,
) -> ForkResult:
    """Create a new run whose steps `1 .. at_step - 1` are a verbatim
    replay of the parent's history, then continues live with
    `continuation` starting at step `at_step`.

    The caller is responsible for ensuring `tools`' backing state reflects
    the parent run's state as of just before `at_step` (e.g. a fresh shadow
    world prepared to match, or — as in the demo — tools whose only
    observable effect up to that point is identical to the parent's).
    Aether validates that `at_step` falls within (or one past the end of)
    the parent run, but does not — cannot, without a policy/risk engine —
    decide what the fork does next; that is `continuation`.
    """
    parent_events = aether.recorder.storage.get_events(parent_run_id)
    if not parent_events:
        raise AetherReplayError(f"parent run '{parent_run_id}' has no events")
    max_step = parent_events[-1].action.step
    if at_step < 1 or at_step > max_step + 1:
        raise AetherReplayError(
            f"fork step {at_step} out of range for run '{parent_run_id}' (valid: 1..{max_step + 1})"
        )

    new_run_id = fork_run_id or f"{parent_run_id}.fork_{at_step}"
    fork_goal = goal or f"fork of {parent_run_id} at step {at_step}"
    resolved_agent_id = agent_id or "fork_agent"

    aether.recorder.start_run(new_run_id, resolved_agent_id, "v1", goal=fork_goal)

    prefix_events = [e for e in parent_events if e.action.step < at_step]
    last_event_id: str | None = None
    for event in prefix_events:
        copied_action = event.action.model_copy(
            update={"run_id": new_run_id, "parent_action_id": last_event_id}
        )
        new_event = aether.recorder.record(copied_action, parent_event_id=last_event_id)
        last_event_id = new_event.event_id

    with aether.run(
        agent=resolved_agent_id, goal=fork_goal, run_id=new_run_id,
        starting_step=at_step - 1, starting_last_event_id=last_event_id,
    ):
        for step in continuation:
            if step.tool not in tools:
                raise KeyError(f"fork continuation references undeclared tool '{step.tool}'")
            tools[step.tool](**step.kwargs)

    return ForkResult(parent_run_id=parent_run_id, fork_run_id=new_run_id, forked_at_step=at_step)
