from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from aether.core.action import Action, ActionSource, ActionStatus, Authorization
from aether.core.errors import AetherRollbackError
from aether.core.side_effects import SideEffect, SideEffectType
from aether.recording.hashchain import verify_chain
from aether.recording.recorder import Recorder
from aether.sandbox.filesystem import ShadowFilesystem
from aether.state.snapshot import content_hash
from aether.state.world import WorldState
from aether.undo.builtin import fs_restore_write, fs_undo_create, fs_undo_delete, world_restore_from_result
from aether.undo.compensation import CompensationRegistry
from aether.undo.rollback import RollbackEngine


@pytest.fixture()
def setup(tmp_path: Path, data_dir: Path):
    recorder = Recorder(data_dir=data_dir)
    run_id = "run_undo"
    recorder.start_run(run_id, "tester", "v1", goal="undo test")

    fs = ShadowFilesystem(root=tmp_path / "shadow_fs")
    world = WorldState(data_dir=data_dir, run_id=run_id)

    registry = CompensationRegistry()
    registry.register("fs.restore_write", fs_restore_write)
    registry.register("fs.undo_create", fs_undo_create)
    registry.register("fs.undo_delete", fs_undo_delete)
    registry.register("crm.restore_previous", world_restore_from_result("customer_id"))

    engine = RollbackEngine(recorder, registry)
    context = {"fs": fs, "world": world}
    return SimpleNamespace(recorder=recorder, run_id=run_id, fs=fs, world=world,
                           registry=registry, engine=engine, context=context)


def _record_fs_create(recorder: Recorder, run_id: str, step: int, fs: ShadowFilesystem, path: str, content: str) -> None:
    result = fs.create(path, content)
    recorder.record(
        Action(
            run_id=run_id, step=step, agent_id="tester", tool="fs.create",
            arguments={"virtual_path": path}, result=result,
            side_effects=[SideEffect(type=SideEffectType.CREATE, reversible=True, compensation="fs.undo_create")],
            authorization=Authorization(role="agent"),
        )
    )


def _record_crm_update(recorder: Recorder, run_id: str, step: int, world: WorldState, customer_id: str, note: str) -> None:
    data = world.load()
    previous = data.get(customer_id)
    data[customer_id] = {"note": note}
    world.save(data)
    recorder.record(
        Action(
            run_id=run_id, step=step, agent_id="tester", tool="crm.update",
            arguments={"customer_id": customer_id, "note": note},
            result={"customer_id": customer_id, "previous": previous},
            side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="crm.restore_previous")],
            authorization=Authorization(role="agent"),
        )
    )


def _record_irreversible_payment(recorder: Recorder, run_id: str, step: int, amount: float) -> None:
    recorder.record(
        Action(
            run_id=run_id, step=step, agent_id="tester", tool="payment.create",
            arguments={"amount": amount},
            result={"payment_id": "sim_1", "status": "simulated"},
            side_effects=[SideEffect(type=SideEffectType.IRREVERSIBLE, reversible=False)],
            authorization=Authorization(role="agent"),
        )
    )


def test_rollback_restores_fs_write_exactly(setup) -> None:
    recorder, run_id, fs, engine, context = setup.recorder, setup.run_id, setup.fs, setup.engine, setup.context
    _record_fs_create(recorder, run_id, 1, fs, "notice.txt", "original content")

    # checkpoint the state right after step 1
    checkpoint_hash = content_hash(fs.list_all())

    # step 2: overwrite it
    write_result = fs.write("notice.txt", "MUTATED CONTENT")
    recorder.record(
        Action(
            run_id=run_id, step=2, agent_id="tester", tool="fs.write",
            arguments={"virtual_path": "notice.txt"}, result=write_result,
            side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="fs.restore_write")],
            authorization=Authorization(role="agent"),
        )
    )
    assert fs.read("notice.txt") == "MUTATED CONTENT"

    report = engine.rollback_to(run_id, target_step=1, context=context)
    assert len(report.compensated) == 1
    assert fs.read("notice.txt") == "original content"
    assert content_hash(fs.list_all()) == checkpoint_hash  # rollback fidelity, by content hash


