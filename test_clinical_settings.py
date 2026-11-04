"""Stage 1A step 5: clinician-adjustable settings, and the supported-population finding.

Three promises under test: (1) defaults change nothing a recorded fingerprint covers; (2) a setting that is
tuned is effective, validated, and visible in the verdict's identity; (3) a patient outside the supported
population is never scored less severely for it, only flagged.
"""
import json
from datetime import datetime, timezone

import pytest

import pediatric_cassette
from clinical_settings import ClinicalSettings, SettingsError
from observe_consolidated import ObserveClinicalEngine, OperationalRegime, RiskAdapters, VitalsSnapshot, get_age_group
from pediatric_cassette import PediatricCassette

T = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def snap(hr=110, o2=98.0, rr=25, temp=37.0, age=24, pid="P1", **ctx):
    context = {} if age is None else {"age_months": age}
    context.update(ctx)
    return VitalsSnapshot(pid, T, hr, o2, rr, temp, context)


def engine(**overrides):
    return ObserveClinicalEngine(cassette=PediatricCassette(settings=ClinicalSettings.from_mapping(overrides)))


# ---- defaults are inert ------------------------------------------------------------------------------------

def test_default_settings_record_nothing_and_keep_the_shared_engine_table():
    c = PediatricCassette()
    assert c.settings.non_default() == {}
    assert c.identity_extras == {}
    assert c.engines() is pediatric_cassette._ENGINES
    assert c.settings.weakenings() == []


def test_default_engine_outputs_are_identical_with_and_without_the_new_arguments():
    for age in (1, 6, 24, 60, 150):
        for v in (snap(168, 83.0, 46, 39.5, age), snap(60, 95.0, 20, 34.0, age), snap(age=age)):
            assert RiskAdapters.heuristic(v).triggered_rules == PediatricCassette().engines()["heuristic"](v).triggered_rules
            assert RiskAdapters.bayesian(v).risk_score == PediatricCassette().engines()["bayesian"](v).risk_score


# ---- validation ----------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"max_age_mnths": 200},                                   # a typo must not silently do nothing
    {"max_age_months": -1}, {"max_age_months": 5000}, {"max_age_months": "old"}, {"max_age_months": True},
    {"outside_population": "ignore"},
    {"age_group_upper_months": (12, 6, 36)}, {"age_group_upper_months": (3, 12)},
    {"critical_o2_below": 120}, {"critical_o2_below": float("nan")},
    {"hypothermia_below": 38}, {"hard_rule_confirm_window": 0}, {"hard_rule_confirm_window": 2.5},
    {"hard_rule_corroboration_margin": 2}, {"probe_off_spo2_at_or_below": 100},
    {"norms": {"adult": {"hr_high": 100}}},                   # unknown group
    {"norms": {"child": {"hr_hgh": 100}}},                    # unknown key
    {"norms": {"child": {"hr_high": 105}}},                   # would make the faulted-channel placeholder (110) a finding
    {"norms": {"child": {"o2_low": 99}}},
    {"norms": {"child": {"hr_low": 120}}},
])
def test_invalid_settings_are_refused(bad):
    with pytest.raises(SettingsError):
        ClinicalSettings.from_mapping(bad)


def test_settings_load_from_json_and_partial_norms_merge_over_the_defaults():
    s = ClinicalSettings.from_json(json.dumps({"max_age_months": 252, "norms": {"child": {"hr_high": 125}}}))
    assert s.max_age_months == 252 and s.norms["child"]["hr_high"] == 125
    assert s.norms["child"]["hr_low"] == 100 and s.norms["infant"]["hr_high"] == 150
    with pytest.raises(SettingsError):
        ClinicalSettings.from_json("[1, 2]")
    with pytest.raises(SettingsError):
        ClinicalSettings.from_json("{not json")


def test_a_cassette_refuses_something_that_is_not_settings():
    with pytest.raises(TypeError):
        PediatricCassette(settings={"max_age_months": 200})


