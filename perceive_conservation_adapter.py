"""
PERCEIVE → Conservation Kernel Adapter

Submits PERCEIVE decisions to Conservation Kernel for verification.
Ensures all governance decisions pass through conservation boundary.
"""

from governance_contracts import GovernanceDecision, ConservationDecision
from datetime import datetime, timezone
import hashlib

# Optional conservation_kernel dependency, resolved once at import time.
#
# This resolver used to be defined and never called, so `Actor`, `ActorKind`
# and `Artifact` were never bound to anything. Every call to
# verify_perceive_decision raised `NameError: name 'Actor' is not defined`,
# the orchestrator caught it as a generic Exception, and the run came back
# "REJECTED -- Conservation Kernel rejected decision". The Conservation
# Kernel stage had never actually executed; it only ever looked like a
# principled refusal. Binding the names at module scope is what makes the
# stage real, and the tests below assert it now runs rather than raises.
def _import_conservation():
    try:
        from conservation_kernel import (
            ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
            DeclaredChange, Dimension, TransitionKind,
        )
        return (ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
                DeclaredChange, Dimension, TransitionKind)
    except ModuleNotFoundError:
        return (None,) * 8


(ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
 DeclaredChange, Dimension, TransitionKind) = _import_conservation()


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

        # The input artifact, as the Conservation Kernel understands it. This
        # is the thing Sentinel produced and PERCEIVE ruled on.
        input_artifact = Artifact(
            artifact_id=input_artifact_id,
            content=input_artifact_content,
            propositions=(),
            producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM),
        )

        # The decision artifact is a *derivation* of the input, not a free
        # standing object: parent_artifact_ids and the version bump are what
        # let the kernel reconstruct the lineage later.
        decision_artifact = Artifact(
            artifact_id=f"decision-{governance_decision.decision_id}",
            content=decision_content,
            propositions=(),
            producer=perceive_actor,
            parent_artifact_ids=(input_artifact_id,),
            version=input_artifact.version + 1,
        )

        # The record the kernel actually verifies against.
        #
        # `declared_changes` is a tuple of DeclaredChange, not a prose string.
        # An earlier version passed a sentence here and omitted `transformer`,
        # `transformation_type`, `input_hashes` and `output_hash` entirely, so
        # construction raised TypeError before the kernel was ever reached --
        # the orchestrator caught it and reported "Conservation Kernel
        # rejected", which read as a governance refusal but was a broken call.
        #
        # The CONTENT change has to be declared explicitly. Writing a decision
        # about an artifact produces different content from the artifact, and
        # the kernel refuses an output that moved a protected dimension the
        # record did not account for (UNDECLARED_CHANGE). Declaring it is not
        # a formality that satisfies the checker -- it is the claim the kernel
        # then independently re-derives from the digests and can refuse.
        #
        # TransitionKind.DERIVATION is the honest kind here: the decision is
        # derived from the artifact. It is not an adoption, a promotion, or an
        # escalation of authority, and claiming one of those would assert
        # something about human involvement that did not happen.
        content_change = DeclaredChange(
            subject_id=decision_artifact.artifact_id,
            dimension=Dimension.CONTENT,
            from_value=input_artifact.content_digest,
            to_value=decision_artifact.content_digest,
            reason=(
                f"PERCEIVE governance evaluation over "
                f"{len(governance_decision.applied_gates)} gate(s); "
                f"decision recorded as a derived artifact"
            ),
            transition_kind=TransitionKind.DERIVATION,
        )

        # The hashes come from the artifacts' own digests. That is the point of
        # the record: the kernel re-derives them and refuses if what was
        # declared does not match what was actually submitted.
        transformation_record = TransformationRecord(
            transformation_id=f"perceive-verify-{governance_decision.decision_id}",
            input_artifact_ids=(input_artifact_id,),
            output_artifact_id=decision_artifact.artifact_id,
            transformer=perceive_actor,
            transformation_type="GOVERNANCE_EVALUATION",
            declared_changes=(content_change,),
            input_hashes=(input_artifact.artifact_digest,),
            output_hash=decision_artifact.artifact_digest,
            reason=(
                f"PERCEIVE governance evaluation "
                f"(gates: {', '.join(governance_decision.applied_gates)})"
            ),
        )

        # Submit to Conservation Kernel. The input has to be registered as a
        # root first -- the kernel will not verify a transformation whose
        # input it has never seen, which is exactly the provenance guarantee
        # it exists to make.
        self.kernel.register_root(input_artifact)
        verification_result = self.kernel.submit(
            input_artifact,
            decision_artifact,
            transformation_record,
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
