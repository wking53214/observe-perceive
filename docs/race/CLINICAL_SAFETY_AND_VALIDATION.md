# Clinical safety review, validation plan, rule register and alarm architecture

Evidence classes: **EXECUTED** (I ran it at default settings and quote the output), **INSPECTED** (read in source, line named),
**PROPOSED** (a plan; nothing enforces it). **This document is an engineering review, not a clinical one.** It reports what the software does.
It does not say what is clinically right: that is for the clinical owner (a pediatrician or pediatric intensivist the hospital
names), and no such sign-off exists in this repository. No clinical value below was invented.

## 1. Pediatric assessment safety review

### 1.1 What the engine sees and how a reading becomes a regime (INSPECTED)

Inputs per reading: heart rate, SpO2, respiratory rate, temperature, plus an optional context (`age_months`, previous values and
short histories, an alert status, optional physiology telemetry). **There is no blood pressure, capillary refill, perfusion, mental
status, urine output, lactate or oxygen-delivery input**, so a pediatric sepsis pattern that is recognised mainly by perfusion cannot be
seen here, whatever the thresholds are.

Path: the reading is validated (impossible values are a data fault, never a clinical state) then up to seven engines score it
(`heuristic` always; `bayesian`, `trajectory`, `drift`, `behavioral`, `adversarial`, `physiological_reserve` under selection rules) and a
confidence-weighted fusion gives a risk and a regime. Two bypasses skip the dwell: a **hard rule** (heuristic risk at or above 0.5; SpO2 below 88 alone is exactly 0.5, and
other combinations such as 0.2 + 0.2 + 0.15 reach it) which holds WARNING once it is *confirmed* (it fired again within the window, another
channel is abnormal beside it, or the window is 1), and a **named syndrome** (septic shock, respiratory distress, hypovolemic shock).
Otherwise a first escalation needs two consecutive confirming readings (`ESCALATION_DWELL_THRESHOLD = 2`) and then locks for 300 s.

### 1.2 Safety properties that hold (INSPECTED, each pinned by tests)

- A faulted reading is never reported as stable (it is at least WARNING), and the channels that still work are still assessed.
- A bypass raises the regime and never lowers a tracked one; a CRITICAL is held.
- An OBSERVE crash returns a decision that says nobody was assessed, and a person is told; a crash leaves the patient's tracked state as it was.
- A patient above the supported age is reported at WARNING at least with a named finding (the engine does not pretend to norms it lacks).
- Every escalation event the clinician's settings name opens a ticket with a lifecycle and an acknowledgement.
- Every decision records the settings and parameters it was made under.

### 1.3 What the engine does on common single-channel abnormalities (EXECUTED)

Default settings, no history, one reading repeated at one-minute intervals, `ClinicalGovernanceSystem` thresholds untouched. "First
page" is the first reading with `escalation_required` true.

| Case | Reading 1 | 2 | 3 | First page |
|---|---|---|---|---|
| infant 6 months, HR 190, RR 55, T 39.8, SpO2 97 | stable | caution | caution | never |
| toddler 24 months, HR 160, RR 42, T 38.9, SpO2 95 | stable | stable | stable | never |
| child 5 years, HR 140, RR 30, T 38.5, SpO2 91 | stable | stable | stable | never |
| child 5 years, HR 200 alone | stable | stable | stable | never |
| child 5 years, HR 45 alone | stable | caution | caution | never |
| infant 6 months, SpO2 89 alone | stable | stable | stable | never |
| neonate 1 month, T 34.5 alone | stable | stable | stable | never |
| neonate 1 month, T 38.8 alone | stable | stable | stable | never |
| toddler, SpO2 96, RR 70 alone | stable | stable | stable | never |

