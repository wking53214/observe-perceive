"""Stage 1A step 6: a hard rule is corroborated by another abnormal channel; both knobs are settings.

A lone low SpO2 beside otherwise normal vitals may be a sensor artefact and is judged as before. A low
SpO2 beside a raised respiratory rate, temperature or heart rate is not an artefact and holds the regime at
WARNING at once, whatever the fusion says. The probe-off note is annotation only.
"""
from datetime import datetime, timedelta, timezone

import pytest

from cassette import conformance_failures
from clinical_settings import ClinicalSettings
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from pediatric_cassette import PediatricCassette

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
CALM = (100, 98, 24, 37.0)


def engine(**overrides):
    return ObserveClinicalEngine(cassette=PediatricCassette(settings=ClinicalSettings.from_mapping(overrides)))


def run(readings, eng=None, step_minutes=60, age=24):
    eng, out, prev = eng or ObserveClinicalEngine(), [], None
    for i, (hr, o2, rr, temp) in enumerate(readings):
        ctx = {"age_months": age}
        if prev:  # history, as an integrator supplies it: this adds the trajectory engine to the fusion
            ctx.update(previous_hr=prev[0], previous_o2=prev[1], previous_rr=prev[2], previous_temp=prev[3],
                       time_delta_seconds=step_minutes * 60)
        out.append(eng.evaluate(VitalsSnapshot("P1", T0 + timedelta(minutes=i * step_minutes), hr, o2, rr, temp, ctx)))
        prev = (hr, o2, rr, temp)
    return out


# the case that exposed the gap: SpO2 86 with fever, fast heart and breathing, and 15 minutes of history
SICK = (138, 86.0, 34, 39.1)


def test_a_low_spo2_with_other_abnormal_vitals_escalates_with_and_without_history():
    with_history = run([(112, 92, 26, 38.2), SICK], step_minutes=15)[1]
    without = run([SICK])[0]
    assert with_history.escalation_required and without.escalation_required
    assert any("corroborated by another abnormal channel" in r for r in with_history.triggered_rules)
    assert with_history.risk_score >= 0.5


def test_a_low_spo2_beside_normal_vitals_is_still_judged_as_a_lone_reading():
    r = run([CALM, (102, 85, 26, 37.5)])
    assert not r[1].escalation_required


def test_corroboration_can_be_switched_off_and_that_is_recorded_as_a_weakening():
    off = engine(hard_rule_corroboration=False)
    assert not run([(112, 92, 26, 38.2), SICK], off, 15)[1].escalation_required
    ident = PediatricCassette(settings=ClinicalSettings.from_mapping({"hard_rule_corroboration": False})).identity_extras
    assert ident["settings_weaker"] == "hard_rule_corroboration"


def test_the_corroboration_margin_is_adjustable():
    strict = engine(hard_rule_corroboration_margin=0.9)           # nothing short of near-certain counts
    assert not run([(112, 92, 26, 38.2), SICK], strict, 15)[1].escalation_required


@pytest.mark.parametrize("window,expected", [(2, False), (3, True), (5, True)])
def test_the_confirmation_window_is_a_setting(window, expected):
    spikes = [CALM, (102, 85, 26, 37.5), (104, 97, 28, 37.6), (106, 82, 30, 37.7)]   # low, normal, low
    assert run(spikes, engine(hard_rule_confirm_window=window))[3].escalation_required is expected


def test_a_window_of_one_means_only_corroboration_confirms():
    eng = engine(hard_rule_confirm_window=1)
    two_lows = run([CALM, (102, 85, 26, 37.5), (104, 84, 28, 37.6)], eng)
    assert not two_lows[2].escalation_required                    # repeats no longer count, nothing corroborates
    assert run([(112, 92, 26, 38.2), SICK], eng, 15)[1].escalation_required


def test_a_hard_rule_never_lowers_a_patient_already_tracked_at_critical():
    """Review finding: the bypass overwrote the tracked regime with WARNING."""
    rows = [(90, 87, 60, 38.0), (135, 98, 12, 38.0), (160, 98, 45, 38.0), (90, 91, 38, 33.0)]
    for age in (24, 60, 120):
        r = run(rows, step_minutes=1, age=age)
        assert [v.regime.value for v in r][:3] == ["critical"] * 3, age
        assert r[3].regime.value == "critical", (age, r[3].regime.value, r[3].triggered_rules)


# ---- probe-off note: annotation only ----------------------------------------------------------------------------

def test_a_zero_spo2_carries_a_probe_note_and_the_regime_is_not_lowered_by_it():
    noted = run([CALM, (100, 0.0, 24, 37.0)])[1]
    plain = run([CALM, (100, 0.0, 24, 37.0)], engine(probe_off_spo2_at_or_below=None))[1]
    assert any("POSSIBLE_SENSOR_DISCONNECT" in r for r in noted.triggered_rules)
    assert not any("POSSIBLE_SENSOR_DISCONNECT" in r for r in plain.triggered_rules)
    assert noted.regime == plain.regime and noted.escalation_required == plain.escalation_required
    assert noted.decision_fingerprint != plain.decision_fingerprint            # the note is in the evidence


def test_the_probe_note_threshold_is_adjustable():
    assert not any("POSSIBLE_SENSOR_DISCONNECT" in r for r in run([(100, 4.0, 24, 37.0)])[0].triggered_rules)
    assert any("POSSIBLE_SENSOR_DISCONNECT" in r
               for r in run([(100, 4.0, 24, 37.0)], engine(probe_off_spo2_at_or_below=5.0))[0].triggered_rules)


def test_a_normal_spo2_has_no_note_and_clean_entries_keep_their_shape():
    v = ObserveClinicalEngine()
    out = v.evaluate(VitalsSnapshot("P1", T0, *CALM, {"age_months": 24}))
    assert not any("SENSOR_DISCONNECT" in r for r in out.triggered_rules)
    assert "advisory_notes" not in v.audit_ledger.entries[-1]["data"]


def test_the_cassette_still_conforms_with_the_new_optional_members():
    assert conformance_failures(PediatricCassette()) == []
