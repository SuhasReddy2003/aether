"""The interceptor: `@aether.tool(...)` and the `Aether` runtime object.

This is the core of Phase 1: every decorated tool call is captured, timed,
executed, and recorded — exceptions are recorded and re-raised, never
swallowed.
"""
from __future__ import annotations

import contextvars
import functools
import inspect
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Self, TypeVar

from aether.core.action import Action, ActionStatus, Authorization, new_id
from aether.core.errors import AetherRunConflictError
from aether.core.event import Event
from aether.core.side_effects import SideEffect, SideEffectType
from aether.recording.recorder import Recorder

F = TypeVar("F", bound=Callable[..., Any])

# Per-context "current run" state, so nested/concurrent runs (including
# across asyncio tasks) do not clobber each other's step counters.
_current_run: contextvars.ContextVar[RunContext | None] = contextvars.ContextVar(
    "aether_current_run", default=None
)


class RunContext:
    def __init__(self, run_id: str, agent_id: str, agent_version: str, role: str, starting_step: int = 0) -> None:
        self.run_id = run_id
        self.agent_id = agent_id
        self.agent_version = agent_version
        self.role = role
        self.step = starting_step
        self.last_event_id: str | None = None

    def next_step(self) -> int:
        self.step += 1
        return self.step


class RunHandle:
    """Context manager returned by `aether.run(...)`."""

    def __init__(
        self,
        aether: Aether,
        run_id: str,
        agent_id: str,
        agent_version: str,
        goal: str,
        role: str,
        starting_step: int = 0,
        starting_last_event_id: str | None = None,
    ) -> None:
        self._aether = aether
        self.run_id = run_id
        self._ctx = RunContext(run_id, agent_id, agent_version, role, starting_step=starting_step)
        self._ctx.last_event_id = starting_last_event_id
        self._token: contextvars.Token | None = None
        self._goal = goal

    def __enter__(self) -> Self:
        self._aether.recorder.start_run(self.run_id, self._ctx.agent_id, self._ctx.agent_version, self._goal)
        existing_events = self._aether.recorder.storage.get_events(self.run_id)
        # A run_id with pre-existing events is only ever legitimate when
        # the caller is deliberately resuming from a known point (e.g.
        # `fork_run`, which copies a verbatim prefix first and then
        # resumes with `starting_step` set to exactly that prefix's
        # length). Any other case — most commonly, a fixed run_id
        # accidentally reused across two separate script invocations —
        # would otherwise silently restart step numbering at 1 while
        # appending to the SAME run_id, corrupting the recorded history
        # with duplicate, colliding step numbers. Found for real while
        # testing Phase 3's fork_demo.py against a persistent data dir.
        if existing_events and self._ctx.step != len(existing_events):
            raise AetherRunConflictError(
                f"run_id '{self.run_id}' already has {len(existing_events)} recorded event(s), "
                f"but this run was started expecting to begin at step {self._ctx.step}. "
                "Reusing a run_id for a fresh run silently corrupts step ordering — use a "
                "new run_id, or pass a starting_step that matches the existing history exactly "
                "(as fork_run does)."
            )
        self._token = _current_run.set(self._ctx)
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        if self._token is not None:
            _current_run.reset(self._token)

    async def __aenter__(self) -> Self:
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.__exit__(exc_type, exc, tb)