I am **not** claiming each of these is a clinical miss: some are single readings in isolation, history changes the trajectory and drift
engines, and what a given patient needs is a clinical judgement. What the table shows is the **design**: findings add small amounts to a
heuristic score (0.1 to 0.5), the fusion averages engines by confidence, and so **one abnormal channel does not by itself reach WARNING;
the exceptions are SpO2 below 88 (a hard rule, held at WARNING once repeated or corroborated, as the register's D1 discusses) and a named
syndrome, which is a conjunction of vitals**. A clinical owner should read this table first and decide which rows are acceptable.
The repository's own detection record says the same in another way: it pins three deterioration scenarios that get no escalation at all
(`gradual_hypoxia_child`, `reactive_airway_toddler`, `toddler_viral_fever`) and four sensor-fault cases that fail to escalate
(`coordinated_lie_sepsis_masking`, `slow_o2_drift_downward`, `stuck_hr_baseline`, `stuck_o2_sensor`).

A documentation inconsistency the probe exposed: `observe_consolidated.py:1637` says a hard rule is "heuristic risk >= 0.5: CRITICAL_O2,
HYPOTHERMIA, etc.", but hypothermia alone scores 0.4, so it is **not** a hard rule (neonate at 34.5 C reads stable above).

### 1.4 Other limits of the current design

- One "child" norms row covers 3 to 18 years (healthy 12 year old at HR 70 is flagged bradycardia).
- Pulse oximetry is the key input and is known to read differently across patient groups (skin pigmentation, perfusion, motion). The
  engine has no input for any of that and no per-group evaluation; validation (section 2) must stratify by whatever the hospital records.
- Heart rate alone can corroborate a hard rule, but it usually shares the probe with SpO2 (open question, unanswered).
- Sensor-fault handling replaces a faulted channel with a neutral placeholder (HR 110, SpO2 98, RR 25, T 37); the other channels are still
  assessed, but a sustained fault is a standing WARNING that pages once per window, which is its own alarm burden.

## 2. Sepsis and deterioration validation plan (PROPOSED; none of it has been run)

Goal: find out whether the thresholds and rules, as the clinical owner will set them, detect deterioration early enough with a burden
the unit can carry. **The targets are the clinical owner's; I propose the method, not the numbers.**

1. **Define the events** with the clinical owner: for example a documented sepsis alert, unplanned transfer to higher care, rapid response
   call, intubation, or death, each with a clock time from the chart; and a lookback window before each.
2. **Cohort and data:** de-identified, time-stamped vitals for pediatric inpatients across all age bands and units, with the events above
   as labels, extracted through the pipeline in `DATA_PRIVACY_AND_RESEARCH.md` (a research request, privacy and IRB approval first).
3. **Replay:** run the engine over each patient's readings in order, exactly as in production (history in `context`, one engine per
   patient), at the clinical owner's candidate settings. Record regime and page per reading.
4. **Measures:** per age band and unit: lead time from first page to event; share of events with any page in the lookback; alarms per
   patient-day and per bed-day; share of pages with no documented clinical action; time to acknowledge. Report with intervals.
5. **Stratify** by every attribute the hospital records that could change a result (age band, unit, sex, race and ethnicity where
   lawfully recorded, oximeter model, time of day) and report differences, not only averages.
6. **Stress:** the existing adversarial sensor-fault suite and the deterioration simulator, extended with the nine rows of section 1.3 as
   fixtures; a changed result is a change request (`CHANGE_CONTROL_AND_VALIDATION.md`).
7. **Silent mode:** run live beside current practice for a period the clinical owner sets, paging nobody from the new rules; review every
   disagreement with a clinician.
8. **Decide and record:** the clinical owner signs the rule register (section 3) with the evidence attached.

Not available: outcome-labelled data, a pediatrician sign-off, a defined event set. Until they exist the system has engineering evidence only.

## 3. Clinical rule approval register

Every row is **NOT APPROVED: no clinical sign-off is recorded in this repository.** "Provenance" is exactly what the nearby comment
says. "Adjustable" means through `ClinicalSettings` (the notification settings are separate). Approver is the clinical owner
(pediatrics) unless stated. Lines are in `observe_consolidated.py` unless a file is named. (Source: a one-pass inventory I spot-checked
against the code, then a second claims check that corrected the provenance of R14 and R15.)

