from __future__ import annotations

import json
from pathlib import Path

import pytest

from aether.core.errors import AetherCassetteError, AetherIntegrityError
from aether.core.action import Action, Authorization
from aether.recording.cassette import export_cassette, import_cassette, load_cassette
from aether.recording.recorder import Recorder
from aether.storage.sqlite import SQLiteStorage


def _record_run(recorder: Recorder, run_id: str, n: int = 4) -> None:
    recorder.start_run(run_id, "tester", "v1", goal="test goal")
    for i in range(1, n + 1):
        recorder.record(
            Action(
                run_id=run_id,
                step=i,
                agent_id="tester",
                tool="test.tool",
                arguments={"n": i},
                result={"ok": True},
                authorization=Authorization(role="agent"),
            )
        )


def test_export_import_roundtrip(recorder: Recorder, tmp_path: Path) -> None:
    run_id = "run_roundtrip"
    _record_run(recorder, run_id)

    out = tmp_path / "run.aether"
    export_cassette(recorder.storage, run_id, out)
    assert out.exists()

    new_storage = SQLiteStorage(tmp_path / "other.db")
    imported_id = import_cassette(new_storage, out, verify=True)
    assert imported_id == run_id

    original_events = recorder.storage.get_events(run_id)
    imported_events = new_storage.get_events(run_id)
    assert len(original_events) == len(imported_events)
    for a, b in zip(original_events, imported_events):
        assert a.hash == b.hash


def test_export_fails_on_empty_run(storage: SQLiteStorage, tmp_path: Path) -> None:
    storage.create_run("run_empty", "tester", "v1")
    with pytest.raises(AetherCassetteError):
        export_cassette(storage, "run_empty", tmp_path / "empty.aether")


def test_import_rejects_malformed_json(tmp_path: Path, storage: SQLiteStorage) -> None:
    bad = tmp_path / "bad.aether"
    bad.write_text("{not valid json")
    with pytest.raises(AetherCassetteError):
        import_cassette(storage, bad)


def test_import_rejects_missing_fields(tmp_path: Path, storage: SQLiteStorage) -> None:
    bad = tmp_path / "bad2.aether"
    bad.write_text(json.dumps({"cassette_schema_version": 1}))
    with pytest.raises(AetherCassetteError):
        import_cassette(storage, bad)


def test_import_rejects_wrong_schema_version(tmp_path: Path, storage: SQLiteStorage) -> None:
    bad = tmp_path / "bad3.aether"
    bad.write_text(json.dumps({"cassette_schema_version": 999, "run_id": "x", "events": []}))
    with pytest.raises(AetherCassetteError):
        import_cassette(storage, bad)


def test_import_rejects_tampered_chain(recorder: Recorder, tmp_path: Path, storage: SQLiteStorage) -> None:
    run_id = "run_tampered_export"
    _record_run(recorder, run_id)
    out = tmp_path / "tampered.aether"
    export_cassette(recorder.storage, run_id, out)

    data = json.loads(out.read_text())
    data["events"][1]["action"]["arguments"]["n"] = -1  # tamper post-export
    out.write_text(json.dumps(data))

    with pytest.raises(AetherIntegrityError):
        import_cassette(storage, out, verify=True)


def test_import_rejects_forged_signature(recorder: Recorder, tmp_path: Path, storage: SQLiteStorage) -> None:
    run_id = "run_forged_sig"
    _record_run(recorder, run_id)
    out = tmp_path / "forged.aether"
    export_cassette(recorder.storage, run_id, out)

    data = json.loads(out.read_text())
    data["signature"]["signature_b64"] = "not_a_real_signature_base64=="
    out.write_text(json.dumps(data))

    with pytest.raises(AetherIntegrityError):
        load_cassette(out, verify=True)


def test_import_rejects_oversized_cassette(tmp_path: Path, storage: SQLiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    import aether.recording.cassette as cassette_mod

    monkeypatch.setattr(cassette_mod, "MAX_CASSETTE_BYTES", 10)
    big = tmp_path / "big.aether"
    big.write_text(json.dumps({"cassette_schema_version": 1, "run_id": "x", "events": []}))
    with pytest.raises(AetherCassetteError):
        import_cassette(storage, big)


def test_import_rejects_too_many_events(tmp_path: Path, storage: SQLiteStorage, monkeypatch: pytest.MonkeyPatch) -> None:
    import aether.recording.cassette as cassette_mod

    monkeypatch.setattr(cassette_mod, "MAX_EVENTS", 2)
    huge = tmp_path / "huge.aether"
    huge.write_text(json.dumps({"cassette_schema_version": 1, "run_id": "x", "events": [{}, {}, {}]}))
    with pytest.raises(AetherCassetteError):
        import_cassette(storage, huge)