def test_rollback_restores_world_state_exactly(setup) -> None:
    recorder, run_id, world, engine, context = setup.recorder, setup.run_id, setup.world, setup.engine, setup.context
    _record_crm_update(recorder, run_id, 1, world, "cust_1", "first note")
    checkpoint_hash = world.content_hash()

    _record_crm_update(recorder, run_id, 2, world, "cust_1", "SECOND NOTE OVERWRITE")
    assert world.load()["cust_1"]["note"] == "SECOND NOTE OVERWRITE"

    report = engine.rollback_to(run_id, target_step=1, context=context)
    assert len(report.compensated) == 1
    assert world.load()["cust_1"]["note"] == "first note"
    assert world.content_hash() == checkpoint_hash


def test_irreversible_action_is_reported_honestly_not_faked(setup) -> None:
    recorder, run_id, world, engine, context = setup.recorder, setup.run_id, setup.world, setup.engine, setup.context
    _record_crm_update(recorder, run_id, 1, world, "cust_1", "note")
    _record_irreversible_payment(recorder, run_id, 2, 2840.0)

    report = engine.rollback_to(run_id, target_step=0, context=context)
    irreversible = [r for r in report.results if r.tool == "payment.create"]
    assert len(irreversible) == 1
    assert irreversible[0].outcome == "irreversible"
    assert "UNAVAILABLE" in irreversible[0].detail


def test_rollback_is_append_only_and_chain_stays_valid(setup) -> None:
    recorder, run_id, fs, world, engine, context = setup.recorder, setup.run_id, setup.fs, setup.world, setup.engine, setup.context
    _record_fs_create(recorder, run_id, 1, fs, "a.txt", "hello")
    _record_crm_update(recorder, run_id, 2, world, "cust_1", "note")

    events_before = recorder.storage.get_events(run_id)
    engine.rollback_to(run_id, target_step=0, context=context)
    events_after = recorder.storage.get_events(run_id)

    # append-only: every original event is still present, unmodified
    assert events_after[: len(events_before)] == events_before
    # new rollback events were appended
    assert len(events_after) > len(events_before)
    assert all(e.action.source == ActionSource.ROLLBACK for e in events_after[len(events_before):])
    # the whole chain, including rollback events, still verifies
    valid, bad_id = verify_chain(events_after)
    assert valid, f"chain invalid at {bad_id}"


def test_rollback_failure_is_recorded_not_swallowed(setup) -> None:
    recorder, run_id, fs, registry, engine, context = setup.recorder, setup.run_id, setup.fs, setup.registry, setup.engine, setup.context

    def _broken_compensator(event, ctx):
        raise RuntimeError("simulated compensator failure")

    registry.register("fs.undo_create", _broken_compensator)
    _record_fs_create(recorder, run_id, 1, fs, "a.txt", "hello")

    report = engine.rollback_to(run_id, target_step=0, context=context)
    assert len(report.failures) == 1
    assert "simulated compensator failure" in report.failures[0].detail

    # the failure itself must be a real recorded event, not silently dropped
    events = recorder.storage.get_events(run_id)
    failure_events = [e for e in events if e.action.status == ActionStatus.ERROR]
    assert len(failure_events) == 1


def test_rollback_fail_fast_raises(setup) -> None:
    recorder, run_id, fs, registry, engine, context = setup.recorder, setup.run_id, setup.fs, setup.registry, setup.engine, setup.context

    def _broken_compensator(event, ctx):
        raise RuntimeError("boom")

    registry.register("fs.undo_create", _broken_compensator)
    _record_fs_create(recorder, run_id, 1, fs, "a.txt", "hello")

    with pytest.raises(AetherRollbackError):
        engine.rollback_to(run_id, target_step=0, context=context, fail_fast=True)


def test_no_compensator_registered_reports_irreversible_not_silent_skip(setup) -> None:
    recorder, run_id, engine, context = setup.recorder, setup.run_id, setup.engine, setup.context
    recorder.record(
        Action(
            run_id=run_id, step=1, agent_id="tester", tool="mystery.tool",
            arguments={}, result={},
            side_effects=[SideEffect(type=SideEffectType.UPDATE, reversible=True, compensation="nonexistent.compensator")],
            authorization=Authorization(role="agent"),
        )
    )
    report = engine.rollback_to(run_id, target_step=0, context=context)
    assert report.results[0].outcome == "irreversible"
    assert "no compensator registered" in report.results[0].detail
