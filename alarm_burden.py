"""alarm_burden.py -- how many pages does this engine make on these readings?

The clinical owner's first question about any alarm system is "how many?", and until now nothing in this repository could answer it
(docs/race/CLINICAL_SAFETY_AND_VALIDATION.md section 4.2: "No alarm-burden measurement"). This module replays a recorded stream of
readings through a fresh OBSERVE engine under given clinical settings and COUNTS:

  * escalation events (OBSERVE's own `escalation_required`), and the subset that would reach a person under the notification
    settings (the default tells a person only at CRITICAL, on a severity increase, and for an unassessable reading, so many WARNING
    escalations are events that nobody is paged for);
  * per patient-day (a calendar UTC day on which a patient has at least one reading): the mean, median, 90th and 99th percentile and
    the maximum count, and the share of patient-days with any page;
  * pages by regime, kind, age group and triggered rule; pages on a patient's first reading; pages for a patient within an hour of
    their previous page (the repeat rate); and how concentrated the pages are in a few patients.

What it is NOT. It counts what the ENGINE does on the readings it is given. It does not say whether any alarm was right, wanted or
acted on: that needs outcomes and a clinician (CLINICAL_SAFETY_AND_VALIDATION.md section 2). A number from synthetic or unrepresentative
readings is not a measure of any unit. Every report says where its readings came from, because the source is part of the number.
It never prints a patient identifier.

Use:   python alarm_burden.py readings.csv --source "unit 4 East, 2026-10-01 to 10-07, de-identified" [--settings settings.json]
CSV columns: patient_id, timestamp (ISO 8601), heart_rate, oxygen_saturation, respiratory_rate, temperature, age_months (optional).
An empty cell is a missing channel and is replayed as NaN, which the engine treats as a data-integrity fault (a standing WARNING), so
a stream with gaps shows what those gaps cost.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence

from clinical_notification import MIN_REGIMES, ClinicalNotificationSettings, notification_reason
from clinical_settings import ClinicalSettings
from observe_consolidated import ENGINE_LOGIC_DIGEST, ObserveClinicalEngine, VitalsSnapshot, get_age_group

__all__ = ["BurdenReport", "read_csv", "replay", "render_text", "main"]

REPEAT_WINDOW = timedelta(hours=1)
_TOP_RULES = 12


def _percentile(sorted_values: Sequence[int], p: float) -> int:
    """Nearest-rank percentile of an already sorted, non-empty sequence."""
    rank = max(1, math.ceil(p / 100.0 * len(sorted_values)))
    return sorted_values[rank - 1]


@dataclass
class BurdenReport:
    source: str
    settings_digest: str
    logic_digest: str
    notification: Dict[str, Any]
    patients: int = 0
    readings: int = 0
    unassessable_readings: int = 0
    first_reading: Optional[str] = None
    last_reading: Optional[str] = None
    patient_days: int = 0
    escalation_events: int = 0
    notifications: int = 0
    notifications_by_reason: Dict[str, int] = field(default_factory=dict)
    events_per_100_patient_days: float = 0.0
    notifications_per_100_patient_days: float = 0.0
    patient_days_with_an_event_share: float = 0.0
    events_per_patient_day: Dict[str, float] = field(default_factory=dict)
    events_by_regime: Dict[str, int] = field(default_factory=dict)
    events_by_kind: Dict[str, int] = field(default_factory=dict)
    events_by_age_group: Dict[str, int] = field(default_factory=dict)
    events_by_rule: Dict[str, int] = field(default_factory=dict)
    events_on_first_reading: int = 0
    events_unassessable: int = 0
    repeat_events_within_1h: int = 0
    share_of_events_from_busiest_5pct_of_patients: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _float(cell: str) -> float:
    cell = (cell or "").strip()
    return float("nan") if cell == "" else float(cell)


def read_csv(path: Any) -> List[VitalsSnapshot]:
    """Readings from a CSV file (see the module doc for the columns)."""
    out: List[VitalsSnapshot] = []
    with open(path, newline="", encoding="utf-8") as fh:
        for n, row in enumerate(csv.DictReader(fh), start=2):
            try:
                when = datetime.fromisoformat(row["timestamp"].strip())
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
                age = (row.get("age_months") or "").strip()
                context = {"age_months": int(float(age))} if age else {}
                out.append(VitalsSnapshot(row["patient_id"].strip(), when, _float(row["heart_rate"]), _float(row["oxygen_saturation"]),
                                          _float(row["respiratory_rate"]), _float(row["temperature"]), context))
            except (KeyError, ValueError, AttributeError) as exc:
                raise ValueError(f"{path} line {n}: cannot read this row ({type(exc).__name__}: {exc})") from None
    return out


def replay(readings: Iterable[VitalsSnapshot], *, source: str, settings: Optional[ClinicalSettings] = None,
           notification: Optional[ClinicalNotificationSettings] = None) -> BurdenReport:
    """Replay `readings` (in time order; ties keep input order) through a fresh engine and count what it does."""
    from pediatric_cassette import PediatricCassette
    if not source or not str(source).strip():
        raise ValueError("say where the readings came from (source=...): the source is part of the number")
    settings = settings or ClinicalSettings()
    notification = notification or ClinicalNotificationSettings()
    problems = notification.problems()
    if problems:
        raise ValueError("notification settings refused: " + "; ".join(problems))
    ordered = sorted(readings, key=lambda r: r.timestamp)
    patients = {r.patient_id for r in ordered}
    engine = ObserveClinicalEngine(max_tracked_patients=max(10000, len(patients) + 1), cassette=PediatricCassette(settings=settings))
    report = BurdenReport(source=str(source).strip(), settings_digest=settings.digest(), logic_digest=ENGINE_LOGIC_DIGEST,
                          notification=notification.as_dict(), patients=len(patients), readings=len(ordered))
    if not ordered:
        return report
    report.first_reading, report.last_reading = ordered[0].timestamp.isoformat(), ordered[-1].timestamp.isoformat()
    days: Dict[Any, int] = defaultdict(int)                       # (patient, UTC day) -> events that day
    per_patient: Dict[str, int] = defaultdict(int)
    last_event: Dict[str, datetime] = {}
    seen: set = set()
    regimes, kinds, groups, rules, reasons = Counter(), Counter(), Counter(), Counter(), Counter()
    for reading in ordered:
        verdict = engine.evaluate(reading)
        key = (reading.patient_id, reading.timestamp.astimezone(timezone.utc).date())
        days[key] += 0                                            # the day exists once there is a reading
        first = reading.patient_id not in seen
        seen.add(reading.patient_id)
        if verdict.unassessable:
            report.unassessable_readings += 1
        if not verdict.escalation_required:
            continue
        report.escalation_events += 1
        days[key] += 1
        per_patient[reading.patient_id] += 1
        regime = verdict.regime.value
        regimes[regime] += 1
        kinds[verdict.escalation_kind or "none"] += 1
        groups[get_age_group((reading.context or {}).get("age_months"), settings.age_group_upper_months)] += 1
        for rule in verdict.triggered_rules:
            rules[str(rule).split(":", 1)[0].strip()] += 1                # the name of the rule, not its numbers
        report.events_on_first_reading += int(first)
        report.events_unassessable += int(bool(verdict.unassessable))
        previous = last_event.get(reading.patient_id)
        if previous is not None and reading.timestamp - previous <= REPEAT_WINDOW:
            report.repeat_events_within_1h += 1
        last_event[reading.patient_id] = reading.timestamp
        reason = notification_reason(notification, regime=regime, escalation_required=True,
                                     escalation_kind=verdict.escalation_kind, unassessable=bool(verdict.unassessable))
        if reason is not None:
            report.notifications += 1
            reasons[reason] += 1
    counts = sorted(days.values())
    report.patient_days = len(counts)
    total = report.escalation_events
    report.events_per_100_patient_days = round(100.0 * total / len(counts), 2)
    report.notifications_per_100_patient_days = round(100.0 * report.notifications / len(counts), 2)
    report.patient_days_with_an_event_share = round(sum(1 for c in counts if c) / len(counts), 4)
    report.events_per_patient_day = {"mean": round(total / len(counts), 3), "median": _percentile(counts, 50),
                                     "p90": _percentile(counts, 90), "p99": _percentile(counts, 99), "max": counts[-1]}
    report.notifications_by_reason = dict(sorted(reasons.items()))
    report.events_by_regime, report.events_by_kind = dict(sorted(regimes.items())), dict(sorted(kinds.items()))
    report.events_by_age_group = dict(sorted(groups.items()))
    report.events_by_rule = dict(rules.most_common(_TOP_RULES))
    if total:
        busiest = sorted(per_patient.values(), reverse=True)[:max(1, math.ceil(0.05 * len(patients)))]
        report.share_of_events_from_busiest_5pct_of_patients = round(sum(busiest) / total, 4)
    return report


def render_text(report: BurdenReport) -> str:
    r = report
    lines = [
        "ALARM BURDEN: a count of what the engine does on these readings, not a measure of whether any alarm was right.",
        f"source: {r.source}",
        f"engine logic {r.logic_digest[:12]}; clinical settings {r.settings_digest[:12]}; notification {json.dumps(r.notification, sort_keys=True)}",
        f"{r.patients} patients, {r.readings} readings ({r.first_reading} to {r.last_reading}), {r.patient_days} patient-days, "
        f"{r.unassessable_readings} unassessable readings",
        "",
        f"escalation events: {r.escalation_events} ({r.events_per_100_patient_days} per 100 patient-days)",
        f"  that would reach a person: {r.notifications} ({r.notifications_per_100_patient_days} per 100 patient-days) {r.notifications_by_reason}",
        f"  per patient-day: {r.events_per_patient_day}; patient-days with any event: {r.patient_days_with_an_event_share:.1%}",
        f"  by regime {r.events_by_regime}; by kind {r.events_by_kind}; by age group {r.events_by_age_group}",
        f"  on a patient's first reading: {r.events_on_first_reading}; on an unassessable reading: {r.events_unassessable}; "
        f"repeats within 1 hour of the same patient's previous event: {r.repeat_events_within_1h}",
        f"  share of events from the busiest 5% of patients: {r.share_of_events_from_busiest_5pct_of_patients:.1%}",
        f"  most frequent triggered rules in events: {r.events_by_rule}",
    ]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Count the pages the OBSERVE engine makes on a stream of readings.")
    parser.add_argument("readings", help="CSV file of readings")
    parser.add_argument("--source", required=True, help="where the readings came from (printed in the report)")
    parser.add_argument("--settings", help="ClinicalSettings JSON file (default: the shipped defaults)")
    parser.add_argument("--min-regime", choices=MIN_REGIMES, help="notification threshold (default: critical)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    settings = ClinicalSettings.from_json_file(args.settings) if args.settings else None
    notification = ClinicalNotificationSettings(min_regime=args.min_regime) if args.min_regime else None
    report = replay(read_csv(args.readings), source=args.source, settings=settings, notification=notification)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True) if args.json else render_text(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
