"""test_alarm_burden.py -- the alarm-burden replay counts what the engine does, exactly, and never hides where its readings came from.

Every expected number below was worked out by hand from a few readings whose behaviour is pinned elsewhere
(test_single_channel_behaviour.py): the case is chosen, the counts are derived, and the report must agree.
"""
import json
import math
from datetime import datetime, timedelta, timezone

import pytest

import alarm_burden as ab
from clinical_notification import ClinicalNotificationSettings
from clinical_settings import ClinicalSettings
from observe_consolidated import VitalsSnapshot

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def V(patient, minute, hr, o2, rr, temp, age=60):
    return VitalsSnapshot(patient, T0 + timedelta(minutes=minute), hr, o2, rr, temp, {} if age is None else {"age_months": age})


ADULT = (70, 98, 14, 37.0, 480)             # out of scope: WARNING on its first reading (population floor)
HYPOXIC_CHILD = (100, 86, 24, 37.0, 60)     # WARNING on its first reading (hard rule, window 1)
CRITICAL_TODDLER = (168, 83, 46, 39.5, 24)  # CRITICAL

CSV_WITH_A_GAP = ("patient_id,timestamp,heart_rate,oxygen_saturation,respiratory_rate,temperature,age_months\n"
                  "A,2026-11-04T08:00:00,100,,24,37.0,60\n"
                  "B,2026-11-04T08:00:00+00:00,100,98,24,37.0,60\n")


def replay(readings, **kw):
    return ab.replay(readings, source="unit test", **kw)


def test_a_source_is_required_because_it_is_part_of_the_number():
    with pytest.raises(ValueError, match="where the readings came from"):
        ab.replay([V("A", 0, *ADULT)], source="  ")


def test_an_empty_stream_is_a_zero_report_not_an_error():
    report = replay([])
    assert (report.patients, report.readings, report.escalation_events, report.patient_days) == (0, 0, 0, 0)
    assert "0 readings" in ab.render_text(report)


def test_an_event_and_a_notification_are_different_counts():
    """The default tells a person only at CRITICAL: a WARNING escalation is an event nobody is paged for."""
    adult = replay([V("A", 0, *ADULT)])
    assert (adult.escalation_events, adult.notifications, adult.events_by_regime) == (1, 0, {"warning": 1})
    hypoxic = replay([V("A", 0, *HYPOXIC_CHILD)])
    assert (hypoxic.escalation_events, hypoxic.notifications) == (1, 0)
    critical = replay([V("A", 0, *CRITICAL_TODDLER)])
    assert (critical.escalation_events, critical.notifications) == (1, 1)
    assert critical.notifications_by_reason == {"regime_at_or_above_threshold": 1}


def test_the_notification_threshold_changes_who_is_told_and_is_recorded():
    warn = ClinicalNotificationSettings(min_regime="warning")
    report = replay([V("A", 0, *ADULT)], notification=warn)
    assert report.notifications == 1 and report.notification["min_regime"] == "warning"
    silent = replay([V("A", 0, *CRITICAL_TODDLER)],
                    notification=ClinicalNotificationSettings(min_regime="none", notify_on_severity_increase=False,
                                                              notify_on_blocked_escalation=False, notify_on_unassessable=False))
    assert silent.escalation_events == 1 and silent.notifications == 0


def test_a_bad_notification_setting_is_refused():
    with pytest.raises(ValueError, match="notification settings refused"):
        replay([V("A", 0, *ADULT)], notification=ClinicalNotificationSettings(min_regime="loud"))


def test_clinical_settings_change_the_count_and_the_report_names_them():
    base = replay([V("A", 0, *HYPOXIC_CHILD)])
    tuned = ClinicalSettings(critical_o2_below=80.0)                  # SpO2 86 is no longer a hard rule
    changed = replay([V("A", 0, *HYPOXIC_CHILD)], settings=tuned)
    assert (base.escalation_events, changed.escalation_events) == (1, 0)
    assert base.settings_digest == ClinicalSettings().digest() and changed.settings_digest == tuned.digest() != base.settings_digest


def test_patient_days_are_calendar_utc_days_with_a_reading():
    readings = [V("A", 0, *HYPOXIC_CHILD),                                  # A, day 1: one event
                V("A", 24 * 60 + 5, 100, 98, 24, 37.0),                     # A, day 2: a reading, no event
                V("B", 0, 100, 98, 24, 37.0)]                               # B, day 1: no event
    report = replay(readings)
    assert report.patient_days == 3 and report.escalation_events == 1
    assert report.events_per_100_patient_days == 33.33 and report.patient_days_with_an_event_share == round(1 / 3, 4)


def test_the_distribution_per_patient_day_uses_nearest_rank():
    readings = [V("A", 0, *HYPOXIC_CHILD[:4], age=60), V("A", 10, *CRITICAL_TODDLER[:4], age=60), V("B", 0, *HYPOXIC_CHILD[:4], age=60)]
    readings += [V(f"Q{i}", 0, 100, 98, 24, 37.0) for i in range(8)]
    report = replay(readings)                                               # 10 patient-days: counts [0]*8 + [1, 2]
    assert report.patient_days == 10 and report.escalation_events == 3
    assert report.events_per_patient_day == {"mean": 0.3, "median": 0, "p90": 1, "p99": 2, "max": 2}
    assert report.patient_days_with_an_event_share == 0.2


