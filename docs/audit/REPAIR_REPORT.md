# REPAIR_REPORT.md

Repair mission run on 2026-09-08 against the governance stack, from the
container that ran the technical audit. Plan: `REPAIR_PLAN.md` beside this
file. Every count below was produced by running the suite; nothing is
estimated.

## Scope, restated

The repair target was the operational pipeline as it actually exists in
observe-perceive (Gateway admission → AUGUR screen → PERCEIVE → fortress →
Conservation Kernel → execute → OBSERVE → CCC record) with the Conservation
Kernel as constitutional infrastructure. The epistemic pipeline (TIE → HERALD
→ CCC → ecology) and the archived legs of the Full Voltron are BLOCKED, not
faked; see section 4 and section 9. This session could push only to
observe-perceive; no other repository was modified.

## 1. What was broken

| id | defect | evidence |
|---|---|---|
| N1 | `audit_chain_valid` was a presence check. `verify_governance_chain` tested that six fields were non-empty and recomputed nothing; a record with every hash altered, or an approval built by hand that never saw a kernel, was "valid" | `gsa815_observe_adapter.py:79-96` (before) |
| N2 | Execution failure had no record. The caller's operation was called unguarded; an exception left no result at all, after Conservation had verified and execution had been approved | `governance_orchestrator.py` Phase 5 (before) |
| N3 | Observation failure after execution lost the execution record: `observe_engine.evaluate` unguarded; `observe_engine=None` with vitals raised after the action ran | Phase 6 (before) |
| N4 | Conservation never compared the carried `artifact_hash` to the content it verified: content altered after hashing passed with the stale hash on every downstream record | `perceive_conservation_adapter.py` (before) |
| N5 | The request was not in the result: the record could not be reconstructed from the record | all return sites (before) |
| N6 | No handoff record: nothing said which producer, contract version, authority, epistemic status or strict flags produced a result | same |
| N7 | `execution_id` was a random uuid the caller could not fix; a canonical scenario could not replay | `conservation_gsa815_adapter.py:63` |
| N8 | Execution time was not recorded | Phase 5 |
| N9 | A missing or malformed artifact raised `AttributeError` from inside Phase 1 | entry |
| N10 | A crashing simulation screen propagated out of the chain; "could not evaluate" would otherwise read as "no objection" | Phase 1b |
| N11 | A forged `ExecutionApproval`, internally consistent, was indistinguishable from a real one: nothing checked the ledgers | verifier |
| N12 | The outcome record's `governance_decision_id` held the execution id, not the decision id | `gsa815_observe_adapter.py` |
| N13 | The success path was the only path without an `audit_chain` key | final return |
| N14 | The Gateway-admitted path could not reach OBSERVE: `govern_admitted` had no way to pass vitals, so every production entry point ran with OBSERVE skipped | `gateway_admission_adapter.py` |
| A8 | Two HMAC audit logs were tracked and grew on every test run | `augur_audit.log`, `fortress_audit.log` |
| A9 | `pytest.ini` carried an inert `env =` block; one test hard-coded `/home/wking53214/CCC` | `pytest.ini`, `test_orchestrator_ccc_adapter.py:23` |

## 2. What was repaired

All in `wking53214/observe-perceive`.

