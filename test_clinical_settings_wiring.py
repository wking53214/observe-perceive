"""Stage 1A step 7: a clinician can tune the system, and every decision says what it was tuned to."""
import json
from datetime import datetime, timezone

import pytest

from clinical_governance_system import ClinicalGovernanceSystem, build_multi_hospital_system, build_single_hospital_system
from clinical_settings import ClinicalSettings, SettingsError
from observe_consolidated import VitalsSnapshot

T = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def reading(age=24, hr=110, o2=98.0, rr=25, temp=37.0, **ctx):
    return VitalsSnapshot("P1", T, hr, o2, rr, temp, {"age_months": age, **ctx})


def test_every_decision_names_the_settings_in_force():
    system = build_single_hospital_system(allow_no_alert_channel=True)
    d = system.process_vitals(reading())
    assert d.settings_digest == ClinicalSettings().digest()
    tuned = build_single_hospital_system(allow_no_alert_channel=True, settings={"critical_o2_below": 90.0})
    assert tuned.process_vitals(reading()).settings_digest == tuned.settings.digest() != d.settings_digest


def test_settings_can_be_a_mapping_an_object_or_a_json_file(tmp_path):
    f = tmp_path / "unit_settings.json"
    f.write_text(json.dumps({"max_age_months": 252, "norms": {"child": {"hr_high": 125}}}))
    for given in ({"max_age_months": 252, "norms": {"child": {"hr_high": 125}}},
                  ClinicalSettings.from_mapping({"max_age_months": 252, "norms": {"child": {"hr_high": 125}}}),
                  f, str(f)):
        s = ClinicalGovernanceSystem(allow_no_alert_channel=True, settings=given).settings
        assert s.max_age_months == 252 and s.norms["child"]["hr_high"] == 125


def test_bad_or_missing_settings_refuse_to_start_rather_than_run_on_defaults(tmp_path):
    with pytest.raises(SettingsError):
        build_single_hospital_system(allow_no_alert_channel=True, settings={"max_age_mnths": 200})
    with pytest.raises(SettingsError):
        build_single_hospital_system(allow_no_alert_channel=True, settings=tmp_path / "missing.json")
    with pytest.raises(SettingsError):
        build_single_hospital_system(allow_no_alert_channel=True, settings=42)
    (tmp_path / "bad.json").write_text("{oops")
    with pytest.raises(SettingsError):
        build_single_hospital_system(allow_no_alert_channel=True, settings=tmp_path / "bad.json")


def test_the_multi_hospital_builder_takes_settings_too():
    s = build_multi_hospital_system(["a", "b", "c"], allow_no_alert_channel=True, settings={"require_age": True})
    assert s.settings.require_age is True


def test_a_tuned_system_changes_what_is_escalated_and_the_ledger_names_the_change():
    base = build_single_hospital_system(allow_no_alert_channel=True)
    tuned = build_single_hospital_system(allow_no_alert_channel=True,
                                         settings={"norms": {"child": {"hr_high": 120}}})
    assert any("TACHYCARDIA" in r for r in tuned.observe.evaluate(VitalsSnapshot("P2", T, 128, 96.0, 30, 37.5,
                                                                                 {"age_months": 60})).triggered_rules)
    assert not any("TACHYCARDIA" in r for r in base.observe.evaluate(VitalsSnapshot("P2", T, 128, 96.0, 30, 37.5,
                                                                                    {"age_months": 60})).triggered_rules)
    data = tuned.observe.audit_ledger.entries[-1]["data"]
    assert data["cassette"]["setting.norms.child.hr_high"] == "120"
    assert "setting.norms.child.hr_high" not in base.observe.audit_ledger.entries[-1]["data"]["cassette"]


def test_an_adult_is_flagged_for_a_person_not_silently_scored_as_a_child():
    alerts = []
    system = build_single_hospital_system(alert_human=alerts.append, settings={})
    d = system.process_vitals(reading(age=480, hr=110, o2=98.0))
    assert d.escalation_required and "supported pediatric population" in " ".join(d.triggered_rules)
    # turning the finding off is a recorded, named choice
    off = build_single_hospital_system(allow_no_alert_channel=True, settings={"outside_population": "assess_anyway"})
    assert not off.process_vitals(reading(age=480)).escalation_required
    assert off.observe.audit_ledger.entries[-1]["data"]["cassette"]["settings_weaker"] == "outside_population"
