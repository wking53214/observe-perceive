# CLAUDE_ARCHITECTURE_STATE

Persistent hand-off state for the CLOSE THE SYSTEM mission. Read this before
touching anything. Full reports: docs/closure/.

## CURRENT OBJECTIVE

Determine whether the portfolio forms a coherent, enforceable,
provenance-preserving governance system, then close the real gaps. The
invariant under test: a governed action must not occur silently, without the
required evidence, without crossing the appropriate authority boundary, and
without leaving a verifiable explanation of why it was permitted.

## CURRENT PHASE

Phase 5 complete; the seven residual risks worked (1.3.0, 2026-09-08).
Verdict: proven for the shipped configuration against an external caller,
a file editor without the key, a second process on the shared ledger, and
a restart. What remains is the key holder, a callable invoked outside the
orchestrator, and processes that do not share the file. See
docs/closure/ARCHITECTURE_CLOSURE_REPORT.md sections 1 and 12.

## ARCHITECTURE MAP (actual, from code)

SOURCE (duck-typed sentinel artifact with event time) → PRESERVE (Gateway
seal, verified by the orchestrator when passed; kernel root admission with
real propositions) → INTERPRET (PERCEIVE gates, advisory by default) →
SCREEN (AUGUR, opt-in) → CONTAIN (fortress, opt-in) → GATE/AUTHORIZE (kernel
`submit` of the decision as a derived artifact; self-describing approval;
execution context ISSUED into the hash-chained execution ledger) → EXECUTE
(caller callable, `guarded` refuses never-issued, altered or used contexts;
consumption recorded either way) → OBSERVE (when vitals; strict flag) →
AUDIT (unconditional outcome; 34-check verifier; hash-chained receipts;
kernel snapshot; CCC recording explicit).

Contract 1.1.0: `event_time` committed, `ingested_at` recorded,
`execution_id` committed, approval self-describing, canonical result hash.
Processing timestamps stay outside the commitments by design and are covered
by the receipt and ledger entry hashes.

## REPOSITORY STATUS

| repo | role | branch / state |
|---|---|---|
| observe-perceive | the spine | claude/prompt-red-blue-team-sj9a31; 1.3.0; 610 passed / 5 skipped (all packs) |
| conservation_kernel | constitutional hub | main at 25145aa (0.3.0, PR #5 merged, signed snapshots); 75 passed. Spine pins that commit |
| Governance_Gateway | admission / sealing | main; 0.1.0; CI green |
| ghost_tools | assurance | main (PR #11 merged); 0.5.2 + baseline integrity; 167 passed |
| CCC | recurrence ledger | main (PR #12 merged into the frozen repo); 142 passed, 1 xfailed |
| AUGUR, fortress-kernel | opt-in stages | FROZEN |
| sentinel_os, GSA-815 | never imported | FROZEN |
| ANVIL | candidate lineage layer | not integrated; not needed by the closure |
| GRAPH, VANGUARD, TBCA, CITADEL, archived repos | irrelevant | |

## DISCOVERED GAPS

Fourteen gaps G1-G14, all closed or documented: see the gap matrix in
docs/closure/ARCHITECTURE_CLOSURE_REPORT.md section 4.

## ATTACKS PERFORMED

Spine 12, kernel 6, CCC 5, ghost_tools 5, all measured before and after
repair; second-order attacks on each repair. Post-repair run in the shipped
configuration: 11 attacks, 0 effects written, every refusal recorded.
docs/closure/GOVERNANCE_BYPASS_REPORT.md.

## REPAIRS PERFORMED

1.3.0: signed ledger/receipts/snapshots (kernel 0.3.0 Signer); orchestrator
guards every executor; shared ledger file (lock, re-read, one issuance per
artifact); receipt probe before issuance; PERCEIVE ledger persisted; strict
profile; attested event time (contract 1.2.0). Earlier:
observe-perceive: execution_guard (ledger, authorize, guarded);
governance_record (record, verify_record, ReceiptLog); contract 1.1.0;
verifier hardening; adapter hands the kernel real propositions; scope
binding to the seal; unconditional outcome; PERCEIVE crash guard; receipt
degradation; temporal anomalies; vertical slice; kernel-only install clean.
conservation_kernel: root admission, born-authoritative rule, trusted
humans, scoped wildcard, kept reports, snapshot/restore with re-verification.
CCC: event dates on load, provider validation and ordering, index rebuild,
failure audit event. ghost_tools: portable baselines, stale entries,
escalation, exit codes, JSON accept.

## TEST RESULTS

observe-perceive 610 passed / 5 skipped / 3 subtests (was 529);
kernel-only clean clone 494 / 63 (was 3 collection errors, 24 failures);
conservation_kernel 75 (was 52); CCC 142 + 1 xfail (was 134); ghost_tools
167 (was 159). ruff clean everywhere.

## UNRESOLVED RISKS (ranked)

After 1.3.0: the signing key holder (hold it outside the process; the
Signer protocol admits a signing service); a callable invoked outside the
orchestrator; processes that do not share the ledger file; a disk filling
between the receipt probe and the write; sources without a key (still
claims, recorded as such); the advisory default profile (visible on every
record); R9 CCC recording explicit; R10 OBSERVE clinical regime.

## NEXT HIGHEST-VALUE ACTION

1. Merge observe-perceive PR #24 (1.3.0). Create tag v0.3.0 on kernel
   commit 25145aa from the GitHub UI.
2. Key management: a Signer backed by a service or HSM, and key rotation
   (a record names its key_id, so rotation is a verifier-side map).
3. A shared ledger service for processes that cannot share a file.
