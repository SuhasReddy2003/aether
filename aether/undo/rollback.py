"""Rollback engine.

Walks a run's events backward from the tail to a target step and, for each
action strictly after that step, either:
  - applies its registered compensation, or
  - honestly reports `Compensation: UNAVAILABLE` if the action was not
    declared reversible, or no compensator is registered for it.

Every outcome — success, unavailability, or failure — is itself recorded as
a new, append-only event (never a rewrite of history), with
`source=ActionSource.ROLLBACK`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from aether.core.action import Action, ActionSource, ActionStatus, Authorization
from aether.core.errors import AetherRollbackError
from aether.recording.recorder import Recorder
from aether.undo.compensation import CompensationRegistry

Outcome = str  # "compensated" | "irreversible" | "failed" | "skipped_error_status"


@dataclass
class RollbackResult:
    action_id: str
    tool: str
    step: int
    outcome: Outcome
    detail: str = ""


@dataclass
class RollbackReport:
    run_id: str
    target_step: int
    results: list[RollbackResult] = field(default_factory=list)
    # None = no verification was requested (so nothing is claimed either way);
    # True/False = the post-rollback state hash did / did not match the expected one.
    state_verified: bool | None = None
    state_detail: str = ""

    @property
    def failures(self) -> list[RollbackResult]:
        return [r for r in self.results if r.outcome == "failed"]

    @property
    def compensated(self) -> list[RollbackResult]:
        return [r for r in self.results if r.outcome == "compensated"]

    @property
    def irreversible(self) -> list[RollbackResult]:
        return [r for r in self.results if r.outcome == "irreversible"]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target_step": self.target_step,
            "results": [r.__dict__ for r in self.results],
            "state_verified": self.state_verified,
            "state_detail": self.state_detail,
        }


class RollbackEngine:
    def __init__(self, recorder: Recorder, registry: CompensationRegistry) -> None:
        self.recorder = recorder
        self.registry = registry

    def rollback_to(
        self,
        run_id: str,
        target_step: int,
        context: dict[str, Any] | None = None,
        fail_fast: bool = False,
        expected_state_hash: str | None = None,
        state_probe: Callable[[], str] | None = None,
    ) -> RollbackReport:
        """Roll back. Compensators reporting success is NOT proof the state was
        restored (found by the Phase 3 self-audit: out-of-band drift made
        rollback report `compensated` while state differed from the
        checkpoint). To actually verify, pass `expected_state_hash` (e.g.
        from a checkpoint) and a `state_probe` returning the current state's
        content hash; the report then carries `state_verified` True/False.
        Without both, `state_verified` stays None: unverified, not "ok"."""
        context = context or {}
        events = self.recorder.storage.get_events(run_id)
        to_undo = [e for e in events if e.action.step > target_step]
        to_undo.reverse()  # undo most-recent-first

        report = RollbackReport(run_id=run_id, target_step=target_step)

        for event in to_undo:
            action = event.action
            last = self.recorder.storage.get_last_event(run_id)
            parent_id = last.event_id if last else None

            if action.status != ActionStatus.SUCCESS:
                report.results.append(
                    RollbackResult(action.action_id, action.tool, action.step, "skipped_error_status",
                                    "action never succeeded; nothing to undo")
                )
                continue

            reversible_effect = next((se for se in action.side_effects if se.reversible), None)
            if reversible_effect is None:
                detail = "Compensation: UNAVAILABLE (action was not declared reversible)"
                self._record(run_id, action, "irreversible", detail, parent_id)
                report.results.append(RollbackResult(action.action_id, action.tool, action.step, "irreversible", detail))
                continue

            compensator = self.registry.get(reversible_effect.compensation) if reversible_effect.compensation else None
            if compensator is None:
                detail = f"Compensation: UNAVAILABLE (no compensator registered for '{reversible_effect.compensation}')"
                self._record(run_id, action, "irreversible", detail, parent_id)
                report.results.append(RollbackResult(action.action_id, action.tool, action.step, "irreversible", detail))
                continue

            try:
                result = compensator(event, context)
                self._record(run_id, action, "compensated", str(result), parent_id, result=result)
                report.results.append(RollbackResult(action.action_id, action.tool, action.step, "compensated", str(result)))
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                self._record(run_id, action, "failed", detail, parent_id, is_error=True)
                report.results.append(RollbackResult(action.action_id, action.tool, action.step, "failed", detail))
                if fail_fast:
                    raise AetherRollbackError(
                        f"rollback of {action.tool} (step {action.step}) failed: {detail}"
                    ) from exc

        if expected_state_hash is not None and state_probe is not None:
            actual = state_probe()
            report.state_verified = actual == expected_state_hash
            report.state_detail = (
                "post-rollback state matches the expected hash"
                if report.state_verified
                else f"STATE MISMATCH: expected {expected_state_hash[:16]}..., got {actual[:16]}... "
                "(compensators ran, but the resulting state is not the checkpointed state)"
            )
        return report

    def _record(
        self,
        run_id: str,
        original_action: Action,
        outcome: str,
        detail: str,
        parent_event_id: str | None,
        result: dict[str, Any] | None = None,
        is_error: bool = False,
    ) -> None:
        last = self.recorder.storage.get_last_event(run_id)
        next_step = (last.action.step + 1) if last else 1
        rollback_action = Action(
            run_id=run_id,
            step=next_step,
            agent_id="aether_rollback_engine",
            tool=f"rollback.{original_action.tool}",
            arguments={"target_action_id": original_action.action_id, "target_step": original_action.step, "outcome": outcome},
            result=result if result is not None else {"detail": detail},
            status=ActionStatus.ERROR if is_error else ActionStatus.SUCCESS,
            error=detail if is_error else None,
            source=ActionSource.ROLLBACK,
            authorization=Authorization(role="system"),
        )
        self.recorder.record(rollback_action, parent_event_id=parent_event_id)
