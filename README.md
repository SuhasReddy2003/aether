# Aether

**Git for Autonomous AI Actions.**
Record. Replay. Fork. Diff. Undo.

> **Status: Phase 3 of 6 — Provenance + Taint + Replay + Fork.** This
> README describes what exists today, honestly. The policy/risk engine,
> regression testing, benchmarks, and the web UI are future phases and are
> **not implemented yet**. See [`docs/PROGRESS.md`](docs/PROGRESS.md).

## 1. What is Aether?

Agents now change real state: databases, files, payments, email, other
agents. Aether sits between an agent and its tools. Phase 1 gives you the
foundation everything else builds on: a **tamper-evident, signed flight
log** of every tool call an agent makes.

## 2. Why it exists

When an agent does something wrong, you need to know *what happened* and
you need to be sure the record of what happened hasn't been quietly edited
after the fact. That's what Phase 1 does — nothing more, nothing less.

## 3. What's actually implemented (Phase 1)

- **Interceptor** — `@aether.tool(...)` wraps any Python function (sync or
  async). Every call is timed, executed, and recorded. Exceptions are
  recorded and re-raised, never swallowed.
- **Signed, hash-chained recording** — every event's hash covers the
  previous event's hash plus its own canonical-JSON content
  (`hash_i = SHA256(hash_{i-1} || canonical_json(event_i))`). The chain
  head is signed with a locally-generated Ed25519 key.
- **Tamper detection** — `aether verify <run_id>` recomputes the chain and
  reports `VALID` or `INVALID`, and on `INVALID` **names the exact first
  event that fails**. This is tested against real byte-level tampering of
  the live SQLite database, not simulated.
- **SQLite storage** (WAL mode) behind a `Storage` interface, so the backend
  is replaceable.
- **Cassette export/import** (`.aether` files) — portable, schema-versioned,
  and hardened against malformed JSON, oversized files, too many events,
  tampered chains, corrupted signatures, and unsigned cassettes. **Note (Phase 3
  audit):** a cassette re-signed with an attacker's *own* key still verifies
  unless you pin trusted signer keys (`aether import --trusted-key`); a
  signature proves integrity, not authenticity, without a trust anchor. See
  `docs/self-audit-phase3.md`.
- **Redaction** — configurable field redaction (passwords, API keys, card
  numbers) applied **before** hashing, so redacted values never enter the
  chain at all (and therefore cannot be recovered later, including by
  Aether itself).
- **CLI** (Typer + Rich): `run`, `inspect`, `events`, `verify`, `tools`,
  `export`, `import`, `demo`, `doctor`.
- **Scripted demo agent** (`examples/support_agent.py`) — a deterministic,
  fully offline simulation of a support agent that reads a customer record,
  reads an email, updates a CRM, and creates a payment. Nothing here
  contacts a real database, inbox, or payment processor.

## 3b. What's actually implemented (Phase 2)

- **Shadow database** (`aether/sandbox/database.py`) — clones a real
  SQLite file and runs writes only against the clone; reports rows
  created/modified/deleted and which foreign-key-dependent tables were
  touched. The real database is never written to.
- **Shadow filesystem** (`aether/sandbox/filesystem.py`) — confined to a
  temp root, hardened against path traversal, symlink escape, Windows
  drive-letter paths, null bytes, and Unicode tricks (tested).
- **Shadow network** (`aether/sandbox/network.py`) — never makes a real
  request; represents what a network-touching tool *would* do as a typed
  simulated effect. Contains no socket/HTTP import at all, structurally.
- **Compensation + rollback engine** (`aether/undo/`) — walks a run
  backward from a target step and applies registered compensations.
  Reversibility is never faked: if an action wasn't declared reversible,
  or no compensator is registered, the engine reports `Compensation:
  UNAVAILABLE` honestly. Every outcome (compensated, irreversible, or
  failed) is itself a new, append-only event — history is never rewritten.
- **`aether checkpoint` / `checkout` / `rollback` / `watch`** — `rollback`
  works as a genuine cross-process operation: mutate state in one process,
  roll it back from a separate CLI invocation later, and the fix is real
  on disk.
- **MCP proxy** (`aether/integrations/mcp_proxy.py`) — a transparent
  recording proxy in front of a **real** MCP server over stdio. Tested
  against the actual, official, open-source
  `@modelcontextprotocol/server-filesystem` package — not a mock. Honestly
  classifies reversibility: this particular server has no delete tool, so
  creating a new file through the proxy is correctly reported as not
  reversible, while overwriting an existing file or moving one is
  genuinely reversible (the compensator reconnects to the same real
  server and undoes the change for real).
