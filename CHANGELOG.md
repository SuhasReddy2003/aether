# Changelog

## Phase 1 — Flight Recorder

### Added
- Core models: `Action`, `Event`, `SideEffect` (with reversibility-honesty
  validation), `Authorization`.
- Deterministic canonical JSON serialization (`aether/core/canonical.py`),
  property-tested against arbitrary JSON-like values via Hypothesis.
- SHA-256 hash chain (`aether/recording/hashchain.py`):
  `hash_i = SHA256(hash_{i-1} || canonical_json(event_i))`.
- Ed25519 signing of the chain head, local key generation/persistence.
- SQLite storage (`aether/storage/sqlite.py`), WAL mode, behind a `Storage`
  interface (`aether/storage/base.py`).
- Interceptor / SDK: `Aether()`, `@aether.tool(...)`, `aether.run(...)`,
  sync and async support, exceptions recorded and re-raised.
- Configurable field redaction applied before hashing/storage.
- Cassette export/import (`.aether` files), schema-versioned, hardened
  against malformed JSON, oversized files, event-count caps, tampered
  chains, and forged signatures.
- CLI (Typer + Rich): `run`, `inspect`, `events`, `verify`, `tools`,
  `export`, `import`, `demo`, `doctor`.
- Scripted, fully offline demo agent (`examples/support_agent.py`).
- Test suite: 48 tests covering canonical JSON, interceptor (sync/async/
  exceptions/redaction), hash-chain tamper detection (byte tamper, delete,
  reorder, truncate, cross-run splice, forged signature), cassette
  round-trip and import hardening, concurrency (threads + asyncio, same-run
  and cross-run), real subprocess SIGKILL crash-safety, Ed25519 signing,
  side-effect honesty rules, and CLI (including an automated tamper-
  detection test through the CLI itself).
- Packaging: `pyproject.toml`, wheel build verified, clean-venv install
  verified.

### Fixed (found during Phase 1 testing, not pre-existing "known issues")
- **Event ordering bug**: `SQLiteStorage.get_events`/`get_last_event` were
  ordering by the caller-supplied `step` field. Under concurrent same-run
  writers, `step` values could land out of true append order, causing
  `verify_chain()` to report a false tamper on a chain that was never
  touched. Fixed by adding a DB-assigned autoincrement `seq` column as the
  authoritative order for the chain; `step` remains a human-readable label
  only.
- **`Recorder.__init__` did not coerce a `str` `data_dir` to `Path`**,
  raising `AttributeError` when called with a string path (surfaced by the
  crash-safety subprocess test, which passes `data_dir` as a `repr()`'d
  string into a generated worker script).
- `ruff` findings: unused imports, missing `check=` on two `subprocess.run`
  calls.

### Verified, not assumed
- Real end-to-end run: `python examples/support_agent.py` → real SQLite DB
  → `aether inspect` → `aether verify` (VALID).
- Real tamper test: manually flipped one field in the live database →
  `aether verify` → INVALID, named the exact tampered event, exit code 1.
- `pytest`: 48 passed, 91% coverage overall, 100% on `core/` and
  `recording/hashchain.py` and `recording/recorder.py`.
- `ruff check`: clean.
- `mypy`: `Success: no issues found in 20 source files`.
- Wheel build (`python -m build --wheel`): succeeded.

### Known limitations (see README §15 and docs/limitations.md)
- Local-file Ed25519 key, no KMS.
- Redaction is permanent/irreversible by design.
- `verify_chain()` alone does not detect truncation of the chain's tail if
  an attacker replaces both the trailing events and the signature with a
  self-consistent forgery; it requires an independently-known head hash
  (e.g. from a previously exported cassette) to catch that case. Documented
  as a limitation rather than silently claimed as covered.

### Not yet implemented
Everything in Phases 2–6 of the specification (shadow world, undo, MCP
proxy, provenance, taint tracking, replay, fork, diff, policy engine, risk
engine, regression testing, CI action, benchmarks, SDK polish, FastAPI
server, and the Aether Studio web UI).