| ID | Rule | Value | Provenance stated | Adjustable |
|---|---|---|---|---|
| R01 | Norms, neonatal (< 3 mo) | HR 80 to 160, RR < 50, SpO2 >= 90, T <= 38.5 | "CLINICAL POLICY: AGE-ADJUSTED PEDIATRIC NORMS"; no source | yes |
| R02 | Norms, infant (3 to < 12 mo) | HR 90 to 150, RR < 45, SpO2 >= 90, T <= 39.0 | same | yes |
| R03 | Norms, toddler (12 to < 36 mo) | HR 95 to 140, RR < 40, SpO2 >= 91, T <= 39.0 | same | yes |
| R04 | Norms, child (>= 36 mo, to the population bound) | HR 100 to 130, RR < 35, SpO2 >= 92, T <= 39.5 | same | yes |
| R05 | Norms, generic (age unknown) | HR 95 to 140, RR < 40, SpO2 >= 91, T <= 39.0 | same | yes |
| R06 | Age-group boundaries | 3, 12, 36 months | none | yes |
| R07 | Supported population bound | 216 months (18 y), floor WARNING | "18 years" (a default I chose in #84, not a finding) | yes |
| R08 | SpO2 hard rule (CRITICAL_O2) | SpO2 < 88, +0.5 | none | yes |
| R09 | Hypothermia | T < 35.0, +0.4 | none (and see 1.3: not a hard rule alone) | yes |
| R10 | Heuristic findings | low SpO2 +0.2, tachycardia +0.2, bradycardia +0.3, tachypnea +0.15, fever +0.1 | none | thresholds yes, point values no |
| R11 | Hard-rule risk | 0.5 | "lifted verbatim from the escalation path" (`pediatric_cassette.py`) | no |
| R12 | Hard-rule confirmation window | 3 readings (1 = at once) | "a starting point, not a clinical finding" | yes |
| R13 | Hard-rule corroboration, margin | on, +0.05 | same | yes |
| R14 | Septic shock | SpO2 < 92 and HR > 140 and RR > 35 and T > 38.5; +0.40; bypass | none at the rule (`observe_consolidated.py:679-683`); the engine-gate comment (`pediatric_cassette.py:298-303`) says the gate cutoffs, which "match the lowest syndrome cutoffs", are "PROVISIONAL -- pending pediatrician sign-off" | no |
| R15 | Respiratory distress | SpO2 < 90 and RR > 45; +0.35; bypass | none at the rule (`:685-688`); same gate comment | no |
| R16 | Hypovolemic shock | HR > 150 and SpO2 < 88; +0.35; bypass | none | no |
| R17 | Benign-pattern reductions | fever response (T > 38.5, HR > 130, RR > 28) -0.15; crying (alert "crying", HR > 140) -0.10 | none | no |
| R18 | Behavioral base risk | SpO2 < 90 +0.4, < 92 +0.2, HR > 150 or < 80 +0.3 | none | no |
| R19 | Engine-selection gate (default `widened_provisional`) | behavioral engine runs for SpO2 < 92, HR > 140 or < 90, RR > 35, T > 38.5 | "PROVISIONAL -- pending pediatrician sign-off" | choice of two gates only |
| R20 | Trajectory | SpO2 -3.0 %/min +0.4; HR +20 bpm/min +0.3, -30 +0.4; RR +5/min +0.25; T -1.0 C/min +0.3; multi-trend +0.2 | comments say the old values were "unrealistic"; no source | no |
| R21 | Drift | z > 2.0 over >= 5 readings, +0.3 (SpO2), +0.25 (HR) | "DriftThresholds.DRIFT_SIGMA in the original spec" | no |
| R22 | Bayesian | centres from norms, SD 3.0 (SpO2); z beyond 2.0; weights 0.4, 0.3 | none | indirectly, via norms |
| R23 | Adversarial (stuck or implausible sensor) | 5 identical SpO2 +0.3; variance < 0.01 +0.15; SpO2 change > 15 %/min +0.25 | "exceeds physiological plausibility"; no source | no |
| R24 | Physiological-reserve axes | weights 0.6/0.4, 0.5/0.5, phase table 0.1 to 0.9, instability HR change / 30 | "Adapted from a six-axis systems-physiology decomposition"; no source for weights | no |
| R25 | Regime from risk | bands at 0.25, 0.50, 0.75; regime floors 0, 0.25, 0.50, 0.75; critical never below 0.01 (drift 0.02) | none | no |
| R26 | Fusion | weight = engine confidence; abstainers excluded; a named syndrome is a floor | "CLINICAL SYNDROME FLOOR" | no |
| R27 | Confidence per engine | heuristic 0.95/0.85/0.70, bayesian 0.85, behavioral 0.90/0.75, adversarial 0.70, drift 0.50 to 0.95, trajectory 0.6 + 0.3 q | none | no |
| R28 | Dwell, lock | 2 confirming readings; lock 300 s; a worsening already-escalated patient skips dwell | "owner decision, PR-3" for the last | no |
| R29 | Faulted-channel placeholder | HR 110, SpO2 98, RR 25, T 37 (`pediatric_cassette.py`) | "A placeholder, never an estimate of the child." | no (norms are validated against it) |
| R30 | Physical-plausibility bounds | HR 0 to 350, SpO2 0 to 100, RR 0 to 150, T 20 to 45 | "ENGINEERING guards, not clinical norms ... open to clinician review" | no |
| R31 | Kalman velocity rules (off by default) | SpO2 -1.0/h 0.20, HR +10/h 0.15, RR +5/h 0.15, T +0.5/h 0.10 | "PROVISIONAL ... pending pediatrician sign-off" | no |
| R32 | Notification settings | page at CRITICAL, on severity increase, on blocked and on unassessable; always on governance or assessment unavailable | "chosen to err toward telling someone about the worst events" | yes (`ClinicalNotificationSettings`) |
| R33 | Escalation policy defaults | PERCEIVE `max_daily` 10 per subject; micropatch gate needs physician approval | none | not a clinical setting |

A rule becomes approved when the clinical owner signs its row with the evidence from section 2 attached, and the release names the
`settings_digest` and `parameter_set_version` in force (`CHANGE_CONTROL_AND_VALIDATION.md`). Until then the honest label for every
row is "engineering default".

## 4. Alarm intelligence architecture

"Alarm intelligence" here means: tell the right person about the right events and no more, never suppress a danger, and know whether
that is working.

### 4.1 What exists (INSPECTED, tested)

| Layer | What it does |
|---|---|
| Event, not state | OBSERVE sets `escalation_required` once per new escalation or severity increase; a patient who stays critical does not page every reading |
| Dwell and lock | two confirming readings for a first escalation; 300 s lock against same-or-lower repeats; a higher severity passes the lock |
| Floors that page once | the population floor pages once until the patient is back in scope; a sensor fault pages once per window per kind |
| Routing | `ClinicalNotificationSettings` (what must reach a person) then `EscalationDesk` tiers (who, how long to wait for an acknowledgement) |
| Lifecycle | raised, dispatched, delivered, acknowledged, overdue, exhausted; signed acknowledgements; a ticket nobody could be reached for is alarmed |
| Liveness | the sweep runner moves unanswered pages to the next tier; the watchdog alarms when it stops |
| Always told | governance unavailable, assessment unavailable, an identity mismatch, an OBSERVE crash |

### 4.2 What is missing (INSPECTED: the desk exposes `open_tickets()`; there is no metrics function)

- **No alarm-burden measurement:** pages per patient-day, per unit, per hour; share acknowledged; time to acknowledge; share with a clinical action. The ticket log records the raw events, so these are computable, but nothing computes them.
- **No grouping:** two pages for one patient from different causes (a population finding and a CRITICAL) are two tickets.
- **No unit or time-of-day tuning:** settings are per system, not per unit.
- **No feedback loop:** a "page was not useful" signal does not reach the settings.

### 4.3 Principles (PROPOSED)

1. **Never suppress by default a CRITICAL, a blocked escalation, or an unassessed patient.** Any suppression is a setting the clinical owner sets, recorded as a weakening.
2. **Measure before tuning:** compute the burden metrics from the ticket log first (a read-only report, no behaviour change).
3. **Group, do not hide:** one ticket can carry several reasons; the person sees all of them.
4. **Escalate on silence, not on repetition:** a missed acknowledgement moves to the next tier (exists); repeating the same page does not.
5. **Every suppression and every grouping is itself recorded** so it can be audited.

## 5. What I could not determine

- Who the clinical owner is, and whether any rule above was ever reviewed outside this repository.
- Whether a deployment turns `kalman` on, picks the conservative gate, or changes any setting.
- Real alarm burden: no data.
