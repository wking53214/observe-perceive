"""test_observe_first.py -- OBSERVE runs first, in every profile, and the assessment is bound into the request (PR 10b).

Findings measured on `main` before this change (docs/race/IDENTITY_BINDING_DESIGN.md, finding 5, and the notes under it):
  1. the advisory profile ran the action and THEN assessed the patient, so a crash in OBSERVE, or a CRITICAL reading, was
     found only after the action had happened;
  2. a request refused at any stage before OBSERVE ran had no assessment and no notification, so a deteriorating patient
     whose request was refused was never paged;
  3. an action that raised was never followed by an assessment in the advisory profile;
  4. the request commitment, and the PERCEIVE ruling and approvals chained to it, said nothing about the assessment.
"""
import pytest
from datetime import datetime, timezone

from conftest import ChainArtifact
from governance_chain import verify_result
from observe_consolidated import ObserveClinicalEngine
from test_identity_binding import CRIT, make_desk, orch, run, vit
from test_audit_p5_fixes import run_strict, strict

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


class Counting(ObserveClinicalEngine):
    """An engine that records every evaluation, to prove the reading is assessed once per call."""
    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.calls = []

    def evaluate(self, vitals):
        self.calls.append(vitals.patient_id)
        return super().evaluate(vitals)


class Crashes(ObserveClinicalEngine):
    def evaluate(self, vitals):
        raise RuntimeError("OBSERVE crashed")


def watched(o):
    order = []
    real = o.observe_engine.evaluate

    def evaluate(v):
        order.append("OBSERVE")
        return real(v)
    o.observe_engine.evaluate = evaluate
    return order


# ---- 1. the order -----------------------------------------------------------------------------------------------------

def test_advisory_assesses_the_patient_before_the_action_not_after():
    o = orch()
    order = watched(o)

    def action(ctx):
        order.append("ACTION")
        return {"status": "done"}
    result = o.orchestrate_request(ChainArtifact("F1"), "escalate", action, vitals_snapshot=vit("P1", **CRIT),
                                   context={"execution_id": "e-f1"})
    assert result["status"] == "APPROVED_AND_EXECUTED" and order == ["OBSERVE", "ACTION"]
    assert result["observe_verdict"].regime.value == "critical"


def test_strict_assesses_before_anything_is_asked_of_perceive_and_before_the_action():
    o = strict(Counting())
    asked = []
    real = o.perceive.evaluate_request
    o.perceive.evaluate_request = lambda r: (asked.append(len(o.observe_engine.calls)), real(r))[1]
    result, order = run_strict(o, "f2", vit("P001"), "P001")
    assert result["status"] == "APPROVED_AND_EXECUTED" and order == ["observe", "executed"]
    assert asked == [1]                                                     # OBSERVE had already run when PERCEIVE was asked


@pytest.mark.parametrize("make", [lambda: orch(engine=Counting()), lambda: strict(Counting())])
def test_the_reading_is_assessed_exactly_once_per_call_in_every_profile(make):
    o = make()
    if o.profile == "strict":
        run_strict(o, "f3", vit("P001"), "P001")
    else:
        run(o, "f3", vit("P001"))
    assert o.observe_engine.calls == ["P001"]


def test_a_refused_request_is_assessed_once_too():
    o = orch(engine=Counting())
    run(o, "f4", vit("P1"), exec_id="e1")
    again = run(o, "f4", vit("P1"), exec_id="e2")                           # refused by the kernel: duplicate artifact
    assert again["status"] == "REJECTED" and o.observe_engine.calls == ["P1", "P1"]


# ---- 2. a refused request still has its assessment and its notification -------------------------------------------------

def test_a_critical_reading_on_a_request_the_kernel_refuses_is_still_paged():
    desk, pager = make_desk()
    o = orch(escalations=desk)
    run(o, "f5", vit("P1"), exec_id="e1")                                   # executes, calm
    refused = run(o, "f5", vit("P2", **CRIT), exec_id="e2", ctx={"patient_id": "P2"})
    assert refused["status"] == "REJECTED" and refused["observe_verdict"].regime.value == "critical"
    clinical = [c for c in pager.calls if c["kind"] == "clinical_escalation"]
    assert len(clinical) == 1 and clinical[0]["patient_id"] == "P2"
    assert refused["clinical_notification"]["status"] == "dispatched"


def test_every_record_carries_the_assessment_whatever_stage_refused_it():
    o = orch()
    run(o, "f6", vit("P1"), exec_id="e1")
    refused = run(o, "f6", vit("P1"), exec_id="e2")
    assert refused["status"] == "REJECTED" and refused["observe_verdict"] is not None


# ---- 3. an action that raised is still followed by an assessment ---------------------------------------------------------

def test_a_failed_action_has_an_assessment_and_a_chain_that_verifies():
    o = orch()

    def boom(ctx):
        raise IOError("downstream unavailable")
    result = o.orchestrate_request(ChainArtifact("F7"), "escalate", boom, vitals_snapshot=vit("P1", **CRIT),
                                   context={"execution_id": "e-f7"})
    assert result["status"] == "EXECUTION_FAILED" and result["observe_verdict"].regime.value == "critical"
    assert result["chain_verification"]["valid"], result["chain_verification"]["failed"]


