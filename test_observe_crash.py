"""test_observe_crash.py -- an OBSERVE crash is never a quiet one, and never moves a patient's tracked state.

OBSERVE moves a patient's escalation state before it fingerprints and records. A crash part way through used to leave the
patient marked "already escalated, locked" for a reading no ledger holds (so the next critical reading was swallowed as a
repeat), propagate out of `ClinicalGovernanceSystem` with no decision, and, on the orchestrator's advisory path, tell nobody.
`evaluate` is now all-or-nothing for the patient's tracked state, so a crash is exactly as if the reading never ran: a crash
after the state moved is undone (the next reading escalates again, a repeat page and never silence), and a crash before it
moved changes nothing (a patient tracked at CRITICAL does not fall silently to STABLE).
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from clinical_governance_system import ClinicalGovernanceSystem, GovernanceStatus
from clinical_notification import ClinicalNotificationSettings, notification_reason
from conftest import run_chain
from governance_orchestrator import GovernanceOrchestrator
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
from observe_consolidated import ObserveClinicalEngine, OperationalRegime, VitalsSnapshot
from pediatric_cassette import PediatricCassette

from conservation_kernel import HmacSigner

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
RESPONDER = HmacSigner(b"observe-crash-responder-key-0123456789", key_id="nurse")
HELD = ["_patient_policies", "_patient_entropy", "_state_commitments", "_patient_fault_paged", "_patient_kalman"]


def crit(i, pid="P1"):
    return VitalsSnapshot(pid, T0 + timedelta(minutes=i), 168, 83.0, 46, 39.5, {"age_months": 12})


def faulted(i, pid="P1"):
    return VitalsSnapshot(pid, T0 + timedelta(minutes=i), 110, 97.0, -5, 37.0, {"age_months": 24})   # a failed channel


class Pager:
    def __init__(self):
        self.calls = []

    def __call__(self, payload):
        self.calls.append(payload)


def make_desk():
    pager = Pager()
    return EscalationDesk(NotificationPolicy((Tier("charge", pager, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": RESPONDER}), pager


def crash_once_after_state_moves(engine):
    """Make `evaluate` raise AFTER it has moved the patient's escalation state: the first ledger write fails."""
    real, calls = engine.audit_ledger.append, {"n": 0}

    def fails_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("ledger write failed")
        return real(*a, **k)
    engine.audit_ledger.append = fails_once


class CrashesBeforeAnyStateMoves(PediatricCassette):
    """Engine selection happens before the policy is written, so a crash there changes nothing yet."""
    armed = False

    def select_engines(self, obs, recent_entropy):
        if self.armed:
            self.armed = False
            raise RuntimeError("engine selection crashed")
        return super().select_engines(obs, recent_entropy)


def snapshot(engine, pid="P1"):
    """What the engine tracks about a patient, in comparable form."""
    out = {}
    for name in HELD:
        store = getattr(engine, name)
        if pid not in store:
            out[name] = None
        elif name == "_patient_policies":
            out[name] = dict(vars(store[pid]))
        elif name == "_patient_kalman":
            out[name] = repr(vars(store[pid]))
        else:
            out[name] = copy.deepcopy(store[pid])
    return out


# ---- the engine is all-or-nothing -------------------------------------------------------------------------------

def test_a_crash_after_the_state_moved_is_undone_so_the_next_critical_reading_escalates_again():
    engine = ObserveClinicalEngine()
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(0))
    assert engine.evaluate(crit(1)).escalation_required                # not swallowed as a repeat of a reading nobody saw


def test_a_crash_before_any_state_moved_does_not_drop_a_patient_tracked_at_critical():
    """The review's blocker: resetting on every crash threw away a real CRITICAL when the crash came first."""
    cassette = CrashesBeforeAnyStateMoves()
    engine = ObserveClinicalEngine(cassette=cassette)
    assert engine.evaluate(crit(0)).regime == OperationalRegime.CRITICAL
    before = snapshot(engine)
    cassette.armed = True
    with pytest.raises(RuntimeError, match="engine selection"):
        engine.evaluate(crit(1))
    assert snapshot(engine) == before                                   # nothing moved, nothing dropped
    after = engine.evaluate(crit(2))
    assert after.regime == OperationalRegime.CRITICAL and after.previous_regime == "critical"
    assert not after.escalation_required                                # still the same escalation, not a new page


