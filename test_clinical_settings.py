"""test_clinical_settings.py -- the values a clinician may tune: engines, settings object, cassette, population floor.

Built up in steps; each group says which finding or promise it holds.
"""
import copy
import json
from datetime import datetime, timedelta, timezone

import pytest

from clinical_settings import ClinicalSettings, SettingsError
from observe_consolidated import AGE_GROUP_UPPER_MONTHS, PEDIATRIC_NORMS, RiskAdapters, VitalsSnapshot, get_age_group

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def vit(hr=100, o2=98.0, rr=24, t=37.0, age=24, pid="P1"):
    return VitalsSnapshot(pid, T0, hr, o2, rr, t, {"age_months": age})


# ---- step 1: the engines take the settings as arguments, and the defaults are exactly what they were ---------------------

def test_the_age_bounds_default_to_the_original_table():
    assert AGE_GROUP_UPPER_MONTHS == (3, 12, 36)
    assert [get_age_group(m) for m in (None, 0, 2.9, 3, 11, 12, 35, 36, 500)] == [
        "generic", "neonatal", "neonatal", "infant", "infant", "toddler", "toddler", "child", "child"]


def test_the_age_bounds_can_be_moved():
    assert get_age_group(20, (3, 12, 24)) == "toddler" and get_age_group(30, (3, 12, 24)) == "child"
    assert get_age_group(30) == "toddler"


def test_the_heuristic_defaults_are_unchanged():
    plain = RiskAdapters.heuristic(vit(hr=70, o2=86.0, age=480, t=34.0))
    explicit = RiskAdapters.heuristic(vit(hr=70, o2=86.0, age=480, t=34.0), norms=PEDIATRIC_NORMS,
                                      age_bounds=AGE_GROUP_UPPER_MONTHS, critical_o2_below=88.0, hypothermia_below=35.0)
    assert plain.risk_score == explicit.risk_score and plain.triggered_rules == explicit.triggered_rules
    assert "CRITICAL_O2: 86.0% (<88%)" in plain.triggered_rules and "HYPOTHERMIA: 34.0°C < 35.0°C" in plain.triggered_rules


def test_a_norms_table_changes_what_the_heuristic_flags():
    norms = copy.deepcopy(PEDIATRIC_NORMS)
    norms["child"]["hr_low"] = 60                                           # a clinician's decision, not ours
    assert any("BRADYCARDIA" in r for r in RiskAdapters.heuristic(vit(hr=70, age=100)).triggered_rules)
    assert not any("BRADYCARDIA" in r for r in RiskAdapters.heuristic(vit(hr=70, age=100), norms=norms).triggered_rules)


def test_the_critical_o2_and_hypothermia_lines_can_be_moved():
    low = RiskAdapters.heuristic(vit(o2=89.0), critical_o2_below=90.0)
    assert any(r.startswith("CRITICAL_O2") for r in low.triggered_rules) and low.risk_score >= 0.5
    assert not any(r.startswith("CRITICAL_O2") for r in RiskAdapters.heuristic(vit(o2=89.0)).triggered_rules)
    assert any(r.startswith("HYPOTHERMIA") for r in RiskAdapters.heuristic(vit(t=35.5), hypothermia_below=36.0).triggered_rules)


def test_the_bayesian_engine_reads_the_same_settings():
    norms = copy.deepcopy(PEDIATRIC_NORMS)
    norms["child"].update(hr_high=200, hr_low=40)
    base = RiskAdapters.bayesian(vit(hr=135, age=100))
    tuned = RiskAdapters.bayesian(vit(hr=135, age=100), norms=norms)
    assert base.risk_score != tuned.risk_score
    assert RiskAdapters.bayesian(vit(hr=135, age=30), age_bounds=(3, 12, 24)).risk_score == base.risk_score


# ---- step 2: the settings object -------------------------------------------------------------------------------------

def test_defaults_are_what_the_system_already_used():
    s = ClinicalSettings()
    assert s.non_default() == {} and s.weakenings() == []
    assert dict(s.norms) == PEDIATRIC_NORMS and s.age_group_upper_months == AGE_GROUP_UPPER_MONTHS
    assert (s.critical_o2_below, s.hypothermia_below) == (88, 35)
    assert (s.hard_rule_confirm_window, s.hard_rule_corroboration, s.hard_rule_corroboration_margin) == (1, True, 0.05)   # window 1: decision D1


@pytest.mark.parametrize("bad", [
    {"max_age_mnths": 200},                                   # a typo must not silently do nothing
    {"max_age_months": -1}, {"max_age_months": 5000}, {"max_age_months": "old"}, {"max_age_months": True},
    {"outside_population": "ignore"}, {"require_age": "yes"},
    {"age_group_upper_months": (12, 6, 36)}, {"age_group_upper_months": (3, 12)},
    {"critical_o2_below": 120}, {"critical_o2_below": float("nan")},
    {"hypothermia_below": 38}, {"hard_rule_confirm_window": 0}, {"hard_rule_confirm_window": 2.5},
    {"hard_rule_confirm_window": 1001}, {"hard_rule_corroboration": "no"},
    {"hard_rule_corroboration_margin": 2}, {"hard_rule_corroboration_margin": 0.0},
    {"norms": {"adult": {"hr_high": 100}}},                   # unknown group
    {"norms": {"child": {"hr_hgh": 100}}},                    # unknown key
    {"norms": {"child": {"hr_high": 105}}},                   # would make the faulted-channel placeholder (110) a finding
    {"norms": {"child": {"o2_low": 99}}},
    {"norms": {"child": {"hr_low": 120}}},
])
def test_invalid_settings_are_refused(bad):
    with pytest.raises(SettingsError):
        ClinicalSettings.from_mapping(bad)


