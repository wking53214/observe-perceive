# ARCHITECTURE_CLOSURE_REPORT

Mission: determine whether the governance portfolio forms a coherent,
enforceable, provenance-preserving system, and close the real gaps. Date of
measurement: 2026-09-08. Companion documents in this directory:
GOVERNANCE_BYPASS_REPORT, PROVENANCE_CLOSURE_REPORT,
FAILURE_SEMANTICS_MATRIX, INTEGRATION_MATRIX. Hand-off state:
CLAUDE_ARCHITECTURE_STATE.md at the repository root.

## 1. Verdict on the success condition

The invariant: a governed action cannot occur silently, without the required
evidence, without crossing the appropriate authority boundary, and without
leaving a verifiable explanation of why it was permitted.

**Proven**, for an action executed through the spine in its shipped
configuration (guarded executor, on-disk execution ledger, on-disk receipts,
kernel snapshot), against an attacker who can call the public API, hand the
executor forged or replayed contexts, replay requests, edit any file on disk,
crash any stage, or restart the process:

- not silently: every issuance, consumption and refusal is a hash-chained
  ledger entry; every result is a hash-chained receipt; a refusal to execute
  is itself recorded (attack_spine_2: 11 attacks, 0 effects, all recorded);
- not without evidence: the Conservation Kernel verifies the decision as a
  derived artifact over the request's actual propositions, and refuses a
  decision that claims more authority or canonical status than its inputs
  and authorizations grant;
- not without crossing the authority boundary: an executor that is guarded
  runs only a context the orchestrator issued, unaltered, once; a bare scope
  claim is recorded as a claim and can be refused; a sealed admission that
  was escalated, re-targeted or sealed read-only is refused in every mode;
- with a verifiable explanation: the vertical slice reconstructs nine steps
  from the effect line alone after a restart, and the record verifies with
  34 checks.

**Not proven**, and stated as such:

- against an attacker inside the process or with write access to the
  files: the commitments are unkeyed and the files are integrity-checked
  but not signed, so a consistent forgery is possible (R1, R4);
- for an executor that is not wrapped with `guarded`: enforcement there is
  by contract, not by code (R2);
- across processes that do not share the ledger and kernel files (R6);
- when the receipt store fails after the action (R7): the action is
  recorded only in the return value.

Why these are the boundary: each one is a property of where the system
runs, not of the system. The library now carries every field a signature
would cover, and refuses everything it can see. What it cannot see is what a
deployment must add.

## 2. The actual architecture (from code)

| Stage | Implementation | On the orchestrator path | Authority exercised |
|---|---|---|---|
| SOURCE | duck-typed sentinel artifact (`artifact_id`, `content`, `metadata.*`, optional `occurred_at`) | yes | OBSERVE: claims origin, authority, epistemic status, event time |
| PRESERVE | Governance_Gateway seal (`GatewayAdmissionAdapter.seal / admit`); kernel root admission with propositions | Gateway manual but verified by the orchestrator when passed; kernel yes | GATE (seal), AUDIT (root admission) |
| INTERPRET | PERCEIVE gates (`SentinelPerceiveAdapter.evaluate_through_perceive`) | yes | INTERPRET, RECOMMEND; GATE when enforce flags set |
| SCREEN | AUGUR (`simulation_screen=True`) | opt-in | GATE, refuse-only |
| CONTAIN | fortress-kernel (`fortress_controller=`) | opt-in | GATE, refuse-only |
| GATE / AUTHORIZE | kernel `submit` of the decision with a FunctionalContract; `ConservationGSA815Adapter.approve_execution` mints a self-describing approval and context; the context is ISSUED into the execution ledger | yes | AUTHORIZE |
| EXECUTE | caller callable `func(execution_context)`, ideally `guarded(func, ledger, kernel=)`; consumption recorded either by the guard or after the fact by the orchestrator | yes; executor outside the library | EXECUTE |
| OBSERVE | `ObserveClinicalEngine.evaluate(vitals)` when vitals supplied; strict flag refuses otherwise | conditional | OBSERVE |
| AUDIT | unconditional `OutcomeContext`; `verify_result` (34 checks); `ReceiptLog`; kernel snapshot; CCC recording explicit | yes | AUDIT |

