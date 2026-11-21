"""test_identity_binding.py -- what a governance decision is about: the attempt, the patient, and the reading.

Each group reproduces a finding measured on `main` before this change (see docs/race/IDENTITY_BINDING_DESIGN.md):
  1. `request_id` is the artifact id, so a refused attempt and its retry were indistinguishable by id;
  2. an action about patient A was governed with patient B's vitals, in advisory and in strict, with nothing naming it;
  3. with no patient in the context PERCEIVE's subject was the artifact, so per-patient limits never accumulated;
  4. the request commitment was identical for calm and CRITICAL vitals.
"""
import dataclasses
from datetime import datetime, timedelta, timezone

from conftest import ChainArtifact, make_perceive
from conservation_kernel import ConservationKernel, HmacSigner
from root_admission import refuse_all_evidence
from governance_chain import observation_digest, verify_result
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from sentinel_perceive_adapter import SentinelPerceiveAdapter

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
CRIT = dict(hr=168, o2=83.0, rr=46, t=39.5)


def vit(pid="P1", hr=100, o2=98.0, rr=24, t=37.0, ts=T0, **ctx):
    return VitalsSnapshot(pid, ts, hr, o2, rr, t, {"age_months": 24, **ctx})


def orch(**kw):
    return GovernanceOrchestrator(make_perceive(), ConservationKernel(allow_unguarded_roots=True, evidence_guard=refuse_all_evidence),
                                  kw.pop("engine", ObserveClinicalEngine()), **kw)


def run(o, art_id, vitals, ctx=None, exec_id=None, ran=None):
    def action(c):
        if ran is not None:
            ran.append("ACTION")
        return {"status": "done"}
    context = {"execution_id": exec_id or f"exec-{art_id}", **(ctx or {})}
    return o.orchestrate_request(ChainArtifact(art_id), "escalate", action, vitals_snapshot=vitals, context=context)


# ---- 1. an attempt has its own id --------------------------------------------------------------------------------------

def test_every_attempt_has_its_own_id_though_a_retry_shares_the_artifacts(tmp_path):
    o = orch(receipts=ReceiptLog(tmp_path / "r.jsonl"))
    first = run(o, "ART-1", vit(), exec_id="e1")
    again = run(o, "ART-1", vit(), exec_id="e2")                           # refused by the kernel: a duplicate artifact
    assert first["status"] == "APPROVED_AND_EXECUTED" and again["status"] == "REJECTED"
    assert first["governance_request"].request_id == again["governance_request"].request_id == "ART-1"
    assert first["attempt_id"] and again["attempt_id"] and first["attempt_id"] != again["attempt_id"]
    assert len(o.receipts.find(request_id="ART-1")) == 2                   # the request id alone cannot tell them apart
    assert len(o.receipts.find(attempt_id=first["attempt_id"])) == 1 and len(o.receipts.find(attempt_id=again["attempt_id"])) == 1


def test_the_attempt_id_factory_is_the_callers_to_set():
    counter = iter(range(100))
    o = orch(attempt_id_factory=lambda: f"attempt-{next(counter)}")
    assert run(o, "A1", vit())["attempt_id"] == "attempt-0" and run(o, "A2", vit())["attempt_id"] == "attempt-1"


def test_the_attempt_id_is_in_the_hashed_record_so_it_cannot_be_changed_afterwards(tmp_path):
    o = orch(receipts=ReceiptLog(tmp_path / "r.jsonl"))
    result = run(o, "A1", vit())
    assert o.receipts.matches(result) and result["receipt"]["attempt_id"] == result["attempt_id"]
    result["attempt_id"] = "someone-elses-attempt"
    assert not o.receipts.matches(result)


def test_an_invalid_request_still_has_an_attempt_id():
    result = orch().orchestrate_request(None, "escalate", lambda c: {}, vitals_snapshot=vit(), context={})
    assert result["status"] == "INVALID_REQUEST" and result["attempt_id"]


class Pager:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)


