# The clinical owner: what the role decides and signs (decision D15)

**For whoever recruits or appoints this person.** Every clinical number and rule in this software is currently marked NOT APPROVED
(`CLINICAL_SAFETY_AND_VALIDATION.md` section 3: 33 rules, none signed), and engineering has deliberately not invented any threshold. Until
one named person with the authority to approve them exists, the system can be evaluated but not responsibly used on patients. This page says
what that person must be able to do. It describes the role, not a person, and the hospital's own clinical governance decides who holds it
and how an approval is evidenced.

## Who

A pediatric clinician (which role, physician or nurse leader, is the hospital's choice) who
(a) is accountable under the hospital's clinical governance for approving a monitoring alert rule, (b) knows the unit's patients and its
alarm culture, and (c) can say yes or no to a threshold without needing to understand the code. A named deputy is needed for absences:
nothing may ship while no one can sign. Engineering does not decide any of this and cannot stand in.

## What they decide (each item has a ready document)

| Decision | Where it is put to them | What engineering supplies |
|---|---|---|
| For each of twelve example readings: fine, page at WARNING, page at CRITICAL, or other, and the threshold wanted | `CLINICIAN_REVIEW_SHEET.md` section A | the engine's current behaviour on each, pinned by tests |
| Accept or change the norms table, age boundaries, SpO2 and hypothermia lines, syndromes, hard-rule window, probe-off default, supported age | the sheet, section C | the setting that changes each, with weakening warnings |
| **At what regime a person is told** (today only CRITICAL, a severity increase and an unassessable reading; a WARNING event reaches nobody by default) | the sheet, section D3; `ALARM_BURDEN.md` | counts of events versus people told, from a replay |
| An acceptable alarm burden (events and notifications per patient-day, per unit) | `ALARM_BURDEN.md`, `REAL_DATA_REPLAY_CHECKLIST.md` | `alarm_burden.py` on a real de-identified stream; `ticket_burden.py` on a real pilot's desk log |
| Which events count as deterioration for validation (documented sepsis alert, rapid response call, unplanned transfer, intubation) | the sheet, section D2; validation plan section 2 | a replay design once the events are defined |
| Whether adults and older adolescents are in scope (floor, adult cassette, per-unit age limit) | decision D7 in `PRODUCTION_READINESS.md` | the floor exists; a cassette needs their numbers |
| Single-channel rules (a lone HR 45 or 200, a lone SpO2 below the age norm, temperature, RR) and an adolescent age row | the sheet, S1 to S10 | a rule behind a setting with their numbers, pinned by a test |
| Every change to a number, rule or default afterwards | `CHANGE_CONTROL_AND_VALIDATION.md` section 3 | classification (safe direction, weakening, structural), validation, shadow run, the four release identifiers |

## What they sign, and how

Each rule has a row in the register. Approval means: the row names the exact rule, the data it was checked against, the person, the date and
the identifiers of the release it applies to (source revision, parameter-set version, cassette version, settings digest and the engine logic
digest the software now records in every decision). A change to any of those is a new approval, not a continuation. The hospital's process
decides the form of the signature; the recorded digests let an auditor compare what was approved with what ran.

## What they are not asked to do

Read code, judge the cryptography, run tests or choose infrastructure. They are asked to say what the system should do for a child, and to
refuse to sign what they cannot defend.

## What they should refuse to sign

- A rule or threshold whose source they cannot name, or that engineering chose without them.
- Any weakening (fewer pages) without a stated reason and a validation run; the software warns when a setting weakens an alert.
- Use on patients before an evaluation in silent mode (the system assesses but tells no one) on their own unit's real readings.
- A claim of validation: none has been done; no outcome-labelled data has been analysed.

## First two weeks of the role (suggested order)

1. Mark the review sheet, or ask for different example cases.
2. Decide at what regime a person should be told, and set a first alarm-burden ceiling to be tested, not a final one.
3. Define the deterioration events for validation.
4. Name the deputy and the evidence format for approvals.

No estimate of the time this takes is offered: engineering does not know the hospital's process.
