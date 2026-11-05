"""Regression tests for the audited defects OP-1 to OP-8 and the root-admission roster (agent P5).

Every test drives real objects: a strict orchestrator with a real RootAdmission, a real PERCEIVE kernel, a real
OBSERVE engine, a real Conservation Kernel and a sealed Gateway admission. Needs CNS and Governance Gateway.
"""
import dataclasses
import datetime as dt

import pytest

from conftest import ChainArtifact, importorskip_cns, make_perceive, make_vitals

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.decision_guard")
gw = importorskip_cns("governance_gateway.models")

from conservation_kernel import ConservationKernel, HmacSigner  # noqa: E402
from gateway_admission_adapter import GatewayAdmissionAdapter  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from observe_consolidated import ObserveClinicalEngine, RiskOutput  # noqa: E402
from pediatric_cassette import PediatricCassette  # noqa: E402
from root_admission import RootAdmission  # noqa: E402
from sentinel_perceive_adapter import SentinelPerceiveAdapter  # noqa: E402

SOURCE = HmacSigner(b"audit-p5-source-key-0123456789abcdefgh", key_id="audit-p5-src")
WHEN = dt.datetime.now(dt.timezone.utc).replace(second=0, microsecond=0) - dt.timedelta(hours=1)
JUSTIFIED = {"justification": "SpO2 trending down and heart rate rising", "severity": "warning"}


class Attested(ChainArtifact):
    def __init__(self, artifact_id, content="escalate P001"):
        super().__init__(artifact_id, content=content)
        self.metadata.occurred_at = WHEN
        self.metadata.event_time_attestation = SentinelPerceiveAdapter.attest_event_time(
            SOURCE, artifact_id, WHEN.isoformat())


def sealed(artifact_id, content="escalate P001"):
    return GatewayAdmissionAdapter.seal(
        artifact_id=artifact_id, payload={"content": content}, provenance={"source": "SENTINEL"},
        epistemic_status=gw.EpistemicStatus.INFERENCE, authority_actor="CHARGE_NURSE",
        authority_grant="unit-escalation", execute=True)


def strict(observe_engine, alerts=None):
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    return GovernanceOrchestrator(make_perceive(), kernel, observe_engine, profile="strict",
                                  source_signers=[SOURCE], root_admission=admission,
                                  alert_human=None if alerts is None else alerts.append)


def run_strict(orchestrator, artifact_id, vitals, patient, extra=None):
    """Run one strict request; return the result and the order OBSERVE and the action happened in."""
    order = []
    engine = orchestrator.observe_engine
    real = engine.evaluate

    def watched(v):
        order.append("observe")
        return real(v)
    engine.evaluate = watched

    def action(ctx):
        order.append("executed")
        return {"status": "done"}
    context = {"gateway_scope": "EXECUTE", "gateway_admission": sealed(artifact_id), "patient_id": patient,
               "execution_id": f"exec-{artifact_id}", **JUSTIFIED, **(extra or {})}
    result = orchestrator.orchestrate_request(Attested(artifact_id), "escalate", action, vitals_snapshot=vitals,
                                              context=context)
    return result, order


def all_nan():
    return dataclasses.replace(make_vitals(), heart_rate=float("nan"), oxygen_saturation=float("nan"),
                               respiratory_rate=float("nan"), temperature=float("nan"))


# ---- control ------------------------------------------------------------------------------------------------------

def test_control_strict_with_sound_vitals_observes_first_then_executes():
    result, order = run_strict(strict(ObserveClinicalEngine()), "p5-ok", make_vitals(), "P101")
    assert result["status"] == "APPROVED_AND_EXECUTED", (result.get("refused_by"), result.get("reason"))
    assert order == ["observe", "executed"]
    assert result["observe_enforced"] is True and result["observe_verdict"] is not None


# ---- OP-1: strict OBSERVE runs before the action, and an unassessable reading is a refusal -------------------------

def test_op1_strict_refuses_an_unassessable_observation_before_the_action_runs():
    result, order = run_strict(strict(ObserveClinicalEngine()), "p5-op1", all_nan(), "P111")
    assert result["status"] == "REJECTED"
    assert result["refused_by"] == "observe" and result["stage_refused"] is True
    assert "executed" not in order and order == ["observe"]
    assert result["observe_verdict"].unassessable is True


