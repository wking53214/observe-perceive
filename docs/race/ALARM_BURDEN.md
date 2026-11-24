# Alarm burden: counting what the engine does

**Status: a measuring tool, with no measurement yet.** No real readings have been replayed. Every number below that is marked
SYNTHETIC is a demonstration of the format, not a finding about any unit.

## Why

The first question a clinical owner asks about an alarm system is "how many?", and `CLINICAL_SAFETY_AND_VALIDATION.md` section 4.2 recorded
that nothing here could answer it. `alarm_burden.py` replays a recorded stream of readings through a fresh OBSERVE engine under the
clinical settings you give it, and counts. It is the number the clinical owner reacts to, and the baseline any later change to a
threshold is compared against.

## Use

```
python alarm_burden.py readings.csv --source "unit 4 East, 2026-10-01 to 10-07, de-identified" \
    [--settings clinical_settings.json] [--min-regime warning] [--json]
```

CSV columns: `patient_id, timestamp (ISO 8601), heart_rate, oxygen_saturation, respiratory_rate, temperature, age_months` (age optional).
A timestamp with no offset is read as UTC. An empty cell is a missing channel and is replayed as NaN, which the engine treats as a
data-integrity fault (a standing WARNING that pages), so a stream with gaps shows what the gaps cost. `--source` is required, because
the source of the readings is part of the number. The report never prints a patient identifier.

## What it counts

| Measure | Meaning |
|---|---|
| Escalation events | OBSERVE's own `escalation_required`: a new move up into WARNING or CRITICAL, or a severity increase |
| Would reach a person | The subset the notification settings would tell someone about (`ClinicalNotificationSettings`). **The default tells a person only at CRITICAL, on a severity increase, and for an unassessable reading, so a WARNING escalation is an event that nobody is paged for.** `--min-regime warning` shows the cost of changing that |
| Per patient-day | A patient-day is a calendar UTC day on which that patient has at least one reading. Reported as events per 100 patient-days, the mean, median, 90th and 99th percentile (nearest rank) and maximum per patient-day, and the share of patient-days with any event |
| Breakdowns | By regime, by kind, by age group (using the settings' boundaries), and by the name of each triggered rule |
| First reading | Events on a patient's first reading in the stream (the population floor and a lone hard rule do this) |
| Repeats | An event within one hour of the same patient's previous event (alarm fatigue from re-paging) |
| Concentration | The share of all events that come from the busiest 5% of patients (rounded up to at least one patient) |
| Provenance | The source label, the engine logic digest, the clinical settings digest and the notification settings, so two reports can be compared only when they should be |

## What it does not do

- **It does not say whether an alarm was right.** That needs outcomes and a clinician (validation plan, section 2 of
  `CLINICAL_SAFETY_AND_VALIDATION.md`). A high count is not a defect and a low count is not safety; a lone abnormal channel that never
  pages (`CLINICIAN_REVIEW_SHEET.md`, S1 to S10) is a quiet system, not a good one.
- It counts OBSERVE escalation events and applies the notification policy. It does not model the ticket desk (acknowledgement,
  tiering, overdue, exhaustion) or governance blocking, so it is the burden on the escalation desk's input, not a measurement of the
  nurse's day.
- It is only as representative as the readings. Replay of a de-identified week from one unit says what the engine does to that unit's week.
- Patients are tracked from their first reading in the stream, with no history from before it. A stream that starts mid-stay starts every
  patient cold.

## SYNTHETIC demonstration (not a rate)

The twelve review-sheet cases, three identical readings each, one patient per case:

```
12 patients, 36 readings, 12 patient-days, 0 unassessable readings
escalation events: 2 (16.67 per 100 patient-days)
  that would reach a person: 0 (0.0 per 100 patient-days) {}
  by regime {'warning': 2}; by kind {'new_escalation': 2}; by age group {'preschool': 2}
  on a patient's first reading: 2; repeats within 1 hour: 0
  most frequent triggered rules in events: {'BASE_RISK': 2, 'OUTSIDE_SUPPORTED_POPULATION': 1, 'BRADYCARDIA': 1, 'CLINICAL_SAFETY_BYPASS': 1, 'CRITICAL_O2': 1}
```

The two events are the adult (S11) and the 5 year old at SpO2 86 (S12). **Neither would reach a person under the default notification
settings**, because both are WARNING. This was not visible in the review sheet's "Paged" column, which meant "an escalation event";
the sheet now says so.

## From the desk's own log: `ticket_burden.py`

`alarm_burden.py` says what the engine WOULD page on a recorded stream. `ticket_burden.py` says what the escalation desk DID, from its
hash-chained log (`human_escalation.EscalationLog`), with no new instrumentation:

```
python ticket_burden.py escalation_log.jsonl --source "ward 4 pilot, 2026-10-01 to 10-07" [--json]
```

| Measure | Meaning |
|---|---|
| Tickets | By kind, reason and regime; tickets per day with any; tickets per patient-day with a ticket |
| Time to first dispatch | Opening to the first dispatch that was not a failure (median, 90th percentile, maximum, in seconds) |
| Time to acknowledge | Opening to the signed acknowledgement, **for acknowledged tickets only**: read it beside the unacknowledged share, or it flatters |
| Outcome | Acknowledged, exhausted (nobody could be reached) and still open at the end of the log, with the age of the oldest open ticket |
| Escalation up the tiers | Tickets that needed more than one tier, overdue events, failed dispatches |
| Re-paging and concentration | Tickets for one patient within an hour of their last; the share from the busiest 5% of patients |

Handling rules it follows: it **reads the file read-only** (no writer lock, nothing repaired, unlike opening an `EscalationDesk`); it checks the
hash chain from the first event and, on the first break or a half-written last line, reports the figures for the verified part only with a loud
warning and exit status 2; it reports whether the log is signed but **does not verify signatures** (that needs the responders' keys); and it
never prints a patient identifier, although the log it reads holds them (identifier data, `CLINICAL_ESCALATION_NOTIFICATION.md`), so keep the
log and any copy of it in a controlled environment. A signed acknowledgement shows a responder took responsibility, not that they acted or that
the page was needed. Tested against a real desk on a controlled clock (`test_ticket_burden.py`, 18 tests, hand-derived numbers).

SYNTHETIC demonstration on a hand-built six-ticket ward (not a rate):

```
6 tickets; acknowledged 2 (33.3%); exhausted (nobody reached) 1; open at the end 3 (oldest 100.0 s)
seconds to first successful dispatch: median 0, p90 0, max 0; to acknowledgement (acknowledged only): median 30, p90 80, max 80
needed more than one tier: 1; overdue events: 1; failed dispatches: 3; repeat tickets within 1 hour for one patient: 1
```

## For the clinical owner

Two decisions this makes concrete:

1. **Who should be told at WARNING?** Today a WARNING escalation is recorded and nobody is told unless the deployment sets
   `min_regime` to `warning`. Run the same stream both ways and compare the two counts before choosing.
2. **What count is acceptable?** Replay a real, representative week and name a ceiling (events or notifications per patient-day, per
   unit). Until someone does, every threshold in the system is unjudged against its cost.