def test_a_failed_evaluation_restores_every_kind_of_per_patient_state_exactly():
    engine = ObserveClinicalEngine(enable_kalman=True)
    engine.evaluate(faulted(0))                                         # a fault-page stamp
    engine.evaluate(crit(1))
    before = snapshot(engine)
    assert all(before[name] is not None for name in HELD)                # the comparison below is not vacuous
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(2))
    assert snapshot(engine) == before


def test_a_failed_first_evaluation_leaves_no_state_behind():
    engine = ObserveClinicalEngine(enable_kalman=True)
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(0))
    assert all(v is None for v in snapshot(engine).values())


def test_a_crash_for_one_patient_leaves_every_other_patient_alone_and_writes_nothing_to_the_ledger():
    engine = ObserveClinicalEngine()
    engine.evaluate(crit(0, "B"))
    other, entries = snapshot(engine, "B"), len(engine.audit_ledger.entries)
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(1, "A"))
    assert snapshot(engine, "B") == other and len(engine.audit_ledger.entries) == entries


def test_the_hard_rule_history_is_not_rolled_back_because_a_retry_is_never_its_own_repeat():
    engine = ObserveClinicalEngine()
    ctx = dict(age_months=24, previous_hr=100, previous_o2=98, previous_rr=24, previous_temp=37.0, time_delta_seconds=3600)
    low = VitalsSnapshot("P1", T0 + timedelta(hours=1), 102, 85, 26, 37.5, ctx)
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(low)
    assert engine._patient_hard_rule_recent["P1"]                       # the low reading is remembered
    assert not engine.evaluate(low).escalation_required                 # the retry of the same reading is not a repeat
    later = VitalsSnapshot("P1", T0 + timedelta(hours=2), 102, 85, 26, 37.5, ctx)
    assert engine.evaluate(later).escalation_required                   # a genuinely later low reading is


def test_any_failure_including_a_non_exception_rolls_back_and_is_reraised_unchanged():
    engine = ObserveClinicalEngine()
    engine.evaluate(crit(0))
    before = snapshot(engine)
    real = engine.audit_ledger.append

    def interrupted(*a, **k):
        raise KeyboardInterrupt()
    engine.audit_ledger.append = interrupted
    with pytest.raises(KeyboardInterrupt):
        engine.evaluate(crit(1))
    engine.audit_ledger.append = real
    assert snapshot(engine) == before


def test_a_tracker_that_cannot_be_copied_is_dropped_on_rollback_not_fatal():
    engine = ObserveClinicalEngine(enable_kalman=True)
    engine.evaluate(crit(0))
    tracker = engine._patient_kalman["P1"]

    class Uncopyable(type(tracker)):
        def __deepcopy__(self, memo):
            raise TypeError("no copy")
    tracker.__class__ = Uncopyable                                      # the real tracker, now impossible to snapshot
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(1))
    assert "P1" not in engine._patient_kalman                           # restarts fresh rather than keep a half-moved one


def test_a_successful_evaluation_is_unchanged_by_the_snapshot():
    a, b = ObserveClinicalEngine(), ObserveClinicalEngine()
    for i in range(4):
        va, vb = a.evaluate(crit(i)), b.evaluate(crit(i))
        assert (va.regime, va.escalation_required, va.decision_fingerprint) == (
            vb.regime, vb.escalation_required, vb.decision_fingerprint)


# ---- ClinicalGovernanceSystem -------------------------------------------------------------------------------------

