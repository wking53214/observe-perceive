# GOVERNANCE_BYPASS_REPORT

Every route to governed state that was attacked, what happened before repair,
what was changed, what happens now, and the second-order attack tried on the
repair. "Measured" means the attack was run as code against the real
implementation; every post-repair result is also pinned by a regression test.
Classification follows the mission's vocabulary: FIX_NOW (done), ADD_TEST,
DOCUMENT, FIX_WITH_ADAPTER, DEFER, DO_NOT_TOUCH.

Post-repair run (attack_spine_2, the shipped configuration: guarded
executor, on-disk execution ledger, on-disk receipts, kernel snapshot):

```
ATTACK H1 forged context executes: FAILED | ExecutionRefusal: ledger.issued: execution id 'forged-1' was never issued
ATTACK H2 issued context executes again: FAILED | ExecutionRefusal: ledger.single_use: already consumed
ATTACK H2b whole request replayed: FAILED | REJECTED conservation (duplicate artifact)
ATTACK H3 processing timestamp backdated in memory still verifies: SUCCEEDED (by design; see B3)
ATTACK H3a event_time rewritten still verifies: FAILED | ['request.state_commitment']
ATTACK H3c execution_id relabelled still verifies: FAILED | ['execution_context.state_commitment', 'outcome.state_commitment']
ATTACK H3d processing timestamp backdated on disk, log reopens: FAILED | receipts.jsonl:3: record hash does not recompute
ATTACK H4 forged root passes in_kernel_ledger: FAILED
ATTACK H5 altered kernel snapshot loads: FAILED | SnapshotIntegrityError
ATTACK H6 issued context replayed after restart: FAILED | ExecutionRefusal: ledger.single_use
ATTACK H6b altered execution ledger loads: FAILED | executions.jsonl:1: entry hash does not recompute
effects written by attacks: 0
```

## The spine (observe-perceive)