- **`examples/undo_demo.py`** — mutates a CRM-style record and a file,
  takes a checkpoint, corrupts both plus attempts a payment, rolls back,
  and asserts the restored state's content hash matches the checkpoint
  exactly.
- **`examples/mcp_demo.py`** — the same story through the real MCP server,
  on real disk. Requires Node.js; see §4b.

## 3c. What's actually implemented (Phase 3)

- **Provenance graph** (`aether/provenance/graph.py`, NetworkX-backed) —
  nodes for goals, agents, tool calls, data artifacts, and decisions;
  `why()`, `root_cause()`, `descendants()`, and `explain_path()` are real
  graph traversals, not string templates.
- **Taint tracking** (`aether/security/taint.py`) — session taint (coarse:
  once untrusted content enters a run, everything downstream is flagged)
  plus value matching (narrower: did *this specific argument* come from
  that untrusted content). Reported as evidence with a confidence score,
  never as proof — two failure modes (a paraphrase that defeats matching
  entirely, and a coincidental numeric overlap that produces a false
  positive) are directly tested, not just written about.
- **Agent driver** (`aether/runtime/driver.py`) — `ScriptedAgent`: a
  deterministic, no-LLM driver that executes a fixed `Scenario` (an
  ordered list of tool calls) against a tool registry. Every Phase 3 demo
  and test runs on this.
- **Replay** (`aether/replay/player.py`) — `replay_strict` reconstructs a
  run's history directly from storage (no tool code is re-executed, since
  a portable cassette carries none); `replay_live_sim` re-executes a known
  scenario against fresh tools and reports any divergence from the
  original honestly, rather than assuming determinism.
