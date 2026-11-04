# Clinician-adjustable settings

For the clinical owner of a deployment. Nothing here needs to be decided today: every value has a default
that is exactly what the system already did (two documented exceptions below), and every value can be
changed later without touching code.

## How to change a value

Write only the values you want to change in a JSON file and give the file (or a mapping, or a
`ClinicalSettings` object) to the system:

```json
{
  "max_age_months": 252,
  "critical_o2_below": 90,
  "norms": { "child": { "hr_high": 125 } }
}
```

```python
system = build_single_hospital_system(alert_human=page_charge_nurse, settings="unit_settings.json")
```

* A misspelt name, a wrong type, or a value that breaks an assumption (for example a norm that makes the
  placeholder for a faulted sensor look abnormal) stops the system from starting, with the reason. It never
  quietly runs on the defaults.
* Every assessment records a digest of the full settings, and every value that differs from the default is
  named in the assessment's audit record, so any verdict can be traced to what it was tuned to.
* Values are frozen once the system is built: the settings recorded are the settings in use.
* A change that makes an alert **less** likely, or whose direction cannot be shown to be safe, is marked as a weakening in that record and logged at WARNING.
  It is allowed. It is never silent.
* Who may change a value and how that is approved is the hospital's process, not this software's. The
  recorded digest is what lets that process be audited.

## What can be changed now

| Setting | Default | Meaning | Less alerting when |
|---|---|---|---|
| `max_age_months` | 216 (18 y) | Upper bound of the supported pediatric population. `null` removes it. **New default.** | higher, or `null` |
| `outside_population` | `alert` | `alert`: an older patient is still assessed on every channel, never scored lower, but floored at WARNING with a named finding. `assess_anyway`: no finding. **New default behavior.** | `assess_anyway` |
| `require_age` | false | true: a reading with no age is a finding instead of "generic norms" | n/a |
| `age_group_upper_months` | 3, 12, 36 | Ends of the neonatal, infant and toddler groups | any change is listed: moving a boundary puts some children on a different row, and which way that goes depends on the child |
| `norms.<group>.hr_high / hr_low / rr_high / o2_low / temp_high` | pediatric table | The per-age-group reference values. Name only the ones you change. Groups: neonatal, infant, toddler, child, generic | hr_high, rr_high, temp_high higher; hr_low, o2_low lower |
| `critical_o2_below` | 88 | SpO2 below this is the hard rule | lower |
| `hypothermia_below` | 35.0 | Temperature below this is hypothermia | lower |
| `hard_rule_confirm_window` | 3 | A hard rule that fires again within this many of the patient's readings holds the regime at WARNING. 1 means repeats do not count | lower (1 turns repeat confirmation off) |
| `hard_rule_corroboration` | true | A hard rule beside another abnormal finding holds the regime at WARNING at once. **New default.** | false |
| `hard_rule_corroboration_margin` | 0.05 | How far above the hard rule's own score the heuristic must be to count as corroborated | higher |
| `probe_off_spo2_at_or_below` | 0.0 | At or below this SpO2 a "possible sensor disconnect, check the probe and the patient" note is added. Annotation only: it never lowers the regime. `null` disables | n/a |

## What a clinician should know about the two new defaults

1. **Population bound.** A patient above 216 months used to be scored as a child with no sign the system was
   out of scope. Now such a patient is assessed exactly as before, tracked exactly as before (a CRITICAL is still
   held), and reported no lower than WARNING with the finding `OUTSIDE_SUPPORTED_POPULATION`, paged once per
   window. Adult norms are a separate future module, not a setting.
2. **Corroboration.** A low SpO2 with other abnormal vitals now holds WARNING even when history context would
   have diluted it. A low SpO2 beside normal vitals is still treated as a possible artefact until it repeats.

## Not yet settings (still constants in source; next in line)

The syndrome (septic shock, respiratory distress, hypovolemic shock) thresholds; fusion weights and the
risk-to-regime bands; dwell count and the post-escalation lock seconds; which engines run on which readings;
the physical-plausibility bounds on vitals; the escalation rate limits and the physician-approval requirement
in the governance layer. They follow the same pattern: a named setting, the current value as its default, a
digest in the record, and a weakening mark where it applies.
