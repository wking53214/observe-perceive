"""PR-3: a cooldown may suppress repeats at the same severity, never a severity increase.

Each test checks the resulting regime AND the escalation event: that a higher-priority escalation is really
generated, is distinguishable from the notification before it, and that same-severity repeats still are not
re-announced. The regime alone is not the test: before this change a patient could be reported at CRITICAL
with no escalation event at all.
"""
from datetime import datetime, timedelta, timezone

from clinical_governance_system import GovernanceStatus, build_single_hospital_system
from observe_consolidated import ESCALATION_LOCK_SECONDS, EscalationPolicy, ObserveClinicalEngine
from observe_consolidated import OperationalRegime as R
from observe_consolidated import VitalsSnapshot

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)

WARN_RULE = (150, 86.0, 30, 38.0)    # a hard rule fires, regime WARNING
CRIT_RULE = (170, 70.0, 55, 40.0)    # hard rule, regime CRITICAL
CALM = (100, 98.0, 24, 37.0)


def at(seconds):
    return T0 + timedelta(seconds=seconds)


def system():
    return build_single_hospital_system(allow_no_alert_channel=True)


def feed(sys_, rows, step=60):
    out = []
    for i, (hr, o2, rr, t) in enumerate(rows):
        out.append(sys_.process_vitals(VitalsSnapshot("P", at(i * step), hr, o2, rr, t,
                                                      {"age_months": 24, "physician_approved": True})))
    return out


# ---- the policy: the lock holds same severity, not a higher one -----------------------------------------------

def test_warning_then_critical_during_the_cooldown_is_a_new_escalation_at_the_policy():
    p = EscalationPolicy(dwell_threshold=1, lock_seconds=300)
    assert p.evaluate(R.WARNING, at(0)) == (R.WARNING, True)
    assert p.escalation_locked
    assert p.evaluate(R.CRITICAL, at(60)) == (R.CRITICAL, True)            # inside the lock


def test_a_worsening_from_an_already_escalated_state_acts_on_the_first_reading_with_no_dwell():
    p = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
    p.evaluate(R.WARNING, at(0))
    p.evaluate(R.WARNING, at(1))                                           # escalated, locked
    assert p.evaluate(R.CRITICAL, at(60)) == (R.CRITICAL, True)            # first reading, inside the lock
    assert p.evaluate(R.CRITICAL, at(90)) == (R.CRITICAL, False)           # and it is not repeated


def test_a_worsening_after_the_lock_has_expired_also_acts_on_the_first_reading():
    p = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
    p.evaluate(R.WARNING, at(0))
    p.evaluate(R.WARNING, at(1))
    assert p.evaluate(R.CRITICAL, at(1000)) == (R.CRITICAL, True)


def test_a_first_escalation_from_below_still_needs_its_dwell():
    for start in (R.STABLE, R.CAUTION):
        p = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
        if start is R.CAUTION:                                             # track CAUTION first (two readings)
            p.evaluate(R.CAUTION, at(0))
            p.evaluate(R.CAUTION, at(1))
        assert p.current_regime == start
        assert p.evaluate(R.WARNING, at(10)) == (start, False)             # one reading is not enough
        assert p.evaluate(R.WARNING, at(20)) == (R.WARNING, True)          # the second confirms
        p2 = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
        assert p2.evaluate(R.CRITICAL, at(0)) == (R.STABLE, False)         # STABLE to CRITICAL: dwell applies


def test_a_worsening_re_arms_the_lock_so_a_quick_fall_cannot_downgrade_it():
    p = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
    p.evaluate(R.WARNING, at(0))
    p.evaluate(R.WARNING, at(1))
    assert p.evaluate(R.CRITICAL, at(10)) == (R.CRITICAL, True)
    for s in (20, 30, 40):                                                 # three calm candidates inside the new lock
        assert p.evaluate(R.STABLE, at(s)) == (R.CRITICAL, False)
    assert p.escalation_locked and p.last_escalation_time == at(10)


def test_a_flapping_candidate_does_not_re_escalate_after_a_worsening():
    p = EscalationPolicy(dwell_threshold=2, lock_seconds=300)
    p.evaluate(R.WARNING, at(0))
    p.evaluate(R.WARNING, at(1))
    assert p.evaluate(R.CRITICAL, at(10))[1] is True
    for s, cand in ((20, R.WARNING), (30, R.CRITICAL), (40, R.WARNING), (50, R.CRITICAL)):
        assert p.evaluate(cand, at(s)) == (R.CRITICAL, False)


def test_a_repeat_at_the_same_severity_is_still_suppressed_by_the_cooldown():
    p = EscalationPolicy(dwell_threshold=1, lock_seconds=300)
    assert p.evaluate(R.WARNING, at(0))[1] is True
    for s in (30, 60, 120, 299):
        assert p.evaluate(R.WARNING, at(s)) == (R.WARNING, False)
    p.evaluate(R.CRITICAL, at(10))
    for s in (40, 90):
        assert p.evaluate(R.CRITICAL, at(s)) == (R.CRITICAL, False)         # repeated CRITICAL: no new event


def test_a_lower_candidate_during_the_lock_does_not_downgrade():
    p = EscalationPolicy(dwell_threshold=1, lock_seconds=300)
    p.evaluate(R.CRITICAL, at(0))
    assert p.evaluate(R.WARNING, at(60)) == (R.CRITICAL, False)
    assert p.evaluate(R.STABLE, at(120)) == (R.CRITICAL, False)


# ---- the engine: regime AND event ------------------------------------------------------------------------------