class Aether:
    """The Aether SDK entrypoint.

    Usage:
        aether = Aether()

        @aether.tool(name="database.update", side_effects=[...], reversible=True)
        def update_customer(...): ...

        with aether.run(agent="support", goal="Resolve refund"):
            update_customer(...)
    """

    def __init__(self, data_dir: Path | str | None = None) -> None:
        self.recorder = Recorder(data_dir=Path(data_dir) if data_dir else None)

    def run(
        self,
        agent: str,
        goal: str = "",
        agent_version: str = "v1",
        role: str = "agent",
        run_id: str | None = None,
        starting_step: int = 0,
        starting_last_event_id: str | None = None,
    ) -> RunHandle:
        """Start (or resume) a run. `starting_step`/`starting_last_event_id`
        let a caller resume an existing run's hash chain and step counter
        exactly where it left off — used by `aether.replay.fork` to append
        a live continuation after a verbatim-replayed prefix, rather than
        restarting step numbering from zero."""
        return RunHandle(
            self, run_id or new_id("run"), agent, agent_version, goal, role,
            starting_step=starting_step, starting_last_event_id=starting_last_event_id,
        )

    def tool(
        self,
        name: str,
        side_effects: list[SideEffectType | SideEffect] | None = None,
        reversible: bool = False,
        compensation: str | None = None,
        untrusted_source: bool = False,
    ) -> Callable[[F], F]:
        """Decorator that wraps a tool function so every call is intercepted
        and recorded. Works for both sync and async functions.

        `untrusted_source=True` marks this tool's *output* as untrusted
        content (e.g. email.read) — used by taint tracking from Phase 3
        onward; recorded now so historical events already carry the flag.
        """
        normalized_effects = self._normalize_side_effects(side_effects, reversible, compensation)

        def decorator(func: F) -> F:
            if inspect.iscoroutinefunction(func):

                @functools.wraps(func)
                async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                    return await self._invoke_async(
                        func, name, normalized_effects, untrusted_source, args, kwargs
                    )

                return async_wrapper  # type: ignore[return-value]

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                return self._invoke_sync(func, name, normalized_effects, untrusted_source, args, kwargs)

            return sync_wrapper  # type: ignore[return-value]

        return decorator

    def _normalize_side_effects(
        self,
        side_effects: list[SideEffectType | SideEffect] | None,
        reversible: bool,
        compensation: str | None,
    ) -> list[SideEffect]:
        if not side_effects:
            return []
        normalized: list[SideEffect] = []
        for effect in side_effects:
            if isinstance(effect, SideEffect):
                normalized.append(effect)
            else:
                normalized.append(
                    SideEffect(
                        type=effect,
                        reversible=reversible and effect != SideEffectType.IRREVERSIBLE,
                        compensation=compensation if reversible else None,
                    )
                )
        return normalized

    def _build_arguments(self, func: Callable, args: tuple, kwargs: dict) -> dict[str, Any]:
        try:
            sig = inspect.signature(func)
            bound = sig.bind_partial(*args, **kwargs)
            bound.apply_defaults()
            return dict(bound.arguments)
        except TypeError:
            return {"args": list(args), "kwargs": kwargs}

    def _invoke_sync(
        self, func: Callable, name: str, side_effects: list[SideEffect], untrusted: bool, args: tuple, kwargs: dict
    ) -> Any:
        ctx = self._require_context()
        arguments = self._build_arguments(func, args, kwargs)
        start = time.perf_counter()
        result: Any = None
        error: str | None = None
        status = ActionStatus.SUCCESS
        raised: Exception | None = None
        try:
            result = func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - intentionally broad, re-raised below
            status = ActionStatus.ERROR
            error = f"{type(exc).__name__}: {exc}"
            raised = exc
        duration_ms = (time.perf_counter() - start) * 1000.0
        self._record(ctx, name, arguments, result, error, status, side_effects, duration_ms, untrusted)
        if raised is not None:
            raise raised
        return result

    async def _invoke_async(
        self, func: Callable, name: str, side_effects: list[SideEffect], untrusted: bool, args: tuple, kwargs: dict
    ) -> Any:
        ctx = self._require_context()
        arguments = self._build_arguments(func, args, kwargs)
        start = time.perf_counter()
        result: Any = None
        error: str | None = None
        status = ActionStatus.SUCCESS
        raised: Exception | None = None
        try:
            result = await func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            status = ActionStatus.ERROR
            error = f"{type(exc).__name__}: {exc}"
            raised = exc
        duration_ms = (time.perf_counter() - start) * 1000.0
        self._record(ctx, name, arguments, result, error, status, side_effects, duration_ms, untrusted)
        if raised is not None:
            raise raised
        return result

    def _require_context(self) -> RunContext:
        ctx = _current_run.get()
        if ctx is None:
            raise RuntimeError(
                "No active Aether run. Wrap tool calls in `with aether.run(agent=...):`."
            )
        return ctx

    def _record(
        self,
        ctx: RunContext,
        name: str,
        arguments: dict[str, Any],
        result: Any,
        error: str | None,
        status: ActionStatus,
        side_effects: list[SideEffect],
        duration_ms: float,
        untrusted: bool,
    ) -> Event:
        step = ctx.next_step()
        serializable_result = result if _is_json_safe(result) else str(result)
        action = Action(
            run_id=ctx.run_id,
            step=step,
            agent_id=ctx.agent_id,
            agent_version=ctx.agent_version,
            tool=name,
            arguments=arguments if _is_json_safe(arguments) else {"repr": str(arguments)},
            result=serializable_result,
            error=error,
            status=status,
            authorization=Authorization(role=ctx.role, agent_id=ctx.agent_id),
            side_effects=side_effects,
            duration_ms=duration_ms,
            parent_action_id=ctx.last_event_id,
            untrusted_output=untrusted,
        )
        event = self.recorder.record(action, parent_event_id=ctx.last_event_id)
        ctx.last_event_id = event.event_id
        return event


def _is_json_safe(value: Any) -> bool:
    import json

    try:
        json.dumps(value, default=str)
        return True
    except TypeError:
        return False


# Convenience for `from aether import tool` style usage against a module-level
# default instance is intentionally NOT provided: an implicit global runtime
# would make multi-agent / multi-run programs silently share state. Callers
# always construct `Aether()` explicitly.
def tool(*_args: Any, **_kwargs: Any) -> Any:
    raise RuntimeError(
        "aether.tool is an instance method: create `a = Aether()` then use `@a.tool(...)`."
    )
