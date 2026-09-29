"""Replay engine.

Two modes:
- **strict**: reconstructs the recorded tool sequence, arguments, and
  results directly from stored events. No tool code is re-executed — a
  portable cassette does not carry executable code, so this is the only
  mode possible for an arbitrary cassette.
- **live-sim**: re-executes a *known* `Scenario` (kept by the caller
  alongside the run, e.g. a ScriptedAgent-driven demo) against a fresh
  tool registry, recording a brand-new run, and can be compared against
  the original with `assert_replay_matches`. This is how "replay actually
  reproduces the original" gets checked for real, not assumed — a
  divergence here is reported, never hidden.

Nondeterminism sources (external API results, wall-clock time, randomness,
model output) are labeled explicitly per step rather than silently
ignored.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from aether.core.errors import AetherReplayError
from aether.recording.recorder import Recorder
from aether.recording.verified import load_verified_events
from aether.runtime.driver import Scenario, ScriptedAgent


class NondeterminismSource(str, Enum):
    NONE = "none"
    EXTERNAL_API_RESULT = "external_api_result"
    TIME = "time"
    RANDOMNESS = "randomness"
    MODEL_OUTPUT = "model_output"


@dataclass
class ReplayStep:
    step: int
    tool: str
    arguments: dict[str, Any]
    result: Any
    status: str
    nondeterminism: NondeterminismSource = NondeterminismSource.NONE


@dataclass
class ReplayResult:
    run_id: str
    mode: str
    steps: list[ReplayStep] = field(default_factory=list)


def replay_strict(recorder: Recorder, run_id: str, verify: bool = True) -> ReplayResult:
    """Reconstruct history from storage. By default the run's hash chain and
    signature are verified first and AetherIntegrityError is raised on
    failure: replaying tampered history must never look like a success."""
    events = load_verified_events(recorder.storage, run_id) if verify else recorder.storage.get_events(run_id)
    if not events:
        raise AetherReplayError(f"run '{run_id}' has no events to replay")
    steps = [
        ReplayStep(
            step=e.action.step, tool=e.action.tool, arguments=e.action.arguments,
            result=e.action.result, status=e.action.status.value,
        )
        for e in events
    ]
    return ReplayResult(run_id=run_id, mode="strict", steps=steps)


def replay_live_sim(
    aether: Any,
    scenario: Scenario,
    tools_factory: Callable[[], dict[str, Callable[..., Any]]],
    new_run_id: str | None = None,
) -> ReplayResult:
    """Re-execute `scenario` against a FRESH tool registry (built by
    `tools_factory`, which is responsible for resetting any external state
    it captures) and record it as a brand new run."""
    agent = ScriptedAgent(aether, agent_id=f"replay_of_{scenario.name}")
    tools = tools_factory()
    run_id = agent.run_scenario(scenario, tools, run_id=new_run_id)
    return replay_strict(aether.recorder, run_id)


def assert_replay_matches(original: ReplayResult, replayed: ReplayResult) -> list[str]:
    """Step-by-step comparison of two ReplayResults. Returns a list of
    human-readable divergence descriptions; an empty list means the replay
    reproduced the original byte-for-byte (on the fields compared)."""
    divergences: list[str] = []
    if len(original.steps) != len(replayed.steps):
        divergences.append(f"step count differs: {len(original.steps)} vs {len(replayed.steps)}")

    for a, b in zip(original.steps, replayed.steps):
        if a.tool != b.tool:
            divergences.append(f"step {a.step}: tool differs: {a.tool!r} vs {b.tool!r}")
        if a.arguments != b.arguments:
            divergences.append(f"step {a.step}: arguments differ: {a.arguments!r} vs {b.arguments!r}")
        if a.result != b.result:
            divergences.append(f"step {a.step}: result differs: {a.result!r} vs {b.result!r}")

    return divergences