def test_settings_load_from_json_and_partial_norms_merge_over_the_defaults(tmp_path):
    s = ClinicalSettings.from_json(json.dumps({"max_age_months": 252, "norms": {"child": {"hr_high": 125}}}))
    assert s.max_age_months == 252 and s.norms["child"]["hr_high"] == 125
    assert s.norms["child"]["hr_low"] == 100 and s.norms["infant"]["hr_high"] == 150
    for bad in ("[1, 2]", "{not json"):
        with pytest.raises(SettingsError):
            ClinicalSettings.from_json(bad)
    path = tmp_path / "unit.json"
    path.write_text('{"critical_o2_below": 90}')
    assert ClinicalSettings.coerce(path).critical_o2_below == 90 and ClinicalSettings.coerce(str(path)).critical_o2_below == 90
    with pytest.raises(SettingsError):
        ClinicalSettings.coerce(tmp_path / "missing.json")        # never quietly the defaults


def test_coerce_takes_what_a_caller_holds():
    s = ClinicalSettings(max_age_months=300)
    assert ClinicalSettings.coerce(None).non_default() == {} and ClinicalSettings.coerce(s) is s
    assert ClinicalSettings.coerce({"max_age_months": 300}).max_age_months == 300
    with pytest.raises(SettingsError):
        ClinicalSettings.coerce(42)


def test_settings_cannot_be_changed_after_construction():
    s = ClinicalSettings()
    with pytest.raises(Exception):
        s.critical_o2_below = 70.0
    with pytest.raises(TypeError):
        s.norms["child"]["hr_high"] = 999
    with pytest.raises(TypeError):
        s.norms["child"] = {}
    s.as_dict()["norms"]["child"]["hr_high"] = 999            # a copy, not the live table
    assert s.norms["child"]["hr_high"] == 130


@pytest.mark.parametrize("bad", [
    {"norms": {"generic": {"hr_high": 150, "hr_low": 90, "rr_high": 40, "o2_low": 91, "temp_high": 39.0}}},  # groups missing
    {"norms": None}, {"norms": []}, {"norms": {"child": 5}}, {"age_group_upper_months": None},
    {"age_group_upper_months": "abc"}, {"max_age_months": [1]},
])
def test_malformed_settings_are_a_settings_error_never_a_crash_later(bad):
    with pytest.raises(SettingsError):
        ClinicalSettings(**bad)
    for bad_norms in ({"norms": {"child": 5}}, {"norms": [1]}, {"norms": None}):
        with pytest.raises(SettingsError):
            ClinicalSettings.from_mapping(bad_norms)


def test_every_value_participates_in_the_digest():
    base = ClinicalSettings().digest()
    for change in ({"max_age_months": 217}, {"require_age": True}, {"critical_o2_below": 87.0},
                   {"hard_rule_confirm_window": 4}, {"outside_population": "assess_anyway"},
                   {"hard_rule_corroboration": False}, {"hard_rule_corroboration_margin": 0.2},
                   {"probe_off_spo2_at_or_below": 1.0},
                   {"norms": {"generic": {"temp_high": 38.9}}}, {"age_group_upper_months": (4, 12, 36)}):
        assert ClinicalSettings.from_mapping(change).digest() != base, change


def test_equal_values_are_equal_everywhere_whatever_their_spelling():
    a = ClinicalSettings.from_mapping({"age_group_upper_months": [3.0, 12.0, 36.0], "max_age_months": 216,
                                       "critical_o2_below": 88.0})
    assert a.digest() == ClinicalSettings().digest() and a.non_default() == {}


def test_weakenings_name_only_the_settings_that_alert_less():
    s = ClinicalSettings.from_mapping({
        "norms": {"child": {"hr_high": 140, "hr_low": 90, "o2_low": 90, "rr_high": 30}},
        "hard_rule_confirm_window": 2, "hard_rule_corroboration": False, "outside_population": "assess_anyway"})
    assert s.weakenings() == sorted([
        "norms.child.hr_high", "norms.child.hr_low", "norms.child.o2_low", "hard_rule_confirm_window",
        "hard_rule_corroboration", "outside_population"])                       # rr_high 30 < 35 alerts MORE
    assert "norms.child.rr_high" in s.non_default()


@pytest.mark.parametrize("change,weaker", [
    ({"hard_rule_confirm_window": 1}, False),         # the default: every hard rule holds WARNING at once
    ({"hard_rule_confirm_window": 2}, True),          # a lone hard rule now waits for a repeat: weaker than the default
    ({"hard_rule_confirm_window": 3}, True),          # the former default is weaker than 1
    ({"hard_rule_confirm_window": 10}, True),         # a longer window counts more repeats but still waits for one
    ({"max_age_months": 300}, True),                  # fewer patients get the finding
    ({"max_age_months": None}, True),
    ({"max_age_months": 100}, False),
    ({"age_group_upper_months": (4, 12, 36)}, True),  # direction cannot be shown to be safe, so it is listed
    ({"hard_rule_corroboration_margin": 0.3}, True),
    ({"hard_rule_corroboration_margin": 0.001}, False),
    ({"require_age": True}, False),
    ({"critical_o2_below": 85.0}, True), ({"critical_o2_below": 90.0}, False),
    ({"hypothermia_below": 33.0}, True), ({"hypothermia_below": 36.0}, False),
])
def test_weakening_direction(change, weaker):
    assert bool(ClinicalSettings.from_mapping(change).weakenings()) is weaker, change


