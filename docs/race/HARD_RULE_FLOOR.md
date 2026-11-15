# The hard-rule floor

Status: implemented in `observe_consolidated.py` (the bypass branch of `evaluate`), `pediatric_cassette.py` and the
cassette seam (`cassette.py`), PR 7. Evidence class: every number below was EXECUTED against the engine before and after
the change (the same readings are in `test_hard_rule_floor.py`); that file (42 tests) and a mutation check of 39
changes cover it; whether the defaults are clinically right is UNKNOWN until a clinician reviews them.

## The defect

OBSERVE has a "hard rule": a reading so unambiguous (the pediatric one is `CRITICAL_O2`, SpO2 below 88%) that waiting for a
second reading would be the harm. It skips the dwell. But the skip applied only when the **fused** regime was already
WARNING or CRITICAL. Fusion averages engines that watch different axes, and any `previous_*` history in the request adds
the trajectory engine, which sees a stable trend and pulls the average down. So a hard rule could be fused away.

Measured on the engine as it was (regime / escalation event / fused risk, one entry per reading):

| Readings (with history supplied) | Before | After |
|---|---|---|
| SpO2 86% with fever 39.1, HR 138, RR 34 (a child who is clearly unwell), 15 min after a milder reading | stable / none / 0.33 | warning / **escalation** / 0.50 |
| SpO2 85% twice in a row | stable, stable | stable, then warning / **escalation** |
| SpO2 85%, a normal reading, SpO2 82% | stable throughout | warning / **escalation** on the repeat |

So before this change a child with a critically low oxygen saturation and obvious systemic illness was rated STABLE with no
escalation whenever the integrator supplied history. That is a failure to escalate, the worst kind.

## The rule now

A hard rule **holds the regime at WARNING**, whatever the fusion says, once it is **confirmed**. Confirmation is any of:

1. **Corroboration.** Another channel is abnormal in the same assessment (a raised respiratory rate, temperature, heart
   rate). One sensor's artefact does not raise a child's temperature. In the pediatric domain this is the heuristic score
   sitting above the hard rule's own score by at least `hard_rule_corroboration_margin`.
2. **Repeat.** The same rule fired again within `hard_rule_confirm_window` of the patient's readings (the current one
   included). Counted in readings, not seconds, so it does not depend on how often a unit samples.
3. **A window of 1.** The clinician chose not to wait: every hard rule holds the floor at once.

A **lone, uncorroborated** hard-rule reading is judged exactly as before. It may be a sensor artefact (a probe slipping
reads as a low SpO2), and the next reading decides. A named shock syndrome is a conjunction of vitals, not one sensor, and
always holds the floor (a guard: none of 972 readings searched was found that fusion rates lower, so this branch is not
a demonstrated fix).

The floor only raises. A patient already tracked at CRITICAL stays CRITICAL. The fused risk is floored to match so score and
regime agree. The lift is written into the decision's `safety_bypass` note and says which of the reasons applied. A patient
who stays on a floored hard rule is **one** escalation event, not one per reading.

## The settings (the clinician's to change)

On `PediatricCassette`, validated at construction (a bad value refuses to start):

| Setting | Default | What it does |
|---|---|---|
| `hard_rule_confirm_window` | 3 readings | How many readings make a repeat. 1 holds every hard rule at once. Longer tolerates more artefact and waits longer. |
| `hard_rule_corroboration` | on | Another abnormal channel confirms at once. Off means only repeats confirm. |
| `hard_rule_corroboration_margin` | 0.05 | How far above the hard rule's own score the heuristic must sit to count as corroborated. |

A setting that is not at its default is recorded by name in the cassette identity of every entry made under it, so a
verdict says it was made with a longer window or without corroboration. At the defaults the identity is unchanged and the
cassette version stays 1.1.0, because the existing recorded fingerprints must keep their meaning; the hard-rule logic is
branch logic attested by the source revision, as the rest of `evaluate` is, and any lift it makes is in the entry.

**The default is a decision for a clinician, not a finding.** With window 3 and corroboration on, the one case that still
waits is a single hard-rule reading beside otherwise normal vitals. Setting the window to 1 removes that wait at the price of
acting on a lone artefact. This PR ships the earlier agreed default and makes the other choice one argument.

## Limits

- **Pediatric only.** Corroboration is a domain judgement; another cassette supplies its own or none (a cassette without the
  optional members gets the core default window and no corroboration).
- **A lone low reading beside normal vitals still waits one more reading.** By design, and adjustable.
- **The corroboration test reads the heuristic score.** It assumes each abnormal finding adds to that score. A change to the
  heuristic's scoring changes what corroboration means; the test pins the current behaviour.
- **Per-patient history is in memory** and is evicted with the patient; a restart forgets it, so a repeat that straddles a
  restart is a lone reading. The same is true of the dwell state.
- **Adults are still scored on pediatric norms.** That is a separate item.
- **Not exercised:** real monitor data. The readings are constructed.

## Existing test edited

`test_detection_record.py`: `repeated_spikes_o2` leaves the recorded unsafe cases (and the comment's count goes from five to
four), because this change closes it. Nothing else in that test changed.
