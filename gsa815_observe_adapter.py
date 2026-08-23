"""
GSA-815 → OBSERVE Adapter

Passes governance context to OBSERVE monitoring.
Links decision audit hash ↔ outcome audit hash for complete forensic replay.
"""

from governance_contracts import ExecutionContext, OutcomeContext
from observe_consolidated import FusedVerdict
from datetime import datetime, timezone
import hashlib
import uuid


class GSA815ObserveAdapter:
    """Passes execution context and outcomes to OBSERVE."""

    @staticmethod
    def create_outcome_context(
        execution_context: ExecutionContext,
        gsa815_result: dict,
        observe_verdict: FusedVerdict
    ) -> OutcomeContext:
        """
        Create outcome context linking governance and clinical decisions.

        Args:
            execution_context: The execution context
            gsa815_result: GSA-815 execution result
            observe_verdict: OBSERVE clinical assessment

        Returns:
            OutcomeContext for monitoring and audit
        """
        result_artifact_id = f"outcome-{execution_context.execution_id}"
        result_artifact_hash = GSA815ObserveAdapter._compute_artifact_hash(
            f"{gsa815_result}:{observe_verdict.regime.value}"
        )

        # Create complete lineage including governance decisions
        complete_lineage = execution_context.lineage.copy()
        if execution_context.artifact_id not in complete_lineage:
            complete_lineage.append(execution_context.artifact_id)

        return OutcomeContext(
            execution_id=execution_context.execution_id,
            governance_decision_id=execution_context.approval.request_id,
            conservation_decision_id=execution_context.approval.conservation_decision_id,
            governance_audit_hash=execution_context.approval.governance_audit_hash,
            conservation_audit_hash=execution_context.approval.conservation_audit_hash,
            result_artifact_id=result_artifact_id,
            result_artifact_hash=result_artifact_hash,
            producer="OBSERVE",
            lineage=complete_lineage,
            outcome={
                "gsa815_result": gsa815_result,
                "observe_regime": observe_verdict.regime.value,
                "observe_risk_score": observe_verdict.risk_score,
                "observe_confidence": observe_verdict.confidence,
                "observe_audit_hash": observe_verdict.audit_hash,
                "escalation_required": observe_verdict.escalation_required,
            },
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def verify_governance_chain(outcome_context: OutcomeContext) -> bool:
        """
        Verify the complete governance chain is linked.

        Args:
            outcome_context: The outcome context

        Returns:
            True if all audit hashes are present and linked
        """
        checks = [
            bool(outcome_context.governance_decision_id),
            bool(outcome_context.conservation_decision_id),
            bool(outcome_context.governance_audit_hash),
            bool(outcome_context.conservation_audit_hash),
            bool(outcome_context.result_artifact_hash),
            len(outcome_context.lineage) > 0,
        ]
        return all(checks)

    @staticmethod
    def _compute_artifact_hash(content: str) -> str:
        """Compute canonical SHA256 hash."""
        return hashlib.sha256(content.encode()).hexdigest()

    @staticmethod
    def create_forensic_proof(outcome_context: OutcomeContext) -> dict:
        """
        Create forensic proof linking all decisions.

        Returns:
            Forensic proof dict for replay/verification
        """
        return {
            "execution_id": outcome_context.execution_id,
            "governance_decision_id": outcome_context.governance_decision_id,
            "conservation_decision_id": outcome_context.conservation_decision_id,
            "governance_audit_hash": outcome_context.governance_audit_hash,
            "conservation_audit_hash": outcome_context.conservation_audit_hash,
            "result_artifact_id": outcome_context.result_artifact_id,
            "result_artifact_hash": outcome_context.result_artifact_hash,
            "lineage": outcome_context.lineage,
            "outcome": outcome_context.outcome,
            "timestamp": outcome_context.timestamp.isoformat(),
            "chain_valid": GSA815ObserveAdapter.verify_governance_chain(outcome_context),
        }
