# Clinician settings and the supported population

For the clinical owner of a deployment, and for whoever reviews a deployment's settings. Nothing here has to be decided
today: every value defaults to what the system already did (one documented exception, below) and every value can be
changed later without touching code.

## What was wrong (measured on `main`)

1. Every age from 36 months to 125 years was scored on the "child" norms (heart rate 100 to 130), and nothing said the
   patient was out of scope. A healthy 40 year old at heart rate 70 was reported with `BRADYCARDIA: 70 < 100`; an adult at
   heart rate 135 was rated only on a child's `> 130`; neither carried any note that the patient was not a child.
2. The clinical lines (norms, age-group boundaries, the SpO2 hard line at 88, hypothermia at 35) were constants in source,
   so changing one meant changing code, though most are not engineering decisions and several are marked provisional
   pending clinician sign-off.

## How to change a value

Write only the values you want to change, as a JSON file, a mapping or a `ClinicalSettings`, and give it to the system:

```json
{ "max_age_months": 252, "critical_o2_below": 90, "norms": { "child": { "hr_high": 125 } } }
```

```python
system = build_single_hospital_system(alert_human=page_charge_nurse, settings="unit_settings.json")
```

- A misspelt name, a wrong type, or a value that breaks an assumption (a norm that makes the placeholder for a faulted
  sensor look abnormal) stops the system from starting, with the reason. It never quietly runs on the defaults, and a
  missing file is an error.
- Values are frozen once the system is built: the settings recorded are the settings in use.
- Every `ClinicalDecision` carries `settings_digest` (SHA-256 of the full, canonical settings). Every non-default value is
  also named in the OBSERVE audit record's cassette identity (`setting.<name>`, plus `settings_digest`), so any verdict can
  be traced to what it was tuned to. Defaults record nothing extra, so default fingerprints are unchanged.
- A change that makes an alert **less** likely, or whose direction cannot be shown to be safe, is listed in the record
  (`settings_weaker`) and logged at WARNING when the system is built. It is allowed. It is never silent.
- Who may change a value and how that is approved is the hospital's process, not this software's. The digest in every
  record is what lets that process be audited.

## What can be changed now

| Setting | Default | Meaning | Less alerting when |
|---|---|---|---|
| `max_age_months` | 216 (18 y) | Upper bound of the supported pediatric population. `null` removes it. **New default.** | higher, or `null` |
| `outside_population` | `alert` | `alert`: an older patient is still assessed on every channel, never scored lower, but reported at WARNING at least with a named finding. `assess_anyway`: no finding. **New default behaviour.** | `assess_anyway` |
| `require_age` | false | true: a reading with no usable age is a finding instead of "generic norms". Independent of `outside_population` | n/a |
| `age_group_upper_months` | 3, 12, 36 | Ends of the neonatal, infant and toddler groups | any change is listed: which way it goes depends on the child |
| `norms.<group>.hr_high / hr_low / rr_high / o2_low / temp_high` | pediatric table | Per-age-group reference values; name only the ones you change. Groups: neonatal, infant, toddler, child, generic | hr_high, rr_high, temp_high higher; hr_low, o2_low lower |
| `critical_o2_below` | 88 | SpO2 below this is the hard rule | lower |
| `hypothermia_below` | 35.0 | Temperature below this is hypothermia | lower |
| `hard_rule_confirm_window` | **1** (was 3; decision D1) | A hard rule that fires again within this many of the patient's readings holds WARNING. 1 holds WARNING on a lone hard-rule reading at once, the strongest setting | any value above 1 |
| `heart_rate_corroborates` | false (decision D2) | Whether a heart-rate finding alone corroborates a low SpO2. By default respiratory rate or temperature is needed, because heart rate usually shares the probe with SpO2. Only matters when the window is above 1 | not weaker when true |
| `hard_rule_corroboration` | true | A hard rule beside another abnormal finding holds WARNING at once | false |
| `hard_rule_corroboration_margin` | 0.05 | How far above the hard rule's own score the heuristic must be to count as corroborated | higher |

The hard-rule values keep their old route too: `PediatricCassette(hard_rule_confirm_window=...)`. Give them there or in
the settings, never both (the cassette raises).

## The supported population

`max_age_months` bounds the supported population. A reading above it is:

- **assessed exactly as before**, on every channel, and tracked exactly as before, so a CRITICAL is still held and a
  deteriorating adult is never scored less severely for being out of scope;
- **reported at WARNING at least**, with the finding `OUTSIDE_SUPPORTED_POPULATION` first in the rules, so a person looks;
- **paged once**, when the finding first applies to the patient, and again only after a reading that is back in scope (not
  on a timer: a timed window would re-page a standing adult every five minutes, which a probe showed before this was
  fixed). An improving adult is reported at the floor and is not paged again; a later CRITICAL still pages;
- recorded in the audit entry under `regime_floor` (present only when it applied, so other entries keep their shape).

It is a floor on the report, not a data fault: nothing about the reading is untrustworthy, so no channel is masked. It
never writes the patient's tracked state, so a patient who returns to the supported range is judged fresh.

**This is a floor, not adult scoring.** Adult norms are a separate future cassette. Until then an adult is shown to a
person at WARNING with the finding, rather than reassured or falsely alarmed by child norms.

## Results (executed)

- Healthy 40 year old, heart rate 70: before, `stable` with `BRADYCARDIA: 70 < 100` and no note; now `warning`, paged once,
  `OUTSIDE_SUPPORTED_POPULATION` first.
- Adult at heart rate 135: before `stable` (score 0.2, no note); now `warning`, paged, finding first.
- 92 tests in `test_clinical_settings.py`, and a mutation check of 50 changes to the new code with none surviving (six first
  survived; they were closed by tests, and one by removing code that could never differ). After the review the check was
  rerun on 57 changes, none surviving.

## Limitations and unknowns

- **The child norms still false-flag older children.** A healthy 12 year old at heart rate 70 is still `BRADYCARDIA: 70 <
  100`, because one "child" row (heart rate 100 to 130) covers 3 to 18 years. The floor does not touch this; the clinician
  can now change it (`norms.child.hr_low`, or move `age_group_upper_months`). **PROPOSED, not done:** add school-age and
  adolescent rows. That is clinical content and needs sign-off, so no number was invented here.
- **Not yet settings (still constants in source):** the syndrome thresholds (septic shock, respiratory distress,
  hypovolemic shock), the engine-selection gate (`engine_gate` is a choice of two, not values), fusion weights and
  risk-to-regime bands, dwell count and lock seconds, the physical-plausibility bounds on vitals, and the heuristic's own
  point values. The engine-selection gate still uses its own heart rate and SpO2 cutoffs. They follow the same pattern when
  they are moved.
- `settings_weaker` is recorded when any setting outside the three hard-rule ones is also non-default. A cassette tuned only
  through the hard-rule values records them under their own names, as it always did (a non-default value is visible by name),
  without the `settings_weaker` list. Making it uniform would change recorded identities that already exist, so it is not done.
- The production profile does not yet require that a deployment's settings be supplied or recorded; `ClinicalGovernanceSystem`
  takes them. `GovernanceOrchestrator` is given an engine by its caller, so it uses whatever cassette that engine has.
- Whether 18 years is the right default bound, and WARNING the right floor, are the clinician's to decide.