def test_settings_cannot_be_changed_in_place_after_the_fact():
    s = ClinicalSettings()
    with pytest.raises(Exception):
        s.critical_o2_below = 70.0
    s.as_dict()["norms"]["child"]["hr_high"] = 999            # a copy, not the live table
    assert s.norms["child"]["hr_high"] == 130


# ---- tuned settings are effective and recorded ---------------------------------------------------------------

def test_a_tuned_norm_changes_the_finding_and_is_named_in_the_identity():
    child = dict(age=60, hr=128)
    base = ObserveClinicalEngine().evaluate(snap(**child))
    tuned = engine(norms={"child": {"hr_high": 125}}).evaluate(snap(**child))
    assert not any("TACHYCARDIA" in r for r in base.triggered_rules)
    assert any("TACHYCARDIA" in r for r in tuned.triggered_rules)
    c = PediatricCassette(settings=ClinicalSettings.from_mapping({"norms": {"child": {"hr_high": 125}}}))
    assert c.identity_extras["setting.norms.child.hr_high"] == "125"
    assert len(c.identity_extras["settings_digest"]) == 64
    assert "settings_weaker" not in c.identity_extras           # a lower hr_high alerts more, not less
    assert base.decision_fingerprint != tuned.decision_fingerprint


def test_a_tuned_critical_o2_floor_moves_the_hard_rule():
    v = snap(o2=86.0)
    assert any("CRITICAL_O2" in r for r in ObserveClinicalEngine().evaluate(v).triggered_rules)
    out = engine(critical_o2_below=85.0).evaluate(v)
    assert not any("CRITICAL_O2" in r for r in out.triggered_rules)
    lowered = PediatricCassette(settings=ClinicalSettings.from_mapping({"critical_o2_below": 85.0}))
    assert lowered.identity_extras["settings_weaker"] == "critical_o2_below"
    assert any("CRITICAL_O2" in r and "<90%" in r
               for r in engine(critical_o2_below=90.0).evaluate(snap(o2=89.0)).triggered_rules)


def test_age_group_boundaries_are_adjustable():
    assert get_age_group(30) == "toddler"
    assert get_age_group(30, (3, 12, 24)) == "child"
    base = RiskAdapters.heuristic(snap(hr=135, age=30)).triggered_rules
    out = engine(age_group_upper_months=(3, 12, 24)).evaluate(snap(hr=135, age=30))
    assert any("TACHYCARDIA: 135 > 130" in r for r in out.triggered_rules)      # now judged on the 'child' row
    assert not any("TACHYCARDIA" in r for r in base)                            # toddler row allows up to 140


def test_weakenings_name_only_the_settings_that_alert_less():
    s = ClinicalSettings.from_mapping({
        "norms": {"child": {"hr_high": 140, "hr_low": 90, "o2_low": 90, "rr_high": 30}},
        "hard_rule_confirm_window": 2, "hard_rule_corroboration": False, "outside_population": "assess_anyway"})
    assert s.weakenings() == sorted([
        "norms.child.hr_high", "norms.child.hr_low", "norms.child.o2_low", "hard_rule_confirm_window",
        "hard_rule_corroboration", "outside_population"])                       # rr_high 30 < 35 alerts MORE
    assert "norms.child.rr_high" in s.non_default()


def test_every_value_participates_in_the_digest():
    base = ClinicalSettings().digest()
    for change in ({"max_age_months": 217}, {"require_age": True}, {"critical_o2_below": 87.0},
                   {"hard_rule_confirm_window": 4}, {"probe_off_spo2_at_or_below": 1.0},
                   {"norms": {"generic": {"temp_high": 38.9}}}, {"age_group_upper_months": (4, 12, 36)}):
        assert ClinicalSettings.from_mapping(change).digest() != base, change


# ---- the supported population ------------------------------------------------------------------------------

