"""
Integration Tests: PERCEIVE → FORTRESS → Conservation Chain
============================================================

Tests complete governance flow with FORTRESS safety orchestration
between PERCEIVE and downstream verification.
"""

import pytest

# The adapter raises a plain ImportError (not ModuleNotFoundError) when the
# pack is absent, which recent pytest's importorskip no longer treats as a
# skip; guard it explicitly so a kernel-only install skips instead of erroring.
try:
    from fortress_perceive_adapter import FortressPerceiveAdapter, FortressProcessingResult
except ImportError:  # pragma: no cover - fortress-kernel is an optional pack
    pytest.skip("fortress-kernel checkout not available", allow_module_level=True)


class TestFortressEnergyMode:
    """Test FORTRESS in energy mode."""

    def test_basic_processing(self):
        """Test basic request processing."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        perceive_decision = {
            "error_signal": 5.0,
            "live_signal": 100.0,
        }

        result = adapter.process_governance_request(
            request_id="req-001",
            perceive_decision=perceive_decision,
            artifact_content="Test artifact",
            artifact_metadata={
                "origin": "sentinel-123",
                "signature": "sig-valid"
            }
        )

        assert isinstance(result, FortressProcessingResult)
        assert result.request_id == "req-001"
        assert result.controller == "ENERGY"
        assert result.decision in ["APPROVED", "REJECTED"]

    def test_low_error_approves(self):
        """Low error should approve."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        result = adapter.process_governance_request(
            request_id="req-002",
            perceive_decision={"error_signal": 2.0, "live_signal": 100.0},
            artifact_content="Safe operation",
            artifact_metadata={"origin": "sentinel-123", "signature": "valid"}
        )

        assert result.decision == "APPROVED"
        assert result.approval_confidence > 0.8

    def test_high_error_rejects(self):
        """High error should have higher distortion."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        # Low error baseline
        result_low = adapter.process_governance_request(
            request_id="req-low",
            perceive_decision={"error_signal": 2.0, "live_signal": 100.0},
            artifact_content="Low risk",
            artifact_metadata={"origin": "sentinel-123", "signature": "valid"}
        )

        # High error
        result_high = adapter.process_governance_request(
            request_id="req-high",
            perceive_decision={"error_signal": 35.0, "live_signal": 100.0},
            artifact_content="High risk",
            artifact_metadata={"origin": "sentinel-123", "signature": None}
        )

        # High error should produce higher distortion
        assert result_high.distortion >= result_low.distortion

    def test_audit_trail_integrity(self):
        """Verify audit trail integrity."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        # Process multiple requests
        for i in range(3):
            adapter.process_governance_request(
                request_id=f"req-{i:03d}",
                perceive_decision={"error_signal": 5.0, "live_signal": 100.0},
                artifact_content=f"Artifact {i}",
                artifact_metadata={"origin": "sentinel", "signature": "valid"}
            )

        # Verify audit integrity
        assert adapter.verify_audit_integrity()
        assert len(adapter.get_audit_trail()) >= 3


class TestFortressLyapunovMode:
    """Test FORTRESS in Lyapunov mode."""

    def test_stability_analysis(self):
        """Test Lyapunov stability analysis."""
        adapter = FortressPerceiveAdapter(controller_mode="lyapunov")

        result = adapter.process_governance_request(
            request_id="req-lya-001",
            perceive_decision={"error_signal": 8.0, "live_signal": 100.0},
            artifact_content="Stability test",
            artifact_metadata={
                "origin": "sentinel-456",
                "signature": "sig-valid"
            }
        )

        assert result.controller == "LYAPUNOV"
        assert result.authority is not None

    def test_stress_with_verification(self):
        """Test with provenance verification."""
        adapter = FortressPerceiveAdapter(controller_mode="lyapunov")

        # Verified artifact
        result_verified = adapter.process_governance_request(
            request_id="req-lya-002",
            perceive_decision={"error_signal": 5.0, "live_signal": 100.0},
            artifact_content="Verified artifact",
            artifact_metadata={
                "origin": "sentinel-456",
                "signature": "valid-sig"
            },
            artifact_hash="sha256-def456"
        )

        # Unverified artifact
        result_unverified = adapter.process_governance_request(
            request_id="req-lya-003",
            perceive_decision={"error_signal": 5.0, "live_signal": 100.0},
            artifact_content="Unverified artifact",
            artifact_metadata={
                "origin": "unknown",
                "signature": None
            }
        )

        assert result_verified.integrity == "VERIFIED"
        assert result_unverified.integrity == "UNVERIFIED"


