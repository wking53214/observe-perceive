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

## Governed decision across libraries (optional)

`cns_governed_decision.py` takes the verdicts of gates from separate libraries and turns them into one decision. You give it two lists of gates. The first list (ALPHA) is judged before your work function runs, and if any of them refuses, the work is never called. The second list (OMEGA) is judged on what the work returned, and if any of them refuses, the result is withheld. Every verdict goes through `cns.gate.resolve`, and the answer is a `GovernedDecision`: the outcome (`pass`, `retry` or `terminal_breach`), every verdict, which gates were not evaluated and why, whether the work ran, and a digest of the record.

What it is not. It is a verdict-combining layer. It does not replace `GovernanceOrchestrator`, which stays hard-wired and unchanged, and it does not read the orchestrator's ledger or receipts. It only works with libraries that ship a CNS connector (a class that satisfies `cns.gate.Gate`). It makes no gate's judgment better: a library's PASS is "no objection", and the layer says nothing about whether the result is correct.

What it needs. CNS, a private package, through the optional extra `cns` (pinned to the commit of tag v1.4.0). This repository imports CNS only when a pipeline is built or run. Without CNS, everything else here works as before and building a pipeline raises `CnsNotInstalled` with the install command. No runtime dependency was added.

Run the demo. It builds one pipeline from the real CNS gates of five libraries (governance_gateway, ccc and augur before the work; dit and conservation_kernel on the result) and a stub work function, then runs eleven scenarios and prints every verdict, whether the work ran, and the final decision:

```text
python -m venv .venv-demo && . .venv-demo/bin/activate
pip install -r requirements-demo.txt
PYTHONPATH=. python examples/cns_governed_decision_demo.py
```

`requirements-demo.txt` installs each library from the branch that carries its connector. Those are branch refs, not commit SHAs, and should be pinned once the branches are final. A recorded run is in `docs/CNS_GOVERNED_DECISION_DEMO.md`. The tests are in `test_cns_governed_decision_demo.py` and skip cleanly, with a reason, where CNS or a library is missing.

Limits of the demo. The work is a stub, not a model. Two scenarios (a required library is missing) are simulated by leaving a slot empty; nothing stands in for the missing gate. AUGUR's gate appends audit lines to a log, and the demo sends them to a temporary file that is not deleted. The digests are tamper-evidence, not tamper-proofing: someone who rebuilds a verdict with a recomputed digest is not caught by the digest alone, and the demo shows that too.

The rules the layer enforces, each with tests that fail without it (`test_cns_governed_decision.py`):

| Rule | What the layer does |
|---|---|
| R1 order | ALPHA gates are judged before the work. The work is called only if every ALPHA verdict is PASS. |
| R2 fail closed | A gate that raises, returns something that is not a well-formed `GateResult`, or is missing while required becomes a TERMINAL_BREACH. Never a pass, never a crash. A missing required OMEGA gate refuses the run before the work starts. |
| R3 placement | A gate's declared position must match the slot it sits in. A mismatch refuses the whole run before any gate or the work runs. A position is never relabelled. |
| R4 precedence | All verdicts, the layer's own included, resolve through `cns.gate.resolve`: any TERMINAL_BREACH wins, then any RETRY, and PASS only when nothing objected. |
| R5 short-circuit | After the first TERMINAL_BREACH on an end, later gates on that end are not asked and are listed as not evaluated. A RETRY does not stop evaluation. |
| R6 omega | OMEGA gates run only after every ALPHA gate passed and the work returned. A work function that raises is a TERMINAL_BREACH with `work_error` set. The result is returned only when the outcome is PASS. |
| R7 binding | By default a verdict must record what it judged (a subject and a digest of it). An unbound verdict is refused even when it passes. |

Two further rules are bookkeeping and packaging: duplicate gate names raise `ValueError` and the record says what was judged and why (R8), and CNS is imported lazily with no new runtime dependency (R9).

Proprietary. All rights reserved. See LICENSE.
