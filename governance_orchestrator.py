"""
Governance Orchestrator

Central orchestrator that routes requests through:
1. Sentinel artifact creation
2. PERCEIVE governance evaluation (6 gates, unanimous consensus)
3. Conservation Kernel verification
4. GSA-815 execution (with approval)
5. OBSERVE monitoring
6. Complete audit chain linking
"""

from governance_contracts import GovernanceApproval
from datetime import datetime, timezone
import hashlib
import logging
import sys
import os

logger = logging.getLogger("GovernanceOrchestrator")


class GovernanceOrchestrator:
    """Central orchestrator for unified governance flow."""

    def __init__(self, perceive=None, conservation_kernel=None, observe_engine=None,
                 fortress_controller: str = None):
        """
        Initialize orchestrator with all governance systems.

        Args:
            perceive: PERCEIVE governance kernel (optional for testing)
            conservation_kernel: Conservation Kernel for verification (optional)
            observe_engine: OBSERVE clinical AI system (optional)
            fortress_controller: "energy", "lyapunov" or "sage" to place
                FORTRESS in the chain between PERCEIVE and the Conservation
                Kernel. None (the default) leaves it out entirely, so the
                chain behaves exactly as it did before FORTRESS existed and
                a missing fortress-kernel checkout is never a hard failure.
        """
        self.perceive = perceive
        self.conservation_kernel = conservation_kernel
        self.observe_engine = observe_engine

        # Lazy imports for adapters
        from sentinel_perceive_adapter import SentinelPerceiveAdapter
        from perceive_conservation_adapter import PerceiveConservationAdapter
        from conservation_gsa815_adapter import ConservationGSA815Adapter
        from gsa815_observe_adapter import GSA815ObserveAdapter

        self.sentinel_adapter = SentinelPerceiveAdapter()
        self.perceive_adapter = PerceiveConservationAdapter(conservation_kernel) if conservation_kernel else None
        self.conservation_adapter = ConservationGSA815Adapter()
        self.gsa815_adapter = GSA815ObserveAdapter()

        # FORTRESS is opt-in. Imported only when asked for, so the rest of the
        # chain does not acquire a hard dependency on a sibling checkout.
        self.fortress_adapter = None
        if fortress_controller:
            from fortress_perceive_adapter import FortressPerceiveAdapter
            self.fortress_adapter = FortressPerceiveAdapter(controller_mode=fortress_controller)

    def orchestrate_request(
        self,
        sentinel_artifact,
        operation_type: str,
        gsa815_operation_func,
        vitals_snapshot=None,  # VitalsSnapshot (type annotation removed to avoid import)
        context: dict = None
    ) -> dict:
        """
        Orchestrate complete governance flow.

        Flow:
        1. Sentinel artifact → GovernanceRequest
        2. PERCEIVE evaluation (6 gates, unanimous)
        3. Conservation Kernel verification
        4. GSA-815 execution approval
        5. Execute GSA-815 operation
        6. OBSERVE monitoring
        7. Link all audit chains

        Args:
            sentinel_artifact: The artifact to govern
            operation_type: "escalate", "modify", "export", "override"
            gsa815_operation_func: Function to execute if approved
            vitals_snapshot: Optional vitals for OBSERVE monitoring
            context: Additional context

        Returns:
            Complete governance decision with audit chain
        """
        logger.info(f"[Orchestrator] Starting governance flow for {sentinel_artifact.artifact_id}")

        # PHASE 1: Convert Sentinel artifact to PERCEIVE request
        logger.info("[Orchestrator] Phase 1: Converting Sentinel artifact to governance request")
        if context is None:
            context = {}
        governance_request = self.sentinel_adapter.sentinel_artifact_to_governance_request(
            sentinel_artifact,
            operation_type,
            context
        )
        logger.info(f"[Orchestrator] Request ID: {governance_request.request_id}")

        # PHASE 2: PERCEIVE evaluation
        #
        # Goes through the adapter, not straight at the kernel: PERCEIVE
        # speaks PolicyRequest/PolicyVerdict, everything downstream of here
        # speaks GovernanceDecision. The adapter owns that translation (see
        # sentinel_perceive_adapter for why the two vocabularies stay separate).
        logger.info("[Orchestrator] Phase 2: PERCEIVE evaluation (unanimous consensus across applied gates)")
        perceive_decision = self.sentinel_adapter.evaluate_through_perceive(
            self.perceive,
            governance_request
        )
        logger.info(f"[Orchestrator] PERCEIVE approval: {perceive_decision.approval.value}")
        logger.info(f"[Orchestrator] Applied gates: {perceive_decision.applied_gates}")
        logger.info(f"[Orchestrator] Unanimous: {perceive_decision.unanimous_consensus}")
        if perceive_decision.violations:
            logger.info(f"[Orchestrator] Violations: {perceive_decision.violations}")

        # PHASE 2b: FORTRESS safety containment (opt-in)
        #
        # Sits between PERCEIVE and the Conservation Kernel: PERCEIVE decides
        # whether the request is *permitted*, FORTRESS decides whether acting
        # on it stays inside safe operating bounds. A request can be
        # legitimately approved and still be refused here, which is the whole
        # reason the layer is separate.
        fortress_result = None
        if self.fortress_adapter:
            logger.info(f"[Orchestrator] Phase 2b: FORTRESS containment ({self.fortress_adapter.controller_mode})")
            fortress_result = self.fortress_adapter.process_governance_decision(
                perceive_decision,
                governance_request,
            )
            logger.info(
                f"[Orchestrator] FORTRESS: {fortress_result.decision} "
                f"(distortion={fortress_result.distortion:.3f}, regime={fortress_result.regime})"
            )
            if fortress_result.decision != "APPROVED":
                # Fail closed. A containment refusal stops the chain here --
                # nothing downstream gets to re-approve what FORTRESS refused.
                logger.error("[Orchestrator] FORTRESS refused: halting before Conservation Kernel")
                return {
                    "status": "REJECTED",
                    "reason": (
                        f"FORTRESS containment refused (distortion "
                        f"{fortress_result.distortion:.3f}, regime {fortress_result.regime})"
                    ),
                    "governance_decision": perceive_decision,
                    "fortress_result": fortress_result,
                    "audit_chain": None,
                }

        # PHASE 3: Conservation Kernel verification
        logger.info("[Orchestrator] Phase 3: Conservation Kernel verification")
        try:
            conservation_decision = self.perceive_adapter.verify_perceive_decision(
                perceive_decision,
                governance_request.artifact_id,
                governance_request.artifact_content,
                governance_request.artifact_hash
            )
            logger.info(f"[Orchestrator] Conservation verified: {conservation_decision.verified}")
        except Exception as e:
            logger.error(f"[Orchestrator] Conservation Kernel rejected: {e}")
            return {
                "status": "REJECTED",
                "reason": f"Conservation Kernel rejected decision: {e}",
                "governance_decision": perceive_decision,
                "audit_chain": None,
            }

        # PHASE 4: GSA-815 execution approval
        logger.info("[Orchestrator] Phase 4: GSA-815 execution approval")
        try:
            execution_approval = self.conservation_adapter.approve_execution(
                conservation_decision,
                governance_request.artifact_id,
                governance_request.artifact_hash,
                sentinel_artifact.metadata.origin_status.value if hasattr(sentinel_artifact.metadata.origin_status, 'value') else "Sentinel",
                governance_request.lineage
            )
            execution_context = self.conservation_adapter.create_execution_context(
                execution_approval,
                governance_request.artifact_id,
                governance_request.artifact_hash,
                "GSA-815",
                governance_request.lineage
            )
            logger.info("[Orchestrator] GSA-815 execution approved")
        except Exception as e:
            logger.error(f"[Orchestrator] Execution rejected: {e}")
            return {
                "status": "REJECTED",
                "reason": str(e),
                "governance_decision": perceive_decision,
                "audit_chain": None,
            }

        # PHASE 5: Execute GSA-815 operation
        logger.info("[Orchestrator] Phase 5: GSA-815 execution")
        gsa815_result = gsa815_operation_func(execution_context)
        logger.info(f"[Orchestrator] GSA-815 result: {gsa815_result}")

        # PHASE 6: OBSERVE monitoring
        logger.info("[Orchestrator] Phase 6: OBSERVE clinical monitoring")
        observe_verdict = None
        if vitals_snapshot:
            observe_verdict = self.observe_engine.evaluate(vitals_snapshot)
            logger.info(f"[Orchestrator] OBSERVE verdict: regime={observe_verdict.regime.value}")
        else:
            logger.warning("[Orchestrator] No vitals provided, skipping OBSERVE evaluation")

        # PHASE 7: Link audit chains
        logger.info("[Orchestrator] Phase 7: Linking audit chains")
        if observe_verdict:
            outcome_context = self.gsa815_adapter.create_outcome_context(
                execution_context,
                gsa815_result,
                observe_verdict
            )
            forensic_proof = self.gsa815_adapter.create_forensic_proof(outcome_context)
        else:
            outcome_context = None
            forensic_proof = None

        # Return complete result
        logger.info("[Orchestrator] Governance flow complete")
        return {
            "status": "APPROVED_AND_EXECUTED",
            "governance_decision": perceive_decision,
            "fortress_result": fortress_result,
            "conservation_decision": conservation_decision,
            "execution_approval": execution_approval,
            "execution_context": execution_context,
            "gsa815_result": gsa815_result,
            "observe_verdict": observe_verdict,
            "outcome_context": outcome_context,
            "forensic_proof": forensic_proof,
            "audit_chain_valid": forensic_proof.get("chain_valid", False) if forensic_proof else False,
        }