- **Fork** (`aether/replay/fork.py`) — forking at step N copies steps
  `1..N-1` **verbatim** from the parent into the fork's own hash chain
  (new event IDs, new chain — it's a different run), then executes a
  caller-supplied continuation from step N onward. Aether has no live
  agent to autonomously decide that continuation; that's a stated scope
  boundary (see `docs/scope.md`), not a hidden gap.
- **Counterfactuals** (`aether/replay/counterfactual.py`) and **diff**
  (`aether/replay/comparator.py`) — "what if this had been blocked", built
  directly on fork; diff reports the first point of divergence plus
  tool-call/write/external-call/high-risk-action deltas.
- **`examples/fork_demo.py`** — the full showcase: records the spec's own
  email-lifted-payment scenario, detects the taint with real evidence,
  explains it via the provenance graph, forks at the payment step into a
  blocked alternative, diffs the two runs, and proves 10/10 replay
  determinism. Honestly labels its "WITH AETHER" branch as a human-supplied
  counterfactual — the actual autonomous BLOCK decision needs Phase 4's
  policy engine, which doesn't exist yet.

## 4. Quick start

```bash
git clone <this-repo>
cd aether
pip install -e .

python examples/support_agent.py
# Run complete: run_xxxxxxxxxxxx
# aether inspect run_xxxxxxxxxxxx
# aether verify run_xxxxxxxxxxxx

aether inspect run_xxxxxxxxxxxx
aether verify run_xxxxxxxxxxxx      # VALID

aether doctor                        # environment + database health check
```

Or the one-command version:

```bash
aether demo
```

### Proving tamper detection to yourself

```bash
RUN_ID=$(python -c "
from aether.storage.sqlite import SQLiteStorage
from aether.recording.recorder import DEFAULT_DATA_DIR
print(SQLiteStorage(DEFAULT_DATA_DIR/'aether.db').get_run_ids()[-1])")

aether verify $RUN_ID    # VALID

# Now flip one byte in the live database (this is what tests/test_hashchain_tamper.py
# and tests/test_cli.py do automatically, in isolated tmp dirs):
python -c "
import sqlite3, json
from aether.recording.recorder import DEFAULT_DATA_DIR
db = str(DEFAULT_DATA_DIR / 'aether.db')
conn = sqlite3.connect(db)
row = conn.execute('SELECT event_id, event_json FROM events WHERE step=3').fetchone()
data = json.loads(row[1])
data['action']['arguments']['note'] = 'tampered'
conn.execute('UPDATE events SET event_json=? WHERE event_id=?', (json.dumps(data), row[0]))
conn.commit()
"

aether verify $RUN_ID    # INVALID, names the exact tampered event, exit code 1
```

## 4b. Quick start (Phase 2)

```bash
python examples/undo_demo.py
# ... prints the checkpoint hash, the corruption, the rollback report,
# and VERIFIED lines once restored state hashes match the checkpoint.

aether inspect <run_id>           # see original actions AND rollback events (undo_demo prints the exact run_id)
aether verify <run_id>            # still VALID — rollback is append-only

# Cross-process rollback: mutate in one process, undo from another
aether rollback <run_id> --to <step>
```

### MCP proxy demo (requires Node.js)

```bash
npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem
python examples/mcp_demo.py
```
This connects to the real filesystem MCP server, records a session through
it, then rolls back the reversible part of that session by reconnecting to
the same real server — the restored file is verified on real disk.

## 5. Architecture (Phase 1 slice)

```text
Agent code
   │  @aether.tool(...)
   ▼
Interceptor (runtime/interceptor.py)
   │  captures args, result/exception, duration
   ▼
Recorder (recording/recorder.py)
   │  redacts sensitive fields, builds an Event
   ▼
Hash chain (recording/hashchain.py)     Signing (recording/signing.py)
   │  hash_i = SHA256(hash_{i-1} || canonical_json(event_i))
   ▼
SQLite storage (storage/sqlite.py, behind storage/base.py Storage interface)
   │
   ├──▶ CLI (cli/main.py): inspect / events / verify / tools / export / import / demo / doctor
   └──▶ Cassette (recording/cassette.py): portable, schema-versioned .aether files
```

### Architecture (Phase 2 additions)

```text
Recorder (unchanged)
   │
   ├──▶ ShadowDatabase / ShadowFilesystem / ShadowNetwork (aether/sandbox/)
   │       real-clone / confined-root / no-real-calls simulations
   │
   ├──▶ CompensationRegistry + RollbackEngine (aether/undo/)
   │       walks a run backward, applies compensators, records outcomes
   │       as new append-only events
   │
   └──▶ AetherMCPProxy (aether/integrations/mcp_proxy.py)
           wraps a real MCP server over stdio; records + classifies +
           (from Phase 4) will gate every tool call
```

## 6. Flight recorder & tamper evidence

See section 5. The key property, tested in `tests/test_hashchain_tamper.py`:
mutating any single field of any single stored event — or deleting an
event, reordering events, or splicing events from a different run —
is detected by `verify_chain()`, which reports the exact first event
where the chain breaks.

## 7. Undo & shadow world

See §3b above. Rollback fidelity is proven in
`tests/test_rollback.py` and `tests/test_mcp_proxy.py`: after rolling back,
the restored state's content hash is asserted equal to a checkpoint taken
before the mutation — not just "close enough," byte-for-byte.

## 4c. Quick start (Phase 3)

```bash
python examples/fork_demo.py
```
Prints, in order: the recorded baseline sequence, the taint finding (with
matched spans and confidence), the provenance explanation, a WITHOUT/WITH
comparison via a counterfactual fork, a diff between the two runs, and a
10/10 replay-determinism check. It also prints the exact follow-up
commands to run, e.g.:

```bash
aether replay <run_id>
aether why <run_id> --step 3
aether diff <run_id> <run_id>.counterfactual_blocked_3
```

## 8. Provenance & taint tracking

See §3c above.

## 9. Replay, fork, diff

See §3c above.

## 10–13. Not yet implemented

The policy/risk/intent engines, regression testing & CI action, the
remaining integrations (LangGraph/OpenAI-SDK/OpenTelemetry), and
benchmarks are **Phases 4–5** and do not exist in this codebase yet. The
web UI (Aether Studio) is **Phase 6**.

## 14. Architecture decisions and tradeoffs

- **SQLite over Postgres**: this phase has one writer per run in practice;
  WAL mode plus explicit `BEGIN IMMEDIATE`/`COMMIT` transactions is enough
  to keep concurrent writers from corrupting the chain (tested in
  `tests/test_concurrency.py`), and it keeps the whole project runnable
  with zero infrastructure.
- **Event order is `seq` (DB-assigned autoincrement), not caller-supplied
  `step`.** This was a real bug found while testing: if two writers on the
  same run interleave, their `step` values can land out of true append
  order, and sorting by `step` would make an untampered chain look broken.
  `seq` is the source of truth for hash-chain order; `step` is just a
  human-readable label.
- **Redaction happens before hashing.** This means a redacted field is
  redacted permanently — there is no "verify now, unredact for audit
  later" path. That's a real limitation, documented here rather than
  hidden.
- **Local Ed25519 key, not a KMS.** The signature proves "whoever holds
  this local key attested to this chain head," not more. Protecting the
  key file is the operator's responsibility (see `docs/limitations.md`).
- **WorldState is a JSON file, not a database.** Chosen specifically so
  that `aether rollback` invoked later, from a completely separate CLI
  process, can read and mutate the exact same state a demo script wrote
  earlier — proving rollback is a real capability and not something that
  only works inside one Python process's memory.
- **The MCP proxy is tested against a real server, on purpose.** A mock
  MCP server would have let the proxy's classification logic go untested
  against real protocol quirks (e.g. discovering, empirically, that the
  official filesystem server has no delete tool — which directly shaped
  the honest-reversibility design).
- **Fork copies the causal prefix verbatim rather than re-simulating it.**
  This was a deliberate design choice once it became clear that "fork at
  the risky decision" should mean "keep everything that led up to it,
  including having read the tainted email" — re-running the prefix through
  live tools again would risk it producing different results the second
  time (a new random ID, a different timestamp) and would misattribute a
  divergence to the fork mechanism itself rather than the actual
  continuation being tested.
- **Fork/counterfactual continuations are explicit, not autonomous.**
  Without a live LLM agent or a policy engine (both later phases), Aether
  cannot decide for itself what an agent would have done differently. Fork
  makes that boundary a parameter (`continuation: list[ScenarioStep]`)
  instead of a fake decision `algorithm`.

## 15. Limitations (honest, current)

```text
The signing key is a local file; Aether does not provide key management.
Redaction is irreversible: a redacted field cannot be recovered from the
  chain, by anyone, including a legitimate auditor.
Tampering with the tail of the chain (deleting the most recent events and
  nothing after them, or replacing both an event and the signature at once
  with a self-consistent forgery) is only caught if the true head hash is
  known from an independent source (e.g. a previously exported cassette or
  an externally-stored signature) — verify_chain() alone verifies internal
  consistency, not "nothing was ever removed from the end."
Shadow DB/FS snapshots are full in-memory dumps: fine at demo scale, not
  suitable for large tables or trees.
The real MCP filesystem server has no delete/rmdir tool, so new-file or
  new-directory creation through the proxy cannot be undone through it.
Bypassing the Aether MCP proxy (calling the underlying MCP server
  directly) is possible and invisible to Aether — a stated threat-model
  assumption, not a gap this phase closes.
Rollback takes no lock of its own against two concurrent rollback calls
  on the same run.
Taint tracking is a narrow, conservative heuristic: paraphrase and
  cross-step splitting defeat it (false negative); coincidental numeric
  overlap in unrelated content can trigger it (false positive). Evidence
  with a confidence score, never proof.
Fork and counterfactual continuations are supplied by the caller — no
  policy engine exists yet to decide them autonomously.
No policy engine, risk engine, intent contract, regression testing, or
  benchmarks exist yet — see docs/scope.md and docs/PROGRESS.md for what
  is planned vs. built.
```

Full detail (including Phase-1/2-specific limitations still in effect):
see `docs/limitations.md`.

## 16. Future work

Phases 4–6 as specified: security (policy/risk/intent) + regression
testing + CI; benchmarks + SDK + API; Aether Studio (web UI) + deployment.

## Engineering rules followed in this phase

- No `pass`/`TODO`/`NotImplementedError` for anything claimed as working.
- Every number in this README (test count, coverage %) was generated by the
  commands in `docs/PROGRESS.md`, not hand-typed.
- Structured errors (`AetherError` and subclasses) — no bare exceptions,
  no swallowed exceptions.
- `ruff` and `mypy` both run clean (see CHANGELOG for what was fixed).

## Running the tests yourself

```bash
pip install -e ".[dev,mcp]"
pytest tests/ --cov=aether --cov-report=term-missing
ruff check aether/ examples/ tests/
mypy aether/
```

The MCP proxy tests (`tests/test_mcp_proxy.py`) skip cleanly, with a clear
message, if Node.js or the filesystem MCP server package isn't available —
they never fake a pass.