| ID | Invariant attacked | Route | Before repair (measured) | Repair | After repair (measured) | Second-order attack on the repair | Regression tests | Class |
|---|---|---|---|---|---|---|---|---|
| B1 | No execution without authorization | Hand-build an `ExecutionContext`, recompute its commitments with the public `compute_state_commitment`, hand it to the executor | Executes. Nothing recorded that a context was issued, so nothing could refuse one that was not | `execution_guard.py`: the orchestrator ISSUES every context into an append-only hash-chained `ExecutionLedger`; `authorize_execution` recomputes the structure, matches the issuance record field by field (including timestamps), then marks single use; `guarded(func, ledger)` wraps any executor | `ExecutionRefusal: never issued`; ledger gets a `refused` entry | Forge under a genuinely issued id with one field altered (timestamp, producer, lineage, artifact_hash, execution_id): refused per field. Alter the approval timestamp: refused | test_execution_guard (15) | FIX_NOW |
| B2 | Single use | Call the executor twice with the issued context | Executes twice | Single-use mark in the ledger at authorization; orchestrator consumes after an unguarded executor so the ledger still shows one use per issuance | `ExecutionRefusal: already consumed`, also after a restart (H6) | Reissue the same execution id: `refused_by="execution_ledger"`. Edit the ledger file: `LedgerIntegrityError`, nothing loads | test_execution_guard, test_failure_semantics | FIX_NOW |
| B2b | One decision per artifact | Run the whole request again with a new execution id | Second decision, second execution | Kernel refuses a duplicate artifact id (unchanged behaviour, now reached because the adapter registers the real artifact) | REJECTED by conservation | Two processes: two decisions (see R6) | test_failure_semantics | FIX_NOW + DOCUMENT |
| B3 | Temporal integrity | Rewrite `governance_request.timestamp` and `outcome_context.timestamp`; `time.monotonic` still passes | Verifies. Every timestamp was processing time, outside every commitment | Contract 1.1.0 adds `event_time` (committed) and `ingested_at` (recorded); anomalies computed at stamp time; the receipt hash covers the whole record so any rewrite on disk is detected | In memory: still verifies (processing time is deliberately outside the commitment so a replayed verification is deterministic). On disk: `RecordIntegrityError`. `event_time` rewrite: `request.state_commitment` fails | Naive, future and stale event times are recorded as anomalies, not refused (F14) | test_failure_semantics (3), test_governance_record | FIX_NOW + DOCUMENT (in-memory rewrite is the in-process attacker, R1) |
| B4 | Identity | Relabel `execution_id` on context and outcome | Verifies (id was not committed) | `execution_id` in `execution_context_state` | Two commitments fail | n/a | test_chain_closure | FIX_NOW |
| B5 | Every execution leaves a committed outcome | Run without vitals so OBSERVE does not run | No `OutcomeContext`; result uncommitted; record never `audit_chain_valid` | Outcome is unconditional (also on EXECUTION_FAILED), with `observe_ran`, `execution_status` and `result_artifact_hash` over a canonical representation | Outcome present and committed; verifier checks `outcome.present`, `outcome.execution_status` | Forge an outcome on a stolen record: `outcome.state_commitment` fails (test_chain_adversarial 14) | test_chain_closure (6) | FIX_NOW |
| B6 | Kernel membership | Register a root artifact named `decision-<id>` in the kernel; the verifier's membership check was "reconstruct succeeds" | Passes `conservation.in_kernel_ledger` | Membership requires a non-empty transformation chain and the request artifact among the roots | FAILED (H4): the root is admissible but is not a derived decision | Forged root with dangling parents: `RootAdmissionError` (kernel K1) | test_chain_closure, kernel tests | FIX_NOW |
| B7 | The kernel verifies something | The adapter submitted the decision with `propositions=()` | Every decision "verified" vacuously | Adapter builds real propositions from the spine vocabulary with a conservative map (unknown to UNKNOWN, unknown origin to MACHINE_ORIGINATED, role labels never authority, `authorization_refs` in the request context to HUMAN_AUTHORIZED); the decision is born DECISION / MACHINE / NONE with a declared LINEAGE change | Decisions are verified against their inputs; a claimed canonical or authoritative decision without an authorizing subject is refused | Self-declared HUMAN in the artifact metadata: not authority (K2) | test_conservation_boundary (7) | FIX_NOW |
| B8 | Result hash is stable | `str(dict)` hashing of the executor's result was order-dependent after a JSON round trip | Record verifies in memory, fails from the record | `canonical_repr` | Same hash from memory and from disk | n/a | test_governance_record | FIX_NOW |
| B9 | Scope crossing | `context["gateway_scope"]="EXECUTE"` without any sealed admission | Passes the scope check; the Gateway's seal was consulted only by the admission adapter, which a caller can skip | Orchestrator verifies a passed `gateway_admission` (integrity recomputes, scope matches, artifact id matches), records `scope_sealed`, records the admission summary; `require_declared_scope=True` refuses a bare claim | Default: executes with `scope_sealed=False` recorded. Strict: REJECTED | Escalate scope after sealing: refused in every mode. Seal for another artifact: refused. Sealed READ_ONLY: refused | test_scope_binding (8) | FIX_NOW (strict is opt-in: DOCUMENT) |
| B10 | Commitment covers the request | The admission object entered the committed request context (a mappingproxy), which deep-copy could not handle | Crash before commitment | The admission is popped before the request is built; its summary and integrity are recorded | Committed context carries `gateway_admission_integrity` | n/a | test_scope_binding | FIX_NOW |
| B11 | No crash without a record | PERCEIVE missing or raising | `AttributeError` out of `orchestrate_request`, no result | Phase 2 guarded: REJECTED with `stage_error` | Recorded refusal; raises only under `raise_on_stage_error` | n/a | test_failure_semantics (2) | FIX_NOW |
| B12 | No silent loss after the action | Receipt store unwritable | Exception after the action; the in-memory record lost with it | `receipt=None`, `receipt_error`, CRITICAL log | Result returned and says it was not receipted | The action still happened (R7) | test_failure_semantics | FIX_NOW + DOCUMENT |

## Conservation Kernel (conservation_kernel 0.2.0)

| ID | Invariant | Attack | Before | Repair | After | Tests | Class |
|---|---|---|---|---|---|---|---|
| K1 | Roots are admitted, not asserted | Register a root with dangling parent references and a canonical proposition | Accepted | `admit_root` / `RootAdmissionError`; born-canonical or born-authoritative propositions need an authorizing subject | Refused | test_root_admission_and_authority (9) | FIX_NOW |
| K2 | Human authority is evidence | `Actor(kind=HUMAN)` declared by the caller | Counted as human authorization | `EvidenceRegistry(trusted_humans=...)`; a HUMAN actor outside the set is not authority | Refused | same | FIX_NOW |
| K3 | Evidence is scoped | Wildcard evidence `"*"` | Matched everything | Wildcard only when the caller admits it explicitly | Scoped | same | FIX_NOW |
| K4 | Canonical status is granted, not claimed | A new canonical proposition with an unrelated authorization | Accepted | `_check_new_proposition`: `NEW_CANONICAL_UNAUTHORIZED`, `AUTHORITY_ESCALATION`, `CANONICALIZATION` unless the authorization is about the subject | Refused | same | FIX_NOW |
| K5 | A verification cannot be erased | Uncommittable accept dropped the report | Report lost | Reports kept on the decision | Kept | same | FIX_NOW |
| K6 | The ledger survives a restart | Process exit | Everything lost | `snapshot / from_snapshot / save / load / reverify`, `SnapshotIntegrityError` on any alteration | Restored and re-verified; altered file refused (H5) | test_snapshot_restore (6) | FIX_NOW |

