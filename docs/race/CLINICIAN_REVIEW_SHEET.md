# Clinician review sheet

**For the pediatric clinical owner. Read this page, mark it, and send it back. It is the only thing the software needs from you
before the rules can be validated.** It asks what the system does today on a short list of illustrative readings, and which of its
current numbers you accept. Nothing on this page is a clinical standard: the readings were chosen to show how the engine behaves,
not because they are the right test cases, and the numbers in the right-hand column of section C are the engine's current defaults,
not recommendations. **Nobody has approved any of them** (`CLINICAL_SAFETY_AND_VALIDATION.md` section 3 lists 33 rules, none
signed). Please add cases of your own at the end of section A.

## How to read the table in section A

Each row is one reading, repeated three times one minute apart, with no earlier history, on default settings, at the age shown.
"Regime" is what the engine reports (stable, caution, warning, critical). "Paged" means OBSERVE raised an escalation event. **Whether a person is then told depends on the notification setting, and the default tells a person only at CRITICAL, on a severity increase, and for an unassessable reading, so a WARNING event (S11, S12) reaches nobody unless the deployment sets `min_regime` to `warning`** (`ALARM_BURDEN.md` counts both).
Where the engine reports **stable and never pages**, that is the question: is that acceptable for this patient?

## A. What the engine does today, and your call

> **Changed 2026-11-23 (heart rate, respiratory rate and newborn fever limits re-derived from published references; see the comment above `PEDIATRIC_NORMS`).** Three rows moved, all from caution to stable. **S10** is the fix: a healthy 12-year-old at HR 70 was being flagged because the low heart-rate limit was set to 100 for every child. **S1 and S5 are a loss of sensitivity to look at**: both were caught before only because the old, very narrow heart-rate window made the Bayesian engine swing hard. S5 (a 5-year-old at HR 45) is still flagged as bradycardic by the heuristic; it simply no longer lifts the regime. The "child" group spans 3 to 18 years, which is too wide for one pair of limits; splitting it is the proper fix.

Mark one: **F** fine as it is, **W** should page at WARNING, **C** should page at CRITICAL, **X** something else (say what).
If you mark W, C or X, give the number or rule you want in the last column; the settings that exist today are listed in section B.

| # | Patient and reading (age; HR, RR, T, SpO2) | Regime on readings 1, 2, 3 | Paged | Your call (F / W / C / X) | Threshold or rule you want |
|---|---|---|---|---|---|
| S1 | infant 6 months; HR 190, RR 55, T 39.8, SpO2 97 | stable, stable, stable | never | | |
| S2 | toddler 24 months; HR 160, RR 42, T 38.9, SpO2 95 | stable, stable, stable | never | | |
| S3 | child 5 years; HR 140, RR 30, T 38.5, SpO2 91 | stable, stable, stable | never | | |
| S4 | child 5 years; HR 200, RR 22, T 37.0, SpO2 98 | stable, stable, stable | never | | |
| S5 | child 5 years; HR 45, RR 18, T 37.0, SpO2 98 | stable, stable, stable | never | | |
| S6 | infant 6 months; HR 130, RR 35, T 37.0, SpO2 89 | stable, stable, stable | never | | |
| S7 | neonate 1 month; HR 140, RR 40, T 34.5, SpO2 97 | stable, stable, stable | never | | |
| S8 | neonate 1 month; HR 140, RR 40, T 38.8, SpO2 97 | stable, stable, stable | never | | |
| S9 | toddler 24 months; HR 120, RR 70, T 37.0, SpO2 96 | stable, stable, stable | never | | |
| S10 | child 12 years; HR 70, RR 18, T 37.0, SpO2 98 (a healthy adolescent) | stable, stable, stable | never | | |
| S11 | adult 40 years; HR 70, RR 14, T 37.0, SpO2 98 (out of scope) | warning, warning, warning | reading 1 | | |
| S12 | child 5 years; HR 100, RR 24, T 37.0, SpO2 86 | warning, warning, warning | reading 1 | | |
| | *your own cases* | | | | |

What these rows show about the design, in one paragraph: findings add small amounts to a score, the engines are averaged by
confidence, and so a single abnormal channel (S4 to S9) does not reach WARNING unless it is SpO2 below 88 or a named syndrome. S10
is the opposite problem: one "child" row of norms covers ages 3 to 18, so a healthy adolescent at HR 70 is reported at CAUTION. S11 is
by design (the system is pediatric; adults are reported at WARNING with a finding, which is an event on the record; a person looks only if the notification setting reaches WARNING). S12 pages at once because
the default confirmation window is 1.