# ---- 4. the assessment is bound into the request -----------------------------------------------------------------------

def test_the_request_commits_to_the_assessment_and_a_changed_one_breaks_verification():
    o = orch()
    result = run(o, "f8", vit("P1", **CRIT))
    request = result["governance_request"]
    binding = request.context["observe_assessment"]
    verdict = result["observe_verdict"]
    assert binding == {"status": "assessed", "decision_fingerprint": verdict.decision_fingerprint,
                       "state_commitment": verdict.state_commitment, "regime": "critical",
                       "escalation_required": True, "unassessable": False}
    assert verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive).valid
    binding["regime"] = "stable"                                            # rewrite what the request says OBSERVE found
    broken = verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive)
    assert not broken.valid and "request.state_commitment" in [c.name for c in broken.failed]


def test_calm_and_critical_readings_now_make_different_requests_on_the_same_artifact_and_context():
    calm = run(orch(), "f9", vit("P1"))["governance_request"].state_commitment
    crit = run(orch(), "f9", vit("P1", **CRIT))["governance_request"].state_commitment
    assert calm != crit


def test_a_request_with_no_vitals_has_no_assessment_binding():
    result = run(orch(), "f10", None)
    assert "observe_assessment" not in result["governance_request"].context


def test_a_supplied_assessment_that_is_not_the_one_made_is_an_identity_mismatch():
    fake = {"status": "assessed", "decision_fingerprint": "0" * 64, "state_commitment": "", "regime": "stable",
            "escalation_required": False, "unassessable": False}
    ran = []
    result = run(orch(), "f11", vit("P1", **CRIT), ctx={"observe_assessment": fake}, ran=ran)
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity" and ran == []
    assert any("observe_assessment" in p for p in result["identity_mismatch"]["problems"])
    assert result["observe_verdict"].regime.value == "critical"             # the reading is still assessed and told


# ---- 5. the crash policy, per profile -----------------------------------------------------------------------------------

def test_advisory_proceeds_when_observe_crashes_tells_a_person_and_commits_that_there_is_no_assessment():
    seen, ran = [], []
    result = run(orch(engine=Crashes(), alert_human=seen.append), "f12", vit("P1"), ran=ran)
    assert result["status"] == "APPROVED_AND_EXECUTED" and ran == ["ACTION"]
    assert result["observe_error"].startswith("RuntimeError") and result["observe_verdict"] is None
    assert [a["stage"] for a in seen] == ["observe"] and seen[0]["patient_id"] == "P1"
    assert result["governance_request"].context["observe_assessment"] == {"status": "unavailable",
                                                                           "error_type": "RuntimeError"}


def test_strict_refuses_when_observe_crashes_before_perceive_is_asked_and_tells_a_person():
    alerts = []
    o = strict(Crashes(), alerts)
    asked = []
    o.perceive.evaluate_request = lambda r: asked.append(r)
    result, order = run_strict(o, "f13", vit("P001"), "P001")
    assert result["status"] == "REJECTED" and result["refused_by"] == "observe" and "executed" not in order
    assert asked == []                                                      # PERCEIVE was never asked
    assert [a["stage"] for a in alerts] == ["observe"] and alerts[0]["patient_id"] == "P001"


def test_strict_can_raise_on_a_crash_when_asked():
    alerts = []
    o = strict(Crashes(), alerts)
    o.raise_on_stage_error = True
    with pytest.raises(RuntimeError):
        run_strict(o, "f14", vit("P001"), "P001")
    assert [a["stage"] for a in alerts] == ["observe"]


def test_strict_refuses_an_unassessable_reading_before_perceive_is_asked():
    import dataclasses
    o = strict(ObserveClinicalEngine())
    asked = []
    o.perceive.evaluate_request = lambda r: asked.append(r)
    bad = dataclasses.replace(vit("P001"), heart_rate=float("nan"), oxygen_saturation=float("nan"),
                              respiratory_rate=float("nan"), temperature=float("nan"))
    result, order = run_strict(o, "f15", bad, "P001")
    assert result["status"] == "REJECTED" and result["refused_by"] == "observe" and "executed" not in order
    assert asked == [] and result["observe_verdict"] is not None


def test_an_identity_mismatch_is_assessed_once_and_the_crash_is_told_once():
    seen = []
    result = run(orch(engine=Crashes(), alert_human=seen.append), "f16", vit("B"), ctx={"patient_id": "A"})
    assert result["refused_by"] == "identity" and result["observe_error"].startswith("RuntimeError")
    assert [a["stage"] for a in seen] == ["observe", "identity"] or [a["stage"] for a in seen] == ["identity", "observe"]
    assert len(seen) == 2


# ---- 6. a verifier can check the assessment against OBSERVE's own ledger and the reading the request names -----------------

