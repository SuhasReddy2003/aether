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
