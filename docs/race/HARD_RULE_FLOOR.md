# The hard-rule floor

Status: implemented in `observe_consolidated.py` (the bypass branch of `evaluate`), `pediatric_cassette.py` and the
cassette seam (`cassette.py`), PR 7. Evidence class: every number below was EXECUTED against the engine before and after
the change (the same readings are in `test_hard_rule_floor.py`); that file (60 tests) and a mutation check of 49
changes cover it (3 survivors, all in the unreachable named-syndrome branch described under Limits); one independent Opus
review found six issues, each fixed and tested; whether the defaults are clinically right is UNKNOWN until a clinician reviews them.

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

1. **Corroboration.** The heuristic found something else abnormal in the same assessment: its score sits above the hard
   rule's own score by at least `hard_rule_corroboration_margin`. That is strong evidence for a respiratory rate or
   temperature (a pulse-oximeter artefact does not move them) and weaker evidence for heart rate, which usually shares
   the probe with SpO2. The score does not say which channel added to it. **Open clinical question: should heart rate alone
   count?** Raising the margin asks for more than one extra finding.
2. **Repeat.** A hard rule fired on an **earlier** reading within the last `hard_rule_confirm_window` of the patient's
   readings (the current one included). Counted in readings, not seconds, so it does not depend on how often a unit
   samples. The history records only that *a* hard rule fired, not which one. The same reading seen again (same timestamp:
   a retransmission, a replay, a retry after a crash) is never its own repeat, and neither is an older reading that
   arrives late. A timestamp that cannot be ordered against the history (naive against aware) counts as a repeat, the
   direction that escalates.
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
| `hard_rule_confirm_window` | **1** reading (1 to 1000; was 3 until decision D1) | How many readings make a repeat. 1 holds every hard rule at once. Longer tolerates more artefact and waits longer. |
| `hard_rule_corroboration` | on | Another abnormal finding confirms at once. Off means only repeats confirm. |
| `hard_rule_corroboration_margin` | 0.05 (0.001 to 1) | How far above the hard rule's own score the heuristic must sit to count as corroborated. Not zero: that would make every hard rule corroborated, which is a window of 1 and should be asked for as one. |

The bounds are sanity limits, not clinical ones: a window past 1000 is an unbounded per-patient history, and a margin below
0.001 is vacuous. Settings are fixed once the cassette is built (assigning one raises), so the identity a verdict records is
the behaviour it had. A score exactly at the margin counts on every platform (the heuristic sums decimals, and
0.2 + 0.2 + 0.15 is 0.55000000000000004).

A setting that is not at its default is recorded by name in the cassette identity of every entry made under it, so a
verdict says it was made with a longer window or without corroboration. At the defaults the identity is unchanged and the
cassette version stays 1.1.0, because the existing recorded fingerprints must keep their meaning; the hard-rule logic is
branch logic attested by the source revision, as the rest of `evaluate` is, and any lift it makes is in the entry.

**The default is a decision for a clinician, not a finding.** This PR shipped window 3, where the one case that still waits is a
single hard-rule reading beside otherwise normal vitals. **The default is now 1** (decision D1, taken for a first evaluation unit):
a lone SpO2 below 88 holds WARNING at once, at the price of acting on a lone artefact (EXECUTED: `single_o2_spike_down` is now in the
pinned risky set as an accepted false alarm). A cassette that gives no window still gets the core's own 3. A clinician can set any
window; every window above 1 is recorded as weaker than the default.

## Limits

- **Pediatric only.** Corroboration is a domain judgement; another cassette supplies its own or none (a cassette without the
  optional members gets the core default window and no corroboration).
- **A lone low reading beside normal vitals still waits one more reading.** By design, and adjustable.
- **The corroboration test reads the heuristic score.** It assumes each abnormal finding adds to that score. A change to the
  heuristic's scoring changes what corroboration means; the test pins the current behaviour.
- **Per-patient history is in memory.** A patient is never evicted for capacity while a hard rule in their window is still
  waiting for confirmation (found by review: it was, and the next low reading was then a lone one); a patient with nothing
  pending is, so memory stays bounded. A restart forgets it, so a repeat that straddles a restart is a lone reading. The same
  is true of the dwell state.
- **The named-syndrome branch is unreachable today.** None of 972 readings searched produced a syndrome that fusion rates
  below WARNING, because the syndrome's own risk is already floored into the fused risk. The branch stays as a guard, and its
  three surviving mutants are that guard, an equivalent mutation, and its note text.
- **Adults are still scored on pediatric norms.** That is a separate item.
- **Not exercised:** real monitor data. The readings are constructed.

## Existing test edited

`test_detection_record.py`: `repeated_spikes_o2` leaves the recorded unsafe cases (and the comment's count goes from five to
four), because this change closes it. Nothing else in that test changed.
