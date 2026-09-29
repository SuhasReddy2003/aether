"""Run diff: compare two runs' event sequences, report the first point
where they diverge, plus summary deltas (tool call count, write count,
external/communication call count, and high-risk — financial or
irreversible — action count)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from aether.core.event import Event
from aether.recording.recorder import Recorder
from aether.recording.verified import load_verified_events

WRITE_TYPES = {"write", "update", "create", "delete"}
EXTERNAL_TYPES = {"external_call", "communication"}
HIGH_RISK_TYPES = {"financial", "irreversible"}


@dataclass
class DivergencePoint:
    step: int
    run_a_tool: str | None
    run_b_tool: str | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {"step": self.step, "run_a_tool": self.run_a_tool, "run_b_tool": self.run_b_tool, "reason": self.reason}


@dataclass
class RunDiff:
    run_a: str
    run_b: str
    first_divergence: DivergencePoint | None
    tool_calls_a: int
    tool_calls_b: int
    writes_a: int
    writes_b: int
    external_calls_a: int
    external_calls_b: int
    high_risk_a: int
    high_risk_b: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_a": self.run_a,
            "run_b": self.run_b,
            "first_divergence": self.first_divergence.to_dict() if self.first_divergence else None,
            "tool_calls_a": self.tool_calls_a,
            "tool_calls_b": self.tool_calls_b,
            "writes_a": self.writes_a,
            "writes_b": self.writes_b,
            "external_calls_a": self.external_calls_a,
            "external_calls_b": self.external_calls_b,
            "high_risk_a": self.high_risk_a,
            "high_risk_b": self.high_risk_b,
        }


def _count_by_side_effect(events: list[Event], types: set[str]) -> int:
    return sum(1 for e in events if any(se.type.value in types for se in e.action.side_effects))


def diff_runs(recorder: Recorder, run_a: str, run_b: str, verify: bool = True) -> RunDiff:
    """Diff two runs. Both runs' chains and signatures are verified first by
    default (AetherIntegrityError on failure): a diff of tampered history
    would present a forgery as a comparison of fact."""
    if verify:
        events_a = load_verified_events(recorder.storage, run_a)
        events_b = load_verified_events(recorder.storage, run_b)
    else:
        events_a = recorder.storage.get_events(run_a)
        events_b = recorder.storage.get_events(run_b)

    first_divergence: DivergencePoint | None = None
    for a, b in zip(events_a, events_b):
        if a.action.tool != b.action.tool or a.action.arguments != b.action.arguments:
            first_divergence = DivergencePoint(
                step=a.action.step, run_a_tool=a.action.tool, run_b_tool=b.action.tool,
                reason="tool or arguments differ",
            )
            break
        if a.action.result != b.action.result:
            first_divergence = DivergencePoint(
                step=a.action.step, run_a_tool=a.action.tool, run_b_tool=b.action.tool,
                reason="same tool/arguments but result differs",
            )
            break

    if first_divergence is None and len(events_a) != len(events_b):
        shorter = min(len(events_a), len(events_b))
        if len(events_a) > len(events_b):
            longer_run, longer_events = run_a, events_a
        else:
            longer_run, longer_events = run_b, events_b
        extra_tool = longer_events[shorter].action.tool
        first_divergence = DivergencePoint(
            step=longer_events[shorter].action.step,
            run_a_tool=extra_tool if longer_run == run_a else None,
            run_b_tool=extra_tool if longer_run == run_b else None,
            reason=f"{longer_run} has additional steps beyond this point",
        )

    return RunDiff(
        run_a=run_a,
        run_b=run_b,
        first_divergence=first_divergence,
        tool_calls_a=len(events_a),
        tool_calls_b=len(events_b),
        writes_a=_count_by_side_effect(events_a, WRITE_TYPES),
        writes_b=_count_by_side_effect(events_b, WRITE_TYPES),
        external_calls_a=_count_by_side_effect(events_a, EXTERNAL_TYPES),
        external_calls_b=_count_by_side_effect(events_b, EXTERNAL_TYPES),
        high_risk_a=_count_by_side_effect(events_a, HIGH_RISK_TYPES),
        high_risk_b=_count_by_side_effect(events_b, HIGH_RISK_TYPES),
    )
