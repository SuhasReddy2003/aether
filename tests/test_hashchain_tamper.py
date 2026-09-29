from __future__ import annotations

import json

from aether.core.action import Action, Authorization
from aether.recording.hashchain import verify_chain
from aether.recording.recorder import Recorder
from aether.storage.sqlite import SQLiteStorage


def _make_action(run_id: str, step: int, tool: str = "test.tool") -> Action:
    return Action(
        run_id=run_id,
        step=step,
        agent_id="tester",
        tool=tool,
        arguments={"n": step},
        result={"ok": True},
        authorization=Authorization(role="agent"),
    )


def test_clean_chain_verifies(recorder: Recorder) -> None:
    run_id = "run_clean"
    recorder.start_run(run_id, "tester", "v1")
    for i in range(1, 6):
        recorder.record(_make_action(run_id, i))
    events = recorder.storage.get_events(run_id)
    valid, bad_id = verify_chain(events)
    assert valid
    assert bad_id is None


def test_tampering_one_field_is_detected_at_exact_event(storage: SQLiteStorage, recorder: Recorder) -> None:
    run_id = "run_tamper"
    recorder.start_run(run_id, "tester", "v1")
    for i in range(1, 6):
        recorder.record(_make_action(run_id, i))

    events_before = storage.get_events(run_id)
    target = events_before[2]  # step 3

    conn = storage.raw_connection()
    row = conn.execute("SELECT event_json FROM events WHERE event_id=?", (target.event_id,)).fetchone()
    data = json.loads(row["event_json"])
    data["action"]["arguments"]["n"] = 999999  # tamper without recomputing hash
    conn.execute("UPDATE events SET event_json=? WHERE event_id=?", (json.dumps(data), target.event_id))

    events_after = storage.get_events(run_id)
    valid, bad_id = verify_chain(events_after)
    assert not valid
    assert bad_id == target.event_id


def test_deleting_an_event_breaks_the_chain(storage: SQLiteStorage, recorder: Recorder) -> None:
    run_id = "run_delete"
    recorder.start_run(run_id, "tester", "v1")
    for i in range(1, 6):
        recorder.record(_make_action(run_id, i))

    events_before = storage.get_events(run_id)
    victim = events_before[2]

    conn = storage.raw_connection()
    conn.execute("DELETE FROM events WHERE event_id=?", (victim.event_id,))

    events_after = storage.get_events(run_id)
    valid, bad_id = verify_chain(events_after)
    assert not valid
    # the event immediately after the deleted one now has a broken previous_hash link
    assert bad_id == events_before[3].event_id


def test_reordering_events_breaks_the_chain(storage: SQLiteStorage, recorder: Recorder) -> None:
    run_id = "run_reorder"
    recorder.start_run(run_id, "tester", "v1")
    for i in range(1, 5):
        recorder.record(_make_action(run_id, i))

    events = storage.get_events(run_id)
    swapped = [events[0], events[2], events[1], events[3]]
    valid, _bad_id = verify_chain(swapped)
    assert not valid


def test_truncating_the_tail_still_verifies_the_remaining_prefix(storage: SQLiteStorage, recorder: Recorder) -> None:
    run_id = "run_truncate"
    recorder.start_run(run_id, "tester", "v1")
    for i in range(1, 6):
        recorder.record(_make_action(run_id, i))

    events = storage.get_events(run_id)
    truncated = events[:3]
    valid, bad_id = verify_chain(truncated)
    # a truncated-but-otherwise-untouched prefix is a valid chain on its own;
    # detecting truncation itself requires comparing chain length against an
    # externally-known head (signature/head_hash), which the CLI test covers.
    assert valid
    assert bad_id is None


def test_swapping_events_between_two_runs_breaks_chain(storage: SQLiteStorage, recorder: Recorder) -> None:
    run_a, run_b = "run_a", "run_b"
    recorder.start_run(run_a, "tester", "v1")
    recorder.start_run(run_b, "tester", "v1")
    for i in range(1, 4):
        recorder.record(_make_action(run_a, i))
    for i in range(1, 4):
        recorder.record(_make_action(run_b, i))

    events_a = storage.get_events(run_a)
    events_b = storage.get_events(run_b)
    mixed = [events_a[0], events_b[1], events_a[2]]
    valid, _bad_id = verify_chain(mixed)
    assert not valid


def test_replacing_signature_is_detected(storage: SQLiteStorage, recorder: Recorder) -> None:
    from aether.recording.signing import KeyPair, verify_signature

    run_id = "run_sig"
    recorder.start_run(run_id, "tester", "v1")
    recorder.record(_make_action(run_id, 1))
    events = storage.get_events(run_id)
    sig = storage.get_run_signature(run_id)
    assert sig is not None
    assert verify_signature(sig["public_key_b64"], events[-1].hash.encode(), sig["signature_b64"])

    forged_keypair = KeyPair.generate()
    forged_sig = forged_keypair.sign(events[-1].hash.encode())
    assert not verify_signature(sig["public_key_b64"], events[-1].hash.encode(), forged_sig)