def test_a_crash_returns_a_decision_that_says_no_assessment_exists_and_names_the_status():
    system = ClinicalGovernanceSystem(allow_no_alert_channel=True)
    crash_once_after_state_moves(system.observe)
    d = system.process_vitals(crit(0))                                 # must not raise
    assert d.regime == "assessment_unavailable" and d.escalation_required
    assert d.action == "escalate_assessment_unavailable"
    assert d.governance_status == GovernanceStatus.ASSESSMENT_UNAVAILABLE and not d.governance_evaluated
    assert d.observe_audit_hash == "" and d.active_engines == [] and d.triggered_rules == []
    assert any("ASSESSMENT_UNAVAILABLE: OSError" in v for v in d.governance_violations)
    assert len(system.export_perceive_audit()) == 0                    # governance was never asked about nothing
    json.dumps(d.to_dict(), default=str)


def test_the_next_critical_reading_after_a_crash_is_not_swallowed():
    system = ClinicalGovernanceSystem(allow_no_alert_channel=True)
    crash_once_after_state_moves(system.observe)
    system.process_vitals(crit(0))
    after = system.process_vitals(crit(1))
    assert after.regime == "critical" and after.escalation_required


def test_a_crash_alerts_through_the_legacy_channel_and_records_how_it_went():
    seen = []
    system = ClinicalGovernanceSystem(alert_human=seen.append)
    crash_once_after_state_moves(system.observe)
    d = system.process_vitals(crit(0))
    assert d.human_alert == "delivered" and seen == [d]

    def pager_down(decision):
        raise ConnectionError("pager down")
    down = ClinicalGovernanceSystem(alert_human=pager_down)
    crash_once_after_state_moves(down.observe)
    assert down.process_vitals(crit(0, "Q")).human_alert == "failed"

    none = ClinicalGovernanceSystem(allow_no_alert_channel=True)
    crash_once_after_state_moves(none.observe)
    assert none.process_vitals(crit(0, "Z")).human_alert == "no_channel"


def test_a_crash_with_a_desk_opens_a_ticket_that_names_the_patient_whatever_the_settings():
    desk, pager = make_desk()
    silent = ClinicalNotificationSettings(min_regime="none", notify_on_severity_increase=False,
                                          notify_on_blocked_escalation=False, notify_on_unassessable=False)
    system = ClinicalGovernanceSystem(escalations=desk, notification_settings=silent)
    crash_once_after_state_moves(system.observe)
    d = system.process_vitals(crit(0, "P77"))
    assert d.clinical_notification["reason"] == "assessment_unavailable" and d.human_alert == "dispatched"
    assert pager.calls[0]["patient_id"] == "P77" and pager.calls[0]["regime"] == "assessment_unavailable"
    assert desk.state(d.clinical_notification["ticket"]) == "dispatched"


def test_an_engine_of_any_kind_that_crashes_gives_the_same_decision():
    system = ClinicalGovernanceSystem(allow_no_alert_channel=True)

    class Bare:
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")
    system.observe = Bare()
    d = system.process_vitals(crit(0))
    assert d.action == "escalate_assessment_unavailable" and d.human_alert == "no_channel"


def test_an_assessment_unavailable_is_always_told_and_never_when_nothing_failed():
    off = ClinicalNotificationSettings(min_regime="none", notify_on_severity_increase=False,
                                       notify_on_blocked_escalation=False, notify_on_unassessable=False)
    assert notification_reason(off, regime="assessment_unavailable", escalation_required=False,
                               governance_status="assessment_unavailable") == "assessment_unavailable"
    assert notification_reason(off, regime="stable", escalation_required=False, governance_status="not_evaluated") is None


# ---- GovernanceOrchestrator ---------------------------------------------------------------------------------------

class CrashingObserve(ObserveClinicalEngine):
    def evaluate(self, vitals):
        raise RuntimeError("OBSERVE crashed")


