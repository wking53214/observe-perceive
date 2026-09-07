"""Tests for the Sentinel → PERCEIVE seam.

The orchestrator once called a `perceive.evaluate(GovernanceRequest)` that did
not exist -- PERCEIVE's real entry point is
`evaluate_request(PolicyRequest) -> PolicyVerdict`. The whole 5-system chain
failed at that one line. These tests pin the translation in both directions so
the same drift shows up here, named, instead of as an AttributeError deep in an
orchestration run.
"""

import pytest
from datetime import datetime, timezone

from governance_contracts import (
    GovernanceApproval,
    GovernanceRequest,
    GovernanceRequestType,
)
from perceive_consolidated import (
    PerceiveGovernanceKernel,
    PolicyManifest,
    PolicyRequest,
    PolicyVerdict,
    Provenance,
)
from sentinel_perceive_adapter import SentinelPerceiveAdapter


@pytest.fixture
def perceive():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="seam-test-manifest",
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        policies={},
    ))
    return kernel


def _request(**overrides) -> GovernanceRequest:
    base = dict(
        request_id="req-001",
        request_type=GovernanceRequestType.ESCALATE_PATIENT,
        artifact_id="artifact-001",
        artifact_content="escalate: sustained tachycardia",
        artifact_hash="a" * 64,
        producer="SENTINEL",
        origin="SENTINEL",
        authority="SYSTEM",
        epistemic_status="INFERRED",
        lineage=["parent-001"],
        context={"patient_id": "P001"},
        timestamp=datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc),
    )
    base.update(overrides)
    return GovernanceRequest(**base)


def test_kernel_exposes_evaluate_request_not_evaluate():
    """The exact drift that broke the chain: guard the method name itself."""
    assert hasattr(PerceiveGovernanceKernel, "evaluate_request")
    assert not hasattr(PerceiveGovernanceKernel, "evaluate"), (
        "PERCEIVE grew an `evaluate` method -- confirm which one the adapter "
        "should drive rather than letting the orchestrator pick silently"
    )


def test_request_type_values_line_up_with_perceives_gate_selection():
    """GovernanceRequestType's values are the literals PERCEIVE's
    `_select_gates` matches on. If either side is renamed independently, gate
    selection silently falls back to the boundary gate alone -- an approval
    that skipped every real check."""
    for request_type in (
        GovernanceRequestType.ESCALATE_PATIENT,
        GovernanceRequestType.MODIFY_RULE,
        GovernanceRequestType.EXPORT_DATA,
        GovernanceRequestType.EMERGENCY_OVERRIDE,
    ):
        gates = PerceiveGovernanceKernel._select_gates(
            PolicyRequest(
                request_id="x",
                request_type=request_type.value,
                subject_id="s",
                actor_id="a",
            )
        )
        assert len(gates) > 1, (
            f"{request_type.value} selected only {gates} -- it is not a "
            "request type PERCEIVE recognises"
        )


def test_governance_request_converts_to_a_policy_request():
    request = _request()
    policy_request = SentinelPerceiveAdapter.governance_request_to_policy_request(request)

    assert isinstance(policy_request, PolicyRequest)
    assert policy_request.request_id == "req-001"
    assert policy_request.request_type == "escalate_patient"  # value, not the enum
    assert policy_request.subject_id == "P001"                # from context
    assert policy_request.actor_id == "SENTINEL"
    assert policy_request.state_commitment == request.state_commitment


def test_the_requests_own_timestamp_is_passed_through_for_determinism():
    """PERCEIVE uses the request timestamp as the reference time for its
    rate-limit windows. Dropping it means evaluation falls back to wall-clock
    and the same request can decide differently on a re-run."""
    request = _request()
    policy_request = SentinelPerceiveAdapter.governance_request_to_policy_request(request)
    assert policy_request.timestamp == datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


def test_subject_falls_back_to_the_artifact_when_context_names_none():
    request = _request(context={})
    policy_request = SentinelPerceiveAdapter.governance_request_to_policy_request(request)
    assert policy_request.subject_id == "artifact-001"


def _verdict(**overrides) -> PolicyVerdict:
    base = dict(
        request_id="req-001",
        approved=True,
        confidence=0.9,
        violations=[],
        applied_gates=["boundary_gate", "sentinel"],
        policy_version="1.0.0",
        provenance=Provenance("SENTINEL", "1.0.0", "Consensus of 2 gates"),
        audit_hash="f" * 64,
        state_commitment="c" * 64,
    )
    base.update(overrides)
    return PolicyVerdict(**base)


def test_unanimous_consensus_reads_approval_not_the_optional_dgk_result():
    """The subtle one. PERCEIVE's ConsensusEngine approves only when every
    applied gate approved, so `approved is True` IS the unanimity claim.
    `consensus_result` is the *separate*, optional DGK multi-node consensus
    that runs only for critical request types and is None on the ordinary
    path. Reading it as the unanimity signal reports every normal unanimous
    approval as non-unanimous."""
    verdict = _verdict(approved=True, consensus_result=None)
    decision = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, _request())

    assert decision.unanimous_consensus is True
    assert decision.approval is GovernanceApproval.APPROVED


def test_a_rejected_verdict_carries_its_violations_across():
    verdict = _verdict(
        approved=False,
        violations=["sentinel: anomaly score above threshold"],
        applied_gates=["boundary_gate", "sentinel"],
    )
    decision = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, _request())

    assert decision.approval is GovernanceApproval.REJECTED
    assert decision.unanimous_consensus is False
    assert decision.violations == ["sentinel: anomaly score above threshold"]


def test_decision_id_is_derived_so_the_same_verdict_is_reproducible():
    verdict = _verdict()
    first = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, _request())
    second = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, _request())
    assert first.decision_id == second.decision_id
    assert first.perceive_audit_hash == verdict.audit_hash


def test_perceives_state_commitment_is_carried_not_recomputed():
    """PERCEIVE already chains its commitment onto the request's. Recomputing
    a parallel one here would break the chain the downstream Conservation
    Kernel verifies."""
    verdict = _verdict(state_commitment="deadbeef" * 8)
    decision = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, _request())
    assert decision.state_commitment == "deadbeef" * 8


def test_full_seam_against_the_real_kernel(perceive):
    """End to end through the actual PERCEIVE kernel, not a stand-in."""
    decision = SentinelPerceiveAdapter.evaluate_through_perceive(perceive, _request())

    assert decision.request_id == "req-001"
    assert decision.approval in (GovernanceApproval.APPROVED, GovernanceApproval.REJECTED)
    assert decision.applied_gates, "no gates were applied -- gate selection missed"
    assert decision.perceive_audit_hash, "no audit hash -- the decision is unverifiable"
    assert decision.policy_version == "1.0.0"


def test_the_seam_returns_what_the_next_adapter_consumes(perceive):
    """The downstream PerceiveConservationAdapter reads these attributes off
    whatever Phase 2 produced. A PolicyVerdict has none of them, which is how
    the original break stayed hidden until runtime."""
    decision = SentinelPerceiveAdapter.evaluate_through_perceive(perceive, _request())
    for attribute in (
        "request_id", "decision_id", "approval", "applied_gates",
        "unanimous_consensus", "violations", "policy_version", "timestamp",
    ):
        assert hasattr(decision, attribute), f"downstream adapter needs .{attribute}"
    assert hasattr(decision.approval, "value"), "approval must be the enum, not a bool"