| file | problem | repair | reason | tests added |
|---|---|---|---|---|
| `governance_contracts.py` | no contract version | `CONTRACT_VERSION = "1.0.0"` | a consumer must know what it received | contract test |
| `governance_orchestrator.py` | N5, N6 | `_stamp()` attaches `governance_request` and a `handoff` block (producer, contract version, issued_at, authority, epistemic status, origin, request commitment, strict flags) to every result on every path | reconstruction from the record alone; handoff semantics | `test_every_result_path_carries_handoff_and_request` |
| `governance_orchestrator.py` | N2, N8 | Phase 5 guarded: `EXECUTION_FAILED` status with `execution_error`, `executed_at` (started, finished), `execution_status` completed/failed/partial, every preceding record kept; opt-in `raise_on_execution_error` | failed and partial execution are explicit states | 3 tests |
| `governance_orchestrator.py` | N3 | Phase 6 guarded: `observe_error` recorded, execution record kept, `observe_enforced=False`; `observe_engine=None` with vitals is recorded not raised | an outcome failure must not erase the action | 2 tests |
| `governance_orchestrator.py` | N9 | `INVALID_REQUEST` naming the missing attributes | explicit state | 2 tests |
| `governance_orchestrator.py` | N10 | screen crash refuses with `stage_refused=False`, `stage_error`, honours `raise_on_stage_error` | a crash is neither a veto nor a pass | 1 test |
| `governance_orchestrator.py` | N7 | `context["execution_id"]` fixes the execution id | canonical replay | replay suite |
| `governance_orchestrator.py` | N1, N13 | `_verify_on_the_way_out()` runs the verifier on every executed result; `audit_chain_valid` now means verified and complete; `chain_verification` carries per-check detail; `audit_chain` present on the success path | the claim the record makes must be checkable | all suites |
| `governance_chain.py` (new) | N1, N11 | `verify_result()`: recomputes request, approval, execution-context and outcome commitments; checks hash-vs-content, id continuity across five records, audit-hash chaining, result-artifact hash, outcome-vs-verdict, monotonic time; with kernels, PERCEIVE ledger membership, PERCEIVE ledger integrity, recorded approval and violations vs the ledger entry, Conservation ledger membership. 29 checks on a complete record | the verifier is the enforcement boundary for the historical record | adversarial suite |
| `sentinel_perceive_adapter.py` | commitment formula duplicated | uses `governance_chain.request_state` | producer and verifier cannot drift | replay suite |
| `perceive_conservation_adapter.py` | N4 | refuses with `ConservationRefusal("artifact hash mismatch ...")` when `sha256(content) != artifact_hash` | the constitutional boundary must check what it is handed | 2 tests |
| `gsa815_observe_adapter.py` | N12 | outcome names the decision id; `verify_governance_chain` docstring says it is a presence check | correct lineage | provenance test |
| `gateway_admission_adapter.py` | N14 | `govern_admitted(..., vitals_snapshot=None)` threads vitals to the chain | the admitted path can reach OBSERVE | demo test |
| `.gitignore`, git index | A8 | audit logs untracked and ignored | run artifacts are not fixtures | B3 check |
| `pytest.ini` | A9 | inert `env` block removed with the reason | config that reads as wired and is not | |
| `test_orchestrator_ccc_adapter.py` | A9 | sibling path resolved relative to the file | passes on any machine | |
| `conftest.py` (new) | | shared chain fixtures | | |
| `demo_why.py`, `test_demo_why.py`, README section (new) | Phase 18 | reproducible ten-step scenario with one corruption; exit status is the verdict; every corruption is a test | the commercial demo exercises the real components | 6 tests |

Repairs proved by re-applying the original failure: content altered in flight
is now REJECTED by Conservation (`test_content_altered_after_hashing_is_refused_before_execution`);
an execution that raises now yields `EXECUTION_FAILED` with the approval kept;
a hand-built approval verifies structurally and fails against the ledgers
(`test_14_...`).

## 3. What was connected

- **Gateway → OBSERVE.** An admitted artifact can now be observed; before, no production entry point could supply vitals.
- **Record → verifier.** The result now carries enough (the request, every decision object, execution times) for `verify_result` to re-derive every commitment without the process that made it, and to check it against both kernels' ledgers when they are available.
- **Producer ↔ verifier.** The request commitment is computed over one shared definition.
- **Demo → suite.** The commercial demo is a test; the enforcement boundary cannot regress silently.

## 4. What remains disconnected

- **Epistemic pipeline (Phase 4): TIE → HERALD → CCC → ecology.** TIE, HERALD and ecology were archived on 2026-09-08 on the commercial red team's advice; this session cannot write to them. The spine's HERALD and TIE adapters are duck-typed translators with no code dependency and remain in place; nothing exercises them end to end against the real packages here.
- **Epistemic + operational bridge (Phase 6).** Not built; its inputs are the archived legs.
- **Ecology / historical reconstruction (Phase 8).** ecology is archived and its corpus cannot ship. The reconstruction that does exist is the chain verifier's re-derivation of one record, and CCC's recurrence memory via the existing adapter.
- **GEMS → AUGUR handoff (Phase 5 start).** GEMS is archived; the GEMS adapter remains as a translator into the chain, untested against the package here.
- **ANVIL.** Not a repository in the stack. Execution is the caller's callable, recorded with its approval and context.
- **Ledger stage (sentinel_os, GSA-815).** Frozen; not deployable from a clone without a dependency manifest. The chain verifier's ledger checks use the Conservation Kernel and PERCEIVE ledgers, which are in-process.
- **Deterministic re-execution beyond the request.** PERCEIVE mints the decision id from a time-stamped ledger entry; a re-run is a new decision by design. What is deterministic is stated and tested (`test_chain_replay.py`).

## 5. What was intentionally not changed

- **The permissive defaults** (`require_declared_scope`, `require_vitals`, `raise_on_stage_error`, `raise_on_execution_error`, advisory PERCEIVE gates). Flipping them is the deployment's decision; the record now says on every result which flags were in force.
- **The three broad `except Exception` sites** in `perceive_consolidated.py:1017` (a failed gate votes no), `observe_consolidated.py:1046` (worker requeue), `clinical_governance_system.py:216` (flagged fail-open for clinical escalation). Each is documented and intentional.
- **The Conservation Kernel.** Frozen, not writable from here, and its ledger already refuses duplicates and unseen inputs; the spine relies on those refusals and now surfaces them as refusals.
- **The untyped result dict.** A typed result would be a contract change for every consumer; the handoff block and the promised keys (`status`, `governance_decision`, `audit_chain`, `handoff`, `governance_request`) are the contract this pass commits to.
- **The 344-line `orchestrate_request`.** A structural refactor is not a repair.
- **The clinical engine.** OBSERVE returned "stable, escalation not required" for a demo patient at HR 142, SpO2 91, RR 34, T 39.1, which the audit had already recorded as a known detection gap. The demo prints what the engine said rather than hiding it; the fix is clinical validation work, out of scope.

