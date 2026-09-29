"""Crash safety: if the process is killed mid-write, SQLite's WAL journal
plus our explicit BEGIN IMMEDIATE / COMMIT transaction boundaries must mean
the log is verifiable up to the last *complete* event, with no partial or
corrupt row for the interrupted one.

This is tested against a real subprocess that is hard-killed mid-run, not
simulated in-process, since in-process mocking cannot reproduce what
actually happens to the OS file/WAL on a hard kill.

Uses `Popen.kill()` rather than `send_signal(signal.SIGKILL)` because
SIGKILL does not exist on Windows — `Popen.kill()` maps to SIGKILL on
POSIX and to `TerminateProcess` on Windows, so this test is cross-platform.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from aether.recording.hashchain import verify_chain
from aether.storage.sqlite import SQLiteStorage

WORKER_SCRIPT = """
import sys, time
sys.path.insert(0, {pkg_root!r})
from aether.recording.recorder import Recorder
from aether.core.action import Action, Authorization

recorder = Recorder(data_dir={data_dir!r})
recorder.start_run("crash_run", "tester", "v1")
for i in range(1, 200):
    recorder.record(Action(
        run_id="crash_run", step=i, agent_id="tester", tool="test.slow",
        arguments={{"n": i}}, authorization=Authorization(role="agent"),
    ))
    time.sleep(0.01)
"""


def test_kill_mid_run_leaves_verifiable_prefix(tmp_path: Path) -> None:
    data_dir = tmp_path / "crash_data"
    data_dir.mkdir()
    pkg_root = str(Path(__file__).resolve().parents[1])

    script_path = tmp_path / "worker.py"
    script_path.write_text(
        WORKER_SCRIPT.format(pkg_root=pkg_root, data_dir=str(data_dir))
    )

    proc = subprocess.Popen([sys.executable, str(script_path)])
    time.sleep(0.3)  # let it write several events
    proc.kill()  # SIGKILL on POSIX, TerminateProcess on Windows
    proc.wait(timeout=5)

    storage = SQLiteStorage(data_dir / "aether.db")
    events = storage.get_events("crash_run")

    # We must have recorded a real, non-trivial prefix (proves the kill
    # landed mid-run, not before it started or after it finished).
    assert 1 <= len(events) < 199

    valid, bad_id = verify_chain(events)
    assert valid, f"prefix should verify cleanly up to the last complete event, but failed at {bad_id}"

    # steps must be contiguous with no gaps/duplicates - no partial event survived
    steps = [e.action.step for e in events]
    assert steps == list(range(1, len(events) + 1))
