# OBSERVE crash handling

Status: implemented in `observe_consolidated.py` (the all-or-nothing `evaluate`), `clinical_governance_system.py`,
`governance_orchestrator.py` and `clinical_notification.py` (PR 8). Evidence class: EXECUTED by `test_observe_crash.py` (26
tests) and a mutation check of 42 changes of the new code with none surviving. Two independent Opus reviews: the first found a
blocker in the first design (which is why the design below is not the one first committed); the second found no blocker in this
one and two should-fix items, both fixed.

## What went wrong

OBSERVE assesses a patient and, as it does, moves that patient's escalation state (the tracked regime and the post-escalation
lock, plus the state-commitment head, entropy, fault-page stamps and trajectory tracker) **before** it fingerprints and writes the
reading to its ledger. Three consequences when it crashed part way:

1. **The next critical reading was swallowed.** The state said "already escalated, locked" for a reading no ledger holds, so the
   patient's next CRITICAL was treated as a repeat inside the cooldown and raised no event.
2. **`ClinicalGovernanceSystem` returned no decision.** The exception propagated to the caller. Nobody was told, and unless the
   caller caught it the patient was simply not assessed with nothing saying so.
3. **The orchestrator's advisory path told nobody.** An OBSERVE crash after the action was recorded as `observe_error` and
   nothing else; only the strict profile alerted a person.

## What happens now

The rule: when state is uncertain, be wrong in the direction of a repeat page, never silence.

**`ObserveClinicalEngine.evaluate` is all-or-nothing for the patient's tracked state.** It snapshots that patient's policy, entropy,
state-commitment head, fault-page stamps and trajectory tracker on entry, and puts them back if the evaluation raises (any
exception, including a non-`Exception`), re-raising the original error unchanged. It writes nothing to the audit ledger (the ledger
may be what failed). This is exact in both directions, which a "reset on every crash" is not:

- a crash **after** the state moved is undone, so the next reading escalates again (a repeat page, never silence);
- a crash **before** it moved changes nothing, so a patient already tracked at CRITICAL stays CRITICAL and is not silently dropped
  to STABLE. (The first design reset the patient on every crash and did exactly that; a review found it, a test now pins it.)

**Two edges, found by the second review.** Once the audit ledger holds the reading, the state is not rolled back (that would
fork the state-commitment chain, so two records would claim the same predecessor): the only thing after the write is logging, and
a log handler that raises can no longer fail the assessment. And a patient another evaluation **evicted** while theirs was in
flight is not brought back by the rollback (it would exceed the cap and chain the next record onto a head the ledger retired);
only a calm, unlocked patient is ever evicted, so nothing is lost.

The hard-rule history is deliberately not rolled back: it only ever makes a later hard rule confirm sooner, and a retried reading
carries the same timestamp so it is never its own repeat (see `HARD_RULE_FLOOR.md`). A trajectory tracker that cannot be copied is
dropped on rollback and restarts rather than being kept half-moved.

**`ClinicalGovernanceSystem.process_vitals` returns a decision for a crash**, with `regime = "assessment_unavailable"`,
`escalation_required = True`, `action = "escalate_assessment_unavailable"` and its own status,
`GovernanceStatus.ASSESSMENT_UNAVAILABLE` (not `NOT_EVALUATED`, which means "nothing to do"). It carries no risk judgement
(`risk_score` is 0.0 only because the field is required; the regime says no assessment exists), and governance is never asked.

**A person is always told.** Through the escalation desk if one is attached (reason `assessment_unavailable`, naming the patient,
**not switchable off** by the notification settings, like an unavailable governance verdict), otherwise through the legacy
`alert_human` callable with the usual `delivered` / `failed` / `no_channel` record.

**`GovernanceOrchestrator`:** the strict path already refused and alerted. The advisory path (the action has already run, so the
record must stand) now alerts too and records the result in `human_alert` / `escalation_ticket`. Alerts for the `observe` stage now
carry the patient identifier and the reason `assessment_unavailable`, because a page that cannot name the patient cannot be acted on.

## Limits

- **A missing OBSERVE engine is now loud.** With no engine configured, an advisory request that carries vitals raises a stage-error
  alert every time, where before it only set `observe_error`. That is a configuration fault paging per request, which is the point,
  and a behaviour change.
- **A hung alert channel now delays the advisory record.** The alert is made before the record is built, as it already is for a
  failed execution. With a desk the delay is bounded by `desk.max_dispatch_seconds()`; a legacy `alert_human` callable has no bound.
- **A failing `STATE_EVICTED` ledger write fails the evaluation that triggered the eviction,** which belongs to a different
  patient (found while testing the above; not changed here). The evicted patient's state is already gone by then.
- **One patient's readings must still be serialised by the caller** (the README says so). A concurrent evaluation of the same
  patient while another one rolls back can write to a state that was just restored; the rollback does not make that worse, but it
  does not fix it either.
- **The advisory orchestrator path still runs OBSERVE after the action.** Only the strict profile runs it before. That ordering is
  a separate item (identity binding).
- A request refused before OBSERVE ran has no verdict (see `CLINICAL_ESCALATION_NOTIFICATION.md`).
- A custom engine that is not `ObserveClinicalEngine` has whatever rollback it implements; the decision and the alert do not depend
  on it.
