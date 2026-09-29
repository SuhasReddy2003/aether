"""Load a run's events only after verifying them.

Found by the Phase 3 self-audit (docs/self-audit-phase3.md): `replay_strict`
and `diff_runs` originally read events straight from storage, so replaying a
tampered run "succeeded" and silently reproduced the tampered history.
Anything that presents recorded history as fact must go through this.
"""
from __future__ import annotations

from aether.core.errors import AetherIntegrityError
from aether.core.event import Event
from aether.recording.hashchain import verify_chain
from aether.recording.signing import verify_signature
from aether.storage.base import Storage


def load_verified_events(
    storage: Storage, run_id: str, trusted_public_keys: set[str] | None = None
) -> list[Event]:
    """Return the run's events, or raise AetherIntegrityError if the chain is
    broken, the run is unsigned, the signature does not match the chain head,
    or (when `trusted_public_keys` is given) the signer is not trusted.
    A run with no events returns an empty list; callers decide what that means.
    """
    events = storage.get_events(run_id)
    if not events:
        return events

    valid, bad_id = verify_chain(events)
    if not valid:
        raise AetherIntegrityError(
            f"run '{run_id}' failed hash-chain verification (first bad event: {bad_id})",
            first_bad_event_id=bad_id,
        )

    sig = storage.get_run_signature(run_id)
    if not sig:
        raise AetherIntegrityError(f"run '{run_id}' is unsigned; a bare hash chain is not treated as verified")
    if not verify_signature(sig["public_key_b64"], events[-1].hash.encode("utf-8"), sig["signature_b64"]):
        raise AetherIntegrityError(f"run '{run_id}' signature does not match its chain head")
    if trusted_public_keys is not None and sig["public_key_b64"] not in trusted_public_keys:
        raise AetherIntegrityError(f"run '{run_id}' is signed by a key outside the trusted set")
    return events
