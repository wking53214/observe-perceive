"""A root registers only after a CNS roster approves that document (root_admission).

The kernel here is built the way a deployment builds it: with `root_guard=RootAdmission()` and no
`allow_unguarded_roots`. Nothing in this file turns the guard off.
"""
import threading
from datetime import datetime, timezone

import pytest

from conftest import importorskip_cns, make_perceive, run_chain
from conservation_kernel import ConservationKernel
from conservation_kernel.errors import RootAdmissionError
from governance_contracts import GovernanceApproval, GovernanceDecision
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyEnforcementConfig, PolicyManifest

importorskip_cns("cns_composition.decision_guard", "root admission needs the CNS release with DecisionGuard")
importorskip_cns("conservation_kernel.cns_connector", "root admission needs the kernel's CNS connector")

from root_admission import REQUIRED_SEATS, RequestGate, RootAdmission, RootNotAdmitted, Ruling, RulingGate  # noqa: E402


def _guarded(perceive=None):
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    orchestrator = GovernanceOrchestrator(perceive or make_perceive(), kernel, ObserveClinicalEngine(),
                                          root_admission=admission)
    return orchestrator, kernel, admission


def _enforcing_perceive():
    perceive = PerceiveGovernanceKernel(enforcement=PolicyEnforcementConfig(enforce_export_controls=True))
    perceive.register_manifest(PolicyManifest(manifest_id="m", version="1.0.0",
                                              created_at=datetime.now(timezone.utc), policies={}))
    return perceive


def _decision(request_id="req-1", audit="abc"):
    return GovernanceDecision(
        request_id=request_id, decision_id="dec-1", approval=GovernanceApproval.APPROVED, applied_gates=["boundary_gate"],
        unanimous_consensus=True, violations=[], policy_version="1.0.0", perceive_audit_hash=audit)


def test_a_guarded_kernel_runs_a_request_to_completion_without_the_flag():
    orchestrator, kernel, _ = _guarded()
    result, ran = run_chain(orchestrator, "adm-1")
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert ran
    assert kernel.has_root_guard


def test_the_same_kernel_refuses_a_root_written_to_it_directly():
    """The gap the flag left open: a caller who holds the kernel and skips the orchestrator."""
    orchestrator, kernel, _ = _guarded()
    run_chain(orchestrator, "adm-2")
    with pytest.raises(RootAdmissionError):
        kernel.register_root(_fresh_root("forged-root", "anything"))


def test_a_kernel_with_a_guard_but_an_orchestrator_without_admission_refuses_every_root():
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    orchestrator = GovernanceOrchestrator(make_perceive(), kernel, ObserveClinicalEngine())  # no root_admission
    result, ran = run_chain(orchestrator, "adm-3")
    assert result["status"] == "REJECTED" and not ran
    assert result["refused_by"] == "conservation" and result["stage_refused"] is True


def test_a_request_perceive_refused_still_has_its_root_recorded():
    """The orchestrator registers the root of a refused request too, so the refusal is in the ledger. Admission
    must not change that: the seat says a ruling exists for the document, not that PERCEIVE approved it."""
    orchestrator, kernel, _ = _guarded(_enforcing_perceive())
    result, ran = run_chain(orchestrator, "adm-4", operation="export",
                            context={"export_type": "full_record", "has_consent": False, "will_encrypt": False})
    assert result["status"] == "REJECTED" and not ran
    assert result["refused_by"] == "perceive" and result["stage_refused"] is True
    assert "adm-4" in {a.artifact_id for a in kernel.ledger.artifacts()}


def test_a_replayed_artifact_id_is_refused_at_admission():
    orchestrator, _, _ = _guarded()
    first, _ = run_chain(orchestrator, "adm-5")
    assert first["status"] == "APPROVED_AND_EXECUTED"
    again, ran = run_chain(orchestrator, "adm-5")
    assert again["status"] == "REJECTED" and not ran
    assert again["refused_by"] == "conservation"
    assert "root admission refused" in again["reason"]


def test_an_approval_does_not_outlive_its_request():
    orchestrator, kernel, admission = _guarded()
    run_chain(orchestrator, "adm-6")
    root = kernel.ledger.artifact("adm-6")
    assert admission(root) is False


def test_the_guard_is_closed_before_any_request():
    _, _, admission = _guarded()
    assert admission(object()) is False


def test_an_approval_opened_on_one_thread_is_closed_on_another():
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    root = _fresh_root("adm-7")
    ruling = Ruling(decision=_decision("req-7"), request_id="req-7", artifact_id=root.artifact_id,
                    artifact_hash=root.content_digest)
    admission.admit(kernel, ruling, root)  # open on this thread
    seen = []
    t = threading.Thread(target=lambda: seen.append(admission(root)))
    t.start()
    t.join()
    assert seen == [False]
    admission.close()
    assert admission(root) is False


def _fresh_root(artifact_id="fresh-1", content="escalate P001"):
    from conservation_kernel import Actor, ActorKind, Artifact
    return Artifact(artifact_id=artifact_id, content=content, propositions=(),
                    producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM))


def test_a_ruling_about_another_request_is_refused():
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    root = _fresh_root()
    ruling = Ruling(decision=_decision("req-A"), request_id="req-B", artifact_id=root.artifact_id,
                    artifact_hash=root.content_digest)
    with pytest.raises(RootNotAdmitted, match="different request"):
        admission.admit(kernel, ruling, root)
    assert admission(root) is False


def test_a_ruling_with_no_audit_record_is_refused():
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    root = _fresh_root()
    ruling = Ruling(decision=_decision("req-1", audit=""), request_id="req-1", artifact_id=root.artifact_id,
                    artifact_hash=root.content_digest)
    with pytest.raises(RootNotAdmitted, match="no audit record"):
        admission.admit(kernel, ruling, root)


def test_a_ruling_about_another_document_is_refused_by_the_agreement_seat():
    """Every seat passes on its own; they judged different documents."""
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    root = _fresh_root("doc-1", "true content")
    other = _fresh_root("doc-1", "inflated content")
    ruling = Ruling(decision=_decision(), request_id="req-1", artifact_id=other.artifact_id,
                    artifact_hash=other.content_digest)
    with pytest.raises(RootNotAdmitted, match="different subjects"):
        admission.admit(kernel, ruling, root)


def test_a_kernel_with_no_guard_is_refused_by_the_roster():
    admission = RootAdmission()
    kernel = ConservationKernel(allow_unguarded_roots=True)
    root = _fresh_root()
    ruling = Ruling(decision=_decision(), request_id="req-1", artifact_id=root.artifact_id,
                    artifact_hash=root.content_digest)
    with pytest.raises(RootNotAdmitted, match="no root guard"):
        admission.admit(kernel, ruling, root)


@pytest.mark.parametrize("gate", [RequestGate(), RulingGate()])
def test_the_perceive_gates_fail_closed_on_the_wrong_candidate(gate):
    result = gate.check("not a ruling")
    assert result.outcome.value == "terminal_breach"
    assert result.subject_digest == ""


def test_a_request_that_names_no_request_id_is_refused():
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    root = _fresh_root()
    ruling = Ruling(decision=_decision(), request_id="", artifact_id=root.artifact_id,
                    artifact_hash=root.content_digest)
    with pytest.raises(RootNotAdmitted, match="no request to bind"):
        admission.admit(kernel, ruling, root)


def test_the_required_seats_name_every_check():
    assert REQUIRED_SEATS == ("PERCEIVE:perceive_request", "Conservation_Kernel:root_admission",
                              "PERCEIVE:perceive_ruling", "CNS:same_subject")