## B. What you can already change without a code change

All of these are `ClinicalSettings` (a JSON file or mapping; see `CLINICIAN_SETTINGS_AND_POPULATION.md`). A value that makes alerts
**less** likely is recorded as a weakening and logged.

| You want | Setting that exists today |
|---|---|
| A different norm for an age group (heart rate, respiratory rate, SpO2 floor, fever line) | `norms.<group>.hr_high / hr_low / rr_high / o2_low / temp_high` for neonatal, infant, toddler, child, generic |
| Different age groups (for example a row for 6 to 12 years, or for adolescents) | `age_group_upper_months` moves the three boundaries (3, 12, 36). **A new row cannot be added by a setting**; that needs a code change and your numbers |
| A different SpO2 line for the hard rule, or a different hypothermia line | `critical_o2_below` (88), `hypothermia_below` (35) |
| A hard rule to wait for a repeat, or to be corroborated by RR or temperature | `hard_rule_confirm_window` (1 today), `hard_rule_corroboration`, `hard_rule_corroboration_margin`, `heart_rate_corroborates` |
| A different upper age for the supported population | `max_age_months` (216), `outside_population` |
| Who is told, and at what regime | `ClinicalNotificationSettings` (`min_regime`, severity increases, blocked, unassessable) |

**What does not exist as a setting:** a rule that a *single abnormal channel* (for example HR above some number, or temperature below
some number) pages at WARNING or CRITICAL. If you mark W, C or X for S4 to S9, engineering adds that rule with the thresholds you
write in the last column, behind a setting, and the case is pinned in `test_single_channel_behaviour.py`.

## C. Numbers I would like you to accept or change

| Rule | Where it lives | Today | Accept / change to |
|---|---|---|---|
| The norms table, five rows | `PEDIATRIC_NORMS` | neonatal HR 90 to 180, RR < 60, SpO2 >= 90, T < 38.0; infant HR 90 to 175, RR < 60, SpO2 >= 90, T <= 39.0; toddler HR 75 to 150, RR < 45, SpO2 >= 91, T <= 39.0; child HR 50 to 130, RR < 35, SpO2 >= 92, T <= 39.5 (heart rate, respiratory rate and the newborn fever line re-derived 2026-11-23 as provisional proxies, no clinician sign-off; see the comment above `PEDIATRIC_NORMS`) | |
| Age boundaries | `age_group_upper_months` | 3, 12, 36 months | |
| SpO2 hard line; hypothermia line | `critical_o2_below`, `hypothermia_below` | 88; 35.0 (hypothermia alone scores below a hard rule, so it does not page by itself: S7) | |
| Named syndromes (septic shock, respiratory distress, hypovolemic shock) | `RiskAdapters.behavioral_vaccine` | septic shock: SpO2 < 92 and HR > 140 and RR > 35 and T > 38.5; respiratory distress: SpO2 < 90 and RR > 45; hypovolemic: HR > 150 and SpO2 < 88. **Marked "pending pediatrician sign-off" in the code** | |
| How soon a single hard-rule reading holds WARNING | `hard_rule_confirm_window` | at once (1) | |
| Supported age | `max_age_months` | 18 years | |
| Probe-off note | `probe_off_spo2_at_or_below` | a note at SpO2 0 or below | |
| Dwell before a first escalation; lock after it | `ESCALATION_DWELL_THRESHOLD`, `ESCALATION_LOCK_SECONDS` | two confirming readings; five minutes | |
| Who is paged | `ClinicalNotificationSettings.min_regime` | CRITICAL, any increase, blocked, unassessable | |

## D. Four things only you can answer

1. **How many pages per patient per day is acceptable on a unit?** The system does not measure this yet (the alarm-burden report is
   the next engineering item), so there is no number to react to; your answer sets the target it will be judged against.
2. **Which events count as deterioration** for validation (a documented sepsis alert, a rapid response call, an unplanned
   transfer to higher care, intubation)? The validation plan needs this list before any data can be replayed.
3. **At what regime should a person be told?** Today only CRITICAL (and a severity increase, and an unassessable reading) reaches a person. If WARNING should, the count of how many more people are told is what `alarm_burden.py` is for.
4. **Who signs?** A clinical owner with the authority to approve a rule is a prerequisite for everything in
   `CHANGE_CONTROL_AND_VALIDATION.md`.

Return this page marked. Engineering will turn every W, C and X into a rule with your numbers, pinned by a test, and will not change
any number on this page without your mark.
