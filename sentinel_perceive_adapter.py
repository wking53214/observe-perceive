"""
Sentinel OS → PERCEIVE Adapter

Converts Sentinel artifacts to PERCEIVE governance requests.
Preserves artifact identity, provenance, authority, epistemic state.
"""

from governance_contracts import GovernanceRequest, GovernanceRequestType
from datetime import datetime, timezone
import hashlib
import sys
import os

# Lazy import to handle cross-repo dependencies
def _import_sentinel_types():
    sentinel_path = os.path.join(os.path.dirname(__file__), '..', 'sentinel_os')
    if sentinel_path not in sys.path:
        sys.path.insert(0, sentinel_path)
    from sentinel_os.conservation.types import (
        SentinelArtifact, ArtifactMetadata, EpistemicStatus, AuthorityStatus
    )
    return SentinelArtifact, ArtifactMetadata, EpistemicStatus, AuthorityStatus


class SentinelPerceiveAdapter:
    """Converts Sentinel artifacts to PERCEIVE requests."""

    @staticmethod
    def sentinel_artifact_to_governance_request(
        artifact,  # SentinelArtifact (typing removed to avoid import)
        operation_type: str,
        context: dict = None
    ) -> GovernanceRequest:
        """
        Convert SentinelArtifact to GovernanceRequest.

        Args:
            artifact: The Sentinel artifact
            operation_type: "escalate", "modify", "export", "override"
            context: Additional context (age_months, risk_level, etc.)

        Returns:
            GovernanceRequest ready for PERCEIVE evaluation
        """
        if context is None:
            context = {}

        # Map Sentinel operation to PERCEIVE request type
        request_type_map = {
            "escalate": GovernanceRequestType.ESCALATE_PATIENT,
            "modify": GovernanceRequestType.MODIFY_RULE,
            "export": GovernanceRequestType.EXPORT_DATA,
            "override": GovernanceRequestType.EMERGENCY_OVERRIDE,
        }
        request_type = request_type_map.get(operation_type, GovernanceRequestType.APPROVE_DECISION)

        return GovernanceRequest(
            request_id=artifact.artifact_id,
            request_type=request_type,
            artifact_id=artifact.artifact_id,
            artifact_content=artifact.content,
            artifact_hash=SentinelPerceiveAdapter._compute_hash(artifact.content),
            producer=artifact.metadata.origin_status.value if hasattr(artifact.metadata.origin_status, 'value') else str(artifact.metadata.origin_status),
            origin=artifact.metadata.origin_status.value if hasattr(artifact.metadata.origin_status, 'value') else "SENTINEL",
            authority=artifact.metadata.authority_status.value if hasattr(artifact.metadata.authority_status, 'value') else "SYSTEM",
            epistemic_status=artifact.metadata.epistemic_status.value if hasattr(artifact.metadata.epistemic_status, 'value') else "INFERRED",
            lineage=artifact.metadata.parent_artifact_ids or [],
            context=context,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute canonical SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()
