# OBSERVE crash handling

Status: implemented in `clinical_governance_system.py`, `governance_orchestrator.py`, `observe_consolidated.py` and
`clinical_notification.py` (PR 8). Evidence class: EXECUTED by `test_observe_crash.py` (19 tests; a mutation check of 29
changes of the new code leaves none surviving; an unreachable guard that one mutant exposed was removed).

## What went wrong

OBSERVE assesses a patient and, as it does, moves that patient's escalation state (the tracked regime and the post-escalation
lock) **before** it fingerprints and writes the reading to its ledger. Three consequences when it crashed part way:

1. **The next critical reading was swallowed.** The state said "already escalated, locked" for a reading no ledger holds, so the
   patient's next CRITICAL was treated as a repeat inside the cooldown and raised no event. EXECUTED:
   `test_a_crash_after_state_moved_swallows_the_next_critical_reading_unless_the_state_is_reset` reproduces it on the engine.
2. **`ClinicalGovernanceSystem` returned no decision.** The exception propagated to the caller. Nobody was told, and unless the
   caller caught it the patient was simply not assessed with nothing saying so.
3. **The orchestrator's advisory path told nobody.** An OBSERVE crash after the action was recorded as `observe_error` and
   nothing else; only the strict profile alerted a person.

## What happens now

The rule throughout: when state is uncertain, be wrong in the direction of a repeat page, never silence.

- `ObserveClinicalEngine.reset_tracked_state(vitals)` drops that patient's policy, entropy, state commitment, fault-page stamps and
  Kalman tracker, so the next reading is judged fresh. It writes nothing to the audit ledger (the ledger may be what failed).
  It **keeps** the hard-rule history: that can only make a later hard rule confirm sooner, and a reading already in it is never
  its own repeat (see `HARD_RULE_FLOOR.md`).
- `ClinicalGovernanceSystem.process_vitals` returns a decision for a crash, with `regime = "assessment_unavailable"`,
  `escalation_required = True`, `action = "escalate_assessment_unavailable"` and a status of its own,
  `GovernanceStatus.ASSESSMENT_UNAVAILABLE`, instead of `NOT_EVALUATED`, which means "nothing to do". It carries no risk judgement
  (`risk_score` is 0.0 only because the field is required; the regime says no assessment exists), governance is never asked, and a
  failed reset is recorded on the decision (`TRACKED_STATE_RESET_FAILED`) without stopping the alert.
- A person is always told: through the escalation desk if one is attached (reason `assessment_unavailable`, naming the patient,
  **not switchable off** by the notification settings, like an unavailable governance verdict), otherwise through the legacy
  `alert_human` callable with the usual `delivered` / `failed` / `no_channel` record.
- `GovernanceOrchestrator`: an OBSERVE crash resets the patient's state on **both** paths and alerts on both. The strict path
  already refused and alerted; it now also resets and names the patient in the alert. The advisory path (the action has already
  run, so the record must stand) now alerts too, records the result in `human_alert` / `escalation_ticket`, and notes a failed
  reset in `observe_error`. Alerts for the `observe` stage now carry the patient identifier, because a page that cannot name the
  patient cannot be acted on.

## Limits

- **The reset is a judgement call.** It trades a possible repeat page for never swallowing a critical reading. A patient who
  really was locked after a genuine escalation and whose next evaluation crashes is paged again; that is the intended direction.
- **A crash that happens before OBSERVE moves any state** (for example while validating) is reset too. That is harmless: the
  state was already fresh or, if the patient had a genuine earlier escalation, it is dropped and the next reading re-escalates.
- **The advisory orchestrator path still runs OBSERVE after the action.** Only the strict profile runs it before. That ordering is
  a separate item (identity binding).
- **A request refused before OBSERVE ran has no verdict** (see `CLINICAL_ESCALATION_NOTIFICATION.md`).
- A custom engine without `reset_tracked_state` is recorded as such, not fatal.
