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
  chains, and corrupted signatures. (Correction, Phase 3 audit: this originally
  read "forged signatures", which overstated it. A validly self-signed forgery
  was accepted until trusted-key pinning was added; unsigned cassettes were also
  accepted until signatures became mandatory. See docs/self-audit-phase3.md.)
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

## Phase 2 — Shadow World + Undo + MCP Proxy

### Added
- State snapshot + structured diff utilities (`aether/state/snapshot.py`),
  content-hash based.
- `WorldState` (`aether/state/world.py`): a tiny JSON-file-backed store so
  demo/example state survives process boundaries — this is what makes
  `aether rollback` a genuine cross-process operation, not something that
  only works inside one Python process.
- Shadow database (`aether/sandbox/database.py`): clones a real SQLite file
  and executes writes only against the clone; computes rows
  created/modified/deleted and foreign-key dependent tables.
- Shadow filesystem (`aether/sandbox/filesystem.py`): confined to a temp
  root; hardened against path traversal, symlink escape, Windows
  drive-letter paths, null bytes, and Unicode normalization tricks.
- Shadow network (`aether/sandbox/network.py`): typed simulated effects
  only — the module contains no socket/HTTP code at all, structurally, not
  just by convention (tested by AST-inspecting the module's own imports).
- Compensation registry + rollback engine (`aether/undo/`): applies
  registered compensations walking backward from a target step; records
  every outcome (compensated, irreversible, or failed) as a new
  append-only event; never rewrites history.
- Built-in compensators for shadow-filesystem and `WorldState`-backed tools
  (`aether/undo/builtin.py`).
- CLI: `checkpoint`, `checkout`, `rollback`, `watch`.
- MCP proxy (`aether/integrations/mcp_proxy.py`): a transparent recording
  proxy in front of a real MCP server over stdio. Tested against the real,
  official, open-source `@modelcontextprotocol/server-filesystem` package
  — no mock server. Honest reversibility classification (this particular
  server has no delete tool, so new-file creation is correctly reported as
  not reversible; overwrites and moves are genuinely reversible via a
  real reconnect-and-undo compensator).
- `examples/undo_demo.py`: mutate CRM note + a file, checkpoint, corrupt
  both plus attempt a payment, roll back, and assert restored state
  content-hashes match the checkpoint exactly.
- `examples/mcp_demo.py`: same story through the real MCP server, on real
  disk.
- 50 new tests: shadow filesystem (17, including security tests), shadow
  database (9), shadow network (6), rollback engine (7), CLI undo commands
  (3), MCP proxy against the real server (8).

### Fixed (found during Phase 2 testing)
- **Sandbox filesystem**: an oversized path component leaked a raw
  `OSError` instead of a clean `AetherSimulationError`.
- **MCP compensators**: `asyncio.run()` inside a compensator raised
  `RuntimeError` when rollback was invoked from already-running async code
  (surfaced by the MCP proxy's own async test suite). Fixed with a
  `_run_async()` helper that runs the coroutine in a separate thread when
  a loop is already running.
- `ruff` findings across `tests/` (not previously linted): ~30 real issues
  (unused variables, missing `Self` return types on context managers,
  nested `with` statements, a `zip`→`itertools.pairwise` suggestion).
- `mypy` finding: `AetherMCPProxy.session` could be `None` when a
  classifier ran before `__aenter__` completed; added a proper guard.

### Verified, not assumed
- `examples/undo_demo.py` run for real: rollback restored both a JSON
  world-state record and a file's content to an exact, checkpoint-matching
  content hash.
- `aether rollback` run from a **separate process** than the one that made
  the original mutation, confirmed the fix landed on disk.
- `examples/mcp_demo.py` run for real against the real filesystem MCP
  server: wrote an overwrite, rolled it back via a fresh reconnect to the
  same real server, confirmed the real file on real disk was restored
  exactly.
- `pytest`: 98 passed (48 Phase 1 + 50 Phase 2), 90% coverage.
- `ruff check aether/ examples/ tests/`: clean.
- `mypy aether/`: `Success: no issues found in 33 source files`.
- Wheel rebuilt and reinstalled into a clean venv; `undo_demo.py` run
  through that clean install, not just the editable dev install.

### Known limitations (see docs/limitations.md, Phase 2 section)
- Shadow DB/FS snapshots are full in-memory dumps — fine at demo scale, not
  suitable for large tables/trees.
- The real MCP filesystem server has no delete tool, so new-file/directory
  creation through the proxy cannot be undone through it.
- Bypassing the Aether MCP proxy (calling the underlying server directly)
  is possible and invisible to Aether — documented, not hidden.
- `npx`-based server invocation was unreliable in this sandboxed
  environment; a local `npm install` + direct `node` invocation is the
  tested and supported path.
- Rollback takes no lock of its own against concurrent rollback calls on
  the same run.

### Not yet implemented
Provenance graph, taint tracking, agent driver, replay, fork, diff,
counterfactuals (Phase 3), policy/risk/intent engines and regression
testing (Phase 4), benchmarks/SDK/API (Phase 5), and Aether Studio
(Phase 6).

## Phase 3 — Provenance + Taint + Replay + Fork

### Added
- Provenance graph (`aether/provenance/graph.py`, NetworkX-backed): nodes
  for goal/agent/tool_call/data_artifact/decision; `why()`, `root_cause()`,
  `descendants()`, `explain_path()` queries as real graph traversals.
- Lineage builder (`aether/provenance/lineage.py`): constructs the graph
  automatically from a run's events, wired directly to taint findings so
  a tainted sink's causal chain (`email.read output -> payment.create`) is
  a real, queryable graph path, not a printed string.
- Taint tracker (`aether/security/taint.py`): session taint (coarse,
  run-wide) + value matching (narrower, with matched span/source
  event/confidence). Explicitly reported as evidence, never proof; two
  documented failure modes (paraphrase false negative, coincidental
  numeric-substring false positive) are directly tested, not just
  described.
- `Action.untrusted_output` — a real, persisted field (see Fixed below).
- Agent driver abstraction (`aether/runtime/driver.py`): `Scenario`,
  `ScenarioStep`, `ScriptedAgent` — the deterministic, no-LLM driver every
  Phase 3 demo and test runs on.
- Replay engine (`aether/replay/player.py`): `replay_strict` (reconstructs
  from storage, no tool re-execution) and `replay_live_sim` (re-executes a
  known Scenario against fresh tools and compares against the original;
  divergences are reported, not hidden).
- Fork (`aether/replay/fork.py`): forking at step N copies steps `1..N-1`
  verbatim from the parent into the fork's own hash chain, then runs a
  caller-supplied continuation from step N onward.
- Counterfactuals (`aether/replay/counterfactual.py`): `counterfactual_block`
  and `counterfactual_different_result`, built directly on `fork_run`.
- Run comparator (`aether/replay/comparator.py`): first-divergence
  detection plus tool-call/write/external-call/high-risk-action deltas.
- CLI: `replay`, `diff`, `why` (provenance + taint explanation for a
  specific step — works generically from stored data alone).
- `examples/fork_demo.py`: the full Phase-3 showcase — records the
  email-lifted-payment scenario, detects the taint with evidence, explains
  it via the provenance graph, forks at the payment step into a blocked
  alternative, diffs the two runs, and proves 10/10 replay determinism.
- 50 new tests: taint (8), provenance graph (8), lineage (7), driver (4),
  replay (4), fork/diff (8), counterfactual (2), CLI replay/diff/why (4),
  plus a persisted-flag regression test in the interceptor suite.

### Fixed (found during Phase 3 testing)
- **`untrusted_source=True` was never actually persisted.** Phase 1/2's
  interceptor stashed it into `event.action.__dict__` *after* the event
  was recorded — meaning it was never part of the hash-chained `Action`
  and silently vanished on any reload from storage. This was a real,
  live bug in code shipped in Phases 1-2, only surfaced when Phase 3's
  taint tracker needed to actually read it back. Fixed by adding a
  genuine `Action.untrusted_output` field, set before the event is built.
- **Taint value-matching missed the spec's own showcase amount at first**:
  `str(2840.00)` in Python is `"2840.0"`, which doesn't substring-match
  `"$2,840"` in email text. Fixed with a numeric-formatting-aware matcher.
  Caught immediately by the test built directly from the spec's scenario,
  not discovered later.
- **Reusing a fixed run_id for a fresh run silently duplicated history.**
  Found while wheel-testing `fork_demo.py` against a persistent data
  directory a second time: `RunContext` always started step numbering at 0
  regardless of whether the run_id already had recorded events, so a
  second invocation with the same run_id appended colliding, duplicate
  step numbers instead of failing. Fixed by checking, in
  `RunHandle.__enter__`, whether the run_id already has events and — if
  the caller isn't deliberately resuming with a `starting_step` that
  matches exactly (as `fork_run`'s verbatim-prefix-copy pattern does) —
  raising a new `AetherRunConflictError` instead of corrupting history.
  `fork_demo.py` and `undo_demo.py` were also updated to generate a fresh
  run_id per invocation (matching `support_agent.py`'s existing approach),
  so the demos are safe to re-run against a persistent `~/.aether`.

### Verified, not assumed
- `examples/fork_demo.py` run for real, output inspected line by line:
  taint confidence 0.9 with both `amount` and `to` arguments correctly
  traced to the email; provenance explanation printed a real causal chain;
  fork's ledger was provably untouched by the blocked alternative; diff
  correctly showed 1 high-risk action in the baseline vs 0 in the fork;
  10/10 strict replays were byte-identical.
- `aether replay`, `aether diff`, `aether why` all run against the fork
  demo's real recorded runs via the CLI, not just the Python API.
- `pytest`: 148 passed, 91% coverage.
- `ruff check aether/ examples/ tests/`: clean.
- `mypy aether/`: `Success: no issues found in 44 source files`.

### Known limitations (see docs/limitations.md, Phase 3 section)
- Taint value-matching is a narrow substring heuristic; paraphrase,
  synonyms, and cross-step splitting all defeat it.
- Fork/counterfactual continuations are supplied by the caller — no policy
  engine exists yet to decide them autonomously (Phase 4).
- `root_cause()`'s multi-root case is implemented but not exercised by any
  test yet.
- `aether fork` was deliberately not added as a CLI command (see
  docs/scope.md) since forking needs live tool callables a generic CLI
  invocation cannot reconstruct.

### Not yet implemented
Policy engine, intent contract, risk engine, blast radius, approvals,
attack lab, memory security, multi-agent provenance (Phase 4), regression
testing and CI action (Phase 4), benchmarks/SDK/API (Phase 5), and Aether
Studio (Phase 6).