# ---- step 3: the cassette applies the settings, and the record names them ---------------------------------------------

import pediatric_cassette  # noqa: E402
from observe_consolidated import ObserveClinicalEngine, OperationalRegime  # noqa: E402
from pediatric_cassette import PediatricCassette  # noqa: E402


def engine(**overrides):
    return ObserveClinicalEngine(cassette=PediatricCassette(settings=ClinicalSettings.from_mapping(overrides)))


def snap(hr=110, o2=98.0, rr=25, temp=37.0, age=24, pid="P1", **ctx):
    context = {} if age is None else {"age_months": age}
    context.update(ctx)
    return VitalsSnapshot(pid, T0, hr, o2, rr, temp, context)


def test_default_settings_record_nothing_and_keep_the_shared_engine_table():
    c = PediatricCassette()
    assert c.settings.non_default() == {} and c.identity_extras == {}
    assert c.engines() is pediatric_cassette._ENGINES and c.settings.weakenings() == []


def test_default_engine_outputs_are_identical_through_the_tuned_path_at_default_values():
    """At defaults the cassette shares the module table, so that alone proves nothing. Run the path a tuned cassette
    uses, with every value at its default, and compare text and score: a setting must change only what it names."""
    from functools import partial
    d = ClinicalSettings()
    tuned_h = partial(RiskAdapters.heuristic, norms=d.norms, age_bounds=d.age_group_upper_months,
                      critical_o2_below=d.critical_o2_below, hypothermia_below=d.hypothermia_below)
    tuned_b = partial(RiskAdapters.bayesian, norms=d.norms, age_bounds=d.age_group_upper_months)
    for age in (None, 1, 6, 24, 60, 150, 480):
        for v in (snap(168, 83.0, 46, 39.5, age), snap(60, 95.0, 20, 34.0, age), snap(age=age), snap(100, 98, 24, 39.2, age)):
            assert RiskAdapters.heuristic(v).triggered_rules == tuned_h(v).triggered_rules
            assert RiskAdapters.heuristic(v).risk_score == tuned_h(v).risk_score
            assert RiskAdapters.bayesian(v).risk_score == tuned_b(v).risk_score


def test_tuning_one_value_does_not_rewrite_the_text_of_rows_nobody_touched():
    fever = snap(100, 98, 24, 39.2, 60)
    base = RiskAdapters.heuristic(fever).triggered_rules
    out = engine(critical_o2_below=89.0).evaluate(fever).triggered_rules
    assert [r for r in out if r.startswith("FEVER")] == [r for r in base if r.startswith("FEVER")]
    assert any(r == "HYPOTHERMIA: 34.0°C < 35.0°C" for r in engine(critical_o2_below=89.0).evaluate(snap(temp=34.0, pid="h")).triggered_rules)
    odd = engine(hypothermia_below=34.96).evaluate(snap(temp=34.0, pid="odd")).triggered_rules
    assert any(r == "HYPOTHERMIA: 34.0°C < 34.96°C" for r in odd)               # the line in force, not a rounded one


def test_settings_are_hashable():
    assert hash(ClinicalSettings()) == hash(ClinicalSettings.from_mapping({"critical_o2_below": 88.0}))
    assert hash(ClinicalSettings()) != hash(ClinicalSettings(max_age_months=300))


def test_a_cassette_refuses_something_that_is_not_settings():
    with pytest.raises(TypeError):
        PediatricCassette(settings={"max_age_months": 200})


def test_hard_rule_values_come_from_the_settings_or_the_arguments_never_both():
    from_settings = PediatricCassette(settings=ClinicalSettings(hard_rule_confirm_window=5))
    from_argument = PediatricCassette(hard_rule_confirm_window=5)
    assert from_settings.hard_rule_confirm_window() == from_argument.hard_rule_confirm_window() == 5
    assert from_settings.identity_extras == from_argument.identity_extras == {"hard_rule_confirm_window": "5"}
    with pytest.raises(ValueError):
        PediatricCassette(settings=ClinicalSettings(), hard_rule_confirm_window=5)
    with pytest.raises(ValueError):
        PediatricCassette(hard_rule_confirm_window=None)                     # still refused, as before


def test_a_tuned_norm_changes_the_finding_and_is_named_in_the_identity():
    child = dict(age=60, hr=128)
    base = ObserveClinicalEngine().evaluate(snap(**child))
    tuned = engine(norms={"child": {"hr_high": 125}}).evaluate(snap(**child))
    assert not any("TACHYCARDIA" in r for r in base.triggered_rules)
    assert any("TACHYCARDIA: 128 > 125" in r for r in tuned.triggered_rules)         # 125, not 125.0
    c = PediatricCassette(settings=ClinicalSettings.from_mapping({"norms": {"child": {"hr_high": 125}}}))
    assert c.identity_extras["setting.norms.child.hr_high"] == "125"
    assert len(c.identity_extras["settings_digest"]) == 64
    assert "settings_weaker" not in c.identity_extras                               # a lower hr_high alerts more
    assert base.decision_fingerprint != tuned.decision_fingerprint


