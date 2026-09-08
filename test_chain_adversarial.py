"""
Attack the recorded chain (Phase 10 of the repair mission).

Every test introduces one controlled violation and asserts the expected
outcome: DETECTED by `governance_chain.verify_result`, or REJECTED by the
chain before execution. A violation the chain accepted would be a finding,
and the test would fail.

The record under attack is a real APPROVED_AND_EXECUTED result: real PERCEIVE,
real Conservation Kernel, real OBSERVE engine, with vitals so the chain reaches
an outcome.
"""
from datetime import timedelta

import pytest

from conftest import run_chain
from conservation_kernel.errors import LedgerError
from governance_chain import verify_result
from governance_contracts import (
    ConservationDecision, GovernanceApproval, GovernanceDecision, GovernanceRequest,
    GovernanceRequestType, compute_state_commitment,
)
from governance_chain import request_state
from conservation_gsa815_adapter import ConservationGSA815Adapter
from perceive_conservation_adapter import ConservationRefusal, PerceiveConservationAdapter


@pytest.fixture
def executed(orchestrator):
    result, ran = run_chain(orchestrator, "attack-base")
    assert result["status"] == "APPROVED_AND_EXECUTED" and ran
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]
    return result


def _failed(result, orchestrator):
    return set(verify_result(result, kernel=orchestrator.conservation_kernel, perceive=orchestrator.perceive).as_dict()["failed"])


# 1. change source evidence -----------------------------------------------------

def test_1_source_evidence_changed_after_hashing_is_refused_by_conservation(perceive, kernel):
    """In-chain: content altered between hashing and verification."""
    adapter = PerceiveConservationAdapter(kernel)
    decision = GovernanceDecision(
        request_id="r", decision_id="d1", approval=GovernanceApproval.APPROVED,
        applied_gates=[], unanimous_consensus=True, violations=[], policy_version="0", perceive_audit_hash="00",
    )
    with pytest.raises(ConservationRefusal, match="hash mismatch"):
        adapter.verify_perceive_decision(decision, "art-1", "content that was altered", "ab" * 32)


def test_1b_source_evidence_changed_in_the_record_is_detected(executed, orchestrator):
    executed["governance_request"].artifact_content = "something else"
    failed = _failed(executed, orchestrator)
    assert "request.artifact_hash_matches_content" in failed


# 2 and 4. change provenance / authority ------------------------------------------

@pytest.mark.parametrize("field,value", [("authority", "ROOT"), ("origin", "ATTACKER"), ("producer", "ATTACKER"), ("epistemic_status", "EXPLICIT")])
def test_2_provenance_or_authority_rewritten_breaks_the_request_commitment(executed, orchestrator, field, value):
    setattr(executed["governance_request"], field, value)
    assert "request.state_commitment" in _failed(executed, orchestrator)


# 3 and 15. alter timestamp / inject future knowledge ------------------------------

def test_3_outcome_dated_before_the_decision_is_detected(executed, orchestrator):
    executed["outcome_context"].timestamp = executed["governance_decision"].timestamp - timedelta(hours=1)
    assert "time.monotonic" in _failed(executed, orchestrator)


def test_15_altering_the_outcome_cannot_change_what_the_decision_committed_to(executed, orchestrator):
    """Future knowledge cannot leak backwards: the request and decision
    commitments are computed over fields that exist before execution, so
    changing the outcome leaves them intact and only the outcome link fails."""
    before = (executed["governance_request"].state_commitment, executed["governance_decision"].state_commitment)
    executed["outcome_context"].outcome["observe_regime"] = "STABLE_FORGED"
    failed = _failed(executed, orchestrator)
    assert "outcome.state_commitment" in failed
    assert "request.state_commitment" not in failed and "decision.in_perceive_ledger" not in failed
    assert (executed["governance_request"].state_commitment, executed["governance_decision"].state_commitment) == before


# 5. alter prediction -------------------------------------------------------------------

def test_5_a_prediction_is_not_a_permission_and_cannot_become_one_after_the_fact(executed, orchestrator):
    """The simulation screen is veto-only and sits outside the permission
    chain. Rewriting it after the fact changes nothing the chain committed to,
    and the recorded permission is still PERCEIVE's, verifiable in its ledger."""
    executed["simulation_screen"] = {"proceed": True, "screened": True, "reason": "forged approval"}
    failed = _failed(executed, orchestrator)
    assert not failed, failed
    assert executed["governance_decision"].approval is GovernanceApproval.APPROVED
    assert executed["observe_verdict"] is not executed["simulation_screen"]


# 6. alter permission ---------------------------------------------------------------------

def test_6_flipping_the_recorded_permission_is_caught_against_perceive_ledger(executed, orchestrator):
    executed["governance_decision"].approval = GovernanceApproval.REJECTED
    assert "decision.matches_perceive_ledger" in _failed(executed, orchestrator)


def test_6b_adding_or_removing_violations_is_caught(executed, orchestrator):
    executed["governance_decision"].violations = ["invented after the fact"]
    assert "decision.matches_perceive_ledger" in _failed(executed, orchestrator)


# 7 and 8. alter execution record / outcome -------------------------------------------

def test_7_altering_the_execution_result_is_detected(executed, orchestrator):
    executed["gsa815_result"] = {"status": "done", "artifact_id": "someone-else"}
    assert "outcome.result_artifact_hash" in _failed(executed, orchestrator)


def test_8_altering_the_recorded_outcome_is_detected(executed, orchestrator):
    executed["outcome_context"].outcome["escalation_required"] = not executed["outcome_context"].outcome["escalation_required"]
    assert "outcome.state_commitment" in _failed(executed, orchestrator)


# 9. replay stale state -------------------------------------------------------------------

