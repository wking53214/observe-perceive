# Escalation integrity: design

Scope: what OBSERVE's escalation policy decides and reports. Notification, delivery, receipt and acknowledgement are a separate change (the human escalation and receipt model) and are **not** solved here.

## The defect (EXECUTED on `bcc5697`)

1. `EscalationPolicy.evaluate` returned early for any candidate while the 300 s lock was active. A patient escalated at WARNING who worsened to CRITICAL stayed reported as WARNING until the lock expired and two further readings confirmed.
2. The bypass branch (hard rule or syndrome) set the regime but computed the escalation flag only from a prior regime of STABLE or CAUTION. WARNING to CRITICAL changed the regime and raised **no event**: `escalation_required=False`, and the governed path returned `continue_monitoring`.
3. The bypass branch overwrote the tracked regime with the candidate, so a patient tracked at CRITICAL could be lowered to WARNING by a bypass that rated only WARNING.

## Behavior now

| Rule | Implementation |
|---|---|
| Cooldown suppresses repeats at the same severity | The lock still returns early when the candidate is not higher than the tracked regime |
| Cooldown never suppresses a severity increase | A higher candidate falls through to the dwell logic during the lock |
| A severity increase is a new escalation event | An escalation is any move up into WARNING or CRITICAL, including WARNING to CRITICAL; lock and timestamp are set again |
| The event is distinguishable from the one before it | `FusedVerdict.escalation_kind` is `new_escalation`, `severity_increase`, `de_escalation` or empty, with `previous_regime`; `ClinicalDecision` carries both; the audit entry and fingerprint record the two new kinds; each reading already has its own audit hash and state commitment |
| A persistent CRITICAL stays CRITICAL | The bypass takes the maximum of the candidate and the tracked regime |
| Repeated CRITICAL is one event | Same severity is suppressed by the lock |
| Missing data never downgrades | Unchanged: the frozen-policy path floors at the tracked regime (tested) |
| De-escalation is visible | Recorded as `de_escalation` with the regime it fell from |

## Decisions made, and the alternatives

* **Dwell is kept for a higher candidate.** A higher candidate that does not come from the hard-rule or syndrome bypass still needs the configured dwell (2) confirming readings, now inside the lock, not after it. Alternative: act on the first reading. The hard-rule bypass already acts on the first reading, and on the current engine no input reaches WARNING or CRITICAL without that bypass (0 of 7,200 swept), so this choice affects only future cassettes. Owner decision if they want zero dwell on severity increases.
* **Only two kinds are fingerprinted.** `severity_increase` and `de_escalation` add keys to the decision data only when they occur, so every existing entry and recorded fingerprint is unchanged. `new_escalation` is on the verdict and decision but not in the hashed data, because adding it would change existing fingerprints.

## Not solved here

* **Human governance of recovery.** De-escalation is still automatic after dwell. It is now recorded as an event, but nothing requires a person to confirm recovery. That belongs to the notification and acknowledgement lifecycle.
* **Notification of an approved escalation.** `ClinicalGovernanceSystem` still only returns a decision; it alerts a person only on governance failure. A severity increase now reaches the governance path, but delivery, receipt and acknowledgement are separate.
* **A hard rule that fusion dilutes** (the engine does not enter the bypass when the fused regime is below WARNING). Separate defect, separate change.
* **PERCEIVE rate limits** (advisory by default) are untouched. A severity increase to CRITICAL takes the emergency path, which is not rate-limited.