Commitment chain: request (root, includes `event_time`) → PERCEIVE decision →
conservation audit hash → approval (self-describing) → execution context
(includes `execution_id`) → outcome (unconditional, includes
`execution_status`, canonical result hash). Contract 1.1.0.

## 3. Governance path map: every route to governed state

| Route | Reaches governed state? | Boundary crossed |
|---|---|---|
| `GovernanceOrchestrator.orchestrate_request` | yes: the only path that mints an issued context | all of the above |
| `GatewayAdmissionAdapter.govern_admitted` | same path with a sealed admission passed through | adds the seal |
| a caller constructing `ExecutionContext` by hand | reaches a guarded executor: refused (never issued); reaches an unguarded executor: runs (R2) | none: this is the documented gap outside the library |
| replaying an issued context | refused by the guard (single use), also after restart | ledger |
| replaying the request | refused by the kernel in the same process; two decisions across processes (R6) | kernel |
| registering artifacts in the kernel directly | admitted only if consistent (roots) and never counted as a decision (derived-by-transformation membership) | kernel |
| editing any durable file | the file refuses to load | integrity check |
| `demo_why`, `vertical_slice` | orchestrator path | same |

Authority lattice, as enforced: the kernel enforces that no artifact is born
canonical or authoritative without an authorizing subject; that a HUMAN
actor counts only if registered as trusted; that evidence is scoped. The
spine enforces that a decision is derived from the request, that the
approval names the request's artifact, and that the executor runs an issued
context once. Role labels on the artifact are never authority. Scope is a
claim unless sealed, and the record says which.

## 4. Gap matrix

| Gap (from Phase 1) | Status | Where |
|---|---|---|
| G1 forged context executes | closed by the guard; open for unguarded executors | B1, R2 |
| G2 replay | closed in-process and across restart; open across unshared processes | B2, R6 |
| G3 no outcome without OBSERVE | closed | B5 |
| G4 nothing persisted | closed for ledger, receipts, kernel; open for PERCEIVE's ledger | K6, R3 |
| G5 timestamps rewritable | event time committed; processing time covered by the receipt; in-memory rewrite documented | B3 |
| G6 execution_id uncommitted | closed | B4 |
| G7 advisory by default | documented; strict flags on the record | R5 |
| G8 kernel verified nothing | closed | B7 |
| G9 forged membership | closed | B6 |
| G10 CCC semantic recurrence | closed (5 defects) | C1-C5 |
| G11 ghost baselines inert | closed (5 defects) | G1-G5 |
| G12 scope claim without seal | closed (verified seal, strict mode) | B9 |
| G13 crash without record | closed | B11, B12 |
| G14 minimal install does not test | closed | INTEGRATION_MATRIX |

## 5. Bypass matrix

See GOVERNANCE_BYPASS_REPORT.md: 12 spine bypasses, 6 kernel, 5 CCC, 5
ghost_tools, all measured before and after; 10 residual risks R1-R10 with
their classification.

## 6. Provenance, temporal, failure results

- Provenance: complete from three files and one snapshot after a restart;
  nine-step explanation; 34 checks; two exceptions (PERCEIVE ledger not
  persisted; integrity not authenticity). PROVENANCE_CLOSURE_REPORT.md.
- Temporal: event time committed, ingestion time recorded, processing times
  ordered and receipt-covered, anomalies recorded not refused.
- Failure: 22 rows, every one pinned by a test; three rows are the residual
  risk (F10 receipt after action, F20 cross-process, F8/F15 advisory
  defaults). FAILURE_SEMANTICS_MATRIX.md.

## 7. Test numbers, exact

