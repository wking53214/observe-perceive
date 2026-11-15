"""test_clinical_settings.py -- the values a clinician may tune: engines, settings object, cassette, population floor.

Built up in steps; each group says which finding or promise it holds.
"""
import copy
import json
from datetime import datetime, timezone

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
    assert (s.hard_rule_confirm_window, s.hard_rule_corroboration, s.hard_rule_corroboration_margin) == (3, True, 0.05)


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
    ({"hard_rule_confirm_window": 1}, True),          # 1 turns repeat confirmation off
    ({"hard_rule_confirm_window": 2}, True),
    ({"hard_rule_confirm_window": 10}, False),        # a longer window lets MORE repeats count
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
