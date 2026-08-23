# PERCEIVE Governance Orchestration

Central orchestrator integrating all five governance systems into a unified flow.

## Architecture

```
Sentinel OS (artifact creation)
    ↓ (SentinelPerceiveAdapter)
PERCEIVE (6-gate unanimous consensus)
    ├─ CITADEL gate (intent validation)
    ├─ FORTRESS gate (content safety)
    ├─ Sentinel gate (anomaly detection)
    ├─ Boundary gate
    ├─ Invariant validator
    └─ Micropatch (emergency override)
    ↓ (PerceiveConservationAdapter)
Conservation Kernel (verification)
    ├─ Artifact verification
    ├─ Hash correspondence
    ├─ Provenance preservation
    └─ Audit chain
    ↓ (ConservationGSA815Adapter)
GSA-815 (approved execution)
    ↓ (GSA815ObserveAdapter)
OBSERVE (clinical monitoring)
    ↓
Complete audit chain linking
```

## Request Flow

### 1. Sentinel → PERCEIVE

**Class:** `SentinelPerceiveAdapter`

Converts Sentinel artifacts to PERCEIVE governance requests.

**Input:**
- SentinelArtifact
- Operation type (escalate, modify, export, override)
- Context (patient_id, risk_level, etc.)

**Output:**
- GovernanceRequest with:
  - Request ID
  - Request type
  - Artifact content and hash
  - Producer/origin/authority/epistemic status
  - Lineage
  - Context

**Contract:** Preserves all artifact identity, provenance, authority, epistemic state.

### 2. PERCEIVE Evaluation

**Class:** `PERCEIVE`

Evaluates request through 6 gates with unanimous consensus.

**Gates:**
1. CITADEL — intent validation
2. FORTRESS — content safety
3. Sentinel — anomaly detection
4. Boundary gate
5. Invariant validator
6. Micropatch — emergency override

**Policy Enforcement:**
- Escalation policy (rate limits, cooldowns)
- Rule modification policy (approvals, temporal locks)
- Data export policy (consent, encryption)
- Emergency override policy (multi-node consensus)

**Output:**
- GovernanceDecision with:
  - Approval status (approved/rejected/pending)
  - Applied gates
  - Unanimous consensus flag
  - Violations (if rejected)
  - Policy version
  - PERCEIVE audit hash

### 3. PERCEIVE → Conservation Kernel

**Class:** `PerceiveConservationAdapter`

Submits PERCEIVE decisions to Conservation Kernel for verification.

**Input:**
- GovernanceDecision
- Input artifact ID/content/hash

**Process:**
1. Create Conservation Kernel Artifact for the decision
2. Create TransformationRecord linking input to decision
3. Submit to kernel.submit()
4. Fail closed if kernel rejects

**Output:**
- ConservationDecision with:
  - Governance decision ID
  - Approval status
  - Conservation receipt ID
  - Verified flag
  - Conservation audit hash

**Contract:** All decisions must pass through Kernel. No bypass possible.

### 4. Conservation Decision → GSA-815

**Class:** `ConservationGSA815Adapter`

Gates GSA-815 execution on verified PERCEIVE decisions.

**Input:**
- ConservationDecision (must be approved and verified)
- Artifact details for execution

**Process:**
1. Verify conservation decision is approved
2. Verify conservation decision is verified
3. Create ExecutionApproval
4. Create ExecutionContext

**Output:**
- ExecutionApproval with:
  - Approval status
  - Conservation decision ID
  - Conservation receipt ID
  - Governance audit hash
  - Conservation audit hash

**Contract:** GSA-815 cannot execute without valid ExecutionApproval. Fail-closed on rejection.

### 5. GSA-815 Execution + OBSERVE Monitoring

**Class:** `GSA815ObserveAdapter`

Links governance decisions to clinical outcomes.

**Input:**
- ExecutionContext
- GSA-815 execution result
- OBSERVE clinical verdict

**Process:**
1. Create OutcomeContext linking all decisions
2. Verify complete governance chain
3. Create forensic proof for replay

**Output:**
- OutcomeContext with:
  - Execution ID
  - Governance/conservation decision IDs
  - All audit hashes
  - Result artifact ID and hash
  - Complete lineage
  - Outcome data

**Forensic Proof:**
- All audit hashes linked
- Complete lineage
- Timestamp
- Chain validity flag

## Central Orchestrator

**Class:** `GovernanceOrchestrator`

Routes complete request through all five systems.

### Usage

```python
from governance_orchestrator import GovernanceOrchestrator
from perceive_consolidated import PERCEIVE
from conservation_kernel import ConservationKernel
from observe_consolidated import ObserveClinicalEngine

# Initialize systems
perceive = PERCEIVE()
kernel = ConservationKernel()
observe = ObserveClinicalEngine()

# Create orchestrator
orchestrator = GovernanceOrchestrator(perceive, kernel, observe)

# Execute complete flow
result = orchestrator.orchestrate_request(
    artifact=sentinel_artifact,
    operation_type="escalate",
    gsa815_operation_func=my_gsa815_operation,
    vitals_snapshot=vitals,
    context={"patient_id": "P001"}
)

# Result contains:
# - governance_decision (PERCEIVE verdict)
# - conservation_decision (Kernel verification)
# - execution_approval (GSA-815 gate)
# - gsa815_result (actual execution result)
# - observe_verdict (clinical assessment)
# - forensic_proof (complete audit chain)
```

### Result Structure

```python
{
    "status": "APPROVED_AND_EXECUTED",
    "governance_decision": {...},  # PERCEIVE decision
    "conservation_decision": {...},  # Kernel verification
    "execution_approval": {...},  # GSA-815 approval
    "execution_context": {...},  # Execution details
    "gsa815_result": {...},  # Execution result
    "observe_verdict": {...},  # Clinical verdict
    "outcome_context": {...},  # Outcome linking
    "forensic_proof": {
        "execution_id": "...",
        "governance_decision_id": "...",
        "conservation_decision_id": "...",
        "governance_audit_hash": "...",
        "conservation_audit_hash": "...",
        "result_artifact_hash": "...",
        "lineage": [...],
        "outcome": {...},
        "chain_valid": true,
    }
}
```

## Audit Chain Linking

All decisions are linked through SHA256-chained audit hashes:

1. **Sentinel audit hash** — Original artifact and operation
2. **PERCEIVE audit hash** — Governance evaluation and gates
3. **Conservation audit hash** — Kernel verification
4. **GSA-815 execution hash** — Approved operation and result
5. **OBSERVE audit hash** — Clinical assessment

Complete chain enables forensic replay: any modification breaks the chain.

## Fail-Closed Behavior

- PERCEIVE rejection → stop, no conservation
- Conservation rejection → stop, no GSA-815
- GSA-815 gate fails → stop, no execution
- Audit chain missing → outcome not verified

## Testing

See `test_governance_orchestrator.py` for:
- Complete approval flow test
- Unanimous consensus verification
- Conservation Kernel verification
- Audit chain linking

## Integration with Existing Systems

### Sentinel OS Integration

Create `sentinel_os/governance/perceive_adapter.py` to wire Sentinel artifacts into orchestrator.

### GSA-815 Integration

Create `GSA-815/governance/perceive_gate.py` to enforce ExecutionApproval before operation.

### OBSERVE Integration

OBSERVE receives GovernanceContext from GSA-815 adapter for outcome monitoring.