def test_a_patient_at_or_inside_the_bound_has_no_population_finding():
    for age in (0, 24, 215, 216):
        v = ObserveClinicalEngine().evaluate(snap(age=age))
        assert not has_finding(v), age


def has_finding(v):
    return any("OUTSIDE_SUPPORTED_POPULATION" in r for r in v.triggered_rules)


def test_a_patient_above_the_bound_is_flagged_and_floored_at_warning():
    v = ObserveClinicalEngine().evaluate(snap(age=480))
    assert v.regime == OperationalRegime.WARNING and v.escalation_required
    assert not v.unassessable                       # nothing about the reading is untrustworthy
    assert has_finding(v) and any("interpret with caution" in r for r in v.triggered_rules)


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
        assert has_finding(engine(require_age=True).evaluate(snap(age=unusable)))


def test_a_population_finding_and_a_faulted_channel_together_still_mask_the_channel():
    v = ObserveClinicalEngine().evaluate(snap(age=480, o2=float("nan")))
    assert v.unassessable and len(v.validation_faults) == 1 and has_finding(v)
    assert not any("CRITICAL_O2" in r for r in v.triggered_rules)               # the NaN channel is still masked


def test_an_over_age_patient_keeps_the_critical_hold_a_child_gets():
    """Review finding: the population finding used to bypass policy tracking, so CRITICAL dropped to WARNING."""
    rows = [(90, 87, 60, 38.0), (135, 98, 12, 38.0), (160, 98, 45, 38.0)]
    from datetime import timedelta
    for age in (120, 240, 480):
        e, seen = ObserveClinicalEngine(), []
        for i, (hr, o2, rr, t) in enumerate(rows):
            seen.append(e.evaluate(VitalsSnapshot("P", T + timedelta(minutes=i), hr, o2, rr, t, {"age_months": age})).regime)
        assert seen == [OperationalRegime.CRITICAL] * 3, (age, seen)


def test_the_population_floor_pages_once_per_window_and_never_suppresses_a_later_critical_page():
    from datetime import timedelta
    e = ObserveClinicalEngine()
    pages = [e.evaluate(VitalsSnapshot("A", T + timedelta(minutes=m), 110, 98.0, 25, 37.0, {"age_months": 480})
                        ).escalation_required for m in (0, 1, 2)]
    assert pages == [True, False, False]                                         # one page, then held
    crit = e.evaluate(VitalsSnapshot("A", T + timedelta(minutes=3), 168, 83.0, 46, 39.5, {"age_months": 480}))
    assert crit.regime == OperationalRegime.CRITICAL and crit.escalation_required


# ---- review findings: direction, validation, immutability, canonical form ----------------------------------------

@pytest.mark.parametrize("change,weaker", [
    ({"hard_rule_confirm_window": 1}, True),          # 1 turns repeat confirmation off
    ({"hard_rule_confirm_window": 2}, True),
    ({"hard_rule_confirm_window": 10}, False),        # a longer window lets MORE repeats count
    ({"max_age_months": 300}, True),                  # fewer patients get the finding
    ({"max_age_months": None}, True),
    ({"max_age_months": 100}, False),
    ({"age_group_upper_months": (4, 12, 36)}, True),  # direction cannot be shown to be safe, so it is listed
    ({"hard_rule_corroboration_margin": 0.3}, True),
    ({"hard_rule_corroboration_margin": 0.0}, False),
    ({"require_age": True}, False),
])
def test_weakening_direction(change, weaker):
    assert bool(ClinicalSettings.from_mapping(change).weakenings()) is weaker, change