| Repo | Before mission | After | Added | ruff |
|---|---|---|---|---|
| observe-perceive (all packs) | 529 passed, 5 skipped | 589 passed, 5 skipped, 3 subtests | 60 | clean |
| observe-perceive (kernel-only clean clone) | 3 collection errors, 24 failures | 494 passed, 63 skipped | | clean |
| conservation_kernel | 52 | 67 | 15 | clean |
| CCC | 134 | 142 passed, 1 xfailed | 8 | clean |
| ghost_tools | 159 | 167 | 8 | clean |

New spine test files: test_execution_guard (15), test_chain_closure (6),
test_governance_record (8), test_conservation_boundary (7),
test_scope_binding (8), test_vertical_slice (5), test_failure_semantics (11).

## 8. Remaining risks, ranked

1. R1/R4 unkeyed commitments and unsigned files (in-process or file-level
   attacker). Next boundary to close; adapter-level keying.
2. R2 unguarded executors. Contract; make `guarded` the only documented way.
3. R6 cross-process replay without shared files.
4. R7 receipt failure after the action.
5. R3 PERCEIVE ledger not persisted.
6. R5 advisory defaults (visible on every record).
7. R8 event time is a source claim.
8. R9 CCC recording explicit.
9. R10 OBSERVE clinical regime (out of scope).

## 9. Production readiness, scored (0 to 10, this measurement)

| Dimension | Score | Basis |
|---|---|---|
| Correctness of the governance chain | 8 | 34 verifier checks, 60 new tests, 11 attacks refused; two documented in-memory exceptions |
| Enforcement at the execution boundary | 6 | strong when guarded; contract-only when not (R2) |
| Provenance and explainability | 8 | nine-step reconstruction after restart from files alone; PERCEIVE ledger not persisted |
| Persistence and restart | 7 | three ledgers plus snapshot, all integrity-checked; nothing authenticated |
| Failure semantics | 8 | 22 rows pinned; receipt-after-action is the one honest hole |
| Temporal integrity | 7 | event time committed; source clock unverified |
| Resistance to an in-process attacker | 3 | unkeyed commitments (R1) |
| Cross-repo integration | 7 | contracts enumerated, minimal install tested; kernel pinned to its 0.2.0 merge commit |
| Release hygiene | 7 | clean-clone installs and CI for the active repos; optional packs still resolved as siblings first |
| Operability (deploying it) | 5 | the vertical slice is the only end-to-end reference; no service, no key management, no shared ledger |

## 10. Commercial readiness

What can be claimed today, truthfully: a library that gives every automated
action a verifiable, replay-resistant, restart-surviving explanation, with
refusals recorded as first-class events, and a measured attack surface. The
claim holds for actions executed through a guarded executor with shared
ledger files.

What cannot be claimed: tamper-proof against an operator of the host;
clinical validity of the OBSERVE engine; enforcement over executors the
library does not wrap.

What a pilot must configure: guarded executor, ledger and receipt paths on
durable shared storage, kernel snapshot on the same storage, strict flags
chosen deliberately, and a key for the commitments once R1 is closed.

## 11. Decision classification of everything found

FIX_NOW: B1-B12, K1-K6, C1-C5, G1-G5 (done, tested, measured).
FIX_WITH_ADAPTER: R1 keying, R7 pre-execution receipt probe.
ADD_TEST: none outstanding; every repair has a regression test.
DOCUMENT: R2, R5, R6, R8, R9, B3 in-memory case.
DEFER: R3 PERCEIVE persistence, R4 snapshot authentication.
DO_NOT_TOUCH: R10 OBSERVE clinical model; frozen repositories (AUGUR,
fortress-kernel, sentinel_os, GSA-815) beyond their freeze banners.

## 12. Final adversarial review

"If I wanted to prove this architecture is unsafe, where would I attack?"
In order: (1) the process itself or its files, with the public commitment
function (R1); (2) an executor someone forgot to wrap (R2); (3) a second
process with its own files (R6); (4) the receipt store at the moment after
the effect (R7); (5) the source clock (R8). None of these is hidden; each is
named on the record or in this report, and the first two are the next
engineering steps. Nothing in the attack list executes silently against the
shipped configuration.