def test_a_repeat_is_an_event_within_an_hour_of_the_same_patients_last():
    soon = replay([V("A", 0, *HYPOXIC_CHILD), V("A", 10, *CRITICAL_TODDLER[:4], age=60)])
    later = replay([V("A", 0, *HYPOXIC_CHILD), V("A", 90, *CRITICAL_TODDLER[:4], age=60)])
    assert (soon.escalation_events, soon.repeat_events_within_1h, soon.events_by_kind) == (2, 1, {"new_escalation": 1, "severity_increase": 1})
    assert (later.escalation_events, later.repeat_events_within_1h) == (2, 0)
    assert soon.notifications_by_reason == {"severity_increase": 1}


def test_age_groups_follow_the_settings_boundaries_not_the_defaults():
    wider = ClinicalSettings(age_group_upper_months=(3, 12, 120))             # a 5 year old is a toddler under these bounds
    base, moved = replay([V("A", 0, *HYPOXIC_CHILD)]), replay([V("A", 0, *HYPOXIC_CHILD)], settings=wider)
    assert base.events_by_age_group == {"child": 1} and moved.events_by_age_group == {"toddler": 1}


def test_the_repeat_window_includes_exactly_one_hour_and_no_more():
    exactly = replay([V("A", 0, *HYPOXIC_CHILD), V("A", 60, *CRITICAL_TODDLER[:4], age=60)])
    just_over = replay([V("A", 0, *HYPOXIC_CHILD), V("A", 61, *CRITICAL_TODDLER[:4], age=60)])
    assert (exactly.repeat_events_within_1h, just_over.repeat_events_within_1h) == (1, 0)


def test_a_patient_day_is_a_utc_day_whatever_offset_the_reading_carries():
    five_west = timezone(timedelta(hours=-5))
    one = VitalsSnapshot("A", datetime(2026, 11, 4, 23, 0, tzinfo=five_west), 100, 98, 24, 37.0, {"age_months": 60})   # 04:00Z on the 5th
    two = VitalsSnapshot("A", datetime(2026, 11, 5, 1, 0, tzinfo=timezone.utc), 100, 98, 24, 37.0, {"age_months": 60})  # 01:00Z on the 5th
    assert replay([one, two]).patient_days == 1


def test_first_reading_events_and_age_groups_are_counted():
    readings = [V("A", 0, *ADULT), V("B", 0, *CRITICAL_TODDLER), V("C", 0, *CRITICAL_TODDLER[:4], age=None)]
    report = replay(readings)
    assert report.escalation_events == 3 and report.events_on_first_reading == 3
    assert report.events_by_age_group == {"child": 1, "generic": 1, "toddler": 1}
    assert report.events_by_regime["warning"] == 1
    assert "OUTSIDE_SUPPORTED_POPULATION" in report.events_by_rule and not any(":" in name for name in report.events_by_rule)   # names, not numbers


def test_concentration_is_the_share_from_the_busiest_five_percent_of_patients():
    readings = [V("P0", 0, *HYPOXIC_CHILD), V("P0", 10, *CRITICAL_TODDLER[:4], age=60)]
    readings += [V(f"P{i}", 0, *HYPOXIC_CHILD) for i in range(1, 5)]
    readings += [V(f"P{i}", 0, 100, 98, 24, 37.0) for i in range(5, 30)]       # 30 patients; busiest 5% rounds UP to 2 patients
    report = replay(readings)
    assert report.escalation_events == 6 and report.share_of_events_from_busiest_5pct_of_patients == 0.5     # (2 + 1) / 6


def test_the_result_does_not_depend_on_input_order_and_repeats_exactly():
    readings = [V("A", 0, *HYPOXIC_CHILD), V("A", 10, *CRITICAL_TODDLER[:4], age=60), V("B", 5, *ADULT)]
    forward, backward = replay(readings), replay(list(reversed(readings)))
    assert forward.as_dict() == backward.as_dict() == replay(readings).as_dict()


def test_csv_round_trip_naive_times_are_utc_and_gaps_are_faults(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text(CSV_WITH_A_GAP)
    readings = ab.read_csv(path)
    assert [r.patient_id for r in readings] == ["A", "B"] and readings[0].timestamp.tzinfo is not None
    assert math.isnan(readings[0].oxygen_saturation) and readings[0].context == {"age_months": 60}
    report = replay(readings)
    assert report.unassessable_readings == 1 and report.escalation_events == 1 and report.events_unassessable == 1


def test_a_bad_csv_row_names_its_line(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("patient_id,timestamp,heart_rate,oxygen_saturation,respiratory_rate,temperature\nA,not-a-time,1,2,3,4\n")
    with pytest.raises(ValueError, match="line 2"):
        ab.read_csv(path)


def test_the_command_line_prints_the_source_and_never_a_patient_id(tmp_path, capsys):
    path = tmp_path / "r.csv"
    path.write_text("patient_id,timestamp,heart_rate,oxygen_saturation,respiratory_rate,temperature,age_months\n"
                    "SECRET-MRN-0042,2026-11-04T08:00:00+00:00,100,86,24,37.0,60\n")
    assert ab.main([str(path), "--source", "a labelled test source"]) == 0
    text = capsys.readouterr().out
    assert "a labelled test source" in text and "SECRET-MRN-0042" not in text and "not a measure of whether any alarm was right" in text
    assert ab.main([str(path), "--source", "s", "--json", "--min-regime", "warning"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["notifications"] == 1 and "SECRET-MRN-0042" not in json.dumps(data)


def test_the_command_line_requires_a_source(tmp_path):
    path = tmp_path / "r.csv"
    path.write_text("patient_id,timestamp,heart_rate,oxygen_saturation,respiratory_rate,temperature\n")
    with pytest.raises(SystemExit):
        ab.main([str(path)])
