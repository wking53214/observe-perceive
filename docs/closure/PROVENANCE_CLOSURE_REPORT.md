# PROVENANCE_CLOSURE_REPORT

Question: for a governed action, can its complete provenance be reconstructed
from the durable record alone, after the process that produced it is gone,
and does every link verify?

Answer, measured on 2026-09-08: yes for everything the spine commits, from
three files and one snapshot, with the two exceptions named at the end.

## What is committed, and to what

Each link is `compute_state_commitment(parent, state)`; the state dicts are
in `governance_chain`.

| Link | State committed | Parent |
|---|---|---|
| request | request_id, artifact_id, artifact_hash (sha256 of content), producer, lineage, operation_type, `event_time`, authority, origin, epistemic_status, context (with `gateway_admission_integrity` when sealed) | none (root) |
| PERCEIVE decision | decision_id, request_id, verdict, gates, audit_hash; kept in PERCEIVE's own ledger, `decision.matches_perceive_ledger` when that ledger is present | request |
| conservation | kernel decision id, artifact hash, verified flag, audit hash; membership by reconstruction (non-empty transformation chain, request artifact among the roots) | decision |
| approval | conservation ids, receipt id, governance and conservation audit hashes, artifact_id, artifact_hash, producer, lineage (self-describing since 1.1.0) | conservation audit hash |
| execution context | request_id, artifact_id, artifact_hash, producer, lineage, `execution_id` (since 1.1.0) | approval |
| outcome | execution_id, decision id, audit hashes, `execution_status`, `result_artifact_hash` over `canonical_repr(result)`, observe verdict summary when OBSERVE ran | execution context |
| execution ledger entry | every field above for the issued context plus both timestamps, `previous`, entry `hash` | previous entry |
| receipt | `record_hash` over the full plain record, `previous`, `written_at`, receipt `hash` | previous receipt |

Not committed, deliberately: processing timestamps (`timestamp`,
`ingested_at`, `issued_at`, `executed_at`). They are recorded, covered by the
receipt hash and the ledger entry hash, and checked for monotonic order, but
a re-verification of the same record must give the same answer at any later
time, so they are outside the state commitments. Rewriting one in memory
therefore still verifies (attack H3); rewriting one on disk does not (H3d).

## What the verifier re-derives

`governance_chain.verify_result` recomputes 34 named checks from the result
or from a record rebuilt by `governance_record.from_record`:

request.present, request.state_commitment, request.artifact_hash_matches_content,
decision.present, decision.request_id, decision.in_perceive_ledger,
decision.matches_perceive_ledger, perceive_ledger.integrity,
conservation.present, conservation.verified, conservation.decision_id,
conservation.artifact_hash, conservation.in_kernel_ledger,
approval.present, approval.state_commitment, approval.parent_hash,
approval.conservation_decision_id, approval.receipt, approval.names_request_artifact,
execution_context.present, execution_context.state_commitment,
execution_context.approval, execution_context.artifact,
execution.started, execution.finished,
outcome.present, outcome.state_commitment, outcome.execution_id,
outcome.decision_id, outcome.audit_hashes, outcome.execution_status,
outcome.result_artifact_hash, outcome.matches_verdict, time.monotonic.

Checks that need a live component (`decision.in_perceive_ledger`,
`conservation.in_kernel_ledger`) are skipped and reported as skipped when
that component is not supplied; they are not reported as passed.

## Reconstruction after a restart (the vertical slice)

`vertical_slice.run_slice` performs one governed action through the real
code (Gateway seal, kernel root with propositions, PERCEIVE, kernel decision,
issuance into an on-disk ledger, a guarded executor that appends one line to
an effects log, OBSERVE, receipt, kernel snapshot, CCC recording when
present), then discards every object and calls `explain(effect_line)`
against the files alone. The explanation walks:

1. ACTION: the effect line names `execution_id`, `artifact_id`, `action`, `at`.
2. EXECUTION: the execution ledger has `issued` then `consumed` for that id; the chain recomputes.
3. AUTHORIZATION: the receipt whose record carries that execution context; the context's commitment recomputes to the ledger's issuance record.
4. GOVERNANCE DECISION: the kernel snapshot, re-verified on load, reconstructs the decision as a derived artifact of the request root.
5. INTERPRETATION: PERCEIVE's decision, its gates and audit hash, from the record (ledger membership skipped, reported).
6. EVIDENCE: the propositions the kernel verified, from the snapshot.
7. SOURCE: the sealed Gateway admission (integrity recomputes), the request root, its `event_time`.
8. RECORD: `verify_record` over the receipt record: valid and complete, 34 checks.
9. OBSERVATION: the OBSERVE verdict that the outcome commits to.

Then the same action is replayed three ways (issued context to the guarded
executor; a forged context; the whole request through the orchestrator) and
refused three ways, with the refusals themselves receipted and ledgered.
Measured: nine steps explained, three refusals, zero effect lines written by
the replays. Tests: test_vertical_slice (5).

A tampered effect line (an execution id that was never issued) finds no
explanation at step 2 and the explanation says so.

## Temporal integrity

| Time | Meaning | Where | Committed |
|---|---|---|---|
| `event_time` | when the source says the thing happened (`metadata.occurred_at`) | request | yes |
| `ingested_at` | when the spine received it | request | recorded |
| `timestamp` (request, approval, context, outcome) | processing time at each stage | each object | recorded; ordering checked by `time.monotonic` |
| `handoff.issued_at` | when the result was stamped | result | recorded |
| ledger `recorded_at`, receipt `written_at` | when the durable entry was appended | files | inside the entry hash |

Anomalies recorded (never refused): event time after ingestion, more than
30 days before ingestion, or without a timezone. They travel in
`temporal_anomalies` on the result and the record.

## Persistence and restart

| State | Persisted by | Integrity on load |
|---|---|---|
| issuance and consumption of every execution | `ExecutionLedger(path)` JSONL, hash-chained | `LedgerIntegrityError` |
| every result | `ReceiptLog(path)` JSONL, hash-chained, full record | `RecordIntegrityError` |
| kernel ledger (roots, transformations, decisions, evidence) | `ConservationKernel.save(path)` | `SnapshotIntegrityError`, every artifact re-verified |
| CCC findings and semantic index | CCC store; index rebuilt on load | CCC's own checks |
| PERCEIVE decision ledger | not persisted | n/a (R3) |
| AUGUR and fortress internal ledgers | not persisted (opt-in stages; their verdicts are on the record) | n/a |

## The two exceptions

1. PERCEIVE's ledger membership cannot be re-checked after a restart. The
   decision, its gates and its audit hash are on the record and committed;
   what is lost is the independent second copy.
2. Authenticity. Every file is integrity-checked; none is signed. A writer
   with access to the files and the public commitment function can produce a
   consistent forgery. This is the in-process attacker of R1 and is the next
   boundary to close.
