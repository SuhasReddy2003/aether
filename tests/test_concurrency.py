from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from aether.core.action import Action, Authorization
from aether.recording.hashchain import verify_chain
from aether.recording.recorder import Recorder


def _action(run_id: str, step: int) -> Action:
    return Action(
        run_id=run_id,
        step=step,
        agent_id="tester",
        tool="test.tool",
        arguments={"n": step},
        authorization=Authorization(role="agent"),
    )


def test_concurrent_writers_to_different_runs_do_not_corrupt_chains(data_dir: Path) -> None:
    recorder = Recorder(data_dir=data_dir)
    run_ids = [f"run_{i}" for i in range(5)]
    for rid in run_ids:
        recorder.start_run(rid, "tester", "v1")

    errors: list[Exception] = []

    def worker(run_id: str) -> None:
        try:
            for step in range(1, 21):
                recorder.record(_action(run_id, step))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(rid,)) for rid in run_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    for rid in run_ids:
        events = recorder.storage.get_events(rid)
        assert len(events) == 20
        valid, bad_id = verify_chain(events)
        assert valid, f"run {rid} chain invalid at {bad_id}"


def test_concurrent_writers_to_same_run_serialize_safely(data_dir: Path) -> None:
    # Same-run concurrent writers are expected to serialize (the recorder
    # holds a lock around read-last-event + append), so no event is lost and
    # the resulting chain is still valid, even though step numbers are
    # assigned by the caller here (a real Aether run assigns steps via a
    # single RunContext, so this test targets the Recorder/Storage layer's
    # own safety independent of that).
    recorder = Recorder(data_dir=data_dir)
    run_id = "run_shared"
    recorder.start_run(run_id, "tester", "v1")

    errors: list[Exception] = []
    lock_free_counter = threading.Lock()
    counter = {"n": 0}

    def next_step() -> int:
        with lock_free_counter:
            counter["n"] += 1
            return counter["n"]

    def worker() -> None:
        try:
            for _ in range(10):
                recorder.record(_action(run_id, next_step()))
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors
    events = recorder.storage.get_events(run_id)
    assert len(events) == 40
    valid, bad_id = verify_chain(events)
    assert valid, f"chain invalid at {bad_id}"


def test_asyncio_concurrent_tool_calls_across_tasks(data_dir: Path) -> None:
    from aether.core.side_effects import SideEffectType
    from aether.runtime.interceptor import Aether

    a = Aether(data_dir=data_dir)

    @a.tool(name="test.work", side_effects=[SideEffectType.READ])
    async def work(n: int) -> int:
        await asyncio.sleep(0)
        return n

    async def run_one(idx: int) -> str:
        with a.run(agent=f"agent_{idx}", goal="concurrent") as handle:
            for i in range(5):
                await work(i)
        return handle.run_id

    async def main() -> list[str]:
        return await asyncio.gather(*(run_one(i) for i in range(6)))

    run_ids = asyncio.run(main())
    assert len(set(run_ids)) == 6
    for rid in run_ids:
        events = a.recorder.storage.get_events(rid)
        assert len(events) == 5
        assert [e.action.step for e in events] == [1, 2, 3, 4, 5]
        valid, bad_id = verify_chain(events)
        assert valid, f"run {rid} invalid at {bad_id}"
