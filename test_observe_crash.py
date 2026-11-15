"""test_observe_crash.py -- an OBSERVE crash is never a quiet one.

OBSERVE moves a patient's escalation state before it fingerprints and records. A crash part way through used to leave the
patient marked "already escalated, locked" for a reading no ledger holds (so the next critical reading was swallowed as a
repeat), propagate out of `ClinicalGovernanceSystem` with no decision, and, on the orchestrator's advisory path, tell nobody.
Rule applied throughout: when state is uncertain, be wrong in the direction of a repeat page, never silence.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

from clinical_governance_system import ClinicalGovernanceSystem, GovernanceStatus
from clinical_notification import ClinicalNotificationSettings, notification_reason
from conftest import run_chain
from governance_orchestrator import GovernanceOrchestrator
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot

from conservation_kernel import HmacSigner

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
RESPONDER = HmacSigner(b"observe-crash-responder-key-0123456789", key_id="nurse")


def crit(i, pid="P1"):
    return VitalsSnapshot(pid, T0 + timedelta(minutes=i), 168, 83.0, 46, 39.5, {"age_months": 12})


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


# ---- the engine ---------------------------------------------------------------------------------------------------

def test_a_reset_drops_the_tracked_state_so_the_next_reading_escalates():
    engine = ObserveClinicalEngine()
    assert engine.evaluate(crit(0)).escalation_required
    assert not engine.evaluate(crit(1)).escalation_required          # inside the cooldown: a repeat is held
    engine.reset_tracked_state(crit(2))
    assert engine.evaluate(crit(2)).escalation_required              # after a reset it is judged fresh


def test_a_reset_writes_nothing_to_the_audit_ledger_and_forgets_only_that_patient():
    engine = ObserveClinicalEngine()
    engine.evaluate(crit(0, "A"))
    engine.evaluate(crit(0, "B"))
    entries = len(engine.audit_ledger.entries)
    engine.reset_tracked_state(crit(1, "A"))
    assert len(engine.audit_ledger.entries) == entries                # it may be what failed
    assert "A" not in engine._patient_policies and "B" in engine._patient_policies
    engine.reset_tracked_state(crit(1, "never-seen"))                 # an unknown patient is not an error


def test_a_reset_clears_every_kind_of_per_patient_state():
    engine = ObserveClinicalEngine(enable_kalman=True)
    # a faulted channel on a fresh patient is paged (a fault-page stamp), then a real critical reading
    engine.evaluate(VitalsSnapshot("P1", T0, 110, 97.0, -5, 37.0, {"age_months": 24}))
    engine.evaluate(crit(1))
    held = ["_patient_policies", "_patient_entropy", "_state_commitments", "_patient_fault_paged", "_patient_kalman"]
    assert all("P1" in getattr(engine, name) for name in held)           # the sweep below is not vacuous
    engine.reset_tracked_state(crit(2))
    assert not [name for name in held if "P1" in getattr(engine, name)]


def test_a_reset_keeps_a_pending_hard_rule_because_that_can_only_make_a_repeat_confirm_sooner():
    engine = ObserveClinicalEngine()
    ok, low = (100, 98, 24, 37.0), (102, 85, 26, 37.5)
    ctx = lambda prev: dict(age_months=24, previous_hr=prev[0], previous_o2=prev[1], previous_rr=prev[2],  # noqa: E731
                            previous_temp=prev[3], time_delta_seconds=3600)
    engine.evaluate(VitalsSnapshot("P1", T0, *ok, {"age_months": 24}))
    assert not engine.evaluate(VitalsSnapshot("P1", T0 + timedelta(hours=1), *low, ctx(ok))).escalation_required
    engine.reset_tracked_state(crit(0))
    again = engine.evaluate(VitalsSnapshot("P1", T0 + timedelta(hours=2), *low, ctx(low)))
    assert again.escalation_required                                   # the earlier low reading still counts


def test_a_crash_after_state_moved_swallows_the_next_critical_reading_unless_the_state_is_reset():
    """The failure the reset exists for: shown without it, then fixed with it."""
    engine = ObserveClinicalEngine()
    crash_once_after_state_moves(engine)
    with pytest.raises(OSError):
        engine.evaluate(crit(0))
    assert not engine.evaluate(crit(1)).escalation_required            # swallowed: the state says "already escalated"
    engine2 = ObserveClinicalEngine()
    crash_once_after_state_moves(engine2)
    with pytest.raises(OSError):
        engine2.evaluate(crit(0))
    engine2.reset_tracked_state(crit(0))
    assert engine2.evaluate(crit(1)).escalation_required              # after the reset it is not


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


def test_a_failing_reset_is_recorded_and_the_person_is_still_told():
    seen = []
    system = ClinicalGovernanceSystem(alert_human=seen.append)

    class Broken:
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")

        def reset_tracked_state(self, vitals):
            raise RuntimeError("reset failed")
    system.observe = Broken()
    d = system.process_vitals(crit(0))
    assert any("TRACKED_STATE_RESET_FAILED: reset failed" in v for v in d.governance_violations)
    assert d.human_alert == "delivered" and len(seen) == 1


def test_an_engine_without_a_reset_is_recorded_not_fatal():
    system = ClinicalGovernanceSystem(allow_no_alert_channel=True)

    class Bare:
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")
    system.observe = Bare()
    d = system.process_vitals(crit(0))
    assert d.action == "escalate_assessment_unavailable"
    assert any("TRACKED_STATE_RESET_FAILED" in v for v in d.governance_violations)


def test_an_assessment_unavailable_is_always_told_and_never_when_nothing_failed():
    off = ClinicalNotificationSettings(min_regime="none", notify_on_severity_increase=False,
                                       notify_on_blocked_escalation=False, notify_on_unassessable=False)
    assert notification_reason(off, regime="assessment_unavailable", escalation_required=False,
                               governance_status="assessment_unavailable") == "assessment_unavailable"
    assert notification_reason(off, regime="stable", escalation_required=False, governance_status="not_evaluated") is None


# ---- GovernanceOrchestrator ---------------------------------------------------------------------------------------

class CrashingObserve(ObserveClinicalEngine):
    def __init__(self):
        super().__init__()
        self.resets = []

    def evaluate(self, vitals):
        raise RuntimeError("OBSERVE crashed")

    def reset_tracked_state(self, vitals):
        self.resets.append(vitals.patient_id)


def test_an_advisory_observe_crash_after_the_action_resets_alerts_and_keeps_the_record(perceive, kernel):
    seen, engine = [], CrashingObserve()
    result, ran = run_chain(GovernanceOrchestrator(perceive, kernel, engine, alert_human=seen.append), "oc-1")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1      # the action stands
    assert result["observe_error"].startswith("RuntimeError")
    assert engine.resets == ["P001"]
    assert result["human_alert"] == "delivered"
    assert seen[0]["stage"] == "observe" and seen[0]["patient_id"] == "P001"


def test_an_advisory_observe_crash_with_a_desk_opens_a_ticket_naming_the_patient(perceive, kernel):
    desk, pager = make_desk()
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, CrashingObserve(), escalations=desk), "oc-2")
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["human_alert"] == "dispatched"
    assert pager.calls[0]["stage"] == "observe" and pager.calls[0]["patient_id"] == "P001"
    assert desk.ticket(result["escalation_ticket"])["kind"] == "stage_error"


def test_an_advisory_observe_crash_with_no_channel_says_so_on_the_record(perceive, kernel):
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, CrashingObserve()), "oc-3")
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["human_alert"] == "no_channel"


def test_a_failing_reset_is_recorded_beside_the_crash(perceive, kernel):
    class ResetBreaks(CrashingObserve):
        def reset_tracked_state(self, vitals):
            raise RuntimeError("reset failed")
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, ResetBreaks()), "oc-4")
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert "tracked state reset failed: RuntimeError: reset failed" in result["observe_error"]


def test_an_engine_without_a_reset_still_records_the_crash(perceive, kernel):
    class Bare:
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, Bare()), "oc-5")
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["observe_error"].startswith("RuntimeError")
    assert "reset failed" not in result["observe_error"]


def test_a_clean_observe_run_adds_no_alert_and_no_reset(perceive, kernel):
    seen = []
    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), alert_human=seen.append),
                          "oc-6")
    assert result["status"] == "APPROVED_AND_EXECUTED" and seen == [] and "human_alert" not in result


def test_a_strict_observe_crash_resets_the_patients_state_and_names_the_patient_in_the_alert():
    from test_audit_p5_fixes import run_strict, strict
    alerts, engine = [], CrashingObserve()
    from conftest import make_vitals
    result, order = run_strict(strict(engine, alerts), "oc-7", make_vitals(), "P112")
    assert result["status"] == "REJECTED" and result["refused_by"] == "observe" and "executed" not in order
    assert engine.resets == ["P001"] and alerts[0]["patient_id"] == "P001"