def test_a_tuned_critical_o2_line_moves_the_hard_rule_and_is_marked_weaker():
    v = snap(o2=86.0)
    assert any("CRITICAL_O2" in r for r in ObserveClinicalEngine().evaluate(v).triggered_rules)
    assert not any("CRITICAL_O2" in r for r in engine(critical_o2_below=85.0).evaluate(v).triggered_rules)
    lowered = PediatricCassette(settings=ClinicalSettings.from_mapping({"critical_o2_below": 85.0}))
    assert lowered.identity_extras["settings_weaker"] == "critical_o2_below"
    assert any("CRITICAL_O2" in r and "<90%" in r
               for r in engine(critical_o2_below=90.0).evaluate(snap(o2=89.0)).triggered_rules)


def test_every_age_group_boundary_is_adjustable():
    assert get_age_group(4, (6, 12, 36)) == "neonatal" and get_age_group(4) == "infant"
    assert get_age_group(14, (3, 18, 36)) == "infant" and get_age_group(14) == "toddler"
    assert get_age_group(30, (3, 12, 48)) == "toddler" and get_age_group(40, (3, 12, 48)) == "toddler"
    assert get_age_group(40) == "child"


def test_a_norm_given_as_a_float_is_the_same_value_everywhere():
    out = engine(norms={"child": {"hr_high": 125.0}}).evaluate(snap(age=60, hr=128))
    assert any(r == "TACHYCARDIA: 128 > 125" for r in out.triggered_rules)          # 125, not 125.0
    same = ClinicalSettings.from_mapping({"norms": {"child": {"hr_high": 125.0}}})
    assert same.digest() == ClinicalSettings.from_mapping({"norms": {"child": {"hr_high": 125}}}).digest()
    assert type(same.norms["child"]["hr_high"]) is int


def test_age_group_boundaries_are_adjustable_through_the_cassette():
    base = RiskAdapters.heuristic(snap(hr=135, age=30)).triggered_rules
    out = engine(age_group_upper_months=(3, 12, 24)).evaluate(snap(hr=135, age=30))
    assert any("TACHYCARDIA: 135 > 130" in r for r in out.triggered_rules)          # now judged on the 'child' row
    assert not any("TACHYCARDIA" in r for r in base)                                # toddler row allows up to 140


def test_the_settings_a_cassette_holds_cannot_be_reassigned():
    c = PediatricCassette()
    for name, value in (("settings", ClinicalSettings(max_age_months=300)), ("_engines", {})):
        with pytest.raises(AttributeError):
            setattr(c, name, value)


def test_a_loosened_setting_logs_a_warning_when_the_cassette_is_built(caplog):
    with caplog.at_level("WARNING", logger="PEDIATRIC_CASSETTE"):
        PediatricCassette(settings=ClinicalSettings(critical_o2_below=85.0))
        assert "LESS likely" in caplog.text
        caplog.clear()
        PediatricCassette(settings=ClinicalSettings(critical_o2_below=90.0))
        assert "LESS likely" not in caplog.text


def test_the_two_modules_stay_in_pyproject():
    import pathlib
    import re
    text = (pathlib.Path(__file__).parent / "pyproject.toml").read_text()
    listed = set(re.findall(r'"([a-z_0-9]+)"', text[text.index("py-modules"):]))
    assert {"clinical_settings", "pediatric_cassette", "cassette"} <= listed


# ---- step 4: the supported population is a floor on the report, not a fault -------------------------------------------
# Finding measured on main before this step: every age from 36 months to 125 years was scored on the "child" row, so a
# healthy 40 year old at heart rate 70 was reported as BRADYCARDIA (<100) and nothing said the patient was out of scope.



def has_finding(v):
    return any("OUTSIDE_SUPPORTED_POPULATION" in r for r in v.triggered_rules)


def test_a_patient_at_or_inside_the_bound_has_no_population_finding():
    for age in (0, 24, 215, 216):
        assert not has_finding(ObserveClinicalEngine().evaluate(snap(age=age, pid=f"in-{age}"))), age


def test_a_patient_above_the_bound_is_flagged_and_floored_at_warning():
    v = ObserveClinicalEngine().evaluate(snap(age=480))
    assert v.regime == OperationalRegime.WARNING and v.escalation_required
    assert not v.unassessable                       # nothing about the reading is untrustworthy
    assert has_finding(v) and any("interpret with caution" in r for r in v.triggered_rules)
    assert v.risk_score >= 0.5                       # the score follows the regime it is reported with


def test_being_out_of_scope_never_scores_a_deteriorating_patient_lower():
    sick = dict(hr=168, o2=80.0, rr=46, temp=39.5)
    inside = ObserveClinicalEngine().evaluate(snap(age=200, **sick))
    outside = ObserveClinicalEngine().evaluate(snap(age=480, **sick))
    order = ["stable", "caution", "warning", "critical"]
    assert order.index(outside.regime.value) >= order.index(inside.regime.value)
    assert any("CRITICAL_O2" in r for r in outside.triggered_rules)            # still assessed on every channel
    assert outside.active_engines                                               # not "nothing assessable"


def test_the_population_finding_can_be_turned_off_or_the_bound_moved():
    assert not has_finding(engine(outside_population="assess_anyway").evaluate(snap(age=480)))
    assert not has_finding(engine(max_age_months=None).evaluate(snap(age=480)))
    assert not has_finding(engine(max_age_months=300).evaluate(snap(age=250)))
    assert has_finding(engine(max_age_months=300).evaluate(snap(age=301)))


def test_an_unknown_age_is_assessed_as_before_unless_the_deployment_requires_one():
    assert not has_finding(ObserveClinicalEngine().evaluate(snap(age=None)))
    required = engine(require_age=True).evaluate(snap(age=None))
    assert has_finding(required) and required.regime == OperationalRegime.WARNING
    for unusable in (-5, "old", float("nan"), 2000):   # present but unusable counts as no age
        v = engine(require_age=True).evaluate(snap(age=unusable, pid=f"u{unusable}"))
        assert has_finding(v) and any("requires the patient's age" in r for r in v.triggered_rules), unusable
    newborn = engine(require_age=True).evaluate(snap(age=0, pid="newborn"))          # age 0 IS an age
    assert not has_finding(newborn)


