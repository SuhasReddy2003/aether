# Limitations

This document is updated every phase. It only describes what exists in the
current codebase — see `docs/PROGRESS.md` for what phase that is.

## Phase 1 (current)

- **Signing key is a local file, not a KMS.** `KeyPair.load_or_create`
  writes a base64-encoded raw Ed25519 private key to
  `~/.aether/signing_key` with `chmod 600` (best-effort; not enforced on
  all platforms). Anyone with read access to that file can forge new
  signatures for any chain head. This is a real trust-boundary limit, not
  a bug: see `docs/threat-model.md` (to be written in a later phase) for
  what the signature does and does not prove.
- **Redaction is permanent and irreversible.** Because redaction happens
  before hashing (`Recorder.record` redacts, then seals the event), a
  redacted value never enters the chain in any recoverable form. There is
  no "redact for display, keep original for audit" mode in Phase 1.
- **`verify_chain()` does not, by itself, detect tail truncation combined
  with signature replacement.** If an attacker deletes the most recent N
  events *and* re-signs the new (shorter) chain head with a stolen or
  legitimate key, `aether verify` reports VALID, because internally the
  remaining chain is self-consistent. Detecting this requires comparing
  against an independently-retained head hash (e.g. from a previously
  exported cassette, or a signature stored outside the same database) —
  this cross-check is not implemented yet.
- **No provenance, taint tracking, replay, fork, diff, policy engine, risk
  engine, undo/rollback, shadow world, or benchmarks exist yet.** These are
  Phases 2-5 of the specification.
- **Concurrency**: same-run concurrent writers are serialized by a
  process-local lock in `Recorder`. This is correct for a single Python
  process (tested with threads and asyncio in `tests/test_concurrency.py`)
  but does NOT protect against two separate OS processes writing to the
  same run concurrently — that would require OS-level file locking or a
  single-writer architecture, neither of which is implemented in Phase 1.
- **Crash safety** was tested via SIGKILL of a subprocess mid-run
  (`tests/test_crash_safety.py`): the chain remains verifiable up to the
  last event that reached `COMMIT`. This was tested once, on Linux, with
  SQLite WAL mode; it was not tested under simulated disk-full or
  filesystem-corruption conditions.
