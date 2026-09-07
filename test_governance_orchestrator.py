"""
Tests for Governance Orchestrator

Verifies complete flow:
1. Sentinel → PERCEIVE → Conservation → GSA-815 → OBSERVE
2. Audit chain linking
3. Unanimous consensus
4. Fail-closed on rejection
"""

import pytest
from datetime import datetime, timezone
from governance_orchestrator import GovernanceOrchestrator
from governance_contracts import GovernanceApproval
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from conservation_kernel import ConservationKernel


@pytest.fixture
def perceive():
    manifest = PolicyManifest(
        manifest_id="test-manifest",
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        policies={}
    )
    p = PerceiveGovernanceKernel()
    p.register_manifest(manifest)
    return p


@pytest.fixture
def conservation_kernel():
    return ConservationKernel()


@pytest.fixture
def observe_engine():
    return ObserveClinicalEngine()


@pytest.fixture
def orchestrator(perceive, conservation_kernel, observe_engine):
    return GovernanceOrchestrator(perceive, conservation_kernel, observe_engine)


class TestGovernanceOrchestration:
    """Test complete governance orchestration."""

    def test_complete_approval_flow(self, orchestrator):
        """Test: artifact flows through all systems and is approved."""
        # Create mock artifact
        class MockArtifact:
            def __init__(self):
                self.artifact_id = "test-001"
                self.content = "Test governance request"
                self.metadata = MockMetadata()

        class MockMetadata:
            def __init__(self):
                self.origin_status = MockStatus("SENTINEL")
                self.authority_status = MockStatus("SYSTEM")
                self.epistemic_status = MockStatus("INFERRED")
                self.parent_artifact_ids = []

        class MockStatus:
            def __init__(self, value):
                self._value = value

            @property
            def value(self):
                return self._value

        artifact = MockArtifact()

        # Mock GSA-815 operation
        def mock_operation(context):
            return {"status": "executed", "result": "success"}

        # Execute orchestration
        result = orchestrator.orchestrate_request(
            artifact,
            "escalate",
            mock_operation,
            context={"patient_id": "P001"}
        )

        # Verify flow
        assert result["status"] in ["APPROVED_AND_EXECUTED", "REJECTED"]

    def test_unanimous_consensus_required(self, orchestrator):
        """Test: PERCEIVE requires unanimous consensus."""
        class MockArtifact:
            def __init__(self):
                self.artifact_id = "test-unanimous"
                self.content = "Test unanimous"
                self.metadata = MockMetadata()

        class MockMetadata:
            def __init__(self):
                self.origin_status = MockStatus("SENTINEL")
                self.authority_status = MockStatus("SYSTEM")
                self.epistemic_status = MockStatus("INFERRED")
                self.parent_artifact_ids = []

        class MockStatus:
            def __init__(self, value):
                self._value = value

            @property
            def value(self):
                return self._value

        artifact = MockArtifact()

        def mock_operation(context):
            return {"status": "executed"}

        result = orchestrator.orchestrate_request(
            artifact,
            "escalate",
            mock_operation
        )

        # This used to sit behind an `if status == APPROVED_AND_EXECUTED`
        # guard that was never true (the chain broke earlier, at the PERCEIVE
        # and Conservation seams), so the assertion never ran and the test
        # passed vacuously. Asserted unconditionally now, against the
        # GovernanceDecision contract the chain actually carries -- the old
        # assertion was reading `.approved`, a PolicyVerdict field that never
        # reaches this far.
        assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
        perceive_decision = result["governance_decision"]
        assert perceive_decision.approval is GovernanceApproval.APPROVED
        # Unanimity is the claim: PERCEIVE approves only when every applied
        # gate approved.
        assert perceive_decision.unanimous_consensus is True
        assert len(perceive_decision.applied_gates) > 1, (
            "only the always-on boundary gate ran -- gate selection did not "
            "recognise this request type"
        )
        assert perceive_decision.violations == []

    def test_conservation_kernel_verification(self, orchestrator):
        """Test: Conservation Kernel must verify decision."""
        class MockArtifact:
            def __init__(self):
                self.artifact_id = "test-conservation"
                self.content = "Test conservation"
                self.metadata = MockMetadata()

        class MockMetadata:
            def __init__(self):
                self.origin_status = MockStatus("SENTINEL")
                self.authority_status = MockStatus("SYSTEM")
                self.epistemic_status = MockStatus("INFERRED")
                self.parent_artifact_ids = []

        class MockStatus:
            def __init__(self, value):
                self._value = value

            @property
            def value(self):
                return self._value

        artifact = MockArtifact()

        def mock_operation(context):
            return {"status": "executed"}

        result = orchestrator.orchestrate_request(
            artifact,
            "escalate",
            mock_operation
        )

        # Unconditional: the guard this used to sit behind was never true, so
        # the Conservation Kernel's verification was never actually asserted.
        assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
        conservation_decision = result["conservation_decision"]
        assert conservation_decision.verified
        assert conservation_decision.approval == GovernanceApproval.APPROVED
        assert conservation_decision.conservation_receipt_id, (
            "no receipt id -- the kernel did not return a verifiable result"
        )
        assert conservation_decision.conservation_audit_hash

    def test_audit_chain_linking(self, orchestrator):
        """Test: All audit hashes are linked."""
        class MockArtifact:
            def __init__(self):
                self.artifact_id = "test-audit-chain"
                self.content = "Test audit"
                self.metadata = MockMetadata()

        class MockMetadata:
            def __init__(self):
                self.origin_status = MockStatus("SENTINEL")
                self.authority_status = MockStatus("SYSTEM")
                self.epistemic_status = MockStatus("INFERRED")
                self.parent_artifact_ids = []

        class MockStatus:
            def __init__(self, value):
                self._value = value

            @property
            def value(self):
                return self._value

        artifact = MockArtifact()

        def mock_operation(context):
            return {"status": "executed"}

        # Provide vitals for OBSERVE
        vitals = VitalsSnapshot(
            patient_id="P001",
            timestamp=datetime.now(timezone.utc),
            heart_rate=120,
            oxygen_saturation=95,
            respiratory_rate=20,
            temperature=37.5
        )

        result = orchestrator.orchestrate_request(
            artifact,
            "escalate",
            mock_operation,
            vitals_snapshot=vitals
        )

        # Unconditional, and the nested `if forensic_proof:` is gone too --
        # between the two guards this test asserted nothing at all, which is
        # the worst place in the suite for a vacuous test: the audit chain is
        # the whole forensic claim the chain exists to make.
        assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
        forensic_proof = result["forensic_proof"]
        assert forensic_proof is not None, (
            "no forensic proof was produced -- vitals were supplied, so "
            "OBSERVE ran and the chain should have been linked"
        )
        # Every stage's hash has to be present for the chain to be traceable
        # end to end.
        assert forensic_proof["governance_audit_hash"]
        assert forensic_proof["conservation_audit_hash"]
        assert forensic_proof["result_artifact_hash"]
        assert len(forensic_proof["lineage"]) > 0
        assert forensic_proof["chain_valid"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
