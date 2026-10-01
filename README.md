# observe-perceive

**Hub of the governed action stack.** Version `1.3.0`. Python ≥ 3.11. Hard dependency: [`Conservation_Kernel`](https://github.com/wking53214/Conservation_Kernel) `@25145aa`. Optional extras: Gateway, CCC, AUGUR, GEMS (pinned to last commit that still has `HumanAuthorityGuard`), fortress-kernel.

Pediatric sepsis monitoring is the **reference domain**, not the product boundary.

## 1. Pipeline Position & Role

**Orchestrator spanning Admission → Observation → Policy → Conservation → Execution → Audit.** This repo *is* the live decision path. α/ζ/β/δ are extracted, composable twins; they are **not imported here**.

```text
Signals / artifacts
        │
        ▼
 ADMISSION          Governance_Gateway          well-formed? scoped? digest ok?
        │                                        refusal → NOT_ADMITTED
        ▼
 OBSERVATION        observe_consolidated        evidence → fused regime / Keys
        │
        ▼
 INTERLOCKS         EscalationPolicy (inline)   dwell / cooldown / bypass
        │           (ζ exists, not wired)
        ▼
 POLICY             perceive_consolidated       PERCEIVE gates (advisory default)
        │
        ▼
 OPTIONAL SCREENS   AUGUR (veto-only)           may refuse; may never approve
                    fortress-kernel             containment / slew
        │
        ▼
 CONSERVATION       Conservation_Kernel.submit  undeclared epistemic shift → REJECT
        │
        ▼
 EXECUTION          execution_guard             ISSUED context, single-use
                    caller-supplied callable    "GSA-815" in diagrams ≠ this import
        │
        ▼
 CUSTODY            ExecutionLedger + receipts  hash-chained JSONL
                    CCC (optional)              recurrence memory
```

`GovernanceOrchestrator` (`governance_orchestrator.py`, ~920 lines) runs the chain. `governance_chain.verify_result` **re-derives commitments**; it does not trust stored strings.

## 2. Full System Scope & Architectural Depth

### Contracts (`governance_contracts.py`)

Cross-system request / decision / approval types plus **state commitments**. Contract 1.1.0+: `event_time` committed, `ingested_at` recorded, `execution_id` committed, approval self-describing, canonical result hash. Processing timestamps stay **outside** commitments on purpose (covered by receipt/ledger hashes).

### PERCEIVE (`perceive_consolidated.py`, ~1217 lines)

Policy gates under declared consensus. Historical six-gate unanimous pattern. **`PolicyEnforcementConfig` defaults to advisory** (commercial red team B2): gates record; they do not by themselves stop execution unless the orchestrator is in a strict profile. Crash of PERCEIVE is guarded (fail-closed, not silent skip). PERCEIVE ledger can persist.

**Policy approval ≠ authorization.** Explicit human authority references are required for full authorization; the kernel checks registered events.

### OBSERVE (`observe_consolidated.py`, ~1416 lines)

Clinical fusion: validate → multi-assessor → fuse → regime → escalation. `RiskAdapters.heuristic` and `behavioral_vaccine` are the source α extracted. `EscalationPolicy` is the source ζ extracted. They still run **here**, live.

### Execution guard (`execution_guard.py`)

Measured 2026-09-08: a hand-built `ExecutionContext` with public constructors was indistinguishable from an orchestrator-issued one, and a genuine context could be executed any number of times. Guard closes that:

- `ExecutionLedger`: append-only, hash-chained, in-memory or JSONL.
- `authorize_execution`: re-derives approval/context commitments, field-compares to issuance, refuses never-issued / altered / already-consumed.
- `guarded(func, ledger)` wraps so the check cannot be forgotten.
- **Does not stop a caller who invokes the raw callable.** That is the executor's contract.

Integrity ≠ authenticity: anyone in-process can recompute hashes. With a signer, forged JSONL appends fail (`LedgerAuthenticityError`). Whoever controls the ledger the executor consults controls "issued".

### Adapters (vocabulary translation at seams)

`gateway_admission_adapter` · `augur_screen_adapter` · `fortress_perceive_adapter` · `perceive_conservation_adapter` · `gsa815_observe_adapter` · `sentinel_perceive_adapter` · `gems_governance_adapter` · `herald_governance_adapter` · `innovation_governance_adapter` · `tie_governance_adapter` · `orchestrator_ccc_adapter` · `conservation_gsa815_adapter`

Sibling checkout first, installed package second; tests skip when neither is present.

### Cassettes

`cassette.py`, `pediatric_cassette.py`, `industrial_cassette.py`, `installed_cassettes.py`. Domain packs, not a published Cassette SDK.

## 3. What It Does NOT Do / Non-Goals

- Does **not** import α, ζ, β, δ, GSA-815, or sentinel_os as the live path.
- Does **not** issue human grants (no identity registry, no grant lifecycle).
- Does **not** provide KMS/HSM as a service (optional signer if you pass a key).
- Does **not** ship a deployable SaaS. Library + demo (`demo_why.py`).
- Does **not** claim FDA-cleared clinical detection. `fda_510k_checklist.py` is a checklist, not a submission.
- Does **not** perform post-execution agent routing.

## 4. Brutally Honest Current Status & Gaps

From `CLAUDE_ARCHITECTURE_STATE.md` and `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md` (still current on gaps; 90-day freeze lifted 2026-09-11 without superseding findings):

| Gap | Detail |
|---|---|
| Dual spine | Extracted α-ζ-β-δ is not the orchestrator. Two decision stories. |
| Advisory PERCEIVE default | Strict profile exists; default does not fail-closed on policy. |
| Pediatric misses | Known missed/late detections and sensor-fault gaps filed as **skipped tests**. Not a clinical product. |
| GEMS pin | `273aeea` — parent of `bb4cf40`, which **deleted** `HumanAuthorityGuard` / `HandoffValidator` / `governance/constitution.py` that `gems_governance_adapter.py` still imports. Head of GEMS main will error ~16 tests. |
| Key holder residual | Closure proven against external caller, file editor without the key, second process on shared ledger, restart. **Not** proven against the key holder, a callable invoked outside the orchestrator, or processes that do not share the file. |
| Untyped orchestrator output | Commercial audit: result is an untyped dict. |
| No actor registry / grant lifecycle | Architecture epic, missing. |
| sentinel_os / GSA-815 | Never imported. Diagrams that show them as chain stages are conceptual. |
| Optional stages | AUGUR/CCC/fortress/Gateway/GEMS skipped if absent. A clone without extras is a shorter chain. |

Measured (architecture state): 610 passed / 5 skipped with all packs. `pip install -e ".[test,chain,fortress]" && pytest`.

## 5. Core Invariants & Guarantees

- Fail-closed on conservation REJECT, unissued/altered/replayed execution contexts, PERCEIVE crash (guarded).
- Recomputation over trust for state commitments and ledger hashes.
- Actor self-report is not evidence for kernel judgment.
- Policy ≠ authorization.
- Single-use execution contexts **when the executor uses the guard**.
- Signed ledger/receipts/snapshots when a signer is configured (1.3.0).

## 6. Inputs, Outputs & Type Contracts

Duck-typed sentinel artifact with event time → orchestrator result dict + receipts. Important types: `GovernanceApproval`, `compute_state_commitment`, `ExecutionContext`, `ExecutionLedger`. See `governance_contracts.py` and `execution_guard.issuance_record()`.

`python demo_why.py` — one governed action + deliberate corruption.

## 7. Stack Integration Topology

```text
                    ┌── Governance_Gateway (opt, extra `chain`)
                    ├── CCC (opt)
                    ├── AUGUR (opt, veto-only)
                    ├── GEMS @273aeea (opt; newer GEMS breaks adapter)
                    └── fortress-kernel (opt, extra `fortress`)
observe-perceive ───┤
                    ├── Conservation_Kernel @25145aa   HARD
                    ├── observe_consolidated + perceive_consolidated  (in-tree)
                    └── execution_guard ledger (JSONL)
                         │
                         ✗ does not import α ζ β δ sentinel_os GSA-815
```

Reports: `docs/closure/ARCHITECTURE_CLOSURE_REPORT.md`, `GOVERNANCE_BYPASS_REPORT.md`, `docs/audit/COMMERCIAL_RED_TEAM_2026-09-08.md`.

Proprietary. All rights reserved. See LICENSE.