def test_a_population_finding_and_a_faulted_channel_together_still_mask_the_channel():
    v = ObserveClinicalEngine().evaluate(snap(age=480, o2=float("nan")))
    assert v.unassessable and len(v.validation_faults) == 1 and has_finding(v)
    assert not any("CRITICAL_O2" in r for r in v.triggered_rules)               # the NaN channel is still masked


def test_an_over_age_patient_keeps_the_critical_hold_a_child_gets():
    """The floor is applied after tracking and never writes it, so CRITICAL does not drop to WARNING."""
    rows = [(90, 87, 60, 38.0), (135, 98, 12, 38.0), (160, 98, 45, 38.0)]
    for age in (120, 240, 480):
        e, seen = ObserveClinicalEngine(), []
        for i, (hr, o2, rr, t) in enumerate(rows):
            seen.append(e.evaluate(VitalsSnapshot("P", T0 + timedelta(minutes=i), hr, o2, rr, t, {"age_months": age})).regime)
        assert seen == [OperationalRegime.CRITICAL] * 3, (age, seen)


def test_the_floor_pages_once_however_long_the_patient_stays_out_of_scope():
    """Measured before this fix: the floor reused the fault dedup window, so a standing adult was re-paged every 5 minutes."""
    e = ObserveClinicalEngine()
    pages = [e.evaluate(VitalsSnapshot("A", T0 + timedelta(minutes=m), 110, 98.0, 25, 37.0, {"age_months": 480})
                        ).escalation_required for m in (0, 1, 2, 7, 14, 60, 600)]
    assert pages == [True] + [False] * 6                                         # one page, then held, indefinitely
    crit = e.evaluate(VitalsSnapshot("A", T0 + timedelta(minutes=3), 168, 83.0, 46, 39.5, {"age_months": 480}))
    assert crit.regime == OperationalRegime.CRITICAL and crit.escalation_required


def test_an_improving_adult_is_not_paged_again_as_a_new_escalation():
    """Review finding: tracking fell CRITICAL to STABLE, the floor lifted the report, and it paged as new_escalation."""
    def run(age):
        e, out = ObserveClinicalEngine(), []
        for i, row in enumerate([(168, 83.0, 46, 39.5)] + [(110, 98.0, 25, 37.0)] * 12):
            out.append(e.evaluate(VitalsSnapshot("P", T0 + timedelta(minutes=6 * i), *row, {"age_months": age})))
        return out
    adult, child = run(480), run(100)
    assert adult[0].regime == OperationalRegime.CRITICAL and adult[0].escalation_required
    assert all(v.regime.value in ("critical", "warning") for v in adult)            # never reported below the floor
    assert not any(v.escalation_required for v in adult[1:])                          # nobody is paged for improving
    assert not any(v.escalation_kind == "new_escalation" for v in adult[1:])
    assert [v.escalation_kind for v in adult] == [v.escalation_kind for v in child]  # same kinds as a child


def test_the_floor_pages_again_only_after_the_patient_was_back_in_scope():
    e = ObserveClinicalEngine()

    def go(minute, age):
        return e.evaluate(VitalsSnapshot("A", T0 + timedelta(minutes=minute), 110, 98.0, 25, 37.0, {"age_months": age}))
    assert go(0, 480).escalation_required and not go(1, 480).escalation_required
    assert go(2, 100).regime == OperationalRegime.STABLE                           # back in scope: no floor, no page
    again = go(3, 480)
    assert again.regime == OperationalRegime.WARNING and again.escalation_required  # out of scope again: told again


def test_the_floor_state_is_put_back_when_an_evaluation_fails(monkeypatch):
    e = ObserveClinicalEngine()
    e.evaluate(snap(age=100, pid="A"))
    before = {k: dict(v) for k, v in e._patient_fault_paged.items()}
    monkeypatch.setattr(e, "_commit_state", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("ledger down")))
    with pytest.raises(RuntimeError):
        e.evaluate(snap(age=480, pid="A"))
    assert {k: dict(v) for k, v in e._patient_fault_paged.items()} == before        # the page is not spent by a crash


def test_require_age_does_not_depend_on_the_population_action():
    s = ClinicalSettings(require_age=True, outside_population="assess_anyway")
    floor = PediatricCassette(settings=s).regime_floor(snap(age=None))
    assert floor is not None and "requires the patient's age" in floor[1]
    assert PediatricCassette(settings=s).regime_floor(snap(age=480)) is None         # the bound is still off


def test_the_floor_never_writes_the_tracked_regime():
    e = ObserveClinicalEngine()
    assert e.evaluate(snap(age=480, pid="A")).regime == OperationalRegime.WARNING
    assert e._patient_policies["A"].current_regime == OperationalRegime.STABLE        # reported, not tracked
    later = e.evaluate(VitalsSnapshot("A", T0 + timedelta(hours=2), 110, 98.0, 25, 37.0, {"age_months": 100}))
    assert later.regime == OperationalRegime.STABLE and not later.escalation_required


