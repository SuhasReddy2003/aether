from __future__ import annotations

import asyncio

import pytest

from aether.core.side_effects import SideEffectType
from aether.runtime.interceptor import Aether


def test_sync_tool_call_is_recorded(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.echo", side_effects=[SideEffectType.READ])
    def echo(x: int) -> int:
        return x * 2

    with a.run(agent="tester", goal="test sync") as handle:
        result = echo(21)
    assert result == 42

    events = a.recorder.storage.get_events(handle.run_id)
    assert len(events) == 1
    assert events[0].action.tool == "test.echo"
    assert events[0].action.arguments == {"x": 21}
    assert events[0].action.result == 42
    assert events[0].action.status.value == "success"


def test_async_tool_call_is_recorded(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.async_echo", side_effects=[SideEffectType.READ])
    async def echo(x: int) -> int:
        await asyncio.sleep(0)
        return x + 1

    async def go() -> tuple[str, int]:
        with a.run(agent="tester", goal="test async") as handle:
            result = await echo(9)
        return handle.run_id, result

    run_id, result = asyncio.run(go())
    assert result == 10
    events = a.recorder.storage.get_events(run_id)
    assert len(events) == 1
    assert events[0].action.tool == "test.async_echo"


def test_exception_is_recorded_and_reraised(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.boom", side_effects=[SideEffectType.WRITE])
    def boom() -> None:
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        with a.run(agent="tester", goal="test exception") as handle:
            boom()

    events = a.recorder.storage.get_events(handle.run_id)
    assert len(events) == 1
    assert events[0].action.status.value == "error"
    assert "kaboom" in (events[0].action.error or "")


def test_multiple_steps_increment_and_chain(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.noop", side_effects=[SideEffectType.READ])
    def noop(n: int) -> int:
        return n

    with a.run(agent="tester", goal="steps") as handle:
        for i in range(5):
            noop(i)

    events = a.recorder.storage.get_events(handle.run_id)
    assert [e.action.step for e in events] == [1, 2, 3, 4, 5]
    # each event's previous_hash must equal the prior event's hash
    for prev, cur in zip(events, events[1:]):
        assert cur.previous_hash == prev.hash


def test_call_outside_run_raises(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.orphan", side_effects=[SideEffectType.READ])
    def orphan() -> None:
        return None

    with pytest.raises(RuntimeError, match="No active Aether run"):
        orphan()


def test_redaction_applied_before_storage(aether_instance: Aether) -> None:
    a = aether_instance

    @a.tool(name="test.login", side_effects=[SideEffectType.WRITE])
    def login(username: str, password: str) -> dict:
        return {"ok": True, "password": password}

    with a.run(agent="tester", goal="login") as handle:
        login(username="bob", password="hunter2")

    events = a.recorder.storage.get_events(handle.run_id)
    assert events[0].action.arguments["password"] == "[REDACTED]"
    assert events[0].action.result["password"] == "[REDACTED]"
