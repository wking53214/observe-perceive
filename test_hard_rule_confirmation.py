"""Stage 1A step 2: a hard rule fusion cannot argue away once it repeats.

A CRITICAL_O2 reading used to be pulled back to STABLE with no escalation as soon as a benign engine
joined the fusion (any previous_* history adds the trajectory engine). A lone low reading may be a sensor
artefact and is judged as before; the same rule firing again within HARD_RULE_CONFIRM_WINDOW readings holds
the regime at WARNING. A named shock syndrome is a conjunction of vitals and always holds it.
"""
from datetime import datetime, timedelta, timezone

import pytest

from observe_consolidated import HARD_RULE_CONFIRM_WINDOW, ObserveClinicalEngine, OperationalRegime, VitalsSnapshot

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
SEVERITY = ["stable", "caution", "warning", "critical"]


def run(readings, step_minutes=60, age=24, pid="P1"):
    """readings: (hr, spo2, rr, temp). Previous-reading history is supplied, as an integrator would."""
    engine = ObserveClinicalEngine()
    out, prev = [], None
    for i, (hr, o2, rr, temp) in enumerate(readings):
        ctx = {"age_months": age}
        if prev:
            ctx.update(previous_hr=prev[0], previous_o2=prev[1], previous_rr=prev[2], previous_temp=prev[3],
                       time_delta_seconds=step_minutes * 60)
        out.append(engine.evaluate(VitalsSnapshot(pid, T0 + timedelta(minutes=i * step_minutes), hr, o2, rr, temp, ctx)))
        prev = (hr, o2, rr, temp)
    return out


def test_the_window_is_the_one_the_tests_assume():
    assert HARD_RULE_CONFIRM_WINDOW == 3


def test_a_repeated_hard_rule_escalates_even_when_history_dilutes_the_fusion():
    r = run([(100, 98, 24, 37.0), (102, 85, 26, 37.5), (104, 97, 28, 37.6), (106, 82, 30, 37.7)])
    assert not r[1].escalation_required          # the lone dip: judged as before
    assert r[3].escalation_required              # it fired again two readings later
    assert r[3].regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)
    assert any("held the regime at WARNING" in t for t in r[3].triggered_rules)
    assert r[3].risk_score >= 0.5                # score and regime agree


def test_a_lone_dip_that_recovers_is_not_escalated_when_history_dilutes_the_fusion():
    r = run([(100, 98, 24, 37.0), (102, 96, 26, 37.5), (104, 80, 28, 37.6), (105, 97, 27, 37.7), (103, 98, 25, 37.6)])
    assert not any(v.escalation_required for v in r)


def test_two_low_readings_in_a_row_escalate():
    r = run([(100, 98, 24, 37.0), (102, 85, 26, 37.5), (104, 84, 28, 37.6)])
    assert r[2].escalation_required


@pytest.mark.parametrize("step_minutes", [1, 5, 60, 240])
def test_confirmation_does_not_depend_on_the_sampling_interval(step_minutes):
    r = run([(100, 98, 24, 37.0), (102, 85, 26, 37.5), (104, 97, 28, 37.6), (106, 82, 30, 37.7)], step_minutes)
    assert r[3].escalation_required


def test_a_dip_followed_by_enough_normal_readings_is_a_lone_dip_again():
    low = (104, 85, 28, 37.6)
    ok = (100, 98, 24, 37.0)
    r = run([ok, low, ok, ok, ok, low])         # the earlier dip is outside the 3-reading window
    assert not r[5].escalation_required


def test_a_named_syndrome_always_holds_the_floor():
    # septic-shock conjunction: O2<92, HR>140, RR>35, T>38.5, with history that adds the trajectory engine
    r = run([(120, 96, 30, 38.0), (150, 90, 40, 39.0)], age=24)
    assert r[1].escalation_required
    assert r[1].regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)


def test_the_floor_never_lowers_a_regime():
    r = run([(100, 98, 24, 37.0), (170, 70, 60, 40.0)])
    assert SEVERITY.index(r[1].regime.value) >= SEVERITY.index("warning")


def test_one_patients_dips_do_not_confirm_anothers():
    engine = ObserveClinicalEngine()
    ctx = {"age_months": 24, "previous_o2": 98, "previous_hr": 100, "previous_rr": 24, "previous_temp": 37.0,
           "time_delta_seconds": 60}
    engine.evaluate(VitalsSnapshot("A", T0, 104, 82, 28, 37.6, dict(ctx)))
    b = engine.evaluate(VitalsSnapshot("B", T0 + timedelta(minutes=1), 104, 82, 28, 37.6, dict(ctx)))
    assert not b.escalation_required