def test_the_floor_is_in_the_audit_entry_only_when_it_applied():
    e = ObserveClinicalEngine()
    inside = e.evaluate(snap(age=24, pid="in"))
    outside = e.evaluate(snap(age=480, pid="out"))
    entries = {en["patient_id"]: en for en in e.audit_ledger.entries}
    assert "regime_floor" not in entries["in"]["data"] and entries["out"]["data"]["regime_floor"]
    assert inside.regime != OperationalRegime.WARNING and outside.regime == OperationalRegime.WARNING


def test_the_default_cassette_has_the_floor_and_a_cassette_without_the_hook_is_unchanged():
    assert PediatricCassette().regime_floor(snap(age=480)) is not None
    assert PediatricCassette().regime_floor(snap(age=24)) is None
    assert PediatricCassette(settings=ClinicalSettings(outside_population="assess_anyway")).regime_floor(snap(age=480)) is None
    assert PediatricCassette().regime_floor(object()) is None                    # a reading with no context at all


# ---- step 5: the settings reach the clinician's system, and every decision names them ----------------------------------

from clinical_governance_system import build_multi_hospital_system, build_single_hospital_system  # noqa: E402


def test_a_decision_names_the_settings_it_was_made_under():
    system = build_single_hospital_system(allow_no_alert_channel=True)
    assert system.process_vitals(snap(age=24)).settings_digest == ClinicalSettings().digest()
    tuned = build_single_hospital_system(allow_no_alert_channel=True, settings={"critical_o2_below": 90})
    decision = tuned.process_vitals(snap(age=24, o2=89.0))
    assert decision.settings_digest == ClinicalSettings(critical_o2_below=90).digest() != ClinicalSettings().digest()
    assert any("CRITICAL_O2" in r for r in decision.triggered_rules)


def test_the_system_takes_settings_as_an_object_a_mapping_or_a_file(tmp_path):
    path = tmp_path / "unit.json"
    path.write_text('{"max_age_months": 300}')
    digests = {build_single_hospital_system(allow_no_alert_channel=True, settings=value).settings.digest()
               for value in (ClinicalSettings(max_age_months=300), {"max_age_months": 300}, path)}
    assert len(digests) == 1


def test_bad_or_missing_settings_stop_the_system_rather_than_run_on_the_defaults(tmp_path):
    for bad in ({"max_age_mnths": 1}, tmp_path / "missing.json", 42):
        with pytest.raises(SettingsError):
            build_single_hospital_system(allow_no_alert_channel=True, settings=bad)
    with pytest.raises(SettingsError):
        build_multi_hospital_system(["h1", "h2", "h3"], allow_no_alert_channel=True, settings={"nope": 1})


def test_an_assessment_that_failed_still_names_its_settings():
    system = build_single_hospital_system(allow_no_alert_channel=True, settings={"max_age_months": 300})

    def boom(_):
        raise RuntimeError("engine down")
    system.observe.evaluate = boom
    decision = system.process_vitals(snap(age=24))
    assert decision.regime == "assessment_unavailable" and decision.settings_digest == system.settings.digest()


def test_an_adult_is_reported_at_warning_with_the_finding_in_the_decision():
    system = build_single_hospital_system(allow_no_alert_channel=True)
    decision = system.process_vitals(snap(age=480, hr=70, pid="adult-1"))
    assert decision.regime == "warning" and any("OUTSIDE_SUPPORTED_POPULATION" in r for r in decision.triggered_rules)
    assert decision.escalation_required


def test_the_notification_settings_pass_through_the_builders():
    from clinical_notification import ClinicalNotificationSettings
    system = build_single_hospital_system(allow_no_alert_channel=True,
                                          notification_settings=ClinicalNotificationSettings(min_regime="warning"))
    assert system.notification_settings.min_regime == "warning"


# ---- decisions D4 and D6 -------------------------------------------------------------------------------------------------

def test_a_critical_the_engine_found_is_never_rate_limited_or_counted_even_as_an_ordinary_escalation():
    from perceive_consolidated import PolicyEnforcementConfig, PolicyManifest, PerceiveGovernanceKernel, PolicyRequest
    kernel = PerceiveGovernanceKernel(enforcement=PolicyEnforcementConfig(enforce_escalation_limits=True))
    kernel.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=T0,
                                            policies={"escalation_policy": {"max_daily": 1}}))
    found = {"status": "assessed", "regime": "critical"}                    # what the orchestrator writes from OBSERVE's verdict

    def ask(i, minutes=None, **ctx):
        return kernel.evaluate_request(PolicyRequest(
            request_id=f"R{i}", request_type="escalate_patient", subject_id="P", actor_id="t",
            context={"justification": "x", **ctx}, timestamp=T0 + timedelta(minutes=i if minutes is None else minutes)))
    assert ask(0, severity="warning").approved                              # the first escalation of the day
    assert not ask(1, severity="warning").approved                          # a minute later: the 15-minute cooldown binds
    exempt = ask(2, observe_assessment=found)
    assert exempt.approved                                                  # a CRITICAL the engine found is exempt
    assert not any("cooldown" in str(v).lower() for v in exempt.violations)
    assert exempt.advisory_violations == []                                 # and the exemption is not an "advisory violation"
    note = kernel.audit_ledger.entries[-1].policy_outputs[-1]               # but it is on the record
    assert note["gate_name"] == "escalation_rate_policy" and "never rate-limited" in note["violations"][0]
    for i in range(3, 16):                                                  # thirteen more CRITICALs (the day limit is 10): none is counted
        assert ask(i, observe_assessment=found).approved
    assert ask(600, minutes=600, severity="warning").approved               # so a WARNING hours later is not refused for them