## 6. Test results

Run in this container on 2026-09-08, Python 3.11, with Conservation_Kernel and fortress-kernel installed and CCC, AUGUR, GEMS, Governance_Gateway, fortress-kernel checked out beside the repo.

| run | result |
|---|---|
| full suite before this mission | 475 passed, 5 skipped |
| full suite after | **529 passed, 5 skipped, 3 subtests passed** |
| `test_chain_adversarial.py` (Phase 10) | 24 passed |
| `test_chain_replay.py` (Phase 11) | 4 passed |
| `test_chain_failure_paths.py` (Phases 12, 13) | 20 passed |
| `test_demo_why.py` (Phase 18) | 6 passed |
| existing stage-visibility, orchestrator, gateway suites | 13, 4, 15 passed |
| `OBSERVE_STRICT_CLINICAL=1` on the clinical suites | 5 failed, 5 passed (the five known gaps, by design) |
| `ruff check .` | clean |
| `git status` after the full suite | no tracked file modified |
| `python3 demo_why.py --corrupt {outcome,source,authority,approval,timestamp}` | exit 0 for all five |

The 5 skips are the clinical known-gap tests, unchanged.

## 7. Security results

Controlled violations against a real executed record (Phase 10). "Detected" means a named check fails in `verify_result`; "rejected" means the chain refused before execution.

| # | violation | outcome |
|---|---|---|
| 1 | source content changed after hashing (in flight) | REJECTED by Conservation, `hash mismatch` |
| 1b | source content changed in the record | DETECTED `request.artifact_hash_matches_content` |
| 2, 4 | authority, origin, producer, epistemic status rewritten | DETECTED `request.state_commitment` (4 variants) |
| 3 | outcome dated before the decision | DETECTED `time.monotonic` |
| 5 | prediction rewritten to an approval after the fact | no effect on the permission chain; permission still PERCEIVE's, in its ledger |
| 6 | recorded permission flipped; violations added | DETECTED `decision.matches_perceive_ledger` |
| 7 | execution result altered | DETECTED `outcome.result_artifact_hash` |
| 8 | outcome altered | DETECTED `outcome.state_commitment` |
| 9 | replay of the same artifact | REJECTED by the kernel ledger, `refused_by=conservation` |
| 10 | artifact substituted under an approval | DETECTED `execution_context.artifact`, `execution_context.state_commitment` |
| 11 | READ_ONLY scope; undeclared scope in strict mode | REJECTED before execution |
| 12 | conservation, approval, execution context or decision record removed | DETECTED `*.present` (4 variants) |
| 13 | sealed Gateway artifact altered after sealing | NOT ADMITTED |
| 14 | approval built by hand, internally consistent | structurally valid; DETECTED against ledgers `decision.in_perceive_ledger`, `conservation.in_kernel_ledger` |
| 15 | future knowledge: outcome altered | request and decision commitments unchanged; only the outcome link fails |
| + | kernel ledger raises | REJECTED as a Conservation refusal, not a crash |

Unsuccessful attacks: none of the sixteen was accepted. One limit is stated
in the tests: the simulation screen sits outside the commitment chain, so an
altered screen record is not itself detected; it also cannot change any
permission, which is the property that matters.

## 8. Full Voltron status

- **Operational pipeline (Gateway → AUGUR → PERCEIVE → fortress → Conservation → execute → OBSERVE → verifier):** END-TO-END ADVERSARIALLY VERIFIED, in-process, with the CCC record adapter untested here against the archived package.
- **Epistemic pipeline (TIE → HERALD → CCC → ecology):** NOT FUNCTIONAL here; BLOCKED by the archive decision.
- **Full Voltron as named in the mission:** PARTIALLY FUNCTIONAL.

## 9. Remaining blockers

1. **The archive decision.** Phases 4, 6, 8 and the archived legs of 9 cannot be repaired without unarchiving TIE, HERALD, GEMS and ecology, or without deciding they are out of the product. The commercial memo argues the latter. This is the founder's call, not a repair.
2. **Ledger stage not deployable.** sentinel_os and GSA-815 need a dependency manifest before any end-to-end path through the custody ledger can be executed.
3. **Permissive defaults.** The chain records what it skipped; a deployment must choose to set the strict flags. Nothing here flips them.
4. **Clinical detection gaps.** Five known misses; a validation study, not a code change.

## 10. Recommended next step

Package the gate as a service around `orchestrate_request` with the strict
flags on by configuration and the result's `handoff` and `chain_verification`
as its API, then run the paid thirty-day pilot from the memo. Every repair in
this report was chosen because that demonstration depends on it; nothing
further should be built until a customer has run it.