def test_a_verifier_with_the_engine_checks_the_assessment_against_its_ledger_and_the_reading():
    o = orch()
    result = run(o, "f20", vit("P1", **CRIT))
    names = [c.name for c in verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive,
                                           observe=o.observe_engine).checks]
    assert {"assessment.matches_verdict", "assessment.in_observe_ledger", "assessment.matches_reading",
            "observe_ledger.integrity"} <= set(names)
    assert result["chain_verification"]["valid"]                              # the orchestrator verifies on the way out too


def test_an_assessment_that_is_not_in_the_engines_ledger_fails_verification():
    o, other = orch(), ObserveClinicalEngine()
    result = run(o, "f21", vit("P1", **CRIT))
    check = verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive, observe=other)
    assert not check.valid and "assessment.in_observe_ledger" in [c.name for c in check.failed]


def test_a_record_whose_verdict_was_swapped_fails_verification():
    import dataclasses
    o = orch()
    result = run(o, "f22", vit("P1", **CRIT))
    result["observe_verdict"] = dataclasses.replace(result["observe_verdict"], decision_fingerprint="0" * 64)
    check = verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive, observe=o.observe_engine)
    assert "assessment.matches_verdict" in [c.name for c in check.failed]


def test_the_reading_in_the_ledger_must_be_the_one_the_request_names():
    o = orch()
    result = run(o, "f23", vit("P1", **CRIT))
    result["identity"] = {**result["identity"], "observation_digest": "0" * 64}
    check = verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive, observe=o.observe_engine)
    assert "assessment.matches_reading" in [c.name for c in check.failed]


def test_a_request_that_commits_to_no_assessment_cannot_carry_one():
    o = orch(engine=Crashes(), alert_human=lambda a: None)
    result = run(o, "f24", vit("P1"))
    assert verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive).valid
    result["observe_verdict"] = ObserveClinicalEngine().evaluate(vit("P1"))
    assert "assessment.none_recorded" in [c.name for c in verify_result(result).failed]


# ---- 7. gaps a mutation check found --------------------------------------------------------------------------------------

def test_a_later_refusal_still_carries_the_observe_failure():
    o = orch(engine=Crashes(), alert_human=lambda a: None)
    run(o, "f30", vit("P1"), exec_id="e1")
    refused = run(o, "f30", vit("P1"), exec_id="e2")                          # refused by the kernel: duplicate artifact
    assert refused["status"] == "REJECTED" and refused["observe_error"].startswith("RuntimeError")
    assert refused.get("observe_verdict") is None


def test_a_recorded_assessment_replayed_on_a_fresh_orchestrator_is_not_a_mismatch():
    first = run(orch(), "f31", vit("P1", **CRIT))
    replay = run(orch(), "f31", vit("P1", **CRIT),
                 ctx={"observe_assessment": first["governance_request"].context["observe_assessment"]})
    assert replay["status"] == "APPROVED_AND_EXECUTED"


def test_the_advisory_profile_does_not_refuse_an_unassessable_reading():
    import dataclasses
    ran = []
    bad = dataclasses.replace(vit("P1"), heart_rate=float("nan"), oxygen_saturation=float("nan"),
                              respiratory_rate=float("nan"), temperature=float("nan"))
    result = run(orch(), "f32", bad, ran=ran)
    assert result["status"] == "APPROVED_AND_EXECUTED" and ran == ["ACTION"] and result["observe_verdict"].unassessable


def test_an_outer_call_keeps_its_observe_failure_when_an_inner_call_ran_inside_the_action():
    o = orch(engine=Crashes(), alert_human=lambda a: None)

    def action(ctx):
        o.orchestrate_request(ChainArtifact("f33-inner"), "escalate", lambda c: {"status": "done"},
                              vitals_snapshot=None, context={"execution_id": "e-inner"})
        raise IOError("downstream unavailable")
    result = o.orchestrate_request(ChainArtifact("f33"), "escalate", action, vitals_snapshot=vit("P1"),
                                   context={"execution_id": "e-f33"})
    assert result["status"] == "EXECUTION_FAILED" and result["observe_error"].startswith("RuntimeError")


def test_the_orchestrator_verifies_with_its_own_engine_on_the_way_out():
    result = run(orch(), "f34", vit("P1", **CRIT))
    names = [c["name"] for c in result["chain_verification"]["checks"]]
    assert "assessment.in_observe_ledger" in names and "assessment.matches_reading" in names


def test_a_verdict_with_another_state_commitment_or_regime_fails_verification():
    import dataclasses
    from observe_consolidated import OperationalRegime
    o = orch()
    result = run(o, "f35", vit("P1", **CRIT))
    original = result["observe_verdict"]
    for change in ({"state_commitment": "0" * 64}, {"regime": OperationalRegime.STABLE}):
        result["observe_verdict"] = dataclasses.replace(original, **change)
        assert "assessment.matches_verdict" in [c.name for c in verify_result(result, observe=o.observe_engine).failed], change
