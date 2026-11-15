"""test_hard_rule_floor.py -- a hard rule that fusion would dilute below WARNING is held at WARNING once confirmed.

A CRITICAL_O2 reading used to be pulled back to STABLE with no escalation as soon as a benign engine joined the fusion
(any previous_* history adds the trajectory engine), because the bypass only applied when the fused regime was already
WARNING or CRITICAL. A lone low reading may be a sensor artefact and is judged as before. The same rule firing again within
the confirmation window, or another channel abnormal beside it, is not an artefact and holds the regime at WARNING. The
window, corroboration and its margin are the clinician's to change; a window of 1 holds every hard rule at once.
"""
from datetime import datetime, timedelta, timezone

import pytest

from cassette import conformance_failures
from observe_consolidated import HARD_RULE_CONFIRM_WINDOW, ObserveClinicalEngine, OperationalRegime, VitalsSnapshot
from pediatric_cassette import PediatricCassette

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
SEVERITY = ["stable", "caution", "warning", "critical"]
CALM = (100, 98, 24, 37.0)
LOW = (102, 85, 26, 37.5)            # SpO2 85 beside otherwise normal vitals: a lone low reading
SICK = (138, 86.0, 34, 39.1)         # SpO2 86 with fever, a fast heart and fast breathing: not an artefact


def engine(**settings):
    return ObserveClinicalEngine(cassette=PediatricCassette(**settings))


def run(readings, eng=None, step_minutes=60, age=24, pid="P1"):
    """readings: (hr, spo2, rr, temp). Previous-reading history is supplied, as an integrator would."""
    eng, out, prev = eng or ObserveClinicalEngine(), [], None
    for i, (hr, o2, rr, temp) in enumerate(readings):
        ctx = {"age_months": age}
        if prev:
            ctx.update(previous_hr=prev[0], previous_o2=prev[1], previous_rr=prev[2], previous_temp=prev[3],
                       time_delta_seconds=step_minutes * 60)
        out.append(eng.evaluate(VitalsSnapshot(pid, T0 + timedelta(minutes=i * step_minutes), hr, o2, rr, temp, ctx)))
        prev = (hr, o2, rr, temp)
    return out


# ---- a repeat confirms --------------------------------------------------------------------------------------------

def test_the_default_window_is_the_one_these_tests_assume():
    assert HARD_RULE_CONFIRM_WINDOW == 3 and PediatricCassette().hard_rule_confirm_window() == 3


def test_a_repeated_hard_rule_escalates_even_when_history_dilutes_the_fusion():
    r = run([CALM, (102, 85, 26, 37.5), (104, 97, 28, 37.6), (106, 82, 30, 37.7)])
    assert not r[1].escalation_required                       # the lone dip: judged as before
    assert r[3].escalation_required                           # it fired again two readings later
    assert r[3].regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)
    assert any("held the regime at WARNING" in t for t in r[3].triggered_rules)
    assert r[3].risk_score >= 0.5                             # score and regime agree


def test_a_lone_dip_that_recovers_is_not_escalated_when_history_dilutes_the_fusion():
    r = run([CALM, (102, 96, 26, 37.5), (104, 80, 28, 37.6), (105, 97, 27, 37.7), (103, 98, 25, 37.6)])
    assert not any(v.escalation_required for v in r)


def test_two_low_readings_in_a_row_escalate():
    assert run([CALM, LOW, (104, 84, 28, 37.6)])[2].escalation_required


@pytest.mark.parametrize("step_minutes", [1, 5, 60, 240])
def test_confirmation_does_not_depend_on_the_sampling_interval(step_minutes):
    r = run([CALM, LOW, (104, 97, 28, 37.6), (106, 82, 30, 37.7)], step_minutes=step_minutes)
    assert r[3].escalation_required


def test_a_dip_followed_by_enough_normal_readings_is_a_lone_dip_again():
    low, ok = (104, 85, 28, 37.6), CALM
    assert not run([ok, low, ok, ok, ok, low])[5].escalation_required      # the earlier dip is outside the window


