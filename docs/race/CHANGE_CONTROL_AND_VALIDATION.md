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

## 2. What the version stamp does NOT cover (EXECUTED and INSPECTED), and what now closes it (D9)

**Status: closed by `logic_version` (below), on `main` after PR 17.** The rest of this section is the finding as measured.

`PARAMETER_SET` has exactly these keys (EXECUTED): `drift_critical_floor`, `drift_sigma_threshold`, `escalation_dwell_threshold`,
`escalation_lock_seconds`, `pediatric_norms`, `regime_critical_floor_default`, `regime_distribution_bands`, `regime_risk_floor`,
`schema`, `unassessable_confidence_penalty`, `vitals_physical_bounds`. Searching its serialised form (EXECUTED): `88.0` and `35.0` are
**absent**. So a change to any of these **leaves `parameter_set_version` unchanged**:

- the SpO2 hard line (88) and the hypothermia line (35) as source constants (the *settings* path records them; an edit to the source default does not),
- the heuristic's point values (0.5, 0.2, 0.3, 0.15, 0.1, 0.4 ...),
- the syndrome definitions (septic shock, respiratory distress, hypovolemic shock),
- the engine-selection gate cutoffs,
- the age-group boundaries (3, 12, 36 months) and the population bound (216 months) as source defaults (the settings digest covers them only when a settings object is used),
- the fusion weights and the corroboration and confirmation defaults,
- the Kalman and drift internals not listed above.

Two consequences: (a) **two builds with different clinical behaviour can write the same `parameter_set_version`**; (b) the only thing that
distinguishes them in a record is the source revision, and the cassette `version` ("1.1.0") was deliberately not bumped for the hard-rule
and floor changes (the comments say so), so recorded fingerprints keep their meaning. That was the right call for fingerprint
continuity and the wrong basis for change control.

**The fix (done, decision D9): `logic_version`.** Listing every constant in `PARAMETER_SET` would have meant copying literals out
of functions and trusting the copy; instead `logic_stamp.logic_digest` hashes what the code DOES. It walks each function's or
class's syntax tree and records node kinds, names and constants in order, ignoring comments, formatting, docstrings and logging
calls, and never `ast.dump` (whose fields differ between Python versions, which would change every recorded fingerprint when an
installation upgrades; the digest is pinned and was checked identical on 3.11, 3.12 and 3.13). The core digest covers the validation,
the age groups, the regime bands, every engine, the fusion, the escalation policy, the evaluation path (bypass, floor, hold rules)
and the Kalman tracker and its tuning (`ENGINE_LOGIC_COVERS` names them, and a test pins that list). The cassette digest covers its
rules, `ClinicalSettings` and the settings defaults. Every decision records `logic_version` (the two together) in its provenance, inside
the fingerprinted payload. A changed point value, syndrome threshold, gate cutoff, rule text or default now changes it (EXECUTED:
`test_logic_stamp.py`). It changed every recorded fingerprint ONCE, deliberately; the pinned fingerprints in
`test_engine_gate_profile.py` were regenerated for 3.11 and 3.12 and will move again whenever the scoring logic does, which is the
tripwire's job. It does not say whether a change was approved: that is the change request in section 3.

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
