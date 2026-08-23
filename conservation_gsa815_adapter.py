"""
Conservation Decision → GSA-815 Adapter

Gates GSA-815 execution on verified PERCEIVE decisions.
Ensures no execution without unanimous PERCEIVE consensus + Conservation verification.
"""

from governance_contracts import (
    ConservationDecision, ExecutionApproval, ExecutionContext,
    GovernanceApproval
)
from datetime import datetime, timezone
import hashlib
import uuid


class ConservationGSA815Adapter:
    """Gates GSA-815 execution on conservation-verified PERCEIVE decisions."""

    @staticmethod
    def approve_execution(
        conservation_decision: ConservationDecision,
        artifact_id: str,
        artifact_hash: str,
        producer: str,
        lineage: list,
        execution_id: str = None
    ) -> ExecutionApproval:
        """
        Issue execution approval for GSA-815.

        Args:
            conservation_decision: Verified PERCEIVE decision
            artifact_id: Artifact to be executed against
            artifact_hash: Hash of artifact
            producer: Producer (usually GSA-815)
            lineage: Parent artifact IDs
            execution_id: Optional execution ID

        Returns:
            ExecutionApproval authorizing GSA-815 execution

        Raises:
            Exception if conservation decision is not approved
        """
        # Fail closed if conservation decision not approved
        if conservation_decision.approval != GovernanceApproval.APPROVED:
            raise Exception(
                f"Conservation decision not approved: {conservation_decision.approval.value}"
            )

        if not conservation_decision.verified:
            raise Exception("Conservation decision not verified")

        # Create execution ID if not provided
        if not execution_id:
            execution_id = str(uuid.uuid4())[:8]

        return ExecutionApproval(
            request_id=execution_id,
            approval=GovernanceApproval.APPROVED,
            conservation_decision_id=conservation_decision.governance_decision_id,
            conservation_receipt_id=conservation_decision.conservation_receipt_id,
            governance_audit_hash=conservation_decision.conservation_audit_hash,
            conservation_audit_hash=conservation_decision.conservation_audit_hash,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def create_execution_context(
        execution_approval: ExecutionApproval,
        artifact_id: str,
        artifact_hash: str,
        producer: str,
        lineage: list
    ) -> ExecutionContext:
        """
        Create execution context for GSA-815.

        Args:
            execution_approval: The execution approval
            artifact_id: Artifact ID
            artifact_hash: Artifact hash
            producer: Producer
            lineage: Lineage

        Returns:
            ExecutionContext for GSA-815 operation
        """
        return ExecutionContext(
            request_id=execution_approval.request_id,
            approval=execution_approval,
            artifact_id=artifact_id,
            artifact_hash=artifact_hash,
            producer=producer,
            lineage=lineage,
            execution_id=execution_approval.request_id,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def reject_execution(reason: str) -> ExecutionApproval:
        """
        Reject execution (fail-closed).

        Args:
            reason: Reason for rejection

        Returns:
            ExecutionApproval with REJECTED status
        """
        return ExecutionApproval(
            request_id=str(uuid.uuid4())[:8],
            approval=GovernanceApproval.REJECTED,
            conservation_decision_id="",
            conservation_receipt_id="",
            governance_audit_hash="",
            conservation_audit_hash="",
            timestamp=datetime.now(timezone.utc)
        )