def test_one_patients_dips_do_not_confirm_anothers():
    eng = ObserveClinicalEngine()
    ctx = {"age_months": 24, "previous_o2": 98, "previous_hr": 100, "previous_rr": 24, "previous_temp": 37.0,
           "time_delta_seconds": 60}
    eng.evaluate(VitalsSnapshot("A", T0, 104, 82, 28, 37.6, dict(ctx)))
    b = eng.evaluate(VitalsSnapshot("B", T0 + timedelta(minutes=1), 104, 82, 28, 37.6, dict(ctx)))
    assert not b.escalation_required


def test_the_floor_never_lowers_a_regime_and_a_named_syndrome_always_holds_it():
    r = run([CALM, (170, 70, 60, 40.0)])
    assert SEVERITY.index(r[1].regime.value) >= SEVERITY.index("warning")
    shock = run([(120, 96, 30, 38.0), (150, 90, 40, 39.0)])                # septic-shock conjunction, with history
    assert shock[1].escalation_required and shock[1].regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)


def test_a_hard_rule_never_lowers_a_patient_already_tracked_at_critical():
    rows = [(90, 87, 60, 38.0), (135, 98, 12, 38.0), (160, 98, 45, 38.0), (90, 91, 38, 33.0)]
    for age in (24, 60, 120):
        r = run(rows, step_minutes=1, age=age)
        assert [v.regime.value for v in r][:3] == ["critical"] * 3, age
        assert r[3].regime.value == "critical", (age, r[3].regime.value, r[3].triggered_rules)


def test_a_patient_who_keeps_a_floored_hard_rule_is_one_event_not_one_per_reading():
    r = run([CALM, LOW, (104, 84, 28, 37.6), (104, 83, 28, 37.6), (104, 82, 28, 37.6)])
    assert sum(v.escalation_required for v in r) == 1


# ---- corroboration confirms at once -------------------------------------------------------------------------------

def test_a_low_spo2_with_other_abnormal_vitals_escalates_with_and_without_history():
    with_history = run([(112, 92, 26, 38.2), SICK], step_minutes=15)[1]
    without = run([SICK])[0]
    assert with_history.escalation_required and without.escalation_required
    assert any("corroborated by another abnormal channel" in r for r in with_history.triggered_rules)
    assert with_history.risk_score >= 0.5


def test_a_low_spo2_beside_normal_vitals_is_still_judged_as_a_lone_reading():
    assert not run([CALM, LOW])[1].escalation_required


def test_corroboration_can_be_switched_off_and_that_is_recorded_in_the_cassette_identity():
    off = engine(hard_rule_corroboration=False)
    assert not run([(112, 92, 26, 38.2), SICK], off, 15)[1].escalation_required
    assert PediatricCassette(hard_rule_corroboration=False).identity_extras == {"hard_rule_corroboration": "false"}
    assert off.audit_ledger.entries[-1]["data"]["cassette"]["hard_rule_corroboration"] == "false"


def test_the_corroboration_margin_is_adjustable():
    strict = engine(hard_rule_corroboration_margin=0.9)                    # nothing short of near-certain counts
    assert not run([(112, 92, 26, 38.2), SICK], strict, 15)[1].escalation_required
    assert PediatricCassette(hard_rule_corroboration_margin=0.9).identity_extras == {
        "hard_rule_corroboration_margin": "0.9"}


@pytest.mark.parametrize("window,expected", [(2, False), (3, True), (5, True)])
def test_the_confirmation_window_is_a_setting(window, expected):
    spikes = [CALM, LOW, (104, 97, 28, 37.6), (106, 82, 30, 37.7)]         # low, normal, low
    assert run(spikes, engine(hard_rule_confirm_window=window))[3].escalation_required is expected
    assert PediatricCassette(hard_rule_confirm_window=window).identity_extras.get(
        "hard_rule_confirm_window") == (None if window == 3 else str(window))


