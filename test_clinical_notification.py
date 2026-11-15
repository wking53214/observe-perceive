"""test_clinical_notification.py -- an escalation EVENT reaches a person, per settings the clinician controls.

Before this change an approved CRITICAL, and a CRITICAL governance BLOCKED, returned to the caller and told nobody.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from clinical_governance_system import ClinicalGovernanceSystem, GovernanceStatus
from clinical_notification import ClinicalNotificationSettings, notification_reason, notify, ticket_context
from conftest import ChainArtifact
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot

from conservation_kernel import HmacSigner

RESPONDER = HmacSigner(b"clinical-notification-responder-key-01", key_id="nurse")
S = ClinicalNotificationSettings


class Pager:
    def __init__(self, fail=False, answer=None):
        self.calls, self.fail, self.answer = [], fail, answer

    def __call__(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise ConnectionError("pager unreachable")
        return self.answer


def make_desk(pager=None):
    pager = pager if pager is not None else Pager()
    desk = EscalationDesk(NotificationPolicy((Tier("charge", pager, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": RESPONDER})
    return desk, pager


def vitals(pid="P1", hr=168, o2=83.0, rr=46, temp=39.5, age=12, **ctx):
    context = {"age_months": age, "force_heavy": True}
    context.update(ctx)
    return VitalsSnapshot(pid, datetime.now(timezone.utc), hr, o2, rr, temp, context=context)


CRITICAL = dict(hr=170, o2=82.0, rr=48, temp=39.8, age=9)
WARNING = dict(hr=100, o2=84.0, rr=20, temp=37.5, age=6, force_heavy=False)


def system(desk=None, **kw):
    if desk is not None:
        return ClinicalGovernanceSystem(escalations=desk, **kw)
    return ClinicalGovernanceSystem(allow_no_alert_channel=True, **kw)


# ---- the settings are the clinician's, and bad ones are refused --------------------------------------------------

def test_the_defaults_are_the_documented_ones():
    assert S().as_dict() == {"min_regime": "critical", "notify_on_severity_increase": True,
                             "notify_on_blocked_escalation": True, "notify_on_unassessable": True}


@pytest.mark.parametrize("bad", [dict(min_regime="severe"), dict(min_regime=None), dict(min_regime="CRITICAL"),
                                 dict(notify_on_blocked_escalation="yes"), dict(notify_on_unassessable=1),
                                 dict(notify_on_severity_increase=None)])
def test_unusable_settings_are_problems_and_construction_refuses_them(bad):
    assert S(**bad).problems()
    with pytest.raises(ValueError, match="settings"):
        ClinicalGovernanceSystem(allow_no_alert_channel=True, notification_settings=S(**bad))


# ---- the decision rule: only an event, only what the settings say -----------------------------------------------

def reason(settings=None, **kw):
    base = dict(regime="critical", escalation_required=True, escalation_kind="new_escalation")
    base.update(kw)
    return notification_reason(settings or S(), **base)


def test_a_reading_that_is_not_an_escalation_event_is_never_a_reason():
    for status in (None, "approved", "blocked", "unavailable"):
        assert reason(escalation_required=False, governance_status=status, unassessable=True) is None


def test_default_regime_threshold_is_critical():
    assert reason(regime="critical") == "regime_at_or_above_threshold"
    assert reason(regime="warning") is None and reason(regime="caution") is None


@pytest.mark.parametrize("min_regime,regime,expected", [
    ("warning", "warning", "regime_at_or_above_threshold"), ("warning", "caution", None),
    ("caution", "caution", "regime_at_or_above_threshold"), ("none", "critical", None), ("critical", "warning", None)])
def test_the_regime_threshold_is_adjustable(min_regime, regime, expected):
    assert reason(S(min_regime=min_regime), regime=regime) == expected


def test_a_severity_increase_is_a_reason_until_switched_off():
    assert reason(S(min_regime="none"), regime="critical", escalation_kind="severity_increase") == "severity_increase"
    assert reason(S(min_regime="none", notify_on_severity_increase=False), regime="critical",
                  escalation_kind="severity_increase") is None


def test_a_blocked_escalation_is_a_reason_even_below_the_threshold_until_switched_off():
    assert reason(regime="warning", governance_status="blocked") == "blocked_escalation"
    assert reason(S(notify_on_blocked_escalation=False), regime="warning", governance_status="blocked") is None


def test_an_unassessable_escalation_is_a_reason_until_switched_off():
    assert reason(regime="warning", unassessable=True) == "unassessable_reading"
    assert reason(S(notify_on_unassessable=False), regime="warning", unassessable=True) is None


def test_an_unavailable_governance_verdict_is_always_told_whatever_the_settings():
    off = S(min_regime="none", notify_on_severity_increase=False, notify_on_blocked_escalation=False,
            notify_on_unassessable=False)
    assert reason(off, regime="warning", governance_status="unavailable") == "governance_unavailable"


def test_the_context_names_the_patient_and_why_but_carries_no_vitals_and_caps_long_lists():
    ctx = ticket_context(reason="r", patient_id=7, regime="critical", triggered_rules=[f"rule-{i}" for i in range(30)],
                         governance_status="blocked", governance_violations=["Physician approval required"])
    assert ctx["patient_id"] == "7" and len(ctx["triggered_rules"]) == 21 and "more" in ctx["triggered_rules"][-1]
    assert not {"heart_rate", "hr", "oxygen_saturation", "vitals"} & set(ctx)


def test_notify_never_raises_and_maps_the_desk_states_honestly():
    desk, _ = make_desk(Pager(answer={"delivery_receipt": "vendor-9"}))
    assert notify(desk, context={"patient_id": "p"})[1] == "delivered"
    desk, _ = make_desk()
    assert notify(desk, context={"patient_id": "p"})[1] == "dispatched"
    desk, _ = make_desk(Pager(fail=True))
    ticket, status = notify(desk, context={"patient_id": "p"})
    assert status == "failed" and ticket is not None                       # a ticket exists and is open: exhausted

    class Broken:
        def raise_and_dispatch(self, **kw):
            raise RuntimeError("desk bug")
    assert notify(Broken(), context={"patient_id": "p"}) == (None, "failed")


# ---- ClinicalGovernanceSystem ----------------------------------------------------------------------------------------

def test_an_approved_critical_opens_a_ticket_and_the_page_names_the_patient():
    desk, pager = make_desk()
    d = system(desk).process_vitals(vitals(physician_approved=True, **CRITICAL))
    assert d.action == "escalate_approved" and d.regime == "critical"
    assert d.clinical_notification["reason"] == "regime_at_or_above_threshold"
    assert d.clinical_notification["status"] == "dispatched" and d.clinical_notification["ticket"]
    page = pager.calls[0]
    assert page["patient_id"] == "P1" and page["regime"] == "critical" and page["ticket_id"] == d.clinical_notification["ticket"]
    assert page["governance_status"] == "approved" and page["observe_audit_hash"] == d.observe_audit_hash
    assert desk.state(d.clinical_notification["ticket"]) == "dispatched"
    assert d.human_alert is None                                              # that field stays for the legacy path


def test_a_blocked_critical_opens_a_ticket_that_says_why_it_is_blocked():
    desk, pager = make_desk()
    d = system(desk).process_vitals(vitals(**CRITICAL))
    assert d.action == "escalate_blocked" and d.governance_status == GovernanceStatus.BLOCKED
    assert d.clinical_notification["reason"] == "blocked_escalation"
    assert any("Physician approval required" in v for v in pager.calls[0]["governance_violations"])


def test_a_warning_alone_is_quiet_by_default_and_notifies_when_the_clinician_lowers_the_threshold():
    desk, pager = make_desk()
    quiet = system(desk).process_vitals(vitals(**WARNING))
    assert quiet.regime == "warning" and quiet.escalation_required and quiet.clinical_notification is None
    assert pager.calls == []
    desk2, pager2 = make_desk()
    loud = system(desk2, notification_settings=S(min_regime="warning")).process_vitals(vitals(**WARNING))
    assert loud.clinical_notification["reason"] == "regime_at_or_above_threshold" and len(pager2.calls) == 1


def test_a_patient_who_gets_worse_is_a_new_event_and_a_patient_who_stays_critical_is_not_paged_again():
    desk, pager = make_desk()
    s = system(desk, notification_settings=S(min_regime="none"))
    first = s.process_vitals(vitals(pid="P7", **WARNING))
    assert first.clinical_notification is None
    worse = s.process_vitals(vitals(pid="P7", physician_approved=True, **CRITICAL))
    assert worse.escalation_kind == "severity_increase" and worse.clinical_notification["reason"] == "severity_increase"
    again = s.process_vitals(vitals(pid="P7", physician_approved=True, **CRITICAL))
    assert again.clinical_notification is None and len(pager.calls) == 1


def test_a_stable_reading_opens_nothing():
    desk, pager = make_desk()
    d = system(desk).process_vitals(vitals(hr=110, o2=98.0, rr=24, temp=37.0, age=24, force_heavy=False))
    assert d.clinical_notification is None and pager.calls == [] and desk.open_tickets() == []


def test_unavailable_governance_with_a_desk_opens_a_ticket_and_keeps_the_human_alert_field():
    desk, pager = make_desk()
    s = system(desk)

    def boom(_request):
        raise RuntimeError("perceive is down")
    s.perceive.evaluate_request = boom
    d = s.process_vitals(vitals(**CRITICAL))
    assert d.action == "escalate_governance_unavailable"
    assert d.clinical_notification["reason"] == "governance_unavailable" and d.human_alert == "dispatched"
    assert pager.calls[0]["governance_status"] == "unavailable"


def test_a_failing_pager_or_desk_never_changes_the_decision():
    baseline = system(make_desk()[0]).process_vitals(vitals(**CRITICAL))
    desk, _ = make_desk(Pager(fail=True))
    failed = system(desk).process_vitals(vitals(**CRITICAL))
    assert failed.clinical_notification["status"] == "failed" and failed.action == baseline.action

    class Broken:
        def raise_and_dispatch(self, **kw):
            raise RuntimeError("desk bug")
    s = ClinicalGovernanceSystem(escalations=Broken())
    broken = s.process_vitals(vitals(**CRITICAL))
    assert broken.clinical_notification == {"reason": "blocked_escalation", "status": "failed", "ticket": None}
    assert broken.action == baseline.action


# ---- the legacy callable: a blocked escalation is no longer silent -----------------------------------------------

def test_a_blocked_escalation_calls_the_legacy_alert_and_an_approved_one_does_not():
    alerts = []
    s = ClinicalGovernanceSystem(alert_human=alerts.append)
    blocked = s.process_vitals(vitals(pid="B", **CRITICAL))
    assert blocked.action == "escalate_blocked" and blocked.human_alert == "delivered" and len(alerts) == 1
    approved = s.process_vitals(vitals(pid="A", physician_approved=True, **CRITICAL))
    assert approved.action == "escalate_approved" and approved.human_alert is None and len(alerts) == 1


def test_the_clinician_can_switch_the_blocked_alert_off_on_the_legacy_channel():
    alerts = []
    s = ClinicalGovernanceSystem(alert_human=alerts.append, notification_settings=S(notify_on_blocked_escalation=False))
    d = s.process_vitals(vitals(**CRITICAL))
    assert d.action == "escalate_blocked" and d.human_alert is None and alerts == []


def test_a_blocked_escalation_with_no_channel_at_all_is_loud_on_the_decision():
    d = system().process_vitals(vitals(**CRITICAL))
    assert d.action == "escalate_blocked" and d.human_alert == "no_channel"


def test_a_desk_and_a_legacy_callable_together_are_refused_and_a_desk_needs_no_opt_out():
    desk, _ = make_desk()
    with pytest.raises(ValueError, match="not both"):
        ClinicalGovernanceSystem(alert_human=lambda d: None, escalations=desk)
    ClinicalGovernanceSystem(escalations=desk)


# ---- GovernanceOrchestrator ------------------------------------------------------------------------------------------

def orchestrate(orch, artifact_id, v, func=None):
    ran = []

    def default(ctx):
        ran.append(ctx)
        return {"status": "done"}
    result = orch.orchestrate_request(ChainArtifact(artifact_id), "escalate", func or default, vitals_snapshot=v,
                                      context={"patient_id": v.patient_id, "execution_id": f"exec-{artifact_id}"})
    return result, ran


def test_the_orchestrator_notifies_on_an_escalation_event_and_the_record_says_so(perceive, kernel, tmp_path):
    desk, pager = make_desk()
    receipts = ReceiptLog(tmp_path / "r.jsonl")
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk, receipts=receipts)
    result, ran = orchestrate(orch, "c1", vitals(**CRITICAL))
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    note = result["clinical_notification"]
    assert note["reason"] == "regime_at_or_above_threshold" and note["status"] == "dispatched"
    assert pager.calls[0]["patient_id"] == "P1" and pager.calls[0]["request_id"] == result["governance_request"].request_id
    assert receipts.records()[-1]["clinical_notification"]["ticket"] == note["ticket"]   # the receipt names it


def test_the_orchestrator_is_quiet_for_a_warning_by_default_and_follows_the_settings(perceive, kernel):
    desk, pager = make_desk()
    quiet = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk)
    result, _ = orchestrate(quiet, "w1", vitals(pid="W", **WARNING))
    assert "clinical_notification" not in result and pager.calls == []
    desk2, pager2 = make_desk()
    loud = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk2,
                                  clinical_notification=S(min_regime="warning"))
    result, _ = orchestrate(loud, "w2", vitals(pid="W2", **WARNING))
    assert result["clinical_notification"]["reason"] == "regime_at_or_above_threshold" and len(pager2.calls) == 1


def test_the_orchestrator_never_lets_notification_change_the_outcome(perceive, kernel):
    desk, _ = make_desk(Pager(fail=True))
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk)
    result, ran = orchestrate(orch, "f1", vitals(pid="F", **CRITICAL))
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["clinical_notification"]["status"] == "failed"


def test_without_a_desk_the_orchestrator_adds_nothing(perceive, kernel):
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine())
    result, _ = orchestrate(orch, "n1", vitals(pid="N", **CRITICAL))
    assert "clinical_notification" not in result


def test_settings_without_a_desk_or_with_bad_values_are_refused(perceive, kernel):
    with pytest.raises(ValueError, match="need an escalation desk"):
        GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), clinical_notification=S())
    desk, _ = make_desk()
    with pytest.raises(ValueError, match="settings refused"):
        GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk,
                               clinical_notification=S(min_regime="severe"))


def test_a_request_with_no_vitals_has_no_verdict_and_so_no_notification(perceive, kernel):
    desk, pager = make_desk()
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk)
    result = orch.orchestrate_request(ChainArtifact("v1"), "escalate", lambda ctx: {"status": "done"},
                                      vitals_snapshot=None, context={"patient_id": "P1", "execution_id": "exec-v1"})
    assert "clinical_notification" not in result and pager.calls == []


# ---- an unassessable reading is a blind spot a person should know about ----------------------------------------------

BAD_READING = dict(hr=110, o2=97.0, rr=-5, temp=37.0, age=24, force_heavy=False)    # respiratory rate outside physical range


def test_an_unassessable_escalation_notifies_through_both_entry_points_until_switched_off(perceive, kernel):
    desk, pager = make_desk()
    d = system(desk).process_vitals(vitals(pid="U1", **BAD_READING))
    assert d.regime == "warning" and d.clinical_notification["reason"] == "unassessable_reading"
    desk2, pager2 = make_desk()
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk2)
    result, _ = orchestrate(orch, "u2", vitals(pid="U2", **BAD_READING))
    assert result["clinical_notification"]["reason"] == "unassessable_reading" and len(pager2.calls) == 1
    desk3, pager3 = make_desk()
    off = system(desk3, notification_settings=S(notify_on_unassessable=False)).process_vitals(vitals(pid="U3", **BAD_READING))
    assert off.clinical_notification is None and pager3.calls == []


def test_an_escalation_whose_regime_cannot_be_placed_is_told_not_dropped():
    assert reason(regime="mystery") == "regime_at_or_above_threshold"
    assert reason(S(min_regime="none"), regime="mystery") is None             # unless the clinician switched regimes off


def test_the_orchestrator_pages_once_per_event_and_again_when_the_patient_gets_worse(perceive, kernel):
    desk, pager = make_desk()
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk,
                                  clinical_notification=S(min_regime="none"))
    warning, _ = orchestrate(orch, "e1", vitals(pid="E", **WARNING))
    assert "clinical_notification" not in warning
    worse, _ = orchestrate(orch, "e2", vitals(pid="E", **CRITICAL))
    assert worse["clinical_notification"]["reason"] == "severity_increase" and len(pager.calls) == 1
    same, _ = orchestrate(orch, "e3", vitals(pid="E", **CRITICAL))            # still critical: not a new event
    assert "clinical_notification" not in same and len(pager.calls) == 1


def test_an_unexpected_failure_while_notifying_is_recorded_and_never_changes_the_outcome(perceive, kernel, monkeypatch):
    import clinical_notification

    def broken(**kw):
        raise RuntimeError("context bug")
    monkeypatch.setattr(clinical_notification, "ticket_context", broken)
    desk, pager = make_desk()
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk)
    result, ran = orchestrate(orch, "x1", vitals(pid="X", **CRITICAL))
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1 and pager.calls == []
    assert result["clinical_notification"] == {"reason": None, "status": "failed", "ticket": None}


def test_a_patient_who_stays_critical_is_not_paged_on_every_request(perceive, kernel):
    desk, pager = make_desk()
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk)
    first, _ = orchestrate(orch, "s1", vitals(pid="Stay", **CRITICAL))
    second, _ = orchestrate(orch, "s2", vitals(pid="Stay", **CRITICAL))
    assert first["clinical_notification"]["status"] == "dispatched"
    assert "clinical_notification" not in second and len(pager.calls) == 1