def make_desk():
    pager = Pager()
    return EscalationDesk(NotificationPolicy((Tier("charge", pager, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": HmacSigner(b"identity-test-responder-key-0123456789", key_id="nurse")}), pager


def test_an_alert_and_a_ticket_name_the_attempt_they_belong_to():
    class Crashes(ObserveClinicalEngine):
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")
    seen = []
    legacy = run(orch(engine=Crashes(), alert_human=seen.append), "A1", vit())
    assert seen[0]["attempt_id"] == legacy["attempt_id"]
    desk, pager = make_desk()
    ticketed = run(orch(engine=Crashes(), escalations=desk), "A2", vit())
    assert pager.calls[0]["attempt_id"] == ticketed["attempt_id"]


def test_a_clinical_notification_names_the_attempt():
    desk, pager = make_desk()
    result = run(orch(escalations=desk), "A1", vit(**CRIT), ctx={"patient_id": "P1"})
    assert result["clinical_notification"]["status"] == "dispatched"
    assert pager.calls[0]["attempt_id"] == result["attempt_id"]


# ---- 2. the patient the action is about must be the patient observed -----------------------------------------------------

def test_advisory_refuses_an_action_about_one_patient_with_another_patients_vitals():
    ran, seen = [], []
    engine = ObserveClinicalEngine()
    o = orch(engine=engine, alert_human=seen.append)
    result = run(o, "ART-2", vit("PATIENT-B", **CRIT), ctx={"patient_id": "PATIENT-A"}, ran=ran)
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity" and result["stage_refused"] is True
    assert ran == []                                                       # the action never ran
    assert result["governance_request"].artifact_id == "ART-2"            # the record names what was refused
    assert len(o.perceive.audit_ledger.entries) == 0                       # PERCEIVE was never asked
    mismatch = result["identity_mismatch"]
    assert mismatch["vitals_patient"] == "PATIENT-B" and mismatch["context"] == {"patient_id": "PATIENT-A"}
    assert "PATIENT-A" in result["reason"] and "PATIENT-B" in result["reason"]
    assert result["identity"]["observation_patient"] == "PATIENT-B"


def test_a_mismatch_is_not_an_ordinary_refusal_so_a_person_is_told_naming_the_patient_of_the_vitals():
    seen = []
    result = run(orch(alert_human=seen.append), "ART-2", vit("PATIENT-B"), ctx={"patient_id": "PATIENT-A"})
    assert result["human_alert"] == "delivered" and len(seen) == 1
    assert seen[0]["stage"] == "identity" and seen[0]["patient_id"] == "PATIENT-B" and seen[0]["reason"] == "identity_mismatch"
    assert seen[0]["attempt_id"] == result["attempt_id"]


def test_a_critical_reading_on_a_refused_mismatch_is_still_assessed_and_paged_for_the_vitals_patient():
    """The review's blocker: the refusal returned before OBSERVE, so a deteriorating child's reading reached nobody."""
    desk, pager = make_desk()
    result = run(orch(escalations=desk), "ART-2", vit("PATIENT-B", **CRIT), ctx={"patient_id": "PATIENT-A"})
    assert result["status"] == "REJECTED" and result["observe_verdict"].regime.value == "critical"
    assert result["clinical_notification"]["status"] == "dispatched"
    clinical = [c for c in pager.calls if c["kind"] == "clinical_escalation"]
    assert len(clinical) == 1 and clinical[0]["patient_id"] == "PATIENT-B"
    assert any(c["kind"] == "stage_error" and c["stage"] == "identity" for c in pager.calls)   # and the mismatch is paged too


def test_an_observe_crash_while_refusing_a_mismatch_is_alerted_and_does_not_hide_the_refusal():
    class Crashes(ObserveClinicalEngine):
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")
    seen = []
    result = run(orch(engine=Crashes(), alert_human=seen.append), "A1", vit("B"), ctx={"patient_id": "A"})
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity"
    assert result["observe_error"].startswith("RuntimeError") and [a["stage"] for a in seen] == ["identity", "observe"]


def test_strict_refuses_it_too_before_the_action_runs_and_tells_a_person():
    from test_audit_p5_fixes import run_strict, strict
    alerts = []
    result, order = run_strict(strict(ObserveClinicalEngine(), alerts), "id-strict", vit("PATIENT-B", **CRIT), "PATIENT-A")
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity"
    assert "executed" not in order                                         # the action never ran
    # the desk also pages the CRITICAL reading itself (no "stage" key); the stage alert is the one about identity
    stage_alerts = [a for a in alerts if "stage" in a]
    assert [a["stage"] for a in stage_alerts] == ["identity"] and stage_alerts[0]["patient_id"] == "PATIENT-B"


def test_a_subject_id_that_is_an_entity_is_not_compared_with_the_vitals_patient():
    """Five adapters put a non-patient entity (an approval target, a task, a source) in subject_id; PERCEIVE already gives
    patient_id precedence. The review's second blocker: the first version refused all of them."""
    both = run(orch(), "A1", vit("P1"), ctx={"patient_id": "P1", "subject_id": "DEC-42"})
    assert both["status"] == "APPROVED_AND_EXECUTED" and both["identity"]["patient_id"] == "P1"
    assert SentinelPerceiveAdapter.governance_request_to_policy_request(both["governance_request"]).subject_id == "P1"
    entity_only = run(orch(), "A2", vit("P1"), ctx={"subject_id": "DEC-42"})
    assert entity_only["status"] == "APPROVED_AND_EXECUTED"                 # not refused, and nothing is invented for it
    assert entity_only["identity"] == {"patient_id": None, "subject_source": "context",
                                       "observation_digest": observation_digest(vit("P1")), "observation_patient": "P1"}
    assert "patient_id" not in entity_only["governance_request"].context


def test_the_real_approval_adapter_still_runs_with_a_patient_beside_its_target():
    from approval_governance_adapter import ApprovalGovernanceAdapter
    from test_approval_governance_adapter import _approval
    o = orch()
    result = ApprovalGovernanceAdapter.govern_approval(o, _approval(), context={"patient_id": "P1"})
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")


def test_agreement_is_not_a_mismatch_and_numbers_compare_as_text():
    ok = run(orch(), "A1", vit("P1"), ctx={"patient_id": "P1"})
    assert ok["status"] == "APPROVED_AND_EXECUTED" and ok["identity"]["subject_source"] == "context"
    sub = run(orch(), "A2", vit("P1"), ctx={"subject_id": "P1"})
    assert sub["status"] == "APPROVED_AND_EXECUTED" and sub["identity"]["subject_source"] == "context"
    numeric = run(orch(), "A3", vit("5"), ctx={"patient_id": 5})
    assert numeric["status"] == "APPROVED_AND_EXECUTED"


def test_a_supplied_observation_digest_must_be_the_digest_of_the_vitals():
    v = vit("P1")
    right = run(orch(), "A1", v, ctx={"patient_id": "P1", "observation_digest": observation_digest(v)})
    assert right["status"] == "APPROVED_AND_EXECUTED"
    wrong = run(orch(), "A2", v, ctx={"patient_id": "P1", "observation_digest": observation_digest(vit("P1", **CRIT))})
    assert wrong["status"] == "REJECTED" and "observation_digest" in wrong["reason"]


def test_an_identity_refusal_can_be_audited_from_its_receipt(tmp_path):
    o = orch(receipts=ReceiptLog(tmp_path / "r.jsonl"))
    result = run(o, "ART-9", vit("B"), ctx={"patient_id": "A"})
    assert result["receipt"]["attempt_id"] == result["attempt_id"] and result["receipt"]["request_id"] == "ART-9"
    assert o.receipts.matches(result)
    checked = ReceiptLog(tmp_path / "r.jsonl").verify_all(kernel=o.conservation_kernel, perceive=o.perceive)
    assert [c["valid"] for c in checked] == [True], checked                  # the review: it used to fail 'request.present'


# ---- 3. the patient is the subject PERCEIVE reasons about ---------------------------------------------------------------

def test_with_no_patient_in_the_context_the_vitals_patient_is_bound_into_the_request_and_perceives_subject():
    result = run(orch(), "ART-3", vit("PATIENT-C"))
    request = result["governance_request"]
    assert request.context["patient_id"] == "PATIENT-C"                    # committed, not just assumed
    assert result["identity"] == {"patient_id": "PATIENT-C", "subject_source": "vitals",
                                  "observation_digest": observation_digest(vit("PATIENT-C")),
                                  "observation_patient": "PATIENT-C"}
    assert SentinelPerceiveAdapter.governance_request_to_policy_request(request).subject_id == "PATIENT-C"


def test_a_patients_escalations_accumulate_across_requests_so_the_per_patient_limits_can_apply():
    o = orch()
    for i in range(3):
        assert run(o, f"ART-{i}", vit("PATIENT-D"), exec_id=f"e{i}")["status"] == "APPROVED_AND_EXECUTED"
    day, hour, minute = o.perceive.gov_state.escalation_window_counts("PATIENT-D", datetime.now(timezone.utc) + timedelta(minutes=1))
    assert day == 3                                                         # before this change: 0, each keyed by its artifact


def test_with_neither_vitals_nor_a_patient_the_subject_stays_the_artifact_and_says_so():
    result = run(orch(), "ART-4", None)
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["identity"]["subject_source"] == "artifact"
    assert result["identity"]["patient_id"] is None and result["identity"]["observation_digest"] is None
    assert "patient_id" not in result["governance_request"].context and "observation_digest" not in result["governance_request"].context


def test_the_callers_own_context_is_left_as_it_was_given():
    ctx = {"execution_id": "e1"}
    orch().orchestrate_request(ChainArtifact("A1"), "escalate", lambda c: {}, vitals_snapshot=vit("P1"), context=ctx)
    assert ctx == {"execution_id": "e1"}


# ---- 4. the request commitment covers the reading ---------------------------------------------------------------------

def commitment(vitals, art="ART-5"):
    return run(orch(), art, vitals, ctx={"patient_id": vitals.patient_id}, exec_id="same")["governance_request"].state_commitment


def test_calm_and_critical_vitals_no_longer_give_the_same_commitment():
    assert commitment(vit("P1")) != commitment(vit("P1", **CRIT))          # measured identical before
    assert commitment(vit("P1")) == commitment(vit("P1"))                  # and the same reading is the same commitment


def test_the_digest_covers_every_field_of_the_reading_and_not_the_order_of_its_context():
    base = vit("P1")
    d = observation_digest(base)
    assert d == observation_digest(vit("P1")) and len(d) == 64
    for changed in (dataclasses.replace(base, patient_id="P2"), dataclasses.replace(base, timestamp=T0 + timedelta(seconds=1)),
                    dataclasses.replace(base, heart_rate=101), dataclasses.replace(base, oxygen_saturation=97.0),
                    dataclasses.replace(base, respiratory_rate=25), dataclasses.replace(base, temperature=37.1),
                    dataclasses.replace(base, context={"age_months": 25})):
        assert observation_digest(changed) != d
    a = dataclasses.replace(base, context={"age_months": 24, "x": 1})
    b = dataclasses.replace(base, context={"x": 1, "age_months": 24})
    assert observation_digest(a) == observation_digest(b)
    assert observation_digest(None) is None


def test_the_full_chain_still_verifies_with_the_patient_and_digest_in_the_request():
    o = orch(receipts=None)
    result = run(o, "ART-6", vit("P1", **CRIT))
    assert result["status"] == "APPROVED_AND_EXECUTED"
    verdict = verify_result(result)
    assert verdict.valid, verdict.as_dict()["failed"]


def test_a_receipted_record_with_the_new_fields_verifies_from_the_file_alone(tmp_path):
    o = orch(receipts=ReceiptLog(tmp_path / "r.jsonl"))
    run(o, "ART-7", vit("P1"))
    reloaded = ReceiptLog(tmp_path / "r.jsonl")                            # the file alone, as a verifier would read it
    assert all(r["valid"] for r in reloaded.verify_all(kernel=o.conservation_kernel, perceive=o.perceive))


def test_vitals_that_name_no_patient_make_no_claim_either_way():
    """An empty patient id is 'no patient named', not a patient called ''."""
    blank = vit("")
    named = run(orch(), "A1", blank, ctx={"patient_id": "P1"})
    assert named["status"] == "APPROVED_AND_EXECUTED" and named["identity"]["subject_source"] == "context"
    unnamed = run(orch(), "A2", blank)
    assert unnamed["status"] == "APPROVED_AND_EXECUTED" and unnamed["identity"]["subject_source"] == "artifact"
    assert "patient_id" not in unnamed["governance_request"].context


# ---- found by review: robustness and consistency -----------------------------------------------------------------------

def test_odd_vitals_no_longer_crash_the_request_with_no_record():
    import threading

    class Slotted:
        __slots__ = ("patient_id", "timestamp", "heart_rate", "oxygen_saturation", "respiratory_rate", "temperature", "context")

        def __init__(self):
            self.patient_id, self.timestamp, self.heart_rate = "P1", T0, 100
            self.oxygen_saturation, self.respiratory_rate, self.temperature, self.context = 98.0, 24, 37.0, {"age_months": 24}
    assert len(observation_digest(Slotted())) == 64                        # no __dict__: read field by field
    locked = dataclasses.replace(vit("P1"), context={"age_months": 24, "lock": threading.Lock()})
    assert len(observation_digest(locked)) == 64                             # a real digest, not the 'undigestable' marker
    assert observation_digest(dict(patient_id="P1", heart_rate=1))
    assert observation_digest(object()).startswith("undigestable:")          # marked, never raised, never hidden
    result = run(orch(), "ART-L", locked)
    assert result["status"] == "APPROVED_AND_EXECUTED"                       # it ran on main, and still does


def test_a_context_that_is_not_a_mapping_is_a_refusal_not_a_crash():
    result = orch().orchestrate_request(ChainArtifact("A1"), "escalate", lambda c: {}, vitals_snapshot=vit("P1"), context=[1, 2])
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity" and "not a mapping" in result["reason"]


def test_a_nested_call_does_not_change_what_the_outer_call_records():
    inner_result = {}
    o = orch()

    def executor_that_calls_the_orchestrator_again(ctx):
        inner_result["r"] = o.orchestrate_request(ChainArtifact("INNER"), "escalate", lambda c: {"status": "done"},
                                                  vitals_snapshot=vit("P2"), context={"execution_id": "inner-exec"})
        return {"status": "done"}
    outer = o.orchestrate_request(ChainArtifact("OUTER"), "escalate", executor_that_calls_the_orchestrator_again,
                                  vitals_snapshot=vit("P1"), context={"execution_id": "outer-exec"})
    assert outer["status"] == "APPROVED_AND_EXECUTED" and inner_result["r"]["status"] == "APPROVED_AND_EXECUTED"
    assert outer["attempt_id"] != inner_result["r"]["attempt_id"]
    assert outer["identity"]["patient_id"] == "P1" and inner_result["r"]["identity"]["patient_id"] == "P2"
    assert outer["governance_request"].context["patient_id"] == "P1"        # the wrong value used to be hashed into the receipt


def test_a_patient_id_written_as_a_number_is_committed_as_text_so_limits_do_not_split():
    o = orch()
    a = run(o, "N1", vit("5"), ctx={"patient_id": 5}, exec_id="e1")            # the caller's int
    b = run(o, "N2", vit("5"), exec_id="e2")                                   # bound from the vitals, text
    assert a["governance_request"].context["patient_id"] == "5" and b["governance_request"].context["patient_id"] == "5"
    day, _, _ = o.perceive.gov_state.escalation_window_counts("5", datetime.now(timezone.utc) + timedelta(minutes=1))
    assert day == 2                                                          # not 1 and 1


def test_a_patient_id_of_zero_is_a_patient_not_nobody():
    result = run(orch(), "Z1", vit("0"), ctx={"patient_id": 0})
    assert result["governance_request"].context["patient_id"] == "0"
    assert SentinelPerceiveAdapter.governance_request_to_policy_request(result["governance_request"]).subject_id == "0"


def test_a_caller_supplied_digest_is_compared_without_case_or_surrounding_space():
    v = vit("P1")
    shouted = " " + observation_digest(v).upper() + " "
    assert run(orch(), "D1", v, ctx={"patient_id": "P1", "observation_digest": shouted})["status"] == "APPROVED_AND_EXECUTED"


def test_an_empty_patient_id_in_the_context_names_nobody():
    result = run(orch(), "E1", vit("P1"), ctx={"patient_id": ""})
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["identity"]["subject_source"] == "vitals"
    assert result["governance_request"].context["patient_id"] == "P1"


# ---- review round 2: readings as a dict, a request that cannot be built, values that cannot be read -------------------

def test_a_reading_given_as_a_dict_is_compared_with_the_request_too():
    ran = []
    result = run(orch(), "D1", dict(patient_id="B", **CRIT), ctx={"patient_id": "A"}, ran=ran)
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity" and ran == []
    assert result["identity_mismatch"]["vitals_patient"] == "B"


def test_a_mismatched_request_that_cannot_be_built_is_still_refused_alerted_and_assessed():
    class Weird:
        def __repr__(self):
            raise RuntimeError("cannot be shown")
    seen = []
    result = run(orch(alert_human=seen.append), "W1", vit("B", **CRIT), ctx={"patient_id": "A", "x": Weird()})
    assert result["status"] == "REJECTED" and result["refused_by"] == "identity"
    assert result["governance_request"] is None and result["observe_verdict"].regime.value == "critical"
    assert [a["stage"] for a in seen] == ["identity"] and seen[0]["patient_id"] == "B"


def test_a_request_that_cannot_be_built_and_has_no_mismatch_still_raises_as_before():
    class Weird:
        def __repr__(self):
            raise RuntimeError("cannot be shown")
    import pytest
    with pytest.raises(RuntimeError):
        run(orch(), "W2", vit("A"), ctx={"patient_id": "A", "x": Weird()})


def test_a_patient_that_cannot_be_read_is_a_mismatch_not_a_crash():
    class BadVitals:
        @property
        def patient_id(self):
            raise RuntimeError("no patient")

    class BadStr:
        def __str__(self):
            raise RuntimeError("no text")
    ran = []
    first = run(orch(), "R1", BadVitals(), ctx={"patient_id": "A"}, ran=ran)
    second = run(orch(), "R2", vit("B"), ctx={"patient_id": BadStr()}, ran=ran)
    for result in (first, second):
        assert result["status"] == "REJECTED" and result["refused_by"] == "identity"
    assert ran == []


def test_per_call_state_is_restored_when_an_inner_call_raises():
    o = orch(attempt_id_factory=iter(["outer", "inner"]).__next__)
    seen = []

    def action(_):
        try:
            o.orchestrate_request(ChainArtifact("IN"), "escalate", None, vitals_snapshot=vit("P1"),
                                  context={"execution_id": "e-in"})
        except Exception:                                                   # noqa: BLE001 - the inner call may raise
            pass
        seen.append(o._seen.attempt_id)
        return {"status": "done"}
    result = o.orchestrate_request(ChainArtifact("OUT"), "escalate", action, vitals_snapshot=vit("P1"),
                                   context={"execution_id": "e-out"})
    assert seen == ["outer"] and result["attempt_id"] == "outer"