def test_a_window_of_one_holds_every_hard_rule_at_once_with_nothing_to_wait_for():
    eng = engine(hard_rule_confirm_window=1)
    lone = run([CALM, LOW], eng)[1]
    assert lone.escalation_required and lone.regime in (OperationalRegime.WARNING, OperationalRegime.CRITICAL)
    assert any("window of 1" in r for r in lone.triggered_rules)


LOWS = ((102, 85, 26, 37.5), (100, 80, 24, 37.0), (110, 70, 30, 37.2))     # a hard rule (CRITICAL_O2) at every age
HISTORIES = ([], [CALM], [(130, 97, 30, 38.0), (125, 96, 28, 37.8)])
AGES = (3, 12, 24, 60, 120)


def _hard_rule_fired_on_last(eng):
    heuristic = [o for o in eng.audit_ledger.entries[-1]["data"]["outputs"] if o["engine"] == "heuristic"]
    return bool(heuristic) and heuristic[0]["risk"] >= 0.5


def test_a_hard_rule_is_never_silent_beyond_the_window_across_ages_and_histories():
    """The safety claim: with the default window, two consecutive hard-rule readings always reach WARNING."""
    for age in AGES:
        for history in HISTORIES:
            for low in LOWS:
                eng = ObserveClinicalEngine()
                r = run(history + [low, low], eng, age=age)
                assert _hard_rule_fired_on_last(eng), (age, history, low)             # the sweep is not vacuous
                assert SEVERITY.index(r[-1].regime.value) >= SEVERITY.index("warning"), (age, history, low)


def test_with_a_window_of_one_a_single_hard_rule_always_reaches_warning_across_ages_and_histories():
    for age in AGES:
        for history in HISTORIES:
            for low in LOWS:
                eng = engine(hard_rule_confirm_window=1)
                r = run(history + [low], eng, age=age)
                assert _hard_rule_fired_on_last(eng), (age, history, low)
                assert SEVERITY.index(r[-1].regime.value) >= SEVERITY.index("warning"), (age, history, low)


# ---- the settings are validated, recorded, and part of the cassette contract --------------------------------------

@pytest.mark.parametrize("bad", [dict(hard_rule_confirm_window=0), dict(hard_rule_confirm_window=-1),
                                 dict(hard_rule_confirm_window=True), dict(hard_rule_confirm_window=2.5),
                                 dict(hard_rule_confirm_window="3"), dict(hard_rule_corroboration="yes"),
                                 dict(hard_rule_corroboration=1), dict(hard_rule_corroboration_margin=-0.1),
                                 dict(hard_rule_corroboration_margin=2), dict(hard_rule_corroboration_margin=float("nan")),
                                 dict(hard_rule_corroboration_margin=True), dict(hard_rule_confirm_window=1001),
                                 dict(hard_rule_corroboration_margin=0), dict(hard_rule_corroboration_margin=1e-300),
                                 dict(hard_rule_corroboration_margin=0.0009)])
def test_an_unusable_setting_is_refused_at_construction(bad):
    with pytest.raises(ValueError, match="hard_rule"):
        PediatricCassette(**bad)


def test_the_defaults_add_nothing_to_the_recorded_identity_so_old_fingerprints_keep_their_meaning():
    assert PediatricCassette().identity_extras == {}
    assert PediatricCassette().version == "1.1.0"


def test_the_cassette_conforms_and_a_declared_but_uncallable_member_is_rejected():
    assert conformance_failures(PediatricCassette()) == []
    class Declared(PediatricCassette):
        hard_rule_confirm_window = 3                                       # declared, not callable
    assert any("hard_rule_confirm_window" in f for f in conformance_failures(Declared()))


def test_a_cassette_without_the_optional_members_uses_the_core_default_window():
    class Minimal:
        def __getattr__(self, name):
            return getattr(PediatricCassette(), name)
    stripped = PediatricCassette()
    del_names = ("hard_rule_confirm_window", "hard_rule_corroborated")

    class Without:
        def __init__(self):
            self._c = stripped

        def __getattr__(self, name):
            if name in del_names:
                raise AttributeError(name)
            return getattr(self._c, name)
    r = run([CALM, LOW, (104, 97, 28, 37.6), (106, 82, 30, 37.7)], ObserveClinicalEngine(cassette=Without()))
    assert r[3].escalation_required                                       # core default window of 3 applies