# ---- OP-2: an OBSERVE crash in strict mode is a refusal, and a person is told ---------------------------------------

class Exploding:
    def evaluate(self, vitals):
        raise RuntimeError("OBSERVE engine crashed")


def test_op2_strict_observe_crash_is_a_refusal_and_raises_the_human_alert():
    alerts = []
    result, order = run_strict(strict(Exploding(), alerts), "p5-op2", make_vitals(), "P112")
    assert result["status"] == "REJECTED"
    assert result["refused_by"] == "observe" and result["stage_refused"] is False
    assert "OBSERVE engine crashed" in result["stage_error"]
    assert "executed" not in order
    assert result["human_alert"] == "delivered"
    assert [a["stage"] for a in alerts] == ["observe"]


def test_op2_strict_with_no_alert_channel_still_refuses_and_says_no_channel():
    result, order = run_strict(strict(Exploding()), "p5-op2b", make_vitals(), "P113")
    assert result["status"] == "REJECTED" and "executed" not in order
    assert result["human_alert"] == "no_channel"


# ---- OP-3: a verdict with no evidence is not acceptable -------------------------------------------------------------

class AbstainingCassette(PediatricCassette):
    """Every engine abstains: nothing assessed the patient, so the fused 'stable' rests on no evidence."""

    def engines(self):
        def abstain(name):
            return lambda v: RiskOutput(name, 0.0, 0.0, {"stable": 1.0, "caution": 0.0, "warning": 0.0,
                                                         "critical": 0.0}, [], dt.datetime.now(dt.timezone.utc),
                                        abstained=True)
        return {n: abstain(n) for n in super().engines()}


def test_op3_strict_refuses_a_no_evidence_stable_verdict_for_a_hypoxic_tachycardic_child():
    vitals = dataclasses.replace(make_vitals(), oxygen_saturation=84.0, heart_rate=190)
    result, order = run_strict(strict(ObserveClinicalEngine(cassette=AbstainingCassette())), "p5-op3", vitals, "P114")
    assert result["status"] == "REJECTED"
    assert result["refused_by"] == "observe" and result["stage_refused"] is True
    assert "evidence" in result["reason"]
    assert "executed" not in order


# ---- OP-4: PERCEIVE sees the content's digest, its decision is bound to it, and admission compares it -------------

def _request(content):
    return SentinelPerceiveAdapter.sentinel_artifact_to_governance_request(
        ChainArtifact("same-id", content=content), "escalate", {"patient_id": "P1"})


def test_op4_perceive_is_handed_the_content_digest_and_its_decision_names_it():
    request = _request("escalate P001")
    policy_request = SentinelPerceiveAdapter.governance_request_to_policy_request(request)
    assert policy_request.artifact_id == "same-id" and policy_request.artifact_hash == request.artifact_hash
    decision = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), request)
    assert decision.artifact_id == "same-id" and decision.artifact_hash == request.artifact_hash
    other = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), _request("DISCHARGE P001"))
    assert other.artifact_hash != decision.artifact_hash


def test_op4_a_decision_for_content_a_is_not_admitted_for_content_b_with_the_same_id():
    from perceive_conservation_adapter import ConservationRefusal, PerceiveConservationAdapter
    a, b = _request("escalate P001"), _request("DISCHARGE P001, stop all monitoring")
    decision_a = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), a)
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    adapter = PerceiveConservationAdapter(kernel, admission)
    with pytest.raises(ConservationRefusal):
        adapter.verify_perceive_decision(decision_a, b.artifact_id, b.artifact_content, b.artifact_hash, request=b)
    assert "same-id" not in {x.artifact_id for x in kernel.ledger.artifacts()}
    # The honest pairing still verifies.
    decision_b = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), b)
    verified = adapter.verify_perceive_decision(decision_b, b.artifact_id, b.artifact_content, b.artifact_hash,
                                                request=b)
    assert verified.verified is True


def test_op4_root_admission_refuses_a_ruling_whose_decision_judged_other_content():
    from conservation_kernel import Actor, ActorKind, Artifact
    from root_admission import RootNotAdmitted, Ruling
    a, b = _request("escalate P001"), _request("DISCHARGE P001")
    decision_a = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), a)
    root_b = Artifact(artifact_id="same-id", content=b.artifact_content, propositions=(),
                      producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM))
    admission = RootAdmission()
    kernel = ConservationKernel(root_guard=admission)
    ruling = Ruling(decision=decision_a, request_id=b.request_id, artifact_id="same-id", artifact_hash=b.artifact_hash)
    with pytest.raises(RootNotAdmitted):
        admission.admit(kernel, ruling, root_b)
    assert admission(root_b) is False


