"""
Innovation OS → governance chain Adapter

Feeds an innovation_os approval into the orchestrated governance chain, so a
human's decision about an innovation artifact is evaluated, contained,
conservation-verified and recorded like any other governed decision.

Direction of the seam
---------------------
innovation_os sits *upstream*. It is where ideas become artifacts, get
reviewed, and get approved or rejected by a named reviewer. The chain is
downstream of that: it governs the approval itself -- was this decision
permitted, does acting on it stay in safe bounds, was the transformation
conservative, and has this shape of approval come up before.

So the adapter converts an `ApprovalRecord` into the artifact shape the
chain's SentinelPerceiveAdapter already consumes. It does not import
innovation_os: the record is read structurally (`.approval_id`,
`.target_id`, `.reviewer`, `.decision`, `.rationale`, `.lineage_hash`,
`.decision_id`), the same duck-typed discipline every other seam here uses.

The provenance trap this seam is built around
---------------------------------------------
Every request the chain has handled so far was machine-originated. An
innovation_os approval is not: `ApprovalRecord.reviewer` is a person, and
the record carries their rationale.

Getting that mapping wrong in the obvious direction is a known failure. A
prior hand-run of the HERALD → Sentinel seam mapped a human-confirmed claim
onto Sentinel's "attested" status because it read as the natural fit, and a
provenance invariant written a month earlier -- for an unrelated purpose --
refused it: something attested still carries the name of the extractor that
derived it, and a human confirmation is not a derivation.

The same shape of error is available here, in both directions:

- Understating it. Labelling a human approval as SYSTEM authority erases the
  fact that a person decided, which is the one thing about the record worth
  preserving downstream.
- Overstating it. Labelling the *artifact* as human-origin because a human
  approved it. The reviewer authored a decision about the artifact; they did
  not author the artifact. Conflating those would let machine-generated
  content acquire human provenance by being approved.

So: authority is HUMAN (a person made this call), origin stays
INNOVATION_OS (the record was produced by that system), and epistemic status
is ATTESTED for an approval -- a human asserted it -- but never for a
rejection, where nothing was affirmed at all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

logger = logging.getLogger("InnovationGovernanceAdapter")


@dataclass
class _Status:
    """Duck-typed stand-in for a Sentinel status enum member.

    The chain reads `.value` off origin/authority/epistemic status objects
    and never checks their type, so an approval can enter the chain without
    observe-perceive taking a dependency on Sentinel's enums or on
    innovation_os's.
    """
    value: str


@dataclass
class _Metadata:
    origin_status: _Status
    authority_status: _Status
    epistemic_status: _Status
    parent_artifact_ids: List[str]


@dataclass
class ApprovalArtifact:
    """An innovation_os approval, in the artifact shape the chain consumes."""
    artifact_id: str
    content: str
    metadata: _Metadata


class InnovationGovernanceAdapter:
    """Converts innovation_os approvals into governed chain requests."""

    # An approval is a human assertion about an artifact; a rejection asserts
    # nothing. Kept as data rather than inline so the distinction is visible
    # and reviewable rather than buried in a conditional.
    _AFFIRMATIVE_DECISIONS = frozenset({"approve", "approved", "accept", "accepted"})

    @staticmethod
    def named_reviewer(approval) -> Optional[str]:
        """The reviewer's name, or None when nobody is attached.

        An empty, blank or missing reviewer is not a person. innovation_os's
        ApprovalEngine accepts an approval with reviewer="" (measured), and
        this adapter used to stamp every approval HUMAN / human_reviewed
        regardless -- so an automated pass wearing an approval's shape
        acquired human authority by arriving through this seam. citadel's
        check for approve_decision looked only for the presence of the
        `reviewer` key, which the adapter always supplied, so nothing
        downstream caught it either. Measured: reviewer "", "   " and None
        all came back APPROVED_AND_EXECUTED with authority HUMAN.
        """
        reviewer = getattr(approval, "reviewer", None)
        if not isinstance(reviewer, str) or not reviewer.strip():
            return None
        return reviewer.strip()

    @staticmethod
    def approval_to_artifact(approval) -> ApprovalArtifact:
        """Convert an innovation_os ApprovalRecord into the chain's artifact
        shape.

        The artifact being governed is *the approval*, not the thing approved.
        `target_id` (what was reviewed) is carried as lineage so the chain can
        trace back to it, but the identity is the approval's own -- otherwise
        two different reviewers approving the same target would collide into
        one artifact id and the chain would see one decision where there were
        two.
        """
        decision = (getattr(approval, "decision", "") or "").strip().lower()
        affirmative = decision in InnovationGovernanceAdapter._AFFIRMATIVE_DECISIONS
        reviewer = InnovationGovernanceAdapter.named_reviewer(approval)
        if reviewer is None:
            logger.warning(
                f"Innovation approval {getattr(approval, 'approval_id', '?')} names no "
                "reviewer -- recorded as UNATTRIBUTED, not HUMAN, and routed as "
                "requiring human oversight. Attach a reviewer to the ApprovalRecord "
                "to have it governed as a person's decision."
            )

        content = (
            f"Innovation approval {approval.approval_id}: "
            f"reviewer {approval.reviewer} recorded '{approval.decision}' "
            f"on target {approval.target_id}. "
            f"Rationale: {getattr(approval, 'rationale', '') or 'none recorded'}"
        )

        metadata = _Metadata(
            # The record was produced by innovation_os. That is a fact about
            # where it came from and does not change with the verdict.
            origin_status=_Status("INNOVATION_OS"),
            # A named person made this call. Preserving that is the whole
            # reason this seam is careful -- and the reason an approval with
            # nobody attached must not be called HUMAN.
            authority_status=_Status("HUMAN" if reviewer else "UNATTRIBUTED"),
            # ATTESTED only for an affirmative decision by a named person --
            # a human asserted something. A rejection affirms nothing, and an
            # unattributed approval was asserted by nobody, so both stay
            # UNVERIFIED rather than borrowing the standing of an assertion.
            epistemic_status=_Status("ATTESTED" if (affirmative and reviewer) else "UNVERIFIED"),
            # What was reviewed, and the decision it came from, are lineage --
            # traceable, but not this artifact's identity.
            parent_artifact_ids=[
                pid for pid in (
                    getattr(approval, "target_id", None),
                    getattr(approval, "decision_id", None),
                ) if pid
            ],
        )

        return ApprovalArtifact(
            artifact_id=approval.approval_id,
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def approval_context(approval, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Context for the chain's gates.

        `requires_human_oversight` is False when a named person has
        *already* reviewed this. PERCEIVE's sentinel gate raises a violation
        when a SYSTEM_ actor makes a change requiring human review, and that
        check exists to catch the absence of a person -- firing it on a record
        that is itself a person's decision would be the check misreading its
        own subject. When no person is named the absence is real, so the flag
        is raised and the `reviewer` key is left out entirely: citadel's
        approve_decision check looks for that key, and sending it empty was
        how an unattributed approval cleared the gate.

        `lineage_hash` is passed through so a downstream verifier can tie the
        chain's own record back to innovation_os's lineage chain rather than
        having to trust that they refer to the same event.
        """
        reviewer = InnovationGovernanceAdapter.named_reviewer(approval)
        context: Dict[str, Any] = {
            "subject_id": getattr(approval, "target_id", None),
            "innovation_decision": getattr(approval, "decision", None),
            "innovation_lineage_hash": getattr(approval, "lineage_hash", None),
            "requires_human_oversight": reviewer is None,
            "human_reviewed": reviewer is not None,
            # The reviewer's own rationale IS the justification PERCEIVE's
            # citadel gate looks for. Passing it under that key is what makes
            # the gate a real check on this path rather than a formality: an
            # approval recorded with a one-word rationale fails it.
            "justification": getattr(approval, "rationale", "") or "",
        }
        if reviewer is not None:
            context["reviewer"] = reviewer
        if extra:
            context.update(extra)
        return context

    @classmethod
    def govern_approval(
        cls,
        orchestrator,
        approval,
        operation_func=None,
        operation_type: str = "approve",
        context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Run one innovation_os approval through the full governance chain.

        `operation_type` defaults to "approve", which the chain maps to its
        APPROVE_DECISION request type -- the neutral path. It is not mapped to
        "modify" or "override": an innovation approval is not a rule change or
        an emergency, and routing it through those gate sets would apply
        checks written for a different kind of act.
        """
        artifact = cls.approval_to_artifact(approval)
        return orchestrator.orchestrate_request(
            artifact,
            operation_type,
            operation_func or (lambda execution_context: {
                "status": "recorded",
                "approval_id": artifact.artifact_id,
            }),
            context=cls.approval_context(approval, context),
        )