def test_the_per_patient_state_is_evicted_with_a_patient_who_has_nothing_pending():
    eng = ObserveClinicalEngine(max_tracked_patients=2)
    for pid in ("A", "B", "C"):
        run([CALM], eng, pid=pid)
    assert set(eng._patient_hard_rule_recent) <= set(eng._patient_policies) and len(eng._patient_hard_rule_recent) <= 2


# ---- gaps a mutation check found ----------------------------------------------------------------------------------

def test_the_per_patient_history_stays_as_short_as_the_window():
    eng = engine(hard_rule_confirm_window=5)
    run([CALM, LOW] * 25, eng)
    assert len(eng._patient_hard_rule_recent["P1"]) == 5


class _Window(PediatricCassette):
    def __init__(self, window):
        super().__init__()
        self._w = window

    def hard_rule_confirm_window(self):
        return self._w


@pytest.mark.parametrize("window", [0, -3])
def test_a_custom_cassette_that_answers_below_one_gets_a_window_of_one_not_a_crash_or_a_silence(window):
    eng = ObserveClinicalEngine(cassette=_Window(window))
    r = run([CALM, LOW], eng)
    assert r[1].escalation_required and any("window of 1" in t for t in r[1].triggered_rules)
    run([CALM, LOW] * 20, eng, pid="Long")
    assert len(eng._patient_hard_rule_recent["Long"]) == 1                      # and its history does not grow without bound


def _heuristic(risk, abstained=False):
    from observe_consolidated import RiskOutput
    return RiskOutput("heuristic", risk, 0.9, {}, [], T0, abstained=abstained)


def test_corroboration_is_inclusive_at_the_margin_and_an_abstaining_heuristic_corroborates_nothing():
    at_margin = PediatricCassette(hard_rule_corroboration_margin=0.1)       # 0.5 + 0.1 == 0.6, the score of a fever beside it
    assert at_margin.hard_rule_corroborated([_heuristic(0.6)])
    assert not PediatricCassette(hard_rule_corroboration_margin=0.11).hard_rule_corroborated([_heuristic(0.6)])
    assert not PediatricCassette().hard_rule_corroborated([_heuristic(0.9, abstained=True)])
    assert not PediatricCassette().hard_rule_corroborated([])


# ---- found by independent review ---------------------------------------------------------------------------------

def test_a_patient_with_a_hard_rule_waiting_for_confirmation_is_not_evicted_to_make_room():
    """A had one low reading (a lone dip, judged as before). B and C arrive and the table is full. A's next low
    reading must still be a repeat: forgetting A here would silence exactly the case the window exists to catch."""
    for cap, expect in ((2, True), (10, True)):
        eng = ObserveClinicalEngine(max_tracked_patients=cap)
        run([CALM, LOW], eng, pid="A")
        for pid in ("B", "C"):
            run([CALM], eng, pid=pid)
        again = eng.evaluate(VitalsSnapshot("A", T0 + timedelta(hours=3), 104, 84, 28, 37.6,
                                            {"age_months": 24, "previous_hr": 102, "previous_o2": 85, "previous_rr": 26,
                                             "previous_temp": 37.5, "time_delta_seconds": 3600}))
        assert again.escalation_required is expect, cap


def test_a_calm_patient_is_still_evicted_so_memory_stays_bounded():
    eng = ObserveClinicalEngine(max_tracked_patients=3)
    for i in range(20):
        run([CALM], eng, pid=f"calm-{i}")
    assert len(eng._patient_policies) <= 3 and len(eng._patient_hard_rule_recent) <= 3