def test_9_replaying_the_same_artifact_is_refused_by_the_kernel(orchestrator):
    first, _ = run_chain(orchestrator, "replay-1")
    assert first["status"] == "APPROVED_AND_EXECUTED"
    second, ran = run_chain(orchestrator, "replay-1")
    assert second["status"] == "REJECTED" and not ran
    assert second["refused_by"] == "conservation" and second["stage_refused"] is True
    assert "duplicate" in second["reason"].lower() or "refused" in second["reason"].lower()


# 10. substitute an artifact ---------------------------------------------------------------

def test_10_substituting_the_artifact_under_an_approval_is_detected(executed, orchestrator):
    executed["execution_context"].artifact_id = "a-different-artifact"
    failed = _failed(executed, orchestrator)
    assert {"execution_context.artifact", "execution_context.state_commitment"} <= failed


# 11. inject an unauthorized artifact -------------------------------------------------------

def test_11_read_only_scope_is_refused_before_execution(orchestrator):
    result, ran = run_chain(orchestrator, "ro-1", context={"gateway_scope": "READ_ONLY"})
    assert result["status"] == "REJECTED" and not ran and result["scope_enforced"] is True


def test_11b_undeclared_scope_is_refused_in_strict_mode(perceive, kernel):
    from governance_orchestrator import GovernanceOrchestrator
    from observe_consolidated import ObserveClinicalEngine
    strict = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), require_declared_scope=True)
    result, ran = run_chain(strict, "undeclared-1")
    assert result["status"] == "REJECTED" and not ran and result["scope_enforced"] is False


# 12. remove an intermediate record ----------------------------------------------------------

@pytest.mark.parametrize("record,check", [
    ("conservation_decision", "conservation.present"),
    ("execution_approval", "approval.present"),
    ("execution_context", "execution_context.present"),
    ("governance_decision", "decision.present"),
])
def test_12_removing_an_intermediate_record_is_detected(executed, orchestrator, record, check):
    executed[record] = None
    assert check in _failed(executed, orchestrator)


# 13. change an artifact after sealing (Gateway) ----------------------------------------------

def test_13_a_sealed_gateway_artifact_altered_after_sealing_is_not_admitted():
    """The Gateway artifact is a frozen dataclass sealed with a digest. Rebuild
    it with a different payload and the original seal: the seal no longer
    covers what it claims to cover, and admission must refuse."""
    import dataclasses
    gw = pytest.importorskip("gateway_admission_adapter")
    adapter = gw.GatewayAdmissionAdapter()
    sealed = adapter.seal(
        artifact_id="sealed-1", payload={"order": "escalate"},
        provenance={"source": "test"}, epistemic_status=gw.GatewayEpistemicStatus.INFERENCE,
        authority_actor="clinician", authority_grant="unit-charge", execute=True,
    )
    assert adapter.admit(sealed).admitted
    tampered = dataclasses.replace(sealed, payload={"order": "discharge"})
    admission = adapter.admit(tampered)
    assert not admission.admitted, "payload changed after sealing but the seal still admitted it"


# 14. direct downstream execution: a forged approval ------------------------------------------

def test_14_an_approval_built_by_hand_verifies_structurally_but_not_against_the_ledgers(orchestrator, kernel, perceive):
    """Everything self-consistent, nothing ever went through a kernel."""
    request = GovernanceRequest(
        request_id="forged", request_type=GovernanceRequestType.ESCALATE_PATIENT, artifact_id="forged",
        artifact_content="escalate P001", artifact_hash=__import__("hashlib").sha256(b"escalate P001").hexdigest(),
        producer="SENTINEL", origin="SENTINEL", authority="SYSTEM", epistemic_status="INFERRED",
    )
    request.state_commitment = compute_state_commitment(None, request_state(request))
    decision = GovernanceDecision(
        request_id="forged", decision_id="forged-d", approval=GovernanceApproval.APPROVED,
        applied_gates=[], unanimous_consensus=True, violations=[], policy_version="0", perceive_audit_hash="00",
    )
    conservation = ConservationDecision(
        governance_decision_id="forged-d", approval=GovernanceApproval.APPROVED,
        conservation_receipt_id="never-issued", artifact_hash=request.artifact_hash,
        conservation_audit_hash="ab" * 32, verified=True,
    )
    approval = ConservationGSA815Adapter.approve_execution(conservation, "forged", request.artifact_hash, "SENTINEL", [], execution_id="exec-forged")
    context = ConservationGSA815Adapter.create_execution_context(approval, "forged", request.artifact_hash, "GSA-815", [])
    forged = {
        "status": "APPROVED_AND_EXECUTED", "governance_request": request, "governance_decision": decision,
        "conservation_decision": conservation, "execution_approval": approval, "execution_context": context,
        "outcome_context": None, "gsa815_result": {"status": "done"},
    }
    structural = verify_result(forged)
    assert structural.valid, structural.as_dict()["failed"]  # the forgery is internally consistent
    against_ledgers = verify_result(forged, kernel=kernel, perceive=perceive)
    assert {"decision.in_perceive_ledger", "conservation.in_kernel_ledger"} <= set(against_ledgers.as_dict()["failed"])


# storage refusal surfaces as a refusal, not a crash ---------------------------------------------

def test_kernel_ledger_refusal_is_recorded_as_a_conservation_refusal(orchestrator, monkeypatch):
    def refuse(*a, **k):
        raise LedgerError("simulated ledger refusal")
    monkeypatch.setattr(orchestrator.conservation_kernel, "submit", refuse)
    result, ran = run_chain(orchestrator, "ledger-refuse")
    assert result["status"] == "REJECTED" and not ran
    assert result["refused_by"] == "conservation" and result["stage_refused"] is True
