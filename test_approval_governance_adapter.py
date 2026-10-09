"""Approval records entering the governance chain (written for innovation_os).

The seam's real risk is provenance, not plumbing. An approval record is
the first thing to reach this chain that a *human* authored, and there are two
symmetrical ways to get that wrong -- erase the person, or let the person's
approval launder machine-generated content into human-origin. Most of these
tests are about that, and only a few about whether the request flows.

Precedent: a prior hand-run of the HERALD -> Sentinel seam mapped a
human-confirmed claim onto "attested" because it read as the natural fit, and
a month-old provenance invariant refused it. Same shape of error is available
here; these are the checks that would catch it.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

import pytest

from conservation_kernel import ConservationKernel
from governance_contracts import GovernanceApproval, GovernanceRequestType
from governance_orchestrator import GovernanceOrchestrator
from approval_governance_adapter import ApprovalGovernanceAdapter
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest
from sentinel_perceive_adapter import SentinelPerceiveAdapter


@dataclass
class _ApprovalRecord:
    """Structurally identical to innovation_os.governance.models.ApprovalRecord,
    deliberately not imported -- proving the seam is duck-typed and that
    observe-perceive takes no dependency on innovation_os."""
    approval_id: str
    target_id: str
    reviewer: str
    decision: str
    rationale: str
    decision_id: Optional[str] = None
    lineage_hash: Optional[str] = None


def _approval(decision="approved", **overrides):
    base = dict(
        approval_id="appr-001",
        target_id="idea-042",
        reviewer="w.king",
        decision=decision,
        rationale="Addresses the servicing backlog without new vendor risk.",
        decision_id="dec-007",
        lineage_hash="a1b2c3d4",
    )
    base.update(overrides)
    return _ApprovalRecord(**base)


@pytest.fixture
def orchestrator():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="approval-seam-manifest",
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        policies={},
    ))
    return GovernanceOrchestrator(kernel, ConservationKernel(), ObserveClinicalEngine())


# ---------------------------------------------------------------------------
# Provenance: the part that matters
# ---------------------------------------------------------------------------

def test_a_human_reviewer_is_recorded_as_human_authority():
    """Labelling a person's decision as SYSTEM authority would erase the one
    fact about this record most worth preserving downstream."""
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval())
    assert artifact.metadata.authority_status.value == "HUMAN"


def test_the_artifact_does_not_acquire_human_origin_from_being_approved():
    """The inverse error, and the more dangerous one. The reviewer authored a
    decision *about* the artifact; they did not author the artifact. If
    approval conferred human origin, machine-generated content could launder
    itself into human provenance simply by being signed off."""
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval())
    assert artifact.metadata.origin_status.value == "APPROVAL_RECORD"
    assert artifact.metadata.origin_status.value != "HUMAN"


def test_an_approval_is_attested_but_a_rejection_asserts_nothing():
    """A human approving something asserts it. A human rejecting something
    affirms nothing at all, so it must not borrow the standing of an
    assertion."""
    approved = ApprovalGovernanceAdapter.approval_to_artifact(_approval("approved"))
    rejected = ApprovalGovernanceAdapter.approval_to_artifact(_approval("rejected"))

    assert approved.metadata.epistemic_status.value == "ATTESTED"
    assert rejected.metadata.epistemic_status.value == "UNVERIFIED"


@pytest.mark.parametrize("word", ["approve", "Approved", "ACCEPT", "accepted"])
def test_affirmative_decisions_are_recognised_regardless_of_wording(word):
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval(word))
    assert artifact.metadata.epistemic_status.value == "ATTESTED"


@pytest.mark.parametrize("word", ["reject", "denied", "needs work", ""])
def test_anything_not_clearly_affirmative_is_not_treated_as_an_assertion(word):
    """Fails closed on unknown vocabulary. A decision word this adapter does
    not recognise must not be optimistically read as approval."""
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval(word))
    assert artifact.metadata.epistemic_status.value == "UNVERIFIED"


# ---------------------------------------------------------------------------
# Identity and lineage
# ---------------------------------------------------------------------------

def test_the_governed_artifact_is_the_approval_not_the_thing_approved():
    """Two reviewers approving the same target are two decisions. Keying the
    artifact on target_id would collide them into one and the chain would see
    a single decision where there were two."""
    first = ApprovalGovernanceAdapter.approval_to_artifact(
        _approval(approval_id="appr-001", reviewer="w.king")
    )
    second = ApprovalGovernanceAdapter.approval_to_artifact(
        _approval(approval_id="appr-002", reviewer="a.reviewer")
    )
    assert first.artifact_id != second.artifact_id
    assert first.artifact_id == "appr-001"


def test_what_was_reviewed_is_carried_as_lineage():
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval())
    assert "idea-042" in artifact.metadata.parent_artifact_ids
    assert "dec-007" in artifact.metadata.parent_artifact_ids


def test_missing_lineage_ids_are_omitted_not_recorded_as_none():
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(
        _approval(decision_id=None)
    )
    assert None not in artifact.metadata.parent_artifact_ids
    assert artifact.metadata.parent_artifact_ids == ["idea-042"]


def test_the_reviewer_and_rationale_survive_into_the_governed_content():
    """A downstream reader of the chain's record should be able to see who
    decided and why without going back to the source."""
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval())
    assert "w.king" in artifact.content
    assert "servicing backlog" in artifact.content


def test_approval_lineage_hash_is_passed_through_for_cross_system_tie_back():
    """So a verifier can tie the chain's record to the source's own lineage
    chain rather than trusting that they describe the same event."""
    context = ApprovalGovernanceAdapter.approval_context(_approval())
    assert context["approval_lineage_hash"] == "a1b2c3d4"


# ---------------------------------------------------------------------------
# Gate interaction
# ---------------------------------------------------------------------------

def test_human_oversight_flag_is_not_raised_on_a_record_that_is_itself_review():
    """PERCEIVE's sentinel gate flags a SYSTEM_ actor making a change that
    requires human review. That check exists to catch the *absence* of a
    person. Firing it on a record which is itself a person's decision would
    be the check misreading its own subject."""
    context = ApprovalGovernanceAdapter.approval_context(_approval())
    assert context["requires_human_oversight"] is False
    assert context["human_reviewed"] is True


def test_an_approval_routes_to_the_neutral_request_type(orchestrator):
    """Not "modify" and not "override" -- an approval record is neither a
    rule change nor an emergency, and those gate sets were written for
    different acts."""
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(_approval())
    request = SentinelPerceiveAdapter.sentinel_artifact_to_governance_request(
        artifact, "approve", ApprovalGovernanceAdapter.approval_context(_approval())
    )
    assert request.request_type is GovernanceRequestType.APPROVE_DECISION


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_an_approval_runs_the_whole_chain(orchestrator):
    result = ApprovalGovernanceAdapter.govern_approval(orchestrator, _approval())

    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    decision = result["governance_decision"]
    assert decision.approval is GovernanceApproval.APPROVED
    assert result["conservation_decision"].verified is True


def test_provenance_survives_the_whole_chain(orchestrator):
    """The point of the careful mapping above is that it still holds at the
    far end -- a chain that quietly normalised authority somewhere in the
    middle would pass every unit test above and still lose the fact."""
    result = ApprovalGovernanceAdapter.govern_approval(orchestrator, _approval())
    proof = result["forensic_proof"]
    assert result["status"] == "APPROVED_AND_EXECUTED"
    # The approval's own lineage reached the forensic record.
    assert result["execution_approval"] is not None
    assert proof is None or "idea-042" in str(proof.get("lineage", ""))


def test_a_rejection_is_governed_too_not_silently_dropped(orchestrator):
    """A reviewer saying no is a governed decision. Dropping it would mean the
    chain only ever sees approvals, and the recurrence layer downstream would
    never learn that a class of idea keeps getting turned down."""
    result = ApprovalGovernanceAdapter.govern_approval(
        orchestrator, _approval("rejected")
    )
    assert result["status"] in ("APPROVED_AND_EXECUTED", "REJECTED")
    assert result["governance_decision"] is not None


# ---------------------------------------------------------------------------
# The approve_decision gate policy this seam required PERCEIVE to grow
# ---------------------------------------------------------------------------

def test_approve_decision_selects_a_real_gate_set_not_just_the_boundary():
    """GovernanceRequestType.APPROVE_DECISION was declared in the shared
    cross-system contract but PERCEIVE had no gates for it: the boundary gate
    refused it outright as an unknown type. A type accepted at the door but
    unknown to _select_gates is worse -- it gets only the boundary gate, which
    is an approval that skipped every real check."""
    from perceive_consolidated import PerceiveGovernanceKernel, PolicyRequest

    gates = PerceiveGovernanceKernel._select_gates(PolicyRequest(
        request_id="x", request_type="approve_decision",
        subject_id="idea-042", actor_id="w.king",
    ))
    assert gates != ["boundary_gate"], "approve_decision fell through to boundary only"
    assert "citadel" in gates            # the rationale is substantive
    assert "invariant_validator" in gates
    assert "sentinel" in gates           # anomalous approval volume
    # An approval is not an escalation and not an emergency; those gate sets
    # implement policies written for different acts.
    assert "escalation_rate_policy" not in gates
    assert "micropatch" not in gates


def test_an_approval_with_a_rubber_stamp_rationale_is_refused(orchestrator):
    """The reviewer's rationale is passed as PERCEIVE's `justification`, which
    is what makes citadel a real check on this path. A one-word sign-off
    should not clear it."""
    result = ApprovalGovernanceAdapter.govern_approval(
        orchestrator, _approval(rationale="ok")
    )
    assert result["status"] == "REJECTED"
    assert any("justification" in v.lower() for v in result["governance_decision"].violations)


@pytest.mark.parametrize("reviewer", ["", "   ", None])
def test_an_unnamed_reviewer_is_not_reported_as_human_review(reviewer):
    """innovation_os (now retired) accepted an approval with reviewer="" (measured against
    its ApprovalEngine). This adapter used to stamp it HUMAN / human_reviewed
    regardless, and the whole chain approved and executed it."""
    approval = _approval(reviewer=reviewer)
    artifact = ApprovalGovernanceAdapter.approval_to_artifact(approval)
    assert artifact.metadata.authority_status.value == "UNATTRIBUTED"
    assert artifact.metadata.epistemic_status.value == "UNVERIFIED"
    context = ApprovalGovernanceAdapter.approval_context(approval)
    assert context["human_reviewed"] is False
    assert context["requires_human_oversight"] is True
    assert "reviewer" not in context, "an empty reviewer key is what cleared citadel"


@pytest.mark.parametrize("reviewer", ["", "   ", None])
def test_an_unnamed_reviewer_is_refused_by_the_chain(orchestrator, reviewer):
    ran = []
    result = ApprovalGovernanceAdapter.govern_approval(
        orchestrator, _approval(approval_id=f"appr-unnamed-{len(reviewer or '')}", reviewer=reviewer),
        operation_func=lambda ctx: ran.append(True),
    )
    assert result["status"] == "REJECTED", result
    assert result["governance_decision"].violations
    assert ran == [], "an approval nobody made was executed"


def test_an_approval_with_no_named_reviewer_is_refused():
    """An approval decision with nobody attached is an automated pass wearing
    a human approval's clothes. citadel's matching-context check refuses it."""
    from perceive_consolidated import PolicyGates, PolicyRequest

    output = PolicyGates.citadel(PolicyRequest(
        request_id="x", request_type="approve_decision",
        subject_id="idea-042", actor_id="unknown",
        context={"justification": "A sufficiently long and substantive rationale."},
    ))
    assert output.approved is False
    assert any("missing required context" in v for v in output.violation_details)


@pytest.mark.parametrize("reviewer", ["", "   ", None, 42])
def test_citadel_requires_a_reviewer_name_not_merely_the_key(reviewer):
    """The check used to be `"reviewer" in context`, so a caller sending the
    key with nothing in it cleared it. Presence of a name, not of a key."""
    from perceive_consolidated import PolicyGates, PolicyRequest

    output = PolicyGates.citadel(PolicyRequest(
        request_id="x", request_type="approve_decision",
        subject_id="idea-042", actor_id="unknown",
        context={"reviewer": reviewer,
                 "justification": "A sufficiently long and substantive rationale."},
    ))
    assert output.approved is False
    assert any("missing required context" in v for v in output.violation_details)
