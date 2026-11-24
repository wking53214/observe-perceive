# Clinical gaps briefing: what the engine does not do well, and the one decision each needs

**For the clinical owner, in plain language.** This is the short list of places where the system's behaviour may not match what a
pediatric clinician would want. Each item says what happens, why it matters, and the single decision that moves it forward. Nothing here is a
recommendation of a number: the numbers are yours to give. The register rows (C1 to C18 in `ADVERSARIAL_REGISTER.md`) and the review sheet
(`CLINICIAN_REVIEW_SHEET.md`) hold the detail. **No rule in the system has been clinically approved, and no real patient data has been replayed.**

## 1. One abnormal number usually does not raise an alert (C17)

The engine adds up small warning signs from several measurements, then averages several internal "opinions". A single very abnormal
channel often scores too low to matter. Measured: a 5 year old at heart rate 200 with everything else normal, an infant at oxygen 89, a
newborn at 34.5 C, a toddler at breathing rate 70, none of them caused a page. Only oxygen below 88 or a named combination of signs (septic
shock, respiratory distress, hypovolemic shock) forces an alert.
**Why it matters:** a quiet system looks safe and is not. **Decision needed:** for each of the review sheet's S4 to S9, fine as it is, or
page at WARNING or CRITICAL, and at what value. Engineering adds the rule behind a setting with your number and pins it with a test.

## 2. Three slow or subtle worsenings, and four faulty-sensor cases, are missed (C1)

The system's own test set shows it gives no alert for gradual hypoxia in a child, a reactive-airway toddler, a toddler with a viral fever, and
four sensor fault patterns (a stuck heart rate, a stuck oxygen sensor, a slow oxygen drift downward, and several readings lying together to
hide sepsis). These are pinned, so any change in the list fails the build.
**Why it matters:** these are the system's admitted blind spots. **Decision needed:** which of the seven matter on your unit. Each needs a
rule, more history in the engine, or a written "this is a known limit".

## 3. One "child" row covered ages 3 to 18 (C2): split 2026-11-23, needs sign-off

A healthy 12 year old with a heart rate of 70 used to be reported as too slow, because the single child row was written for a young child. It is now three rows (preschool, school, adolescent) with provisional limits from published references.
**Why it matters:** false alarms teach staff to ignore alerts. **Decision needed:** confirm or change the new age bands and normal ranges (the age
boundaries are a setting; adding a new row needs your numbers).

## 4. Low body temperature alarms only with company (C18)

A newborn at 34.5 C with everything else normal does not page. Add one more mild finding, such as a fast breathing rate, and it pages at
once. (A code comment used to claim otherwise; it is now corrected.)
**Why it matters:** hypothermia in a newborn can be serious on its own. **Decision needed:** should a low temperature alone page, and from what value.

## 5. Two starting settings were guesses (C15)

On the clinician's instruction in an earlier round: a single low-oxygen reading now holds a WARNING at once (before, it waited for a repeat),
and heart rate alone no longer counts as backup evidence for it. Neither has been checked on real readings.
**Why it matters:** the first setting can raise pages from a probe that slipped off. **Decision needed:** keep both, or change them, after
seeing the cost on a real stream (`alarm_burden.py` counts it).

## 6. Who is actually told at WARNING (new, from the alarm-burden work)

Today a person is told only for CRITICAL, for a move to a higher level, and for an unreadable reading. A WARNING that the engine raises is
recorded but reaches nobody unless the deployment lowers the threshold. Measured on the review cases: two events, none of them told to a person.
**Why it matters:** the "page" in the review sheet is an engine event, not a call to a nurse. **Decision needed:** at what level should a
person be told, and what number of notifications per patient per day is acceptable (`ALARM_BURDEN.md`).

## 7. Nothing has been validated against real outcomes (C16)

All the evidence so far is engineering evidence ("the code does what we designed"), not clinical evidence ("it catches deteriorating
children early at an acceptable cost"). **Decision needed:** which events count as deterioration, and who approves a replay on real
de-identified data (`REAL_DATA_REPLAY_CHECKLIST.md`, `CHANGE_CONTROL_AND_VALIDATION.md` section 4).

## Order that wastes the least effort

1. Decide section 6 (who is told), because it changes what every other number means.
2. Mark the review sheet (sections 1, 3 and 4 above are its S4 to S10).
3. Choose which of the seven missed cases in section 2 matter.
4. Approve a real-data replay, then set an alarm-burden ceiling from what it shows.