def test_an_advisory_observe_crash_after_the_action_alerts_and_keeps_the_record(perceive, kernel):
    seen = []
    result, ran = run_chain(GovernanceOrchestrator(perceive, kernel, CrashingObserve(), alert_human=seen.append), "oc-1")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1      # the action stands
    assert result["observe_error"].startswith("RuntimeError")
    assert result["human_alert"] == "delivered"
    assert seen[0]["stage"] == "observe" and seen[0]["patient_id"] == "P001"
    assert seen[0]["reason"] == "assessment_unavailable"                       # the same words the clinical page uses


def test_an_advisory_observe_crash_with_a_desk_opens_a_ticket_naming_the_patient(perceive, kernel):
    desk, pager = make_desk()
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, CrashingObserve(), escalations=desk), "oc-2")
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["human_alert"] == "dispatched"
    assert pager.calls[0]["stage"] == "observe" and pager.calls[0]["patient_id"] == "P001"
    assert pager.calls[0]["reason"] == "assessment_unavailable"
    assert desk.ticket(result["escalation_ticket"])["kind"] == "stage_error"


def test_an_advisory_observe_crash_with_no_channel_says_so_on_the_record(perceive, kernel):
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, CrashingObserve()), "oc-3")
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["human_alert"] == "no_channel"


def test_a_missing_engine_is_a_configuration_fault_that_is_now_loud_on_every_request_that_carries_vitals(perceive, kernel):
    seen = []
    orch = GovernanceOrchestrator(perceive, kernel, None, alert_human=seen.append)
    result, ran = run_chain(orch, "oc-4")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert "no OBSERVE engine configured" in result["observe_error"] and len(seen) == 1


def test_a_clean_observe_run_adds_no_alert(perceive, kernel):
    seen = []
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), alert_human=seen.append),
                          "oc-5")
    assert result["status"] == "APPROVED_AND_EXECUTED" and seen == [] and "human_alert" not in result


def test_a_strict_observe_crash_still_refuses_and_names_the_patient_in_the_alert():
    from conftest import make_vitals
    from test_audit_p5_fixes import run_strict, strict
    alerts = []
    result, order = run_strict(strict(CrashingObserve(), alerts), "oc-6", make_vitals(), "P112")
    assert result["status"] == "REJECTED" and result["refused_by"] == "observe" and "executed" not in order
    assert alerts[0]["patient_id"] == "P001" and alerts[0]["reason"] == "assessment_unavailable"


# ---- state that really moves before the crash ---------------------------------------------------------------------

def test_an_existing_patients_escalation_is_undone_when_the_evaluation_that_made_it_crashes():
    engine = ObserveClinicalEngine()
    engine.evaluate(VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))     # a calm patient with a policy
    before = snapshot(engine)
    assert before["_patient_policies"]["current_regime"] == OperationalRegime.STABLE
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(1))                                        # moves the policy to CRITICAL, then crashes
    assert snapshot(engine) == before                                   # back to STABLE and unlocked
    assert engine.evaluate(crit(2)).escalation_required                 # so the patient's next critical reading pages


def test_fault_page_stamps_that_a_crashed_evaluation_added_are_taken_back():
    engine = ObserveClinicalEngine()
    engine.evaluate(faulted(0))                                         # stamps {"sensor": t0}
    before = snapshot(engine)
    hard_rule_on_a_faulted_reading = VitalsSnapshot("P1", T0 + timedelta(minutes=1), 110, 80.0, -5, 37.0, {"age_months": 24})
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(hard_rule_on_a_faulted_reading)                 # would add a "bypass" stamp and move "sensor"
    assert snapshot(engine)["_patient_fault_paged"] == before["_patient_fault_paged"]
    assert set(snapshot(engine)["_patient_fault_paged"]) == {"sensor"}


def test_only_the_observe_stage_names_a_patient_as_unassessed():
    assert GovernanceOrchestrator._patient_context("observe", "P1") == {"patient_id": "P1", "reason": "assessment_unavailable"}
    assert GovernanceOrchestrator._patient_context("execution", "P1") == {"patient_id": "P1"}
    assert GovernanceOrchestrator._patient_context("observe", None) == {}
