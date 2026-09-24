"""Cassette: a portable, signed, self-contained recording of a run.

A `.aether` file is JSON on disk. It is treated as UNTRUSTED input on
import: schema is validated, size is capped, chain and signature are
verified before anything else is trusted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aether.core.errors import AetherCassetteError, AetherIntegrityError
from aether.core.event import Event
from aether.recording.hashchain import verify_chain
from aether.recording.signing import verify_signature
from aether.storage.base import Storage

CASSETTE_SCHEMA_VERSION = 1
MAX_CASSETTE_BYTES = 100 * 1024 * 1024  # 100MB cap against zip-bomb-style abuse
MAX_EVENTS = 200_000


def export_cassette(storage: Storage, run_id: str, out_path: str | Path) -> Path:
    events = storage.get_events(run_id)
    if not events:
        raise AetherCassetteError(f"run '{run_id}' has no events to export")
    meta = storage.get_run_meta(run_id) or {}
    sig = storage.get_run_signature(run_id)

    cassette: dict[str, Any] = {
        "cassette_schema_version": CASSETTE_SCHEMA_VERSION,
        "run_id": run_id,
        "agent_id": meta.get("agent_id"),
        "agent_version": meta.get("agent_version"),
        "goal": meta.get("goal"),
        "events": [json.loads(e.model_dump_json()) for e in events],
        "signature": sig,
    }
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(cassette, indent=None, sort_keys=True))
    return out_path


def _migrate(data: dict[str, Any]) -> dict[str, Any]:
    """Migrate older cassette schema versions forward. Only version 1
    exists today; this is the seam future versions attach to."""
    version = data.get("cassette_schema_version")
    if version == CASSETTE_SCHEMA_VERSION:
        return data
    if version is None:
        raise AetherCassetteError("cassette missing cassette_schema_version")
    raise AetherCassetteError(
        f"unsupported cassette schema version {version} (this build supports {CASSETTE_SCHEMA_VERSION})"
    )


def _validate_shape(data: Any) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise AetherCassetteError("cassette root must be a JSON object")
    required = {"cassette_schema_version", "run_id", "events"}
    missing = required - data.keys()
    if missing:
        raise AetherCassetteError(f"cassette missing required fields: {sorted(missing)}")
    if not isinstance(data["events"], list):
        raise AetherCassetteError("cassette 'events' must be a list")
    if len(data["events"]) > MAX_EVENTS:
        raise AetherCassetteError(f"cassette has {len(data['events'])} events, exceeds cap of {MAX_EVENTS}")
    if not isinstance(data["run_id"], str) or not data["run_id"]:
        raise AetherCassetteError("cassette 'run_id' must be a non-empty string")
    return data


def load_cassette(path: str | Path, verify: bool = True) -> tuple[dict[str, Any], list[Event]]:
    """Load and validate a cassette file. Raises AetherCassetteError for
    malformed/oversized/unsafe input, AetherIntegrityError if verify=True
    and the chain or signature does not check out.
    """
    path = Path(path)
    size = path.stat().st_size
    if size > MAX_CASSETTE_BYTES:
        raise AetherCassetteError(f"cassette file is {size} bytes, exceeds cap of {MAX_CASSETTE_BYTES}")

    raw_text = path.read_text()
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise AetherCassetteError(f"cassette is not valid JSON: {exc}") from exc

    data = _validate_shape(data)
    data = _migrate(data)

    events: list[Event] = []
    for i, raw_event in enumerate(data["events"]):
        if not isinstance(raw_event, dict):
            raise AetherCassetteError(f"event at index {i} is not an object")
        try:
            events.append(Event.model_validate(raw_event))
        except Exception as exc:  # pydantic ValidationError, keep message
            raise AetherCassetteError(f"event at index {i} failed schema validation: {exc}") from exc

    if verify:
        valid, bad_id = verify_chain(events)
        if not valid:
            raise AetherIntegrityError(
                f"cassette hash chain is invalid (first bad event: {bad_id})", first_bad_event_id=bad_id
            )
        sig = data.get("signature")
        if sig and events:
            head = events[-1]
            ok = verify_signature(sig["public_key_b64"], head.hash.encode("utf-8"), sig["signature_b64"])
            if not ok:
                raise AetherIntegrityError("cassette signature does not verify against chain head")

    return data, events


def import_cassette(storage: Storage, path: str | Path, verify: bool = True) -> str:
    data, events = load_cassette(path, verify=verify)
    run_id = data["run_id"]
    storage.create_run(run_id, data.get("agent_id") or "unknown", data.get("agent_version") or "v1", data.get("goal") or "")
    existing_ids = {e.event_id for e in storage.get_events(run_id)}
    for event in events:
        if event.event_id in existing_ids:
            continue
        storage.append_event(event)
    sig = data.get("signature")
    if sig:
        storage.set_run_signature(run_id, sig["public_key_b64"], sig["signature_b64"], sig["head_hash"])
    return run_id
