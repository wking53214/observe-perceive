# CLAUDE_ARCHITECTURE_STATE

Persistent hand-off state for the CLOSE THE SYSTEM mission. Updated at the end
of every phase. Read this before touching anything.

## CURRENT OBJECTIVE

Determine whether the portfolio forms a coherent, enforceable,
provenance-preserving governance system, then close the real gaps. The
invariant under test: a governed action must not occur silently, without the
required evidence, without crossing the appropriate authority boundary, and
without leaving a verifiable explanation of why it was permitted.

## CURRENT PHASE

Phase 1 (reconstruction) complete for the spine; four read-only audits of
CCC, Conservation Kernel, ANVIL/sentinel_os/GSA-815/GRAPH and ghost_tools
baseline in flight. Phase 3 (red team) started on the spine.

## ARCHITECTURE MAP (actual, from code, 2026-09-08)

The diagram in the mission brief maps onto the code like this. "Participates"
means the component runs on the path of `GovernanceOrchestrator.orchestrate_request`
(observe-perceive/governance_orchestrator.py) and its output is linked into
the commitment chain; "opt-in" means only when the orchestrator is constructed
with the flag; "manual" means a caller must invoke it separately.

| Stage | Implementation | Participation | Authority actually exercised |
|---|---|---|---|
| SOURCE | a "sentinel artifact": any object with `artifact_id`, `content`, `metadata.{origin_status,authority_status,epistemic_status,parent_artifact_ids}` (sentinel_perceive_adapter.py:53). sentinel_os is never imported; the type is duck-typed | participates | OBSERVE (claims origin/authority/epistemic status; nothing verifies the claim) |
| PRESERVE | Governance_Gateway admission (gateway_admission_adapter.py `govern_admitted`) seals the artifact and declares scope; Conservation Kernel registers the input artifact as a root (perceive_conservation_adapter.py:198) | Gateway: manual (caller-side entry); Kernel: participates | GATE (gateway), AUDIT (kernel root registration) |
| INTERPRET | PERCEIVE policy gates via `SentinelPerceiveAdapter.evaluate_through_perceive` (perceive_consolidated.py). Gates are ADVISORY by default (`PolicyEnforcementConfig.enforce_export_controls=False`); violations travel as `advisory_violations` | participates | INTERPRET + RECOMMEND (advisory) or GATE (when enforce flags set) |
| SCREEN | AUGUR behavioural screen, veto-only (governance_orchestrator.py:246) | opt-in | GATE (refuse-only) |
| CONTAIN | fortress-kernel containment (governance_orchestrator.py:310) | opt-in | GATE (refuse-only) |
| GATE/AUTHORIZE | Conservation Kernel `submit` of a decision artifact with a FunctionalContract (perceive_conservation_adapter.py:110-220) producing `ConservationDecision`; then `ConservationGSA815Adapter.approve_execution` mints `ExecutionApproval` + `ExecutionContext` with chained commitments | participates | AUTHORIZE |
| EXECUTE | `gsa815_operation_func(execution_context)`: a caller-supplied callable (governance_orchestrator.py:521). GSA-815 the repository is not imported; "GSA-815" is a label in the context's producer field | participates, but the executor is outside the system | EXECUTE (unconstrained) |
| OBSERVE | `observe_engine.evaluate(vitals_snapshot)` (observe_consolidated.py), only when vitals are supplied | conditional | OBSERVE |
| AUDIT | `OutcomeContext` + `forensic_proof` (gsa815_observe_adapter.py), only when OBSERVE ran; `governance_chain.verify_result` re-derives 29 checks on the way out. CCC recording (`OrchestratorCCCAdapter.record`) is manual and called by no production code path. Nothing is persisted: the record is the returned dict | partial | AUDIT (in-memory only) |

Commitment chain: request (root) → PERCEIVE decision (its own ledger) →
conservation_audit_hash → approval → execution context → outcome. Each link is
`compute_state_commitment(parent, state)` over the fields in
governance_chain.{request_state, approval_state, execution_context_state,
outcome_state}. Timestamps are NOT in any commitment; `execution_id` is not in
the execution-context commitment.

