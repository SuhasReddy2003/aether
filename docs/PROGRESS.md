# PROGRESS

Updated at the end of each phase. Every number here comes from a command
listed next to it — none are hand-typed estimates.

## Phase 1 — Flight Recorder: DONE

Commands used to generate the numbers below:
```bash
pytest tests/ --cov=aether --cov-report=term-missing -q
ruff check aether/ examples/
mypy aether/
```

- Tests: **48 passed**, 0 failed.
- Coverage: **91%** overall. 100% on `aether/core/*`,
  `aether/recording/hashchain.py`, `aether/recording/recorder.py`.
  Lower coverage in `aether/cli/main.py` (82%, the `run`/`tools` error paths
  and a couple of `doctor` branches aren't hit by tests yet) and
  `aether/recording/redaction.py` (82%, the email/card regex substitution
  branches aren't exercised since the default demo doesn't emit emails or
  card numbers as tool arguments).
- `ruff check`: clean (after fixing 2 real issues: 2 `subprocess.run` calls
  missing `check=`, and unused imports).
- `mypy aether/`: `Success: no issues found in 20 source files`.
- Wheel build: succeeded (`python -m build --wheel`).
- Manual verification: ran `examples/support_agent.py` for real, produced a
  real run, `aether inspect`/`aether verify` both worked against it, and a
  manual byte-level tamper of the live SQLite DB was correctly caught by
  `aether verify` (INVALID, named the exact event, exit code 1).

### What was NOT verified in Phase 1
- Not run on macOS or Windows (this environment is Linux only) — CI matrix
  from the spec (Linux + macOS, Python 3.11/3.12) is written for Phase 6
  but not executed here.
- No GitHub Actions run yet (no push to a real GitHub repo happened in this
  environment); the `.github/workflows/ci.yml` file, once added, is
  untested until it actually runs in GitHub's infrastructure.
- Fresh-clean-venv wheel install: see below — this WAS run in this
  environment.

## Phase 2 — Shadow World + Undo + MCP Proxy: DONE

Commands used to generate the numbers below:
```bash
pytest tests/ --cov=aether --cov-report=term-missing -q
ruff check aether/ examples/ tests/
mypy aether/
python -m build --wheel
```

- Tests: **98 passed**, 0 failed (48 from Phase 1 + 50 new).
- Coverage: **90%** overall. 100% on `aether/core/*`, `aether/sandbox/network.py`,
  `aether/state/world.py`, plus everything that was 100% in Phase 1.
- `ruff check aether/ examples/ tests/`: clean (this phase, lint was run
  across `tests/` too for the first time, surfacing and fixing ~30 real
  findings: unused variables, a leaked `OSError`, missing `Self` return
  types, nested `with` statements, a `zip`→`itertools.pairwise` suggestion).
- `mypy aether/`: `Success: no issues found in 33 source files` (fixed one
  real finding: `proxy.session` could be `None` when a classifier runs
  before `__aenter__` completes).
- Wheel build + clean-venv install: succeeded; `undo_demo.py` was run
  through the clean-venv install (not just the editable dev install) and
  passed its own internal assertions.

### Real bugs found and fixed during Phase 2 testing
1. **Sandbox filesystem**: an oversized path component raised a raw
   `OSError` instead of being caught and converted to a clean
   `AetherSimulationError`, meaning a malformed path could crash the
   caller instead of being cleanly rejected. Found by
   `test_oversized_path_component_does_not_crash`.
2. **MCP compensator / asyncio**: `mcp_fs_restore_write` and
   `mcp_fs_restore_move` called `asyncio.run()` unconditionally, which
   raises `RuntimeError: asyncio.run() cannot be called from a running
   event loop` if rollback is invoked from async code (exactly what
   happens in `tests/test_mcp_proxy.py`'s async tests). Fixed with a
   `_run_async()` helper that detects a running loop and, if present,
   runs the coroutine in a separate thread.

### What was NOT verified in Phase 2
- The MCP proxy was tested against exactly one real MCP server (the
  official filesystem server). No other real MCP server (a database
  server, a ticketing system, etc.) has been tried — the proxy's tool
  classification is written to be generic, but that genericity is only
  demonstrated for filesystem-shaped tools so far.
- `npx`-based invocation of the MCP server was found to be unreliable in
  this sandboxed environment (hung or crashed with `EPIPE` on repeated
  attempts); a local `npm install --prefix .mcp_servers` + direct `node
  <script>.js` invocation was used instead, and is what's actually tested.
  This is documented as the supported installation path, not papered over.
- Concurrent rollback (two rollback operations racing on the same run) is
  not tested. The rollback engine takes no lock of its own; only the
  underlying `Recorder`'s per-record lock is held, which prevents chain
  corruption but does not prevent two rollbacks from redundantly
  compensating the same action if run concurrently. Documented as a
  limitation, not silently assumed safe.
- Not run on macOS or Windows, and no GitHub Actions run yet (same caveats
  as Phase 1).

## Phase 3 — Provenance + Taint + Replay + Fork: DONE

Commands used to generate the numbers below:
```bash
pytest tests/ --cov=aether --cov-report=term-missing -q
ruff check aether/ examples/ tests/
mypy aether/
```

- Tests: **148 passed**, 0 failed (98 from Phases 1-2 + 50 new).
- Coverage: **91%** overall.
- `ruff check`: clean.
- `mypy aether/`: `Success: no issues found in 44 source files`.
- `examples/fork_demo.py` run for real: recorded a baseline run with an
  email-lifted payment amount, detected the taint (value-match, confidence
  0.9, both the amount AND recipient traced to the email), explained it via
  the provenance graph, forked at the payment step into a "blocked"
  alternative, diffed the two runs (first divergence at step 3, high-risk
  actions 1 vs 0), and confirmed 10/10 strict replays were byte-identical.

### Real bugs found and fixed during Phase 3 testing
1. **`untrusted_source` flag was never actually persisted** (Phase 1/2
   latent bug, found while building taint tracking): the interceptor
   stashed it into `event.action.__dict__` *after* recording, so it was
   never part of the hash-chained `Action` and vanished on reload from
   storage. Fixed by adding a real `Action.untrusted_output` field, set
   *before* the event is built. Added a regression test
   (`test_untrusted_output_flag_is_actually_persisted`) that reloads from a
   fresh `SQLiteStorage` instance to prove it's genuinely persisted, not
   just an in-memory artifact of one call.
2. **Taint value-matching missed the showcase's own amount** at first: a
   Python float like `2840.00` stringifies as `"2840.0"`, which does not
   substring-match the text `"$2,840"` in an email body. Fixed with a
   `_value_to_match_string()` helper that renders whole-number floats
   without a trailing `.0`. Caught immediately by
   `test_value_matching_detects_amount_lifted_from_email`, which is
   deliberately built from the spec's own showcase scenario.

### Honest design choices worth calling out
- **Fork copies the causal prefix, not just the continuation.** Forking at
  step N copies steps `1..N-1` verbatim from the parent (new event IDs and
  hash chain, since it's a different chain) and then runs the caller-
  supplied continuation starting at step N. This means "fork at the risky
  decision" genuinely preserves everything that led up to it — including
  having read the tainted email — while only replacing what comes after.
- **Fork/counterfactual continuations are supplied by the caller, not
  autonomously decided.** Aether has no live policy/risk engine yet (that
  is Phase 4) and no live LLM agent in this codebase at all. A fork's
  "what happens next" is an explicit `list[ScenarioStep]` the caller
  provides. This is a real, stated scope boundary — documented in
  `fork.py`'s own docstring — not a hidden limitation.
- **Taint findings are evidence with a confidence score, never proof.**
  Two honestly-documented failure modes are tested directly:
  a paraphrased amount defeats value-matching entirely (false negative),
  and a coincidental numeric substring in unrelated untrusted text
  produces a false positive. Both are asserted in `tests/test_taint.py`,
  not just described in prose.

### What was NOT verified in Phase 3
- The taint tracker's value-matching is a narrow, deliberately conservative
  heuristic (substring match after light normalization). It has not been
  tested against a broad corpus of real phishing/injection text — only the
  specific showcase scenario and a handful of hand-constructed edge cases.
- The provenance graph's `root_cause()` can return multiple roots when an
  action derives from more than one upstream artifact; this multi-root
  case is not exercised by any test yet (all current tests have a single
  root: the goal node).
- No policy engine exists to make BLOCK/APPROVE decisions autonomously —
  `fork_demo.py`'s "WITH AETHER" branch is a human-supplied counterfactual,
  clearly labeled as such in the demo's own output and docstring.
- Not run on macOS or Windows; no GitHub Actions run yet (same caveats as
  Phases 1-2).

## Phase 4 onward: NOT STARTED

Policy engine, intent contract, risk engine, blast radius, approvals,
attack lab, memory security, multi-agent provenance, regression testing,
and CI integration have not been built yet.