def test_the_same_reading_submitted_twice_is_not_its_own_repeat():
    eng = ObserveClinicalEngine()
    first = run([CALM], eng)
    reading = VitalsSnapshot("P1", T0 + timedelta(hours=1), 102, 85, 26, 37.5,
                             {"age_months": 24, "previous_hr": 100, "previous_o2": 98, "previous_rr": 24,
                              "previous_temp": 37.0, "time_delta_seconds": 3600})
    one, two = eng.evaluate(reading), eng.evaluate(reading)
    assert first[0].regime.value == "stable"
    assert not one.escalation_required and not two.escalation_required and len(eng._patient_hard_rule_recent["P1"]) == 2


def test_a_retry_after_a_crash_part_way_through_is_not_a_repeat(monkeypatch):
    eng = ObserveClinicalEngine()
    run([CALM], eng)
    reading = VitalsSnapshot("P1", T0 + timedelta(hours=1), 102, 85, 26, 37.5,
                             {"age_months": 24, "previous_hr": 100, "previous_o2": 98, "previous_rr": 24,
                              "previous_temp": 37.0, "time_delta_seconds": 3600})
    real, calls = eng.audit_ledger.append, {"n": 0}

    def fails_once(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("ledger write failed")
        return real(*a, **k)
    monkeypatch.setattr(eng.audit_ledger, "append", fails_once)
    with pytest.raises(OSError):
        eng.evaluate(reading)
    assert not eng.evaluate(reading).escalation_required                      # the retry is the same reading


def test_an_earlier_timestamp_is_not_a_repeat_and_a_later_one_is():
    eng = ObserveClinicalEngine()
    low = lambda hours: VitalsSnapshot("P1", T0 + timedelta(hours=hours), 102, 85, 26, 37.5,       # noqa: E731
                                       {"age_months": 24, "previous_hr": 100, "previous_o2": 98, "previous_rr": 24,
                                        "previous_temp": 37.0, "time_delta_seconds": 3600})
    eng.evaluate(low(5))
    assert not eng.evaluate(low(2)).escalation_required                      # a late-arriving older reading
    assert eng.evaluate(low(8)).escalation_required                          # a genuinely later one


def test_a_timestamp_that_cannot_be_ordered_counts_as_a_repeat_the_direction_that_escalates():
    eng = ObserveClinicalEngine()
    run([CALM], eng)
    eng._patient_hard_rule_recent["P1"] = [(datetime(2026, 1, 1), True)]       # naive: cannot be compared with aware
    r = eng.evaluate(VitalsSnapshot("P1", T0 + timedelta(hours=1), 102, 85, 26, 37.5,
                                    {"age_months": 24, "previous_hr": 100, "previous_o2": 98, "previous_rr": 24,
                                     "previous_temp": 37.0, "time_delta_seconds": 3600}))
    assert r.escalation_required


def test_the_window_and_margin_have_sanity_limits_at_their_edges():
    assert PediatricCassette(hard_rule_confirm_window=1000).hard_rule_confirm_window() == 1000
    assert PediatricCassette(hard_rule_corroboration_margin=0.001).identity_extras == {
        "hard_rule_corroboration_margin": "0.001"}                          # the smallest margin that is not vacuous
    assert PediatricCassette(hard_rule_corroboration_margin=1).identity_extras == {"hard_rule_corroboration_margin": "1.0"}


def test_a_score_exactly_at_the_margin_counts_whatever_the_float_arithmetic_did():
    assert PediatricCassette(hard_rule_corroboration_margin=0.2).hard_rule_corroborated([_heuristic(0.7 - 1e-12)])
    assert not PediatricCassette(hard_rule_corroboration_margin=0.2).hard_rule_corroborated([_heuristic(0.7 - 1e-6)])


@pytest.mark.parametrize("name", ["hard_rule_confirm_window", "hard_rule_corroborated", "engine_gate",
                                  "_hard_rule_confirm_window", "_hard_rule_corroboration", "_hard_rule_corroboration_margin"])
def test_a_setting_cannot_be_changed_after_the_cassette_is_built(name):
    c = PediatricCassette(hard_rule_confirm_window=1)
    with pytest.raises(AttributeError, match="fixed once"):
        setattr(c, name, 1)
    assert c.hard_rule_confirm_window() == 1 and c.identity_extras == {"hard_rule_confirm_window": "1"}
