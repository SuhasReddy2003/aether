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

## Phase 2 onward: NOT STARTED

Shadow world (DB/FS/network sandboxes), undo/compensation/rollback, and the
MCP proxy have not been built yet. Do not assume any Phase 2+ capability
exists.

## Cuts so far

None. See `docs/scope.md` for the running log (empty as of Phase 1).
