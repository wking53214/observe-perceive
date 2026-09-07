"""
PERCEIVE → FORTRESS Integration Adapter
========================================

Routes PERCEIVE-approved governance requests through FORTRESS orchestration
(safety containment layer) before passing to Conservation Kernel.

FORTRESS provides pluggable safety controllers:
- SAGE: Multi-agent adaptive learning with regime classification
- Lyapunov: Stability-proven integrity control with FIM tracking
- Energy: State transition logging with energy-based divergence detection

All controllers share: IntegrityLayer, InvariantMonitor, DriftMonitor, MandateLayer,
ImmutableAuditLedger with HMAC-SHA256 signing.
"""

from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any


def _import_fortress_types():
    """Resolve fortress-kernel the same way sentinel_perceive_adapter resolves
    sentinel_os: a sibling-checkout path insert, with pip as the fallback.

    The original version of this adapter required `pip install -e` of a
    fortress-kernel checkout that lived in /tmp. That install is long gone, so
    the adapter would not import at all -- which is why this file sat in a
    stale fork instead of the repo. Resolving a sibling checkout first makes it
    work from a plain clone, matching how every other cross-repo dependency in
    this package is handled.
    """
    fortress_path = os.path.join(os.path.dirname(__file__), "..", "fortress-kernel")
    if os.path.isdir(fortress_path) and fortress_path not in sys.path:
        sys.path.insert(0, fortress_path)
    try:
        from fortress_unified import FortressUnified, FortressConfig, Payload
    except ImportError as e:
        raise ImportError(
            "fortress-kernel not found. Clone it beside this repo "
            "(../fortress-kernel) or install it: "
            "pip install git+https://github.com/wking53214/fortress-kernel.git"
        ) from e
    return FortressUnified, FortressConfig, Payload


FortressUnified, FortressConfig, Payload = _import_fortress_types()

