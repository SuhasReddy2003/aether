# Self-audit: Phase 3

A hostile-reviewer pass over Phases 1-3, as required by the spec. Every row
below was **actually attempted** with code, and each result is pinned by
`tests/test_self_audit_phase3.py`. Fixes were mutation-tested: each fix was
temporarily broken and the corresponding test confirmed to fail.

This document supersedes an earlier draft of the same audit. That draft
found one real bug (attack 8) but marked several other items clean based on
existing tests rather than new attacks. Actually attacking them found real
problems; see "Corrections to the earlier draft" at the bottom.

A note on method: my first pass at attack 2 was itself flawed. Several
"detected" verdicts were caused by the *recipient* argument still matching,
not the amount being evaded, and my "homoglyph" case used plain ASCII. I
redid the attacks with arguments isolated and a real Cyrillic character. The
numbers below are from the redo.

## Summary

| # | Attack | Result | Status |
|---|--------|--------|--------|
| 1a | Rewrite history, recompute chain, re-sign with attacker's own key | **Accepted as valid** | Mitigated by key pinning; **inherent without a trust anchor** |
| 1b | Strip signature, recompute chain | **Accepted as valid** | **Fixed** |
| 2 | Evade taint detection | 5 evasion classes fixed; 8 still work; 2 config failures | Partly **open** |
| 3 | Escape filesystem sandbox | Hardlink read/write/delete worked; symlink variants blocked | Hardlink **fixed** |
| 4 | Make replay diverge silently | Replaying a tampered run succeeded | **Fixed** |
| 5 | Rollback reports success while state differs | Reported `compensated` with drifted state | **Fixed** (verification is opt-in) |
| 6 | Bypass MCP proxy | Direct calls to the server are invisible to Aether | **Open by design** |
| 7 | UI number differs from backend | No UI exists yet | Deferred to Phase 6 audit |
| 8 | `diff_runs` says "no divergence" for differing runs | Result-only differences were ignored | **Fixed** (earlier draft) |

## 1. Forging a cassette

**1a. Self-signed forgery: accepted.** The public key is embedded in the
cassette. Changing `amount` 84 -> 999999, recomputing every hash, and signing
the new head with a freshly generated key verified as valid. This is not fully
fixable inside Aether: a signature proves only "the holder of key K signed
this", and whether K deserves trust needs an anchor Aether cannot invent.

What changed:
- `load_cassette` / `import_cassette` take `trusted_public_keys`; a validly
  signed cassette from any other key is rejected (tested).
- `aether import --trusted-key <b64>` exposes this on the CLI.
- `aether verify` prints the signer fingerprint and states whether it is
  *this machine's key*, and that integrity is not authenticity when it isn't.
- **Not fixed:** with no pinning, any valid self-signature is accepted.
  `test_known_open_self_signed_forgery_passes_without_a_trust_anchor` keeps
  this visible.

**1b. Unsigned cassette: accepted.** Deleting the signature and recomputing
the chain verified. **Fixed:** signatures are required by default (opt out
with `require_signature=False` / `--allow-unsigned`), and `aether verify`
reports an unsigned run as `INVALID: UNSIGNED` with exit 1.

Also checked and holding: flipping `untrusted_output` on a stored event is
caught, because the field is part of the hashed payload.

## 2. Getting a tainted value into `payment.create` undetected

Email says `$2,840` to `acct_991`; the other argument is kept clean so only
the one under test can match.

| Evasion | Result |
|---|---|
| verbatim; case change | caught |
| spaces inserted (`acct _991`) | evaded -> **fixed** (whitespace stripped) |
| Cyrillic homoglyph, in email or in argument | evaded -> **fixed** (small confusables table) |
| zero-width character in email | evaded -> **fixed** (stripped) |
| fullwidth digits | **fixed** (NFKC) |
| rounded (2840 -> 2800) | **still evades** |
| split across steps (1420 + 1420) | **still evades** |
| cents form (284000) | **still evades** |
| hex string (`0xb18`) | **still evades** |
| European format `2.840,00` | **still evades** |
| spelled-out number | **still evades** |
| base64-encoded recipient | **still evades** |
| reversed string | **still evades** |
| tool not declared `untrusted_source=True` | no taint at all |
| sink tool not in the sink list (`payments.send_money`) | no finding at all |

