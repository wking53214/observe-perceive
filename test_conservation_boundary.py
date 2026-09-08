"""test_conservation_boundary.py -- the kernel adjudicates real propositions.

Measured 2026-09-08: both artifacts handed to the Conservation Kernel on
the spine's path carried `propositions=()`, so every proposition-level
dimension of the constitution was a loop over nothing. These tests hold
the boundary the spine advertises.
"""
from __future__ import annotations

from conftest import ChainArtifact, run_chain
from conservation_kernel import Actor, ActorKind, AuthorizationEvent, TransitionKind
from perceive_conservation_adapter import PerceiveConservationAdapter


def _decision_artifact(kernel, result):
    return kernel.ledger.artifact(f"decision-{result['governance_decision'].decision_id}")


def test_the_kernel_receives_the_requests_epistemic_claims_as_propositions(orchestrator, kernel):
    result, _ = run_chain(orchestrator, "b1", artifact=ChainArtifact("b1", origin="CLINICIAN", authority="CLINICIAN", epistemic="EXPLICIT"))
    assert result["status"] == "APPROVED_AND_EXECUTED"
    root = kernel.ledger.artifact("b1")
    assert [p.proposition_id for p in root.propositions] == ["p-b1"]
    p = root.propositions[0]
    assert (p.epistemic_status.value, p.origin.value, p.authority.value) == ("OBSERVATION", "HUMAN_ORIGINATED", "NONE")
    assert p.metadata["declared_epistemic_status"] == "EXPLICIT" and p.metadata["declared_authority"] == "CLINICIAN"
    decision = _decision_artifact(kernel, result)
    ids = [q.proposition_id for q in decision.propositions]
    assert ids == ["p-b1", f"p-decision-{result['governance_decision'].decision_id}"]
    born = decision.propositions[1]
    assert (born.epistemic_status.value, born.origin.value, born.authority.value) == ("DECISION", "MACHINE_ORIGINATED", "NONE")
    assert born.parent_proposition_ids == ("p-b1",)
    report = kernel.ledger.reports()[-1]
    assert report.accepted and "EPISTEMIC_STATUS" in {d.value for d in report.checked_dimensions}


def test_a_role_label_is_not_authority(orchestrator, kernel):
    """`authority="CLINICIAN"` is who produced it, not an authorization event."""
    result, _ = run_chain(orchestrator, "b2", artifact=ChainArtifact("b2", authority="CLINICIAN"))
    assert kernel.ledger.artifact("b2").propositions[0].authority.value == "NONE"


def test_a_human_authority_claim_with_a_dangling_reference_is_refused_by_the_kernel(orchestrator, kernel):
    result, ran = run_chain(orchestrator, "b3", artifact=ChainArtifact("b3", authority="CLINICIAN"),
                            context={"authorization_refs": ["auth-does-not-exist"]})
    assert result["status"] == "REJECTED" and result["refused_by"] == "conservation" and ran == []
    assert "root refused" in result["reason"] and "authorization_refs not in registry" in result["reason"]


def test_an_authorization_about_another_subject_does_not_authorize_this_one(orchestrator, kernel):
    kernel.registry.add_authorization(AuthorizationEvent(
        authorization_id="auth-other", authorized_by=Actor(actor_id="reviewer-1", kind=ActorKind.HUMAN),
        subject_id="p-somebody-else", transition_kind=TransitionKind.AUTHORITY_ESCALATION,
        from_value="NONE", to_value="HUMAN_AUTHORIZED", reason="unrelated",
    ))
    result, ran = run_chain(orchestrator, "b4", context={"authorization_refs": ["auth-other"]})
    assert result["status"] == "REJECTED" and result["refused_by"] == "conservation" and ran == []
    assert "no authorization for this proposition" in result["reason"]


def test_a_matching_authorization_makes_the_proposition_human_authorized(orchestrator, kernel):
    kernel.registry.add_authorization(AuthorizationEvent(
        authorization_id="auth-b5", authorized_by=Actor(actor_id="reviewer-1", kind=ActorKind.HUMAN),
        subject_id="p-b5", transition_kind=TransitionKind.AUTHORITY_ESCALATION,
        from_value="NONE", to_value="HUMAN_AUTHORIZED", reason="reviewed",
    ))
    result, ran = run_chain(orchestrator, "b5", context={"authorization_refs": ["auth-b5"]})
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert kernel.ledger.artifact("b5").propositions[0].authority.value == "HUMAN_AUTHORIZED"


def test_an_unknown_epistemic_word_is_recorded_as_unknown_not_upgraded(orchestrator, kernel):
    run_chain(orchestrator, "b6", artifact=ChainArtifact("b6", epistemic="VERIFIED_TRUTH"))
    p = kernel.ledger.artifact("b6").propositions[0]
    assert p.epistemic_status.value == "UNKNOWN" and p.metadata["declared_epistemic_status"] == "VERIFIED_TRUTH"


def test_the_mapping_never_upgrades():
    """Every spine word maps to the same or a lower kernel status than its
    literal reading; FACT stays FACT only when the source is cited, which the
    adapter always does."""
    from governance_contracts import GovernanceRequest, GovernanceRequestType
    request = GovernanceRequest(request_id="r", request_type=GovernanceRequestType.APPROVE_DECISION, artifact_id="a",
                                artifact_content="c", artifact_hash="h", producer="x", origin="ROBOT", authority="ROOT",
                                epistemic_status="FACT")
    p = PerceiveConservationAdapter.input_proposition("a", "c", request)
    assert (p.epistemic_status.value, p.origin.value, p.authority.value) == ("FACT", "MACHINE_ORIGINATED", "NONE")
    assert p.source_refs == ("a",) and p.metadata["declared_authority"] == "ROOT"