def test_op4_an_unguarded_kernel_also_refuses_a_decision_about_other_content():
    from perceive_conservation_adapter import ConservationRefusal, PerceiveConservationAdapter
    a, b = _request("escalate P001"), _request("DISCHARGE P001")
    decision_a = SentinelPerceiveAdapter.evaluate_through_perceive(make_perceive(), a)
    adapter = PerceiveConservationAdapter(ConservationKernel(allow_unguarded_roots=True))
    with pytest.raises(ConservationRefusal, match="judged"):
        adapter.verify_perceive_decision(decision_a, b.artifact_id, b.artifact_content, b.artifact_hash, request=b)


def test_op4_root_admission_refuses_a_decision_that_names_no_document():
    from conservation_kernel import Actor, ActorKind, Artifact
    from governance_contracts import GovernanceApproval, GovernanceDecision
    from root_admission import RootNotAdmitted, Ruling
    root = Artifact(artifact_id="unnamed-1", content="escalate P001", propositions=(),
                    producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM))
    decision = GovernanceDecision(request_id="r1", decision_id="d1", approval=GovernanceApproval.APPROVED,
                                  applied_gates=["boundary_gate"], unanimous_consensus=True, violations=[],
                                  policy_version="1", perceive_audit_hash="abc")
    admission = RootAdmission()
    with pytest.raises(RootNotAdmitted):
        admission.admit(ConservationKernel(root_guard=admission),
                        Ruling(decision=decision, request_id="r1", artifact_id=root.artifact_id,
                               artifact_hash=root.content_digest), root)


# ---- the roster the acting side needs is held by the guard, where the installed CNS can hold it ---------------------

def test_the_admission_guard_is_held_to_the_required_roster_when_cns_can_hold_it():
    import inspect

    from cns_composition.decision_guard import DecisionGuard
    from root_admission import REQUIRED_SEATS, _guard_for

    guard = _guard_for(DecisionGuard)
    if "expected_seats" in inspect.signature(DecisionGuard.__init__).parameters:
        assert guard.expected_seats == REQUIRED_SEATS
    else:
        assert not hasattr(guard, "expected_seats"), "the pinned CNS has no roster pin; re-pin to get the check"


# ---- OP-5: an escalation with no justification is allowed, recorded, and a person is told ---------------------------

def test_op5_an_unjustified_escalation_is_allowed_recorded_and_alerted():
    alerts = []
    result, order = run_strict(strict(ObserveClinicalEngine(), alerts), "p5-op5", make_vitals(), "P115",
                               extra={"justification": ""})
    assert result["status"] == "APPROVED_AND_EXECUTED", (result.get("refused_by"), result.get("reason"))
    assert order == ["observe", "executed"]
    assert result["unjustified_escalation"] is True
    assert result["unjustified_escalation_alert"] == "delivered"
    assert [a["stage"] for a in alerts] == ["unjustified_escalation"]
    assert alerts[0]["request_id"] == result["governance_request"].request_id


def test_op5_the_alert_failing_or_missing_never_changes_the_outcome():
    result, _ = run_strict(strict(ObserveClinicalEngine()), "p5-op5b", make_vitals(), "P116",
                           extra={"justification": "  "})
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["unjustified_escalation"] is True and result["unjustified_escalation_alert"] == "no_channel"

    def broken(_alert):
        raise RuntimeError("pager down")
    orchestrator = strict(ObserveClinicalEngine())
    orchestrator.alert_human = broken
    result, _ = run_strict(orchestrator, "p5-op5c", make_vitals(), "P117", extra={"justification": ""})
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["unjustified_escalation_alert"] == "failed"


def test_op5_a_justified_escalation_carries_no_flag_and_sends_no_alert():
    alerts = []
    result, _ = run_strict(strict(ObserveClinicalEngine(), alerts), "p5-op5d", make_vitals(), "P118")
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert "unjustified_escalation" not in result and alerts == []
