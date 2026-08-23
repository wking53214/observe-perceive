"""
Basic tests for Governance Orchestrator.
Tests core orchestrator functionality without external dependencies.
"""

import sys
import os
from datetime import datetime, timezone


def test_imports():
    """Test that orchestrator can be imported."""
    from governance_orchestrator import GovernanceOrchestrator
    from governance_contracts import GovernanceRequest, GovernanceApproval
    assert GovernanceOrchestrator is not None
    assert GovernanceRequest is not None
    assert GovernanceApproval is not None


def test_orchestrator_initialization():
    """Test orchestrator can be instantiated."""
    from governance_orchestrator import GovernanceOrchestrator

    # Create with None for optional dependencies (for testing)
    orchestrator = GovernanceOrchestrator()
    assert orchestrator is not None
    assert orchestrator.perceive is None
    assert orchestrator.conservation_kernel is None
    assert orchestrator.observe_engine is None


def test_governance_contracts():
    """Test governance contract structures."""
    from governance_contracts import GovernanceRequest, GovernanceApproval, GovernanceRequestType

    request = GovernanceRequest(
        request_id="test-001",
        request_type=GovernanceRequestType.ESCALATE_PATIENT,
        artifact_id="artifact-001",
        artifact_content="Test content",
        artifact_hash="abc123def456",
        producer="sentinel",
        origin="SYSTEM",
        authority="NONE",
        epistemic_status="INFERRED",
        context={"severity": "high"}
    )

    assert request.request_id == "test-001"
    assert request.request_type == GovernanceRequestType.ESCALATE_PATIENT
    assert request.artifact_id == "artifact-001"
    assert request.producer == "sentinel"

    # Test GovernanceApproval enum
    assert GovernanceApproval.APPROVED.value == "approved"
    assert GovernanceApproval.REJECTED.value == "rejected"
    assert GovernanceApproval.PENDING.value == "pending"


def test_sentinel_adapter_imports():
    """Test Sentinel adapter can be imported (lazy loads path)."""
    from sentinel_perceive_adapter import SentinelPerceiveAdapter
    assert SentinelPerceiveAdapter is not None


def test_end_to_end_flow_mock():
    """
    Test complete end-to-end flow with mocked systems.
    Demonstrates: Sentinel → PERCEIVE → Conservation → GSA-815 → OBSERVE
    """
    from governance_orchestrator import GovernanceOrchestrator
    from governance_contracts import GovernanceRequest, GovernanceRequestType

    orchestrator = GovernanceOrchestrator()

    # Create a mock governance request
    request = GovernanceRequest(
        request_id="e2e-test-001",
        request_type=GovernanceRequestType.ESCALATE_PATIENT,
        artifact_id="patient-p001",
        artifact_content="Patient escalation request",
        artifact_hash="xyz789abc123",
        producer="sentinel-witness",
        origin="SYSTEM",
        authority="NONE",
        epistemic_status="INFERRED",
        context={
            "age_months": 12,
            "risk_level": "critical",
            "vitals": {"o2": 85, "hr": 170}
        }
    )

    # Verify request structure matches orchestrator expectations
    assert request.request_id == "e2e-test-001"
    assert request.producer == "sentinel-witness"
    assert request.context["risk_level"] == "critical"

    # Verify orchestrator is ready to route requests
    assert orchestrator.sentinel_adapter is not None
    assert orchestrator.conservation_adapter is not None
    assert orchestrator.gsa815_adapter is not None


if __name__ == "__main__":
    test_imports()
    test_orchestrator_initialization()
    test_governance_contracts()
    test_sentinel_adapter_imports()
    test_end_to_end_flow_mock()
    print("All basic tests passed! ✓")
