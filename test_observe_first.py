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

from conftest import ChainArtifact, make_test_desk
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


def test_a_supplied_assessment_is_dropped_and_the_one_committed_is_the_one_made():
    fake = {"status": "assessed", "decision_fingerprint": "0" * 64, "state_commitment": "", "regime": "stable",
            "escalation_required": False, "unassessable": False}
    result = run(orch(), "f11", vit("P1", **CRIT), ctx={"observe_assessment": fake})
    assert result["status"] == "APPROVED_AND_EXECUTED"
    committed = result["governance_request"].context["observe_assessment"]
    assert committed != fake and committed["regime"] == "critical" and committed["decision_fingerprint"]


def test_a_supplied_assessment_with_no_vitals_is_dropped_not_committed():
    for fake in ({"status": "unavailable", "error_type": None}, {"status": "Assessed"}, "stable"):
        result = run(orch(), "f11b", None, ctx={"observe_assessment": fake})
        assert "observe_assessment" not in result["governance_request"].context


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
    result["governance_request"].context["observation_digest"] = "0" * 64           # the digest the request COMMITS to
    check = verify_result(result, kernel=o.conservation_kernel, perceive=o.perceive, observe=o.observe_engine)
    assert "assessment.matches_reading" in [c.name for c in check.failed]
    result["identity"].pop("observation_digest", None)                              # an uncommitted copy decides nothing
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


def test_a_recorded_context_replayed_on_the_same_orchestrator_is_not_refused_or_alerted():
    seen = []
    o = orch(alert_human=seen.append)
    first = run(o, "f31", vit("P1", **CRIT))
    replay = run(o, "f31b", vit("P1", **CRIT),
                 ctx={"observe_assessment": first["governance_request"].context["observe_assessment"]})
    assert replay["status"] == "APPROVED_AND_EXECUTED" and seen == []


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
    assert result["observe_human_alert"] == "delivered"                           # and so does who was told


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


# ---- 8. review round: a forged record, a malformed binding, who was told, strict and attestation -----------------------------

def _recommit(request):
    from governance_chain import request_state
    from governance_contracts import compute_state_commitment
    request.state_commitment = compute_state_commitment(None, request_state(request))


@pytest.mark.parametrize("forged", [{"regime": "warning"}, {"escalation_required": False}])
def test_a_record_forged_in_one_field_fails_against_the_engines_own_entry(forged):
    """Each field the ledger entry holds is compared on its own: forging either alone, in the request and the verdict
    together and with the commitment recomputed, is caught only by the engine's own entry."""
    import dataclasses
    from observe_consolidated import OperationalRegime
    o = orch()
    result = run(o, "f40b", vit("P1", **CRIT))
    request = result["governance_request"]
    request.context["observe_assessment"].update(forged)
    _recommit(request)
    changes = {"regime": OperationalRegime.WARNING} if "regime" in forged else {"escalation_required": False}
    result["observe_verdict"] = dataclasses.replace(result["observe_verdict"], **changes)
    check = verify_result(result, observe=o.observe_engine)
    assert "assessment.matches_ledger_verdict" in [c.name for c in check.failed]


def test_a_ticket_for_an_observe_crash_is_on_every_record():
    desk, pager = make_desk()
    o = orch(engine=Crashes(), escalations=desk)
    run(o, "f45", vit("P1"), exec_id="e1")
    refused = run(o, "f45", vit("P1"), exec_id="e2")
    assert refused["status"] == "REJECTED" and refused["observe_escalation_ticket"]
    assert [c["stage"] for c in pager.calls if c["kind"] == "stage_error"] == ["observe", "observe"]


def test_a_record_rewritten_to_hide_a_critical_reading_fails_against_the_engines_own_entry():
    o = orch()
    result = run(o, "f40", vit("P1", **CRIT))
    import dataclasses
    from observe_consolidated import OperationalRegime
    request = result["governance_request"]
    request.context["observe_assessment"].update(regime="stable", escalation_required=False)
    _recommit(request)                                                         # the commitment is unkeyed: it recomputes
    result["observe_verdict"] = dataclasses.replace(result["observe_verdict"], regime=OperationalRegime.STABLE)
    check = verify_result(result, observe=o.observe_engine)
    assert "assessment.matches_ledger_verdict" in [c.name for c in check.failed]