def test_a_caller_cannot_claim_the_exemption_and_a_warning_assessment_does_not_get_it():
    from perceive_consolidated import PolicyEnforcementConfig, PolicyManifest, PerceiveGovernanceKernel, PolicyRequest
    kernel = PerceiveGovernanceKernel(enforcement=PolicyEnforcementConfig(enforce_escalation_limits=True))
    kernel.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=T0,
                                            policies={"escalation_policy": {"max_daily": 1}}))

    def ask(i, **ctx):
        return kernel.evaluate_request(PolicyRequest(
            request_id=f"R{i}", request_type="escalate_patient", subject_id="P", actor_id="t",
            context={"justification": "x", **ctx}, timestamp=T0 + timedelta(minutes=i)))
    assert ask(0, severity="warning").approved
    for claim in ({"severity": "critical"}, {"regime": "critical"}, {"severity": "CRITICAL", "regime": "critical"},
                  {"severity": "warning", "observe_assessment": {"status": "assessed", "regime": "warning"}},
                  {"observe_assessment": {"status": "unavailable", "regime": "critical"}},
                  {"observe_assessment": "critical"}, {"observe_assessment": ["critical"]}):
        assert not ask(1, **claim).approved, claim                          # a claim or a non-critical assessment: still counted


def test_a_failed_eviction_record_does_not_fail_another_patients_assessment_and_is_reported():
    told = []
    eng = ObserveClinicalEngine(max_tracked_patients=1, on_degraded=told.append)
    eng.evaluate(snap(age=24, pid="A"))
    real = eng.audit_ledger.append

    def failing(patient_id, action, data):
        if action == "STATE_EVICTED":
            raise OSError("ledger full")
        return real(patient_id, action, data)
    eng.audit_ledger.append = failing
    verdict = eng.evaluate(snap(age=24, pid="B"))                          # evicts A; the eviction record fails
    assert verdict.regime == OperationalRegime.STABLE and "B" in eng._patient_policies
    assert told and told[0]["kind"] == "state_evicted_write_failed" and told[0]["patient_id"] == "A"
    assert list(eng.degraded_events)[0]["error"].startswith("OSError")


def test_a_hook_that_raises_does_not_fail_the_assessment_either():
    eng = ObserveClinicalEngine(max_tracked_patients=1, on_degraded=lambda e: 1 / 0)
    eng.evaluate(snap(age=24, pid="A"))
    eng.audit_ledger.append = lambda *a, **k: (_ for _ in ()).throw(OSError("full")) if a[1] == "STATE_EVICTED" else "h"
    assert eng.evaluate(snap(age=24, pid="B")).regime == OperationalRegime.STABLE


