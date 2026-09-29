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

## Phase 2 (current)

- **Row-level table snapshots are full dumps.** `ShadowDatabase.snapshot_table`
  reads the entire table into memory as a list of row dicts. This is
  correct and fast for the small, demo-scale databases this project
  targets. It is NOT suitable for multi-GB production tables — that would
  need paginated/streaming diffing, which is not implemented.
- **The shadow filesystem's `list_all()` snapshot reads every file's full
  content into memory** for hashing/diffing. Same scale caveat as above.
- **Rollback fidelity is verified by content hash of the specific state
  the demo tracks** (a JSON world-state file, a shadow filesystem tree, or
  real files through the MCP proxy). It does not — and cannot — verify
  that *every possible* side effect of an action was undone; it only
  verifies the side effects the action itself declared and that a
  compensator was registered for.
- **The real MCP filesystem server we integrate with has no delete/rmdir
  tool.** Consequently, a brand-new file or directory created through the
  MCP proxy cannot be undone through it — Aether reports `Compensation:
  UNAVAILABLE` honestly rather than pretending otherwise. Only overwrites
  of pre-existing files, and file moves, are genuinely reversible through
  this particular server.
- **Bypassing the Aether MCP proxy entirely is possible and undetectable
  from inside Aether.** If an agent (or a person) connects directly to the
  same underlying MCP server instead of going through
  `AetherMCPProxy`, that call is invisible to Aether — no event, no hash
  chain entry, nothing. This is a stated assumption of the threat model,
  not a gap Aether currently closes (see `tests/test_mcp_proxy.py::test_bypass_the_proxy_calling_server_directly`,
  which demonstrates and documents this rather than hiding it).
- **`npx`-based server invocation was unreliable in this sandboxed
  environment** (intermittent `EPIPE` crashes and hangs). The tested and
  supported path is a local `npm install --prefix .mcp_servers
  @modelcontextprotocol/server-filesystem` followed by direct `node
  <script>.js` invocation. `npx` may well work fine in an unsandboxed
  environment; it just isn't what was verified here.
- **Rollback takes no lock of its own.** Two concurrent `rollback_to()`
  calls against the same run are not guarded against redundantly
  compensating the same action twice. Not tested, not claimed safe.
- **No provenance, taint tracking, replay, fork, diff, policy engine, risk
  engine, or benchmarks exist yet.** These remain Phases 3-5.

## Phase 3 (current)

- **Taint tracking is evidence with a confidence score, never proof.**
  Value-matching is a narrow heuristic: substring match after stripping
  currency symbols/commas/whitespace, plus a numeric-formatting
  normalization (`2840.00` -> `"2840"`). It does NOT understand paraphrase,
  synonyms, unit conversion, or splitting a value across multiple steps —
  all of these defeat it (a documented false negative, tested in
  `test_documented_false_negative_paraphrase_defeats_value_matching`). It
  CAN also false-positive on coincidental numeric substrings in unrelated
  untrusted text (tested in
  `test_documented_false_positive_coincidental_numeric_overlap`).
- **You cannot track values through an LLM's reasoning.** Session taint
  (coarse) and value matching (narrower but still approximate) are
  deliberate, documented approximations of "did untrusted content
  influence this action," not a claim of dataflow-accurate taint analysis
  through arbitrary agent reasoning.
- **Fork and counterfactual continuations are supplied by the caller.**
  Aether has no live LLM agent and no policy engine yet (Phase 4) to
  autonomously decide what a fork does differently. "Fork with a different
  policy" from the master spec becomes literal in Phase 4; today, forking
  means "run this alternate, explicitly-scripted continuation instead,"
  though the fork mechanism itself genuinely preserves the causal prefix
  (steps before the fork point are copied verbatim into the fork's own
  hash chain, not re-simulated).
- **The provenance graph's `root_cause()` supports multiple roots but this
  is not exercised by any test yet** — all current test scenarios have
  exactly one root (the goal node).
- **Live-sim replay determinism depends entirely on the caller resetting
  external state.** `replay_live_sim` re-executes a scenario against a
  fresh tool registry supplied by a `tools_factory` callable; if that
  factory does not actually reset whatever state its tools close over,
  replay will diverge from the original — and Aether reports this
  divergence honestly (tested in
  `test_replay_live_sim_reports_real_divergence_when_state_not_reset`)
  rather than silently succeeding.
- **No policy engine, risk engine, intent contract, approvals, attack lab,
  or regression testing exist yet.** These remain Phases 4-5.