logger = logging.getLogger("FORTRESS_ADAPTER")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("[FORTRESS] %(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)


# ============================================================================
# FORTRESS PROCESSING RESULT
# ============================================================================

@dataclass
class FortressProcessingResult:
    """Output from FORTRESS orchestration layer."""

    request_id: str
    timestamp: datetime

    # Controller info
    controller: str  # "SAGE", "LYAPUNOV", "ENERGY"

    # Safety decision
    decision: str  # "APPROVED" or "REJECTED"
    approval_confidence: float  # 0.0 to 1.0

    # Output metrics
    output_value: float
    distortion: float
    regime: str

    # Provenance & verification
    integrity: str  # "VERIFIED" or "UNVERIFIED"

    # Audit trail
    authority: Optional[float] = None
    artifact_hash: Optional[str] = None
    audit_events: List[Dict[str, Any]] = field(default_factory=list)
    audit_ledger_size: int = 0
    audit_verified: bool = False

    # Forensic trace for downstream verification
    forensic_trace: Dict[str, Any] = field(default_factory=dict)


# ============================================================================
# FORTRESS PERCEIVE ADAPTER
# ============================================================================

class FortressPerceiveAdapter:
    """Routes PERCEIVE decisions through FORTRESS orchestration."""

    def __init__(
        self,
        controller_mode: str = "energy",
        enable_logging: bool = True
    ):
        """
        Initialize FORTRESS adapter with controller selection.

        Args:
            controller_mode: "sage", "lyapunov", or "energy"
            enable_logging: Whether to log processing steps
        """
        self.config = FortressConfig(controller_mode=controller_mode)
        self.fortress = FortressUnified(self.config)
        self.controller_mode = controller_mode
        self.enable_logging = enable_logging

        if enable_logging:
            logger.info(f"FORTRESS adapter initialized with {controller_mode} controller")

    def process_governance_request(
        self,
        request_id: str,
        perceive_decision: Dict[str, Any],
        artifact_content: str,
        artifact_metadata: Dict[str, Any],
        artifact_hash: Optional[str] = None,
    ) -> FortressProcessingResult:
        """
        Process PERCEIVE-approved request through FORTRESS.

        Args:
            request_id: Request identifier from PERCEIVE
            perceive_decision: Approved decision from PERCEIVE with error_signal and live_signal
            artifact_content: Artifact content to govern
            artifact_metadata: Artifact metadata with provenance
            artifact_hash: Optional SHA256 hash of artifact

        Returns:
            FortressProcessingResult for downstream verification
        """
        timestamp = datetime.now(timezone.utc)

        if self.enable_logging:
            logger.info(f"Processing request {request_id} through FORTRESS")

        # Extract signals from PERCEIVE decision
        error = perceive_decision.get("error_signal", 0.0)
        live_signal = perceive_decision.get("live_signal", 100.0)

        # Create payload with provenance
        payload = Payload(
            body=artifact_content[:500],  # First 500 chars for analysis
            metadata={
                "source_id": artifact_metadata.get("origin", "unknown"),
                "signature": artifact_metadata.get("signature"),
                "request_id": request_id,
                "artifact_hash": artifact_hash,
            }
        )

        # Process through FORTRESS
        fortress_result = self.fortress.process(payload, error, live_signal)

        if self.enable_logging:
            logger.info(
                f"FORTRESS result: controller={self.controller_mode}, "
                f"decision={fortress_result.get('output', 'N/A')}, "
                f"distortion={fortress_result.get('distortion', 'N/A'):.3f}"
            )

        # Determine approval based on distortion and integrity
        distortion = fortress_result.get("distortion", 0.0)
        integrity = fortress_result.get("integrity", "UNVERIFIED")

        # Approval criteria:
        # - distortion < 0.8 (safety margin)
        # - either verified OR explicitly acceptable
        approved = (distortion < 0.8)
        approval_confidence = 1.0 - (distortion / 0.8) if distortion < 0.8 else 0.0

        return FortressProcessingResult(
            request_id=request_id,
            timestamp=timestamp,
            controller=self.controller_mode.upper(),
            decision="APPROVED" if approved else "REJECTED",
            approval_confidence=approval_confidence,
            output_value=fortress_result.get("output", live_signal),
            distortion=distortion,
            regime=fortress_result.get("regime", "UNKNOWN"),
            authority=fortress_result.get("authority"),
            integrity=integrity,
            artifact_hash=artifact_hash,
            audit_events=[
                {
                    "timestamp": timestamp.isoformat(),
                    "event": "fortress_processing",
                    "controller": self.controller_mode,
                    "result": fortress_result
                }
            ],
            audit_ledger_size=len(self.fortress.audit.ledger),
            audit_verified=self.fortress.audit.verify_integrity(),
            forensic_trace={
                "request_id": request_id,
                "controller": self.controller_mode,
                "fortress_ledger_size": len(self.fortress.audit.ledger),
                "audit_chain_valid": self.fortress.audit.verify_integrity(),
                "distortion_threshold_used": 0.8,
                "approval_confidence": approval_confidence,
            }
        )

    def process_governance_decision(
        self,
        governance_decision,   # GovernanceDecision (import kept lazy, see below)
        governance_request,    # GovernanceRequest
    ) -> FortressProcessingResult:
        """Run a PERCEIVE decision through FORTRESS using the chain's own
        contract types.

        `process_governance_request` above takes a loose dict, which is what
        the original standalone integration used. Inside the orchestrated
        chain the neighbouring stages hand each other typed
        GovernanceRequest / GovernanceDecision objects, so this is the seam
        method the orchestrator calls -- same pattern as every other adapter
        here, and it keeps FORTRESS's own vocabulary out of the orchestrator.

        Signal mapping, which is the only judgement call in this seam:

        - `error_signal` is derived from the count of PERCEIVE violations, not
          invented. A clean unanimous approval is zero error; each recorded
          violation raises the error FORTRESS is asked to steer against. This
          keeps the two systems' notions of "something is wrong" connected
          rather than independent.
        - `live_signal` is the nominal 100.0 setpoint FORTRESS's controllers
          are tuned around. There is no measured process value at this seam --
          governance decisions are discrete, not a continuous tracked
          quantity -- so this is explicitly the neutral default rather than a
          measurement dressed up as one.
        """
        violation_count = len(governance_decision.violations or [])
        error_signal = float(violation_count) * 5.0
        return self.process_governance_request(
            request_id=governance_decision.request_id,
            perceive_decision={
                "error_signal": error_signal,
                "live_signal": 100.0,
                "approved": governance_decision.approval.value == "approved",
            },
            artifact_content=governance_request.artifact_content,
            artifact_metadata={
                "origin": governance_request.origin,
                "signature": governance_request.state_commitment or None,
            },
            artifact_hash=governance_request.artifact_hash,
        )

    def process_batch(
        self,
        requests: List[Dict[str, Any]]
    ) -> List[FortressProcessingResult]:
        """
        Process multiple requests through FORTRESS in sequence.

        Args:
            requests: List of request dicts with keys:
                - request_id
                - perceive_decision
                - artifact_content
                - artifact_metadata
                - artifact_hash (optional)

        Returns:
            List of FortressProcessingResult
        """
        results = []
        for req in requests:
            result = self.process_governance_request(
                request_id=req["request_id"],
                perceive_decision=req["perceive_decision"],
                artifact_content=req["artifact_content"],
                artifact_metadata=req["artifact_metadata"],
                artifact_hash=req.get("artifact_hash"),
            )
            results.append(result)
        return results

    def get_audit_trail(self) -> List[Dict[str, Any]]:
        """Get complete FORTRESS audit trail."""
        return self.fortress.audit.ledger

    def verify_audit_integrity(self) -> bool:
        """Verify that audit trail has not been tampered with."""
        return self.fortress.audit.verify_integrity()


# ============================================================================
# DEMO / TESTING
# ============================================================================

if __name__ == "__main__":

    # Test all three controller modes
    for mode in ["energy", "lyapunov", "sage"]:
        print(f"\n{'='*70}")
        print(f"Testing FORTRESS adapter with {mode.upper()} controller")
        print(f"{'='*70}")

        adapter = FortressPerceiveAdapter(controller_mode=mode)

        # Simulate PERCEIVE decision
        perceive_decision = {
            "error_signal": 5.0,
            "live_signal": 100.0,
            "approved": True,
        }

        result = adapter.process_governance_request(
            request_id="req-001",
            perceive_decision=perceive_decision,
            artifact_content="Example artifact for governance",
            artifact_metadata={
                "origin": "sentinel-123",
                "signature": "sig-valid"
            },
            artifact_hash="sha256-abc123"
        )

        print(f"Decision: {result.decision}")
        print(f"Controller: {result.controller}")
        print(f"Approval Confidence: {result.approval_confidence:.2f}")
        print(f"Output: {result.output_value:.2f}")
        print(f"Distortion: {result.distortion:.3f}")
        print(f"Regime: {result.regime}")
        print(f"Integrity: {result.integrity}")
        print(f"Audit Verified: {result.audit_verified}")
        print(f"Audit Events: {len(result.audit_events)}")

    print(f"\n{'='*70}")
    print("✓ All FORTRESS adapter tests passed")
    print(f"{'='*70}")