class TestFortressSAGEMode:
    """Test FORTRESS in SAGE mode."""

    def test_multi_agent_learning(self):
        """Test SAGE multi-agent learning."""
        adapter = FortressPerceiveAdapter(controller_mode="sage")

        result = adapter.process_governance_request(
            request_id="req-sage-001",
            perceive_decision={"error_signal": 3.0, "live_signal": 100.0},
            artifact_content="Adaptive learning test",
            artifact_metadata={
                "origin": "sentinel-789",
                "signature": "sig-valid"
            }
        )

        assert result.controller == "SAGE"
        # Regime can be uppercase or normal
        assert result.regime.upper() in ["STABLE", "UNSTABLE", "CRITICAL", "UNKNOWN"]

    def test_regime_classification(self):
        """Test regime classification across error levels."""
        adapter = FortressPerceiveAdapter(controller_mode="sage")

        # Low error: should be STABLE
        result_low = adapter.process_governance_request(
            request_id="req-sage-low",
            perceive_decision={"error_signal": 2.0, "live_signal": 100.0},
            artifact_content="Low error",
            artifact_metadata={"origin": "sentinel", "signature": "valid"}
        )

        result_med = adapter.process_governance_request(
            request_id="req-sage-med",
            perceive_decision={"error_signal": 15.0, "live_signal": 100.0},
            artifact_content="Medium error",
            artifact_metadata={"origin": "sentinel", "signature": "valid"}
        )

        result_high = adapter.process_governance_request(
            request_id="req-sage-high",
            perceive_decision={"error_signal": 60.0, "live_signal": 100.0},
            artifact_content="High error",
            artifact_metadata={"origin": "sentinel", "signature": "valid"}
        )

        # The medium case was computed here and never asserted on: the test
        # claimed to cover "across error levels" while checking exactly one,
        # and its comment hedged ("may be UNSTABLE") about behaviour that is
        # deterministic. Measured: 2.0 -> stable, 15.0 -> unstable,
        # 60.0 -> critical. A test that runs a path without checking it
        # reports the same green whether that path works or not.
        assert result_low.regime.upper() in ["STABLE", "NOMINAL"]
        assert result_med.regime.upper() == "UNSTABLE"
        assert result_high.regime.upper() == "CRITICAL"


class TestMultiControllerComparison:
    """Compare different controllers on same workload."""

    def test_consistent_decisions_on_safe_artifact(self):
        """All controllers should approve safe artifacts."""
        perceive_decision = {
            "error_signal": 3.0,
            "live_signal": 100.0,
        }

        artifact_metadata = {
            "origin": "sentinel-safe",
            "signature": "valid"
        }

        results = {}
        for mode in ["energy", "lyapunov", "sage"]:
            adapter = FortressPerceiveAdapter(controller_mode=mode)
            result = adapter.process_governance_request(
                request_id=f"req-{mode}",
                perceive_decision=perceive_decision,
                artifact_content="Safe artifact",
                artifact_metadata=artifact_metadata
            )
            results[mode] = result

        # All should approve safe artifacts
        for mode, result in results.items():
            assert result.decision == "APPROVED", f"{mode} should approve safe artifact"
            assert result.approval_confidence > 0.7, f"{mode} should have high confidence"

    def test_throughput(self):
        """Test processing throughput."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        requests = []
        for i in range(10):
            requests.append({
                "request_id": f"batch-{i:02d}",
                "perceive_decision": {
                    "error_signal": 5.0,
                    "live_signal": 100.0,
                },
                "artifact_content": f"Artifact {i}",
                "artifact_metadata": {
                    "origin": "sentinel",
                    "signature": "valid"
                },
            })

        results = adapter.process_batch(requests)
        assert len(results) == 10
        assert all(r.controller == "ENERGY" for r in results)


class TestAuditChain:
    """Test audit trail and forensic verification."""

    def test_audit_trail_preservation(self):
        """Audit trail should be immutable."""
        adapter = FortressPerceiveAdapter(controller_mode="energy")

        # Process requests
        for i in range(5):
            adapter.process_governance_request(
                request_id=f"audit-{i:02d}",
                perceive_decision={"error_signal": 5.0, "live_signal": 100.0},
                artifact_content=f"Artifact {i}",
                artifact_metadata={"origin": "sentinel", "signature": "valid"}
            )

        # Verify integrity
        assert adapter.verify_audit_integrity()

        trail = adapter.get_audit_trail()
        assert len(trail) >= 5

    def test_forensic_trace(self):
        """Forensic trace should contain verification info."""
        adapter = FortressPerceiveAdapter(controller_mode="lyapunov")

        result = adapter.process_governance_request(
            request_id="forensic-001",
            perceive_decision={"error_signal": 5.0, "live_signal": 100.0},
            artifact_content="Forensic test",
            artifact_metadata={
                "origin": "sentinel-123",
                "signature": "valid"
            },
            artifact_hash="sha256-abc123"
        )

        assert "request_id" in result.forensic_trace
        assert "controller" in result.forensic_trace
        assert "audit_chain_valid" in result.forensic_trace
        assert "approval_confidence" in result.forensic_trace


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
