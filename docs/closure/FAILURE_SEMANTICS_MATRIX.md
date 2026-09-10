# FAILURE_SEMANTICS_MATRIX

What each boundary of the spine does when it cannot do its job. Measured on
observe-perceive at contract 1.1.0 with the Conservation Kernel 0.2.0.
"Recorded" means the returned result (and the receipt, when a receipt log is
configured) says what happened; "silent" would mean the action could occur or
be lost with nothing to show for it. Every row is pinned by a test named in
the last column.

Design rule applied throughout: a failure before the action refuses and
records; a failure after the action cannot un-do the action, so it degrades
explicitly and says so. Nothing swallows an exception into a success.

| # | Boundary / failure | What happens | Action occurs? | Recorded as | Test |
|---|---|---|---|---|---|
| F1 | PERCEIVE absent or crashes during interpretation | Phase 2 is guarded: REJECTED, `stage_refused=False`, `stage_error` names the exception. Re-raised only under `raise_on_stage_error=True`. The request is kept on the result | No | REJECTED + stage_error | test_failure_semantics: missing_perceive x2 |
| F2 | Conservation Kernel absent | REJECTED, `conservation_enforced=False`, `refused_by=None` (no stage refused; no kernel ran) | No | REJECTED | test_failure_semantics: missing_kernel |
| F3 | Kernel refuses the decision artifact (conservation violation, duplicate id, unauthorized canonical claim) | REJECTED, `refused_by="conservation"`, reason from the kernel | No | REJECTED + reason | test_conservation_boundary, test_failure_semantics: duplicate_artifact |
| F4 | Execution ledger unwritable at issuance (disk full, permissions) | Issuance fails before the executor is called: REJECTED, `stage_error` names the OSError | No | REJECTED + stage_error | test_failure_semantics: unwritable_execution_ledger |
| F5 | Execution ledger refuses reissue (same execution id issued twice) | REJECTED, `refused_by="execution_ledger"` | No | REJECTED | test_execution_guard: reissue |
| F6 | Executor raises | EXECUTION_FAILED; the issued context is consumed with outcome `execution_failed`; an OutcomeContext is still committed with `execution_status`; re-raised only under `raise_on_execution_error` | Attempted; effect unknown to the spine | EXECUTION_FAILED + outcome | test_chain_failure_paths, test_chain_closure |
| F7 | Guarded executor refuses (never issued, altered, already consumed) | `ExecutionRefusal` before the effect; the ledger gets a `refused` entry naming why | No | ledger `refused` entry | test_execution_guard x15 |
| F8 | OBSERVE not run (no vitals) | Outcome is still committed (`observe_ran=False`, producer "GSA-815"); status APPROVED_AND_EXECUTED; warning logged; refused instead under `require_vitals=True` | Yes | outcome without verdict | test_chain_closure: outcome_without_observe |
| F9 | OBSERVE crashes after the action | `observe_error` recorded, outcome committed with `execution_status`, status stays APPROVED_AND_EXECUTED | Yes | observe_error | test_chain_failure_paths |
| F10 | Receipt log unwritable after the action | Result returned with `receipt=None` and `receipt_error`; logged CRITICAL. The action has happened and is not hidden | Yes | receipt_error on the in-memory result only | test_failure_semantics: unwritable_receipt_log |
| F11 | Receipt log altered on disk (any byte of any record) | `ReceiptLog` refuses to open: `RecordIntegrityError` names the line and which hash failed | n/a | refusal to load | test_failure_semantics: broken_chain, test_governance_record |
| F12 | Execution ledger altered on disk | `LedgerIntegrityError` on load; nothing is loaded | n/a | refusal to load | test_execution_guard: altered_entry |
| F13 | Kernel snapshot altered on disk | `SnapshotIntegrityError` on `ConservationKernel.load`; every artifact is re-verified on restore | n/a | refusal to load | conservation_kernel test_snapshot_restore |
| F14 | Event time in the future, more than 30 days stale, or naive (no timezone) | Recorded in `temporal_anomalies`; not refused (the source's clock is evidence, not authority) | Yes | temporal_anomalies | test_failure_semantics: future/stale/naive |
| F15 | Gateway admission missing while a scope is claimed | Default: executes, `scope_sealed=False` recorded. Strict (`require_declared_scope=True`): REJECTED | Default yes | scope_sealed=False | test_scope_binding |
| F16 | Gateway admission tampered (scope escalated after sealing, sealed for another artifact, sealed READ_ONLY) | REJECTED in every mode; reason names the mismatch | No | REJECTED + reason | test_scope_binding x4 |
| F17 | AUGUR screen or fortress stage crashes | REJECTED, `stage_refused=False`, `stage_error` | No | REJECTED | test_chain_failure_paths: crashing_screen |
| F18 | Optional pack absent (AUGUR, fortress, Gateway, GEMS, CCC) | The adapter imports and reports "not available"; the orchestrator runs without the stage only when the stage was not asked for; asking for an absent stage raises ImportError at construction, never mid-chain | n/a | construction-time error | kernel-only install: 494 passed, 63 skipped |
| F19 | Same artifact governed twice in one process | Second run REJECTED by the kernel (duplicate artifact id) | Once | REJECTED | test_failure_semantics: duplicate_artifact |
| F20 | Same artifact governed by two processes (or after restart without the kernel snapshot) | Two independent decisions with different decision ids and identical request commitments. Not refused: nothing shared says the first one happened | Twice | two records | test_failure_semantics: two_processes |
| F21 | CCC semantic provider crashes or returns malformed similarity | Provider failure becomes a `semantic_provider_failed` audit event; non-finite or out-of-range values are rejected; recording continues without the semantic match | n/a | CCC audit event | CCC test_semantic_hardening |
| F22 | ghost_tools scan of zero files, or corrupt baseline | Exit code 2 with the reason on stderr (was exit 0 / 1) | n/a | exit 2 | ghost_tools test_baseline_integrity |

## Rows that are the remaining risk

- F10 is the honest limit of "no silent action": if the receipt store is
  unwritable, the action has already happened and the only record is the
  return value. A deployment that needs a stronger guarantee must make the
  receipt store part of the same durable write as the effect, or refuse to
  execute when the store is unreachable (write a probe entry before Phase 5).
  Classified DOCUMENT here; FIX_WITH_ADAPTER at the deployment boundary.
- F20 is the cross-process replay: the execution ledger and kernel are
  per-process unless their files are shared. Sharing the files closes it
  (F19 then applies after `ConservationKernel.load`). DOCUMENT.
- F8 and F15 are advisory by default and strict by flag. That is a design
  choice recorded in the handoff (`handoff.strict`), so a reader of any
  record can tell which regime produced it. DOCUMENT.

## 1.3.0 additions

| # | Boundary / failure | What happens | Action occurs? | Recorded as | Test |
|---|---|---|---|---|---|
| F10' | Receipt store unreachable before execution | Probe fails: REJECTED `refused_by="receipt_store"`, nothing issued. The old F10 row now applies only to a disk that fills between the probe and the write | No | REJECTED | test_authenticated_boundary: unreachable_receipt_store |
| F20' | Same artifact in two processes sharing the ledger file | Second issuance refused by the execution ledger before the action | Once | REJECTED | test_authenticated_boundary: two_ledger_objects |
| F23 | Ledger, receipt log or snapshot unsigned or signed by another key, opened with a key | Refuses to load (`LedgerAuthenticityError`, `RecordAuthenticityError`, `SnapshotAuthenticityError`) | n/a | refusal to load | test_authenticated_boundary, kernel test_signing |
| F24 | Event time attestation invalid | Advisory: recorded in `temporal_anomalies`, executes. Strict: REJECTED `refused_by="source_attestation"` | profile-dependent | anomaly or REJECTED | test_authenticated_boundary: bad_attestation, strict_refuses |
| F25 | PERCEIVE advised a violation | Advisory: recorded, executes. Strict: REJECTED `refused_by="perceive"` with PERCEIVE's finding | profile-dependent | REJECTED + advisory_violations | test_authenticated_boundary: strict_enforces |
| F26 | PERCEIVE's persisted ledger tampered | Refuses to open (`AuditLedgerIntegrityError`) | n/a | refusal to load | test_authenticated_boundary: tampered_perceive_ledger |
| F27 | Executor already guarded by the caller | Not wrapped twice; the caller's guard consumes, the orchestrator records it | Once | execution_authorization.guard | test_execution_guard |
