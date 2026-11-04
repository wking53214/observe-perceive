"""Regression tests for the audited defects OP-1 to OP-8 and the root-admission roster (agent P5).

Every test drives real objects: a strict orchestrator with a real RootAdmission, a real PERCEIVE kernel, a real
OBSERVE engine, a real Conservation Kernel and a sealed Gateway admission. Needs CNS and Governance Gateway.
"""
import dataclasses
import datetime as dt

from conftest import ChainArtifact, importorskip_cns, make_perceive, make_vitals

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.decision_guard")
gw = importorskip_cns("governance_gateway.models")

from conservation_kernel import ConservationKernel, HmacSigner  # noqa: E402
from gateway_admission_adapter import GatewayAdmissionAdapter  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from observe_consolidated import ObserveClinicalEngine  # noqa: E402
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