def test_warning_then_critical_by_bypass_inside_the_cooldown_raises_a_distinct_event():
    e = ObserveClinicalEngine()
    first = e.evaluate(VitalsSnapshot("P", at(0), *WARN_RULE, {"age_months": 24}))
    second = e.evaluate(VitalsSnapshot("P", at(60), *CRIT_RULE, {"age_months": 24}))
    assert (first.regime, first.escalation_required, first.escalation_kind) == (R.WARNING, True, "new_escalation")
    assert (second.regime, second.escalation_required) == (R.CRITICAL, True)
    assert second.escalation_kind == "severity_increase" and second.previous_regime == "warning"
    assert second.audit_hash != first.audit_hash and second.state_commitment != first.state_commitment
    assert second.decision_fingerprint != first.decision_fingerprint


def test_the_severity_increase_is_in_the_fingerprinted_record_and_a_clean_entry_keeps_its_shape():
    e = ObserveClinicalEngine()
    e.evaluate(VitalsSnapshot("P", at(0), *WARN_RULE, {"age_months": 24}))
    e.evaluate(VitalsSnapshot("P", at(60), *CRIT_RULE, {"age_months": 24}))
    data = [x["data"] for x in e.audit_ledger.entries]
    assert "escalation_kind" not in data[0]                                  # a first escalation: shape unchanged
    assert data[1]["escalation_kind"] == "severity_increase" and data[1]["previous_regime"] == "warning"


def test_repeated_warning_assessments_make_one_event():
    kinds = [v.escalation_required for v in feed(system(), [WARN_RULE] * 4)]
    assert kinds == [True, False, False, False]


def test_repeated_critical_assessments_make_one_event_and_stay_critical():
    out = feed(system(), [CRIT_RULE] * 4)
    assert [d.escalation_required for d in out] == [True, False, False, False]
    assert [d.regime for d in out] == ["critical"] * 4


def test_the_governed_path_acts_on_the_severity_increase_instead_of_continuing_to_monitor():
    out = feed(system(), [WARN_RULE, CRIT_RULE])
    assert out[0].action == "escalate_approved" and out[0].escalation_kind == "new_escalation"
    assert out[1].regime == "critical"
    assert out[1].escalation_required and out[1].action == "escalate_approved"          # was continue_monitoring
    assert out[1].escalation_kind == "severity_increase" and out[1].previous_regime == "warning"
    assert out[1].observe_audit_hash != out[0].observe_audit_hash
    assert out[1].governance_status == GovernanceStatus.APPROVED


def test_a_bypass_never_lowers_a_patient_tracked_at_critical():
    e = ObserveClinicalEngine()
    e.evaluate(VitalsSnapshot("P", at(0), *CRIT_RULE, {"age_months": 24}))
    # A reading whose own bypass rates only WARNING (HR 60, SpO2 70). A first version of this test used WARN_RULE,
    # which never reached the bypass after a CRITICAL reading (stored entropy dilutes the fusion), so it
    # passed against the mutant that lowers the regime. This one reaches it: the mutant drops it to WARNING.
    v = e.evaluate(VitalsSnapshot("P", at(10), 60, 70.0, 24, 36.5, {"age_months": 24}))
    assert any("BYPASS" in r for r in v.triggered_rules), "the test must exercise the bypass branch"
    assert v.regime == R.CRITICAL and not v.escalation_required


def test_critical_then_calm_readings_inside_the_cooldown_stay_critical_with_no_event():
    out = feed(system(), [CRIT_RULE, CALM, CALM, CALM])
    assert [d.regime for d in out] == ["critical"] * 4
    assert [d.escalation_required for d in out] == [True, False, False, False]


def test_recovery_is_recorded_as_a_de_escalation_and_is_not_an_escalation_event():
    out = feed(system(), [CRIT_RULE, CALM, CALM, CALM], step=ESCALATION_LOCK_SECONDS + 60)
    assert out[0].regime == "critical"
    down = [d for d in out if d.escalation_kind == "de_escalation"]
    assert down and down[0].previous_regime == "critical" and not down[0].escalation_required
    assert out[-1].regime == "stable"


def test_missing_data_during_an_active_critical_state_does_not_downgrade_it():
    e = ObserveClinicalEngine()
    e.evaluate(VitalsSnapshot("P", at(0), *CRIT_RULE, {"age_months": 24}))
    for i in (1, 2, 3):
        v = e.evaluate(VitalsSnapshot("P", at(60 * i), 100, float("nan"), 24, 37.0, {"age_months": 24}))
        assert v.regime == R.CRITICAL and not v.escalation_required and v.unassessable


def test_conflicting_signals_during_the_cooldown_neither_downgrade_nor_repeat_the_event():
    out = feed(system(), [WARN_RULE, CRIT_RULE, CALM, WARN_RULE, CRIT_RULE])
    assert [d.regime for d in out] == ["warning", "critical", "critical", "critical", "critical"]
    assert [d.escalation_required for d in out] == [True, True, False, False, False]


def test_a_failed_alert_for_a_severity_increase_stays_visible_and_the_escalation_stands():
    attempts = []

    def pager_down(decision):
        attempts.append(decision.escalation_kind)
        raise RuntimeError("pager unreachable")

    s = build_single_hospital_system(alert_human=pager_down)

    def boom(_request):
        raise RuntimeError("PERCEIVE down")

    feed(s, [WARN_RULE])
    s.perceive.evaluate_request = boom                                       # governance now fails
    inc = s.process_vitals(VitalsSnapshot("P", at(60), *CRIT_RULE, {"age_months": 24, "physician_approved": True}))
    assert inc.escalation_required and inc.escalation_kind == "severity_increase"
    assert inc.governance_status == GovernanceStatus.UNAVAILABLE and inc.human_alert == "failed"
    assert attempts == ["severity_increase"]