def test_a_window_of_one_really_is_the_weaker_behaviour():
    """The flag must match what the engine does: with window 1 two low readings in a row no longer escalate."""
    from datetime import timedelta
    rows = [(100, 98, 24, 37.0), (102, 85, 26, 37.5), (104, 84, 28, 37.6)]

    def third(window):
        e = engine(hard_rule_confirm_window=window)
        prev, out = None, None
        for i, (hr, o2, rr, t) in enumerate(rows):
            ctx = {"age_months": 24}
            if prev:
                ctx.update(previous_hr=prev[0], previous_o2=prev[1], previous_rr=prev[2], previous_temp=prev[3],
                           time_delta_seconds=3600)
            out = e.evaluate(VitalsSnapshot("P", T + timedelta(hours=i), hr, o2, rr, t, ctx))
            prev = (hr, o2, rr, t)
        return out.escalation_required
    assert third(3) and not third(1)
    assert "hard_rule_confirm_window" in ClinicalSettings.from_mapping({"hard_rule_confirm_window": 1}).weakenings()


@pytest.mark.parametrize("bad", [
    {"norms": {"generic": {"hr_high": 150, "hr_low": 90, "rr_high": 40, "o2_low": 91, "temp_high": 39.0}}},  # groups missing
    {"norms": None}, {"norms": []}, {"norms": {"child": 5}}, {"age_group_upper_months": None},
    {"age_group_upper_months": "abc"}, {"max_age_months": [1]},
])
def test_malformed_settings_are_a_settings_error_never_a_crash_later(bad):
    with pytest.raises(SettingsError):
        ClinicalSettings(**bad)


def test_from_mapping_rejects_malformed_norms_with_a_settings_error():
    for bad in ({"norms": {"child": 5}}, {"norms": [1]}, {"norms": None}):
        with pytest.raises(SettingsError):
            ClinicalSettings.from_mapping(bad)


def test_the_norms_table_cannot_be_changed_after_construction():
    s = ClinicalSettings()
    with pytest.raises(TypeError):
        s.norms["child"]["hr_high"] = 999
    with pytest.raises(TypeError):
        s.norms["child"] = {}
    c = PediatricCassette(settings=s)
    assert c.settings.norms["child"]["hr_high"] == 130          # what the record says is what the engine uses


def test_equal_values_are_equal_everywhere_whatever_their_spelling():
    a = ClinicalSettings.from_mapping({"age_group_upper_months": [3.0, 12.0, 36.0], "max_age_months": 216,
                                       "critical_o2_below": 88.0})
    assert a.digest() == ClinicalSettings().digest() and a.non_default() == {}
    assert PediatricCassette(settings=a).identity_extras == {}


def test_default_engine_text_is_unchanged_when_a_norm_is_tuned():
    out = engine(norms={"child": {"hr_high": 125.0}}).evaluate(snap(age=60, hr=130))
    assert any(r == "TACHYCARDIA: 130 > 125" for r in out.triggered_rules)     # 125, not 125.0


def test_every_local_module_the_package_imports_is_listed_in_pyproject():
    """An installed copy imports only what pyproject lists. Review found two omissions (cassette, pediatric_cassette)."""
    import ast
    import pathlib
    import re
    root = pathlib.Path(__file__).parent
    mods = {p.stem for p in root.glob("*.py") if not p.name.startswith("test_") and p.name not in ("conftest.py", "__init__.py")}
    text = (root / "pyproject.toml").read_text()
    listed = set(re.findall(r'"([a-z_0-9]+)"', text[text.index("py-modules"):]))
    frontier, seen, missing = listed & mods, set(), set()
    while frontier:
        m = frontier.pop()
        seen.add(m)
        for n in ast.walk(ast.parse((root / f"{m}.py").read_text())):
            names = [a.name.split(".")[0] for a in n.names] if isinstance(n, ast.Import) else \
                [n.module.split(".")[0]] if isinstance(n, ast.ImportFrom) and n.module and n.level == 0 else []
            for x in names:
                if x in mods and x not in seen:
                    if x not in listed:
                        missing.add(x)
                    frontier.add(x)
    assert not missing, f"imported by the package but not in pyproject py-modules: {sorted(missing)}"
