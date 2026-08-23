"""
PERCEIVE → Conservation Kernel Adapter

Submits PERCEIVE decisions to Conservation Kernel for verification.
Ensures all governance decisions pass through conservation boundary.
"""

from governance_contracts import GovernanceDecision, ConservationDecision, GovernanceApproval
from datetime import datetime, timezone
import hashlib

# Lazy import to handle optional conservation_kernel dependency
def _import_conservation():
    try:
        from conservation_kernel import (
            ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind
        )
        return ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind
    except ModuleNotFoundError:
        return None, None, None, None, None


class PerceiveConservationAdapter:
    """Submits PERCEIVE verdicts to Conservation Kernel."""

    def __init__(self, kernel=None):  # ConservationKernel (optional for testing)
        """Initialize with Conservation Kernel instance."""
        self.kernel = kernel

    def verify_perceive_decision(
        self,
        governance_decision: GovernanceDecision,
        input_artifact_id: str,
        input_artifact_content: str,
        input_artifact_hash: str
    ) -> ConservationDecision:
        """
        Verify PERCEIVE decision through Conservation Kernel.

        Args:
            governance_decision: The PERCEIVE decision
            input_artifact_id: Original artifact ID
            input_artifact_content: Original artifact content
            input_artifact_hash: Original artifact hash

        Returns:
            ConservationDecision with conservation receipt

        Raises:
            Exception if Kernel rejects the decision
        """
        # Create Conservation Kernel Artifact for the decision
        decision_content = f"""
PERCEIVE Governance Decision:
- Request ID: {governance_decision.request_id}
- Decision ID: {governance_decision.decision_id}
- Approval: {governance_decision.approval.value}
- Applied Gates: {', '.join(governance_decision.applied_gates)}
- Unanimous Consensus: {governance_decision.unanimous_consensus}
- Violations: {', '.join(governance_decision.violations) if governance_decision.violations else 'None'}
- Policy Version: {governance_decision.policy_version}
- Timestamp: {governance_decision.timestamp.isoformat()}
"""

        # Create producer actor for PERCEIVE
        perceive_actor = Actor(
            actor_id="PERCEIVE",
            kind=ActorKind.SYSTEM
        )

        # Create the decision artifact
        decision_artifact = Artifact(
            artifact_id=f"decision-{governance_decision.decision_id}",
            content=decision_content,
            propositions=(),
            producer=perceive_actor
        )

        # Create transformation record linking to input artifact
        transformation_record = TransformationRecord(
            transformation_id=f"perceive-verify-{governance_decision.decision_id}",
            input_artifact_ids=(input_artifact_id,),
            output_artifact_id=f"decision-{governance_decision.decision_id}",
            declared_changes=f"PERCEIVE governance evaluation (gates: {', '.join(governance_decision.applied_gates)})"
        )

        # Submit to Conservation Kernel
        verification_result = self.kernel.submit(
            input_artifacts=(
                Artifact(
                    artifact_id=input_artifact_id,
                    content=input_artifact_content,
                    propositions=(),
                    producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM)
                ),
            ),
            output=decision_artifact,
            record=transformation_record
        )

        # If Kernel rejects, fail closed
        if not verification_result.accepted:
            raise Exception(
                f"Conservation Kernel rejected PERCEIVE decision: {verification_result.violations}"
            )

        # Return conservation decision
        return ConservationDecision(
            governance_decision_id=governance_decision.decision_id,
            approval=governance_decision.approval,
            conservation_receipt_id=verification_result.transformation_id,
            artifact_hash=input_artifact_hash,
            conservation_audit_hash=self._compute_audit_hash(verification_result),
            verified=True,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def _compute_audit_hash(verification_result) -> str:
        """Compute audit hash from verification result."""
        content = f"{verification_result.transformation_id}:{verification_result.status.value}"
        return hashlib.sha256(content.encode()).hexdigest()