Value matching is string similarity. It cannot see arithmetic, re-encoding,
or paraphrase; when it misses, only the session-taint flag remains, at
confidence 0.3. The last two rows are configuration failures and arguably
worse than the encodings: taint is only as good as the declaration of what is
untrusted and what is a sink. Each open row is asserted as open in a
`test_known_open_*` test, so improving detection will fail a test and force
the docs to be updated.

Caveats on the fixes: the confusables table is ~30 characters, not the full
Unicode set, and whitespace stripping adds some false positives.

## 3. Sandbox escape

Blocked and unchanged: write through a dangling symlink to an outside path,
write through a symlink to an outside directory, rename onto an outside
symlink, delete through an outside symlink. The outside directory was
verified untouched.

**Succeeded: hardlink.** A hardlink inside the root to an outside file looks
like an ordinary in-root file to every path check. **Fixed:** regular files
with `st_nlink > 1` are refused. Residual: creating the hardlink needs host
access (outside the threat model); the fix prevents *use through the
sandbox*, not the link's existence.

## 4. Silent replay divergence

`replay_strict` and `diff_runs` read straight from storage, so after tampering
with the database, replay "succeeded" and faithfully reproduced the
*tampered* history: a forgery presented as a reproduction. **Fixed:** both
verify the chain and signature first (`load_verified_events`) and raise
`AetherIntegrityError`; `aether replay`, `diff`, and `why` exit 1. Skipping
verification requires an explicit `verify=False`. A run with a stripped
signature is refused too.

## 5. Rollback claims success while state differs

After out-of-band tampering, rollback reported `compensated` with zero
failures while the state hash did not match the checkpoint. Compensators
reporting success was being treated as evidence of restoration. **Fixed
(opt-in):** `rollback_to(expected_state_hash=..., state_probe=...)` sets
`report.state_verified` True/False. `aether rollback` does this automatically
when a checkpoint exists at/before the target step, and exits 1 on mismatch.
**Open:** with no checkpoint or probe, `state_verified` is `None` and the CLI
says `State NOT verified`; it never implies success. Rollback does not
*repair* drift; it refuses to hide it.

## 6. Bypassing the MCP proxy

Unchanged from Phase 2 (`test_bypass_the_proxy_calling_server_directly`): an
agent talking to the underlying server directly leaves no Aether record. All
of Phase 3's provenance and taint analysis is blind to anything that never
became an event. Aether can guarantee only what passes through it.

## 7. UI number mismatch

No UI exists yet. Deferred to the Phase 6 audit.

## 8. `diff_runs` blind spot (found in the earlier draft)

`diff_runs` compared only `tool` and `arguments`, so two runs that called the
same tool with the same arguments but got different results were reported as
not diverging. **Fixed:** results are compared too
(`test_diff_detects_result_only_divergence`).

## Corrections to the earlier draft

The earlier draft made these statements, which the attacks above show to be
wrong or unsupported:

- *"Forging a signature is all caught."* Only corrupted signatures were. A
  valid signature by an attacker's own key, or no signature at all, passed.
- *"Did not find a way to make [replay] diverge silently."* Replaying a
  tampered database succeeded silently. The draft tested only live-sim
  divergence, not tampered storage.
- *"[Rollback] Already covered... no new path exists."* Rollback reported
  success over drifted state; nothing verified the outcome.
- *Splitting and encoding* were described as consequences of the design but
  "not implemented as a test". They are now tested (as documented-open).
- The same claims about signature rejection appear in the Phase 1 README and
  CHANGELOG and are corrected there too.

## What this audit did NOT cover

- Concurrency attacks (racing writers producing a divergent chain) beyond
  Phase 1's tests.
- Side channels, timing, and resource exhaustion beyond the cassette size and
  event-count caps.
- Attacks on the signing key itself (theft, weak file permissions).
- Anything requiring a real LLM: injection *content* was never generated, only
  resulting tool arguments were tested.
- Any measured detection rate. Taint results reflect hand-built variants, not
  a corpus of real injection text.
