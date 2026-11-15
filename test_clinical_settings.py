"""test_clinical_settings.py -- the values a clinician may tune: engines, settings object, cassette, population floor.

Built up in steps; each group says which finding or promise it holds.
"""
import copy
from datetime import datetime, timezone

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
