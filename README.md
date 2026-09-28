# observe-perceive

**Hub of the governed action stack.**  
Admission, optional screens, policy evaluation (PERCEIVE), Conservation Kernel verification, execution, and post-action observation — tied together by recomputable state commitments and a verifiable hash chain.

Pediatric sepsis monitoring is the **reference domain**, not the product boundary.

```bash
pip install -e ".[test,chain,fortress]"   # or: pip install -r requirements.txt
pytest                                    # includes chain adversarial suite
python demo_why.py                        # one governed action + deliberate corruption
```

Version is in `pyproject.toml`. Conservation Kernel is the hard dependency for a full conserved path. Optional stages (Gateway, AUGUR, GEMS, CCC, fortress-kernel) resolve from a sibling checkout first, then the installed package.

**Entry points**

| Piece | Role |
|--------|------|
| `GovernanceOrchestrator` (`governance_orchestrator.py`) | Runs the chain |
| `governance_contracts.py` | Cross-system request/decision/approval types + state commitments |
| `governance_chain.verify_result` | Re-derives commitments; does not trust stored strings |
| Adapters (`gateway_admission_adapter`, `sentinel_perceive_adapter`, `perceive_conservation_adapter`, …) | Translate vocabularies at each seam |

---

## Where this sits in the stack

```text
Signals / artifacts
        │
        ▼
 ADMISSION          Governance_Gateway          well-formed? untampered? scoped?
        │                                        refusal → NOT_ADMITTED (not policy)
        ▼
 OBSERVATION        OBSERVE / interconnected_α  evidence → named Keys
        │
        ▼
 INTERLOCKS         interconnected_ζ            Locks (AND/OR/N-of-M, dwell, force)
        │
        ▼
 POLICY             PERCEIVE (this repo)        may this request proceed?
        │
        ▼
 DECISION           interconnected_β            Decision + reasoning / reversal / instructions
        │
        ▼
 CONSERVATION       Conservation_Kernel         declared changes only; refuse undeclared
        │
        ▼
 EXECUTION          GSA-815 (optional)          act only under approval
        │
        ▼
 CUSTODY            interconnected_δ / sentinel_os   ledger, obligations, fairness, twin
```

This repository **orchestrates** the live path. The composable decision spine (Keys → Locks → Decision → custody) also lives as four small packages:

- [interconnected_alpha](https://github.com/wking53214/interconnected_alpha) — Keys  
- [interconnected_zeta](https://github.com/wking53214/interconnected_zeta) — Locks  
- [interconnected_beta](https://github.com/wking53214/interconnected_beta) — Decision + narrative  
- [interconnected_delta](https://github.com/wking53214/interconnected_delta) — ledger + obligations + fairness  

Domain runtime with cassettes, episodes, and twin custody: [sentinel_os](https://github.com/wking53214/sentinel_os).

---

## What each stage is (and is not)

| Stage | Question | Not |
|--------|----------|-----|
| **Admission (Gateway)** | Is this a well-formed, sealed, correctly scoped artifact? | Policy permission |
| **OBSERVE** | What can be established about state from evidence? | What may be done about it |
| **PERCEIVE** | Given that state, what do the gates allow? | Human authorization to execute |
| **Conservation** | Did this transformation preserve protected dimensions (or declare changes honestly)? | A soft audit log |
| **Execution** | Run only with a valid approval/receipt | Free action |
| **OBSERVE (post)** | What happened after the act? | Rewriting the decision |

**Policy approval is not authorization.** PERCEIVE may approve a request; Conservation maps decision propositions as machine-originated with authority `NONE` unless explicit `authorization_refs` are present. The stack refuses to blur that line in code.

---

## OBSERVE vs PERCEIVE (inside this repo)

```text
OBSERVE     What is happening?     validate → assess → fuse → evidence-bearing state
PERCEIVE    What may be done?      context → gates → consensus → GovernanceDecision
```

OBSERVE does not authorize. PERCEIVE does not invent OBSERVE’s evidence. Missing evidence is not treated as normality (abstention is first-class).

Reference path: physiological signals → multi-engine assessment → fused clinical state → escalation/policy gates → governed decision. Same separation applies outside clinical domains.

---

## Chain verification

`governance_chain.verify_result` checks, among other things:

- artifact hash matches content  
- every state commitment **recomputes** from the same field set the producer used  
- identifiers agree across request → decision → conservation → approval → execution → outcome  
- time runs forward  
- when kernels are present: decision is in PERCEIVE’s ledger; Conservation entry is a **derivation** from the request artifact (not mere presence under an id)  

`audit_chain_valid` requires the checks that could run to pass **and** the chain to reach an observed outcome (`complete`). An earlier presence-only check of non-empty hash fields was replaced; see `governance_chain.py`.

Adversarial coverage: `test_chain_adversarial.py` and related chain tests.

---

## Invariants

1. **Fail closed** — unknown or unevaluated state does not approve.  
2. **Seams** — observation ≠ policy ≠ authorization ≠ execution ≠ custody.  
3. **Recompute** — commitments and chain links are verified by recalculation.  
4. **Self-report is not evidence** — claims travel separately from observed actuals.  
5. **Conservation** — protected dimensions cannot change unless declared and independently checked.  
6. **Admission ≠ rejection** — Gateway refusal is `NOT_ADMITTED`; policy refusal is `REJECTED`.

---

## Optional stages

| Component | Role |
|-----------|------|
| [Governance_Gateway](https://github.com/wking53214/Governance_Gateway) | Front door: structural validity, provenance, scope |
| [AUGUR](https://github.com/wking53214/AUGUR) | Veto-only behavioural simulation screen |
| [Conservation_Kernel](https://github.com/wking53214/Conservation_Kernel) | Transformation integrity (required for conserved path) |
| [GSA-815](https://github.com/wking53214/GSA-815) | Governed execution under approval |
| [fortress-kernel](https://github.com/wking53214/fortress-kernel) | Optional containment / slew bounds |
| Assurance: [ghost_tools](https://github.com/wking53214/ghost_tools), [SWIZZLE](https://github.com/wking53214/SWIZZLE), [TOUCHSTONE](https://github.com/wking53214/TOUCHSTONE) | Integrity, adversarial eval, non-synthetic ground truth — not the live product path |

---

## Demo: why did the system do that?

One reproducible scenario through the real gate, then deliberate corruption that the chain must catch:

```bash
git clone https://github.com/wking53214/observe-perceive
git clone https://github.com/wking53214/Governance_Gateway
git clone https://github.com/wking53214/AUGUR
python3 -m pip install -r observe-perceive/requirements.txt
python3 -m pip install "git+https://github.com/wking53214/Conservation_Kernel"
cd observe-perceive
python3 demo_why.py
python3 demo_why.py --corrupt approval   # or: source, authority, timestamp
```

Exit 0 means the corruption was detected. Without optional checkouts the demo reports which step it could not run and does not pretend it did.

---

## Status

**In development.** Reference implementation is exercised and tested; packaging and production ops still vary by deployment. Known stack gaps outside this repo include authorization *issuance*, a full identity plane, policy-as-governed-object, external auditor packs, and subject contestability — see sibling kernels and the decision spine for what *is* implemented.

Further architecture notes: `docs/closure/`, `GOVERNANCE_ORCHESTRATION.md`, `CLAUDE_ARCHITECTURE_STATE.md`.

---

## Central proposition

> OBSERVE establishes what can be established about state. PERCEIVE interprets that state under policy gates. Conservation checks whether the transformation preserved what it claimed. The chain recomputes evidence instead of trusting stored strings. Authorization remains a separate concern the policy layer does not claim.