@pytest.mark.parametrize("binding", [{"status": "Assessed"}, {"status": "assessed-ish"}, "stable", None, {}])
def test_a_committed_assessment_that_is_not_one_the_orchestrator_writes_fails_verification(binding):
    o = orch()
    result = run(o, "f41", vit("P1"))
    request = result["governance_request"]
    request.context["observe_assessment"] = binding
    _recommit(request)
    assert "assessment.well_formed" in [c.name for c in verify_result(result, observe=o.observe_engine).failed]


def test_a_committed_assessment_with_no_reading_beside_it_fails_verification():
    o = orch()
    result = run(o, "f42", None)
    request = result["governance_request"]
    request.context["observe_assessment"] = {"status": "unavailable", "error_type": None}
    _recommit(request)
    assert "assessment.well_formed" in [c.name for c in verify_result(result).failed]


def test_whether_a_person_was_told_of_an_observe_crash_is_on_every_record_not_only_the_successful_one():
    def broken(_):
        raise OSError("pager down")
    o = orch(engine=Crashes(), alert_human=broken)
    run(o, "f43", vit("P1"), exec_id="e1")
    refused = run(o, "f43", vit("P1"), exec_id="e2")
    assert refused["status"] == "REJECTED" and refused["observe_human_alert"] == "failed"
    failed = o.orchestrate_request(ChainArtifact("f43b"), "escalate", lambda c: (_ for _ in ()).throw(IOError("x")),
                                   vitals_snapshot=vit("P1"), context={"execution_id": "e-f43b"})
    assert failed["status"] == "EXECUTION_FAILED" and failed["observe_human_alert"] == "failed"


def test_an_observe_crash_is_told_even_when_the_request_cannot_be_built():
    seen = []
    o = orch(engine=Crashes(), alert_human=seen.append)

    class Unbuildable:
        artifact_id = "f44"
        content = b"x"
        metadata = {}
        @property
        def producer(self):
            raise RuntimeError("cannot read the artifact")
    with pytest.raises(Exception):
        o.orchestrate_request(Unbuildable(), "escalate", lambda c: {}, vitals_snapshot=vit("P1"), context={})
    assert [a["stage"] for a in seen] == ["observe"] and seen[0]["patient_id"] == "P1"


def test_in_strict_a_reading_is_assessed_even_when_the_request_is_then_refused_for_attestation(perceive):
    """Named change: the request commits to the assessment, so the assessment is made before the request exists and so
    before its attestation can be judged. The reading is the caller's argument, not the artifact's claim."""
    from conftest import run_chain
    from conservation_kernel import ConservationKernel
    from governance_orchestrator import GovernanceOrchestrator
    engine = Counting()
    strict_o = GovernanceOrchestrator(perceive, ConservationKernel(root_guard=lambda artifact: True), engine,
                                      profile="strict", source_signers=[], escalations=make_test_desk())
    unattested, ran = run_chain(strict_o, "st-att", vitals=True, context={"gateway_scope": "EXECUTE"})
    assert unattested["status"] == "REJECTED" and unattested["refused_by"] == "source_attestation" and ran == []
    assert len(engine.calls) == 1 and unattested["observe_verdict"] is not None


def test_a_binding_that_disagrees_with_the_record_on_escalation_or_unassessable_fails_verification():
    for field, value in (("escalation_required", False), ("unassessable", True)):
        o = orch()
        result = run(o, f"f46-{field}", vit("P1", **CRIT))
        request = result["governance_request"]
        request.context["observe_assessment"][field] = value
        _recommit(request)
        assert "assessment.matches_verdict" in [c.name for c in verify_result(result).failed], field