## CCC (semantic recurrence)

| ID | Attack / defect | Before | After | Tests | Class |
|---|---|---|---|---|---|
| C1 | Reload a store with event dates | Dates lost; recurrence windows wrong | Restored on load | test_semantic_hardening (7) | FIX_NOW |
| C2 | Provider returns a non-numeric or out-of-range similarity | Recording crashes | `_validated`: finite, in [0,1] | same | FIX_NOW |
| C3 | Provider returns unsorted answers | The "nearest" match was the weakest | Sorted descending enforced | same | FIX_NOW |
| C4 | Provider raises | No trace | `semantic_provider_failed` audit event; recording continues | same | FIX_NOW |
| C5 | Load, then search | Index empty after load | Rebuilt on load; `CCCSystem.load(path, semantic_index=, semantic_threshold=)` | same | FIX_NOW |

## ghost_tools (assurance)

| ID | Defect | Before | After | Tests | Class |
|---|---|---|---|---|---|
| G1 | Committed baselines stored absolute paths | 137 of 137 entries stale: every baseline inert | Portable paths; stale entries reported | test_baseline_integrity (8) | FIX_NOW |
| G2 | Scan of zero files | Exit 0 ("clean") | Exit 2 with the reason | same | FIX_NOW |
| G3 | A MINOR baseline entry suppressed a CRITICAL finding at the same location | Silent | Escalation surfaces | same | FIX_NOW |
| G4 | Corrupt baseline | Exit 1, indistinguishable from findings | Exit 2 with the reason | same | FIX_NOW |
| G5 | `--accept --json` | Prose on a JSON channel | JSON | same | FIX_NOW |

## Not fixed: documented with the remaining risk

| ID | Risk | Why not fixed here | Class |
|---|---|---|---|
| R1 | An attacker inside the process can recompute any commitment: `compute_state_commitment` is public and unkeyed, so the ledgers are integrity-checked, not authenticated. Anything that can write the ledger file consistently with its chain can forge history | Keying (HMAC or signatures with the key outside the process) is a deployment decision: where the key lives decides what the guarantee means. The record format already carries every field a signature would cover | FIX_WITH_ADAPTER, DEFER |
| R2 | The executor is outside the library. A raw callable handed a forged context runs it (attack_spine_1 H1/H2 still "succeed" against an unguarded function) and, called directly, leaves no ledger entry | Python cannot stop a caller from calling a function. Enforcement is by contract: executors are wrapped with `guarded`; the orchestrator's own consumption still records one use per issuance for anything that went through it | DOCUMENT, FIX_WITH_ADAPTER at the deployment boundary |
| R3 | PERCEIVE's decision ledger is in-memory; after a restart `decision.in_perceive_ledger` cannot be re-checked (verify_record with `perceive=None` skips it and says so) | PERCEIVE persistence is a separate change with its own attack surface; the decision's commitment and audit hash are in the durable record already | DEFER |
| R4 | Kernel snapshots are integrity-checked (digest) not authenticated | Same as R1 | DEFER |
| R5 | PERCEIVE gates, scope sealing and vitals are advisory by default; strict is opt-in. Every record carries `handoff.strict`, so the regime that produced it is visible | The default was chosen for the pediatric pilot; flipping it is a product decision | DOCUMENT |
| R6 | Cross-process replay: two processes with separate ledger and kernel files reach two decisions on the same artifact | Sharing the files closes it; a networked ledger is out of scope | DOCUMENT |
| R7 | A receipt store failure after the action leaves the action with only an in-memory record | See F10; a pre-execution probe write is the adapter-level fix | FIX_WITH_ADAPTER |
| R8 | `event_time` is what the source claims | The kernel records it as evidence, the spine records anomalies; verifying a source clock needs the custody ledger (sentinel_os), which is frozen | DOCUMENT |
| R9 | CCC recording is explicit, not on the orchestrator path | Keeping it out of the spine's hard dependencies was a deliberate choice; the slice shows the call | DOCUMENT |
| R10 | OBSERVE regime "stable" for aggressive vitals in some cases (pediatric engine) | Clinical model quality, not governance; out of mission scope | DO_NOT_TOUCH |