## REPOSITORY STATUS

| repo | role | status |
|---|---|---|
| observe-perceive | the spine (gate) | ACTIVE, 1.1.0, 529 tests |
| conservation_kernel | constitutional hub | ACTIVE dependency, 0.1.0 |
| Governance_Gateway | admission/sealing | ACTIVE, 0.1.0 |
| ghost_tools | assurance tooling | ACTIVE, 0.5.2 |
| CCC | recurrence/audit ledger | FROZEN optional pack; semantic recurrence under audit |
| AUGUR | behavioural screen | FROZEN optional pack |
| fortress-kernel | containment | FROZEN optional pack |
| sentinel_os | custody ledger, Postgres-backed | FROZEN; never imported by the spine |
| GSA-815 | nominal executor | FROZEN; never imported by the spine |
| ANVIL | hash-chained execution lineage kernel (single file) | NOT in the 18-repo audit; candidate EXECUTE/AUDIT record layer; under audit |
| GRAPH | consolidated architecture documents | reference only |
| VANGUARD | retired, flattened | IRRELEVANT |
| TBCA, CITADEL | archived transcripts | IRRELEVANT |
| nine archived repos | see docs/audit | IRRELEVANT |

## DISCOVERED GAPS (running list; severity per mission priority order)

1. CONSTITUTIONAL BYPASS. The executor is a caller-supplied callable that
   receives an `ExecutionContext` and nothing obliges it to verify anything.
   `ExecutionContext`/`ExecutionApproval` are plain mutable dataclasses with
   public constructors and a public `compute_state_commitment`; a hand-built
   context is indistinguishable from an issued one on the executor side.
   Nothing records that a context was issued, so nothing can refuse a
   context that was not.
2. CONSTITUTIONAL BYPASS / REPLAY. A context can be executed any number of
   times; there is no single-use or issuance ledger.
3. PROVENANCE BREAK. When OBSERVE does not run (no vitals), no
   `OutcomeContext` is created: the execution result is uncommitted and the
   record can never be `audit_chain_valid`. An executed action then has no
   verifiable outcome.
4. PROVENANCE BREAK / PERSISTENCE. Nothing persists the record. CCC recording
   is manual. A process exit loses every explanation.
5. TEMPORAL. Every timestamp is processing time (`datetime.now`); no event
   time is carried from the source artifact; timestamps are outside every
   commitment, so `time.monotonic` verifies ordering of values a caller can
   rewrite.
6. IDENTITY. `execution_id` is absent from the execution-context commitment.
7. AUTHORITY CONFUSION (intentional, documented). PERCEIVE gates advisory by
   default; scope and vitals checks are opt-in strict.
8. Pending: CCC semantic recurrence, Conservation Kernel epistemic
   transformations, ghost baseline (audits in flight).

## ACTIVE HYPOTHESES

- H1: a forged ExecutionContext executes via any real executor (gap 1).
- H2: the same context executes twice (gap 2).
- H3: rewriting `governance_request.timestamp` after the fact leaves
  `verify_result` valid (gap 5).
- H4: registering a root artifact named `decision-<id>` in the kernel makes
  `conservation.in_kernel_ledger` pass without a submit (verifier check is
  reconstruct-succeeds only).

## ATTACKS PERFORMED

(none yet recorded; see GOVERNANCE_BYPASS_REPORT.md once written)

## REPAIRS PERFORMED

(none yet)

## TEST RESULTS

Baseline at start: observe-perceive 529 passed / 5 skipped; conservation_kernel
52; Governance_Gateway 45; ghost_tools 159; ruff clean on all four.

## UNRESOLVED RISKS

- The executor boundary is outside the library; enforcement there is by
  contract, not by code, until an execution guard exists.

## NEXT HIGHEST-VALUE ACTION

Run H1-H4 as scripts; if confirmed, build the execution guard with an
issuance ledger (in-memory + append-only file), make the outcome context
unconditional, commit execution_id and event/ingestion time, and add a
durable record format with a from-record verifier.
