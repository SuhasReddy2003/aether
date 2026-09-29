# Scope log

Running record of anything cut from the full specification, and why.
Per the cut rule: cut from P2 first, then P1, never weaken P0 gates, tests,
or honesty rules.

## Phase 1

No cuts. Everything specified for Phase 1 (interceptor, recorder, SQLite
storage behind an interface, hash chain, Ed25519 signing, cassette
export/import with schema versioning, redaction, CLI with all eight
commands, scripted demo agent, tests including tamper/crash/concurrency)
was implemented and verified.

One scope note: the spec's Phase 1 `aether doctor` mentions checking
"optional deps" generically; this build checks specifically for
`networkx` and `hypothesis` since those are the two optional-in-Phase-1
dependencies that later phases (provenance graph; property testing) will
require. This will be extended as more optional deps are introduced.

## Phase 2

No cuts. Everything specified for Phase 2 (state snapshots, shadow
database, shadow filesystem, shadow network, compensation registry,
rollback engine with checkpoints, MCP proxy on a real open-source server,
undo_demo.py, mcp_demo.py) was implemented and verified.

One scope note: the spec's MCP proxy section says to gate calls "from
Phase 4" — that's followed as written; Phase 2's proxy records and
classifies but does not yet block anything (no policy engine exists until
Phase 4).

One environment note: `npx`-based invocation of the real MCP server was
unreliable in this sandboxed environment (intermittent hangs and `EPIPE`
crashes). Testing instead uses a locally-installed copy
(`npm install --prefix .mcp_servers @modelcontextprotocol/server-filesystem`)
invoked directly via `node <script>.js`. This is documented as the
supported path in the README, not silently swapped in without explanation.

## Phase 3

No cuts against the P0 Phase 3 deliverables: provenance graph (NetworkX),
taint tracking (session + value matching), sensitive sinks, agent driver
(ScriptedAgent — an OllamaAgent is optional/stretch per the spec and was
not built, since the spec marks it "always optional"), replay (strict +
live-sim), fork, diff, counterfactuals, and `fork_demo.py` were all
implemented and verified.

Two honest scope notes:
- The master spec's showcase demo (steps 6-7: BLOCK decision, APPROVE
  routing with blast radius) genuinely requires the policy/risk engine,
  which the spec's own phase breakdown assigns to Phase 4. `fork_demo.py`
  demonstrates everything Phase 3 owns — recording, taint detection with
  evidence and confidence, provenance explanation, fork/diff, and replay
  determinism — and clearly labels the "WITH AETHER" branch as a
  human-supplied counterfactual rather than an autonomous policy decision.
  This is not a cut; it's the phases being followed in the order the spec
  itself defines them.
- `aether fork` was deliberately NOT added as a generic CLI command.
  Forking requires live, callable tool functions (the actual Python
  functions decorated with `@aether.tool`), which only exist inside the
  process that defined them — a generic CLI invocation has no way to
  reconstruct arbitrary example-specific tool implementations. `aether
  replay`, `aether diff`, and the new `aether why` all work generically
  from the CLI because they only need data already in storage. Fork and
  counterfactual remain Python-API operations, demonstrated in
  `examples/fork_demo.py`. Documented here rather than shipping a CLI
  command that would have to fake or guess at tool behavior.
