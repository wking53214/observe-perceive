"""
Governance Integration Contracts

Defines the request/decision formats exchanged between:
- Sentinel OS → PERCEIVE
- PERCEIVE → Conservation Kernel
- Conservation Kernel → GSA-815
- GSA-815 → OBSERVE
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional, Any
from enum import Enum


class GovernanceRequestType(Enum):
    ESCALATE_PATIENT = "escalate_patient"
    MODIFY_RULE = "modify_rule"
    EXPORT_DATA = "export_data"
    EMERGENCY_OVERRIDE = "emergency_override"
    APPROVE_DECISION = "approve_decision"


class GovernanceApproval(Enum):
    APPROVED = "approved"
    REJECTED = "rejected"
    PENDING = "pending"


@dataclass
class GovernanceRequest:
    """Request from Sentinel/external system to PERCEIVE."""
    request_id: str
    request_type: GovernanceRequestType
    artifact_id: str
    artifact_content: str
    artifact_hash: str
    producer: str
    origin: str
    authority: str
    epistemic_status: str
    lineage: List[str] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class GovernanceDecision:
    """Decision from PERCEIVE."""
    request_id: str
    decision_id: str
    approval: GovernanceApproval
    applied_gates: List[str]
    unanimous_consensus: bool
    violations: List[str]
    policy_version: str
    perceive_audit_hash: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ConservationDecision:
    """Decision after Conservation Kernel verification."""
    governance_decision_id: str
    approval: GovernanceApproval
    conservation_receipt_id: str
    artifact_hash: str
    conservation_audit_hash: str
    verified: bool
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExecutionApproval:
    """Approval for GSA-815 execution."""
    request_id: str
    approval: GovernanceApproval
    conservation_decision_id: str
    conservation_receipt_id: str
    governance_audit_hash: str
    conservation_audit_hash: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class ExecutionContext:
    """Context for execution."""
    request_id: str
    approval: ExecutionApproval
    artifact_id: str
    artifact_hash: str
    producer: str
    lineage: List[str]
    execution_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class OutcomeContext:
    """Context for OBSERVE monitoring."""
    execution_id: str
    governance_decision_id: str
    conservation_decision_id: str
    governance_audit_hash: str
    conservation_audit_hash: str
    result_artifact_id: str
    result_artifact_hash: str
    producer: str
    lineage: List[str]
    outcome: Dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
