# REPAIR_PLAN.md

Repair queue for the governance stack, built from the 2026-09-08 technical audit
(`AUDIT_REPORT_2026-09-08.md`), the commercial red team
(`COMMERCIAL_RED_TEAM_2026-09-08.md`), and a fresh read of the enforcement
points in the spine. Ranked by criticality × downstream reach × demo value ×
governance importance × low risk.

## Scope decision, stated up front

The mission names an epistemic pipeline (TIE → HERALD → CCC → ecology) and an
operational pipeline (GEMS → AUGUR → PERCEIVE → ANVIL → OBSERVE). On
2026-09-08, on the red team's advice, TIE, HERALD, GEMS, ecology and OBSERVE
were archived and CCC, AUGUR, fortress-kernel, sentinel_os and GSA-815 were
frozen. This session can push only to observe-perceive. "ANVIL" names no
repository in the stack; the execution stage in code is the callable the
caller hands to `orchestrate_request`.

So the repair target is the **operational pipeline as it exists in the
spine** (Gateway admission → AUGUR screen → PERCEIVE → fortress → Conservation
→ execute → OBSERVE → CCC record) with Conservation Kernel as constitutional
infrastructure, plus the temporal, replay, adversarial and failure-path work
around it. The epistemic phases (4, 6, 8) and the archived legs of the Full
Voltron (9) are documented as BLOCKED in the report, with evidence and options,
not faked.

## Findings carried in from the audit

| id | finding | severity | status at start |
|---|---|---|---|
| A1 | Stage crash filed as a refusal (Conservation, execution) | HIGH | fixed e2f0683 |
| A2 | Undeclared scope executed silently | HIGH | fixed 9ea72f9 |
| A3 | OBSERVE skipped silently without vitals | HIGH | fixed 2b8403b |
| A4 | Clinical missed detections filed as skips | HIGH | opt-in strict 2ce17cc |
| A5 | Two chain stages are names, not code (GSA-815, sentinel_os) | MEDIUM | documented; not a code repair |
| A6 | Result is an untyped dict with a path-dependent key set | MEDIUM | open |
| A7 | Input contract is duck-typed; nothing states its version | MEDIUM | open |
| A8 | Tracked audit logs grow on every test run | LOW | open (hygiene) |
| A9 | pytest.ini `env` block inert; hard-coded home path in one test | LOW | open (hygiene) |

## New findings from the enforcement read (this mission)

| id | finding | evidence | severity |
|---|---|---|---|
| N1 | `audit_chain_valid` is a presence check: `verify_governance_chain` tests that six fields are non-empty and recomputes nothing | `gsa815_observe_adapter.py:79-96` | **CRITICAL** for the "why did the system do that" claim |
| N2 | Execution failure has no record: `gsa815_operation_func` is called unguarded; an exception leaves no result at all, after Conservation and approval were recorded | `governance_orchestrator.py:411` | **CRITICAL** |
| N3 | Observation failure after execution loses the execution record: `observe_engine.evaluate` unguarded, `observe_engine=None` with vitals raises after the action ran | `governance_orchestrator.py:417` | **HIGH** |
| N4 | Conservation never compares the carried `artifact_hash` to the content it verifies: a request whose content was altered after hashing passes | `perceive_conservation_adapter.py:61-135` | **HIGH** |
| N5 | The request itself is not in the result: the record cannot be reconstructed from the record | `governance_orchestrator.py` return sites | HIGH (information preservation) |
| N6 | No handoff record: nothing in the result says which producer, contract version, authority or strict flags produced it | same | MEDIUM |
| N7 | `execution_id` is a random uuid the caller cannot fix, so a canonical scenario cannot replay to identical commitments | `conservation_gsa815_adapter.py:63` | MEDIUM |
| N8 | Execution time is not recorded; `ExecutionContext.timestamp` is creation time before the action | same | MEDIUM |
| N9 | A missing or malformed artifact raises `AttributeError` instead of an explicit state | `orchestrate_request` entry | MEDIUM |
| N10 | A crashing simulation screen propagates out of the chain | `governance_orchestrator.py` Phase 1b | MEDIUM |
| N11 | A forged `ExecutionApproval` built by hand is indistinguishable from a real one: nothing checks the receipt against the kernel ledger | `verify_governance_chain` | HIGH (direct downstream execution) |

Intentional, not defects: the three `except Exception` sites in
`perceive_consolidated.py:1017` (a failed gate votes no), `observe_consolidated.py:1046`
(worker requeue), `clinical_governance_system.py:216` (flagged fail-open for
clinical escalation). TODO/FIXME/NotImplementedError: zero in the active repos.

## Repair queue, in execution order

| # | repair | fixes | reach | risk |
|---|---|---|---|---|
| R1 | `CONTRACT_VERSION` in `governance_contracts`; every result carries `handoff` (producer, version, issued_at, authority, epistemic status, strict flags) and `governance_request` | N5, N6, A7 | every consumer | low |
| R2 | Execution failure is a recorded state: `EXECUTION_FAILED` with `execution_error`, `executed_at`; `execution_status` completed/failed/partial; opt-in `raise_on_execution_error` | N2, N8 | every caller | low |
| R3 | OBSERVE failure after execution is recorded (`observe_error`), never loses the execution | N3 | every caller | low |
| R4 | Conservation adapter refuses when `sha256(content) != artifact_hash` | N4 | the constitutional boundary | low |
| R5 | `context["execution_id"]` fixes the execution id for canonical replay | N7 | replay | low |
| R6 | Explicit `INVALID_REQUEST` for a missing or malformed artifact; a crashing screen is recorded as a stage error, refuses by default | N9, N10 | callers | low |
| R7 | `governance_chain.verify_result`: recompute every state commitment link, check hash-vs-content, id continuity, timestamp ordering, and (with kernel and PERCEIVE) ledger membership; `audit_chain_valid` and `forensic_proof.chain_valid` mean this | N1, N11 | the demo claim | medium |
| R8 | Adversarial suite (Phase 10), replay suite (Phase 11), failure-path suite (Phase 12), contract and provenance tests (Phase 13) | all | | low |
| R9 | `demo_why.py`: the reproducible "why did the system do that" scenario with one corruption; documented commands | Phase 18 | commercial | low |
| R10 | `REPAIR_REPORT.md` | Phase 22 | | |

Not in this queue (BLOCKED, see report): Phases 4, 6, 8, the archived legs
of 9; Phase 17 (performance) until correctness lands; Phase 15 beyond the
spine; any change to Conservation Kernel itself (frozen and not writable from
here; its ledger already refuses duplicates and unseen inputs, which is what
the spine relies on).
