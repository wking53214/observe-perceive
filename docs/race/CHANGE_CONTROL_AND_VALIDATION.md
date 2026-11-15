# Model change control and validation

Evidence classes: **EXECUTED**, **INSPECTED**, **PROPOSED**. "Model" here means everything that decides a patient's regime or whether a
person is paged: the engines, the norms, the rules, the settings, the notification settings and the governance gates. This library
contains no machine-learned model; the "model" is rules and numbers, which is why a change to one number is a change to the model.
Nothing here is regulatory advice. Whether this software is a medical device, and under which pathway, is a decision for regulatory
counsel and is not made here.

## 1. What a decision records today about what produced it (INSPECTED)

Every OBSERVE ledger entry carries, inside the fingerprinted payload (`observe_consolidated._provenance`):

- `parameter_set_version`: SHA-256 of `PARAMETER_SET`.
- the cassette identity: `name`, `version`, plus `identity_extras` for anything not at its default (engine gate, hard-rule values, and every
  `ClinicalSettings` value that differs, with `settings_digest` and `settings_weaker`).
- since #86, the request commits to the assessment's `decision_fingerprint` and `state_commitment`.
- every `ClinicalDecision` carries `settings_digest` (the full settings), even at defaults.

So a verdict can be traced to **the numbers it was made with**, provided those numbers are in one of those places.

## 2. What the version stamp does NOT cover (EXECUTED and INSPECTED)

`PARAMETER_SET` has exactly these keys (EXECUTED): `drift_critical_floor`, `drift_sigma_threshold`, `escalation_dwell_threshold`,
`escalation_lock_seconds`, `pediatric_norms`, `regime_critical_floor_default`, `regime_distribution_bands`, `regime_risk_floor`,
`schema`, `unassessable_confidence_penalty`, `vitals_physical_bounds`. Searching its serialised form (EXECUTED): `88.0` and `35.0` are
**absent**. So a change to any of these **leaves `parameter_set_version` unchanged**:

- the SpO2 hard line (88) and the hypothermia line (35) as source constants (the *settings* path records them; an edit to the source default does not),
- the heuristic's point values (0.5, 0.2, 0.3, 0.15, 0.1, 0.4 ...),
- the syndrome definitions (septic shock, respiratory distress, hypovolemic shock),
- the engine-selection gate cutoffs,
- the fusion weights and the corroboration and confirmation defaults,
- the Kalman and drift internals not listed above.

Two consequences: (a) **two builds with different clinical behaviour can write the same `parameter_set_version`**; (b) the only thing that
distinguishes them in a record is the source revision, and the cassette `version` ("1.1.0") was deliberately not bumped for the hard-rule
and floor changes (the comments say so), so recorded fingerprints keep their meaning. That was the right call for fingerprint
continuity and the wrong basis for change control. **PROPOSED fix (small, not done):** put every constant above into `PARAMETER_SET`
(it is a dictionary; adding keys changes the hash once, deliberately, and every later change is then visible), and record the source
revision in each decision.

## 3. A change-control procedure (PROPOSED)

Every change to a number, a rule, a setting default, an engine or a gate is a **model change** and goes through:

1. **Classify** it. *Safe direction*: makes an alert more likely only (still validated, because alarm burden is a harm). *Weakening*: makes
   an alert less likely, or whose direction cannot be shown (`ClinicalSettings.weakenings()` already computes this for settings; extend it to
   source constants). *Structural*: a new rule, engine or ordering.
2. **Write the change request**: what, why, evidence, the register row (`CLINICAL_SAFETY_AND_VALIDATION.md` section 3) it touches, the
   classification, the clinical owner who approves, the rollback.
3. **Validate** proportionally (section 4). A weakening or structural change needs the full plan; a safe-direction change needs at least
   the alarm-burden measurement.
4. **Shadow first.** Run the new version beside the live one on the same readings, record both verdicts, page from the live one only,
   and review every disagreement. The two-engine comparison is cheap here because the engine is deterministic given its inputs
   (the `decision_fingerprint` has no wall clock, INSPECTED).
5. **Approve and release**: the clinical owner signs the register row; the release names the source revision, `parameter_set_version`,
   cassette version and `settings_digest` it ships with; the digest is what an auditor compares.
6. **Roll back by version**, not by hand-edit: the previous release's four identifiers restore the previous behaviour.
7. **Monitor** after release: pages per patient-day, time to acknowledge, share of CRITICAL pages that were acknowledged (the desk records
   these), and any `settings_weaker` in the recorded identity.

Who approves, and how an approval is evidenced, is the hospital's process. The recorded digests are what let that process be audited.

## 4. Validation (PROPOSED; no clinical validation has been done, and none of these results exist)

| Question | Method | Data needed | What I will not invent |
|---|---|---|---|
| Does it detect deterioration early enough? | retrospective replay of de-identified, time-stamped vitals with outcome labels; time-to-first-alert versus clinician-documented deterioration | labelled pediatric vitals across age bands, with sepsis and respiratory failure cases | sensitivity, specificity and lead-time targets: set by the clinical owner |
| Does it alarm too much? | alarms per patient-day and per bed-day by age band and unit; share of pages with no clinical action | the same replay plus unit census | acceptable burden: set by the clinical owner |
| Is each age band right? | per-band error rates against reference ranges the clinical owner cites; the known false flag at ages 3 to 18 (one "child" row) is the first thing to measure | sourced pediatric reference ranges | the numbers themselves (no source is stated in the code, see the register) |
| Does the floor and the population bound behave? | replay with out-of-range ages | synthetic and real | n/a |
| Is behaviour stable under data faults? | the existing adversarial sensor-fault set (`adversarial_sensor_faults.py`) and the deterioration simulator (`deterioration_simulator.py`), extended | exist | n/a |
| Does a change alter anything it should not? | run old and new on a frozen corpus; diff regimes, escalations and pages; every difference explained | a frozen corpus (the simulator scenarios are a start) | n/a |

The deterioration simulator and the detection-record scenarios already in the repository (`deterioration_simulator.py`,
`test_detection_record.py`) are **regression fixtures, not validation evidence**: they check the code does what its authors expected, not
that what they expected is clinically right. A status document that says a stage is complete is a claim to test, not evidence.

## 5. What I could not determine

- Whether any deployment pins a source revision per release, or stores the settings file under version control.
- Whether the clinical owner exists as a named role, who it is, and what approval authority it has.
- The regulatory status of the software; I have assumed nothing.