def test_the_governance_system_tells_a_person_about_failed_housekeeping_through_the_desk():
    from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
    from conservation_kernel import HmacSigner
    calls = []
    desk = EscalationDesk(NotificationPolicy((Tier("charge", calls.append, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": HmacSigner(b"settings-test-responder-key-0123456789", key_id="nurse")})
    from clinical_governance_system import ClinicalGovernanceSystem
    system = ClinicalGovernanceSystem(escalations=desk)
    system.observe._on_degraded({"kind": "state_evicted_write_failed", "error": "OSError: full", "patient_id": "A"})
    assert calls and calls[0]["stage"] == "observe_housekeeping" and "patient_id" not in calls[0]


def test_a_slow_heart_alone_does_not_corroborate_either():
    from clinical_settings import ClinicalSettings
    from observe_consolidated import RiskOutput
    out = RiskOutput("heuristic", 0.8, 0.9, {}, ["CRITICAL_O2: 86.0% (<88%)", "BRADYCARDIA: 60 < 95"], T0)
    assert not PediatricCassette().hard_rule_corroborated([out])
    assert PediatricCassette(settings=ClinicalSettings(heart_rate_corroborates=True)).hard_rule_corroborated([out])


def test_the_governance_system_never_raises_about_housekeeping_with_no_desk_or_a_broken_one(caplog):
    from clinical_governance_system import ClinicalGovernanceSystem
    event = {"kind": "state_evicted_write_failed", "error": "OSError: full"}
    quiet = ClinicalGovernanceSystem(allow_no_alert_channel=True)
    with caplog.at_level("CRITICAL"):
        quiet._engine_degraded(event)
    assert "could not tell a person" not in caplog.text                          # no desk: nothing to try

    class Broken:
        def raise_and_dispatch(self, **kw):
            raise RuntimeError("desk down")
    broken = ClinicalGovernanceSystem(escalations=Broken())
    with caplog.at_level("CRITICAL"):
        broken._engine_degraded(event)                                           # must not raise
    assert "could not tell a person" in caplog.text

# ---- the probe note: an annotation that never changes a regime --------------------------------------------------------

CALM = (110, 98.0, 25, 37.0)


def _run(rows, eng=None, pid="probe"):
    eng = eng or ObserveClinicalEngine()
    return [eng.evaluate(VitalsSnapshot(pid, T0 + timedelta(minutes=i), *row, {"age_months": 24}))
            for i, row in enumerate(rows)]


def test_a_zero_spo2_carries_a_probe_note_and_the_regime_is_not_lowered_by_it():
    noted = _run([CALM, (100, 0.0, 24, 37.0)])[1]
    plain = _run([CALM, (100, 0.0, 24, 37.0)], engine(probe_off_spo2_at_or_below=None), pid="plain")[1]
    assert any("POSSIBLE_SENSOR_DISCONNECT" in r and "check the patient" in r and "not downgraded" in r
               for r in noted.triggered_rules)
    assert not any("POSSIBLE_SENSOR_DISCONNECT" in r for r in plain.triggered_rules)
    assert noted.regime == plain.regime and noted.escalation_required == plain.escalation_required
    assert any(r.startswith("CRITICAL_O2") for r in noted.triggered_rules)         # a zero is still a hard rule
    assert noted.decision_fingerprint != plain.decision_fingerprint                  # the note is in the evidence


def test_the_probe_note_threshold_is_adjustable_and_validated():
    assert not any("POSSIBLE_SENSOR_DISCONNECT" in r for r in _run([(100, 4.0, 24, 37.0)])[0].triggered_rules)
    assert any("POSSIBLE_SENSOR_DISCONNECT" in r
               for r in _run([(100, 4.0, 24, 37.0)], engine(probe_off_spo2_at_or_below=5.0), pid="t")[0].triggered_rules)
    for bad in (100, -1, "x", True, float("nan")):
        with pytest.raises(SettingsError):
            ClinicalSettings(probe_off_spo2_at_or_below=bad)
    assert ClinicalSettings(probe_off_spo2_at_or_below=None).weakenings() == []      # annotation only: never weaker


def test_a_probe_threshold_spelled_5_or_5_point_0_is_one_value():
    a, b = ClinicalSettings(probe_off_spo2_at_or_below=5), ClinicalSettings(probe_off_spo2_at_or_below=5.0)
    assert a.digest() == b.digest() and a.non_default() == b.non_default() == {"probe_off_spo2_at_or_below": "5"}
    assert type(a.probe_off_spo2_at_or_below) is int


def test_a_normal_spo2_has_no_note_and_clean_entries_keep_their_shape():
    v = ObserveClinicalEngine()
    out = v.evaluate(VitalsSnapshot("P1", T0, *CALM, {"age_months": 24}))
    assert not any("SENSOR_DISCONNECT" in r for r in out.triggered_rules)
    assert "advisory_notes" not in v.audit_ledger.entries[-1]["data"]


def test_the_note_is_in_the_audit_entry_and_the_probe_setting_is_recorded_when_changed():
    e = ObserveClinicalEngine()
    e.evaluate(VitalsSnapshot("P1", T0, 100, 0.0, 24, 37.0, {"age_months": 24}))
    assert e.audit_ledger.entries[-1]["data"]["advisory_notes"][0].startswith("POSSIBLE_SENSOR_DISCONNECT")
    c = PediatricCassette(settings=ClinicalSettings(probe_off_spo2_at_or_below=None))
    assert c.identity_extras["setting.probe_off_spo2_at_or_below"] == "None"
    assert PediatricCassette().advisory_notes(object()) == []                        # a reading with no SpO2 at all


def test_a_cassette_without_the_hook_gets_no_note():
    from cassette import conformance_failures
    assert conformance_failures(PediatricCassette()) == []
    assert "advisory_notes" in __import__("cassette").OPTIONAL


def test_the_hook_runs_after_the_assessment_so_a_slow_or_re_entrant_hook_cannot_fail_it():
    seen = []
    eng = ObserveClinicalEngine(max_tracked_patients=1)

    def hook(event):
        seen.append(event["kind"])
        eng.evaluate(snap(age=24, pid="C"))                                 # re-enters the engine from the hook
    eng._on_degraded = hook
    eng.evaluate(snap(age=24, pid="A"))
    real = eng.audit_ledger.append
    eng.audit_ledger.append = lambda pid, action, data: (_ for _ in ()).throw(OSError("full")) if action == "STATE_EVICTED" else real(pid, action, data)
    verdict = eng.evaluate(snap(age=24, pid="B"))
    assert verdict.regime == OperationalRegime.STABLE and seen and seen[0] == "state_evicted_write_failed"


def test_a_patient_evicted_by_another_evaluation_mid_assessment_gets_a_fresh_policy_not_a_crash():
    eng = ObserveClinicalEngine(max_tracked_patients=10)
    eng._get_policy("B")
    real_touch = eng._touch_patient

    def evicting_touch(pid):
        real_touch(pid)
        with eng._state_lock:
            eng._patient_policies.pop(pid, None)                            # another thread evicts it right here
    eng._touch_patient = evicting_touch
    assert eng._get_policy("B").current_regime == OperationalRegime.STABLE


def test_events_pushed_out_of_the_last_hundred_are_counted_not_lost_silently():
    eng = ObserveClinicalEngine()
    for i in range(105):
        eng._degraded({"kind": "state_evicted_write_failed", "error": f"e{i}", "patient_id": f"p{i}"})
    assert len(eng.degraded_events) == 100 and eng.degraded_dropped == 5


def test_through_the_orchestrator_a_critical_reading_is_exempt_and_a_calm_one_is_not():
    """Integration of D4 with PR 13: the orchestrator writes `observe_assessment` from OBSERVE's own verdict, and that is
    what the gate trusts. A calm reading on the same path carries no exemption."""
    from perceive_consolidated import PolicyEnforcementConfig
    from test_identity_binding import CRIT, orch, run, vit
    o = orch()
    o.perceive.enforcement = PolicyEnforcementConfig(enforce_escalation_limits=True)

    def gate_note(artifact, **reading):
        run(o, artifact, vit("P1", **reading))
        outputs = o.perceive.audit_ledger.entries[-1].policy_outputs
        return [x for x in outputs if x["gate_name"] == "escalation_rate_policy"][0]["violations"]
    assert any("never rate-limited" in v for v in gate_note("int-crit", **CRIT))
    assert not any("never rate-limited" in v for v in gate_note("int-calm"))
