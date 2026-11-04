# Escalation hazard analysis

Qualitative, per the repository's method note: severity is the worst credible harm; likelihood is not estimated because no field data exists. Residual risk is undetermined until the validation evidence exists.

| ID | Hazard | Cause | Severity | Mitigation in this change | Residual and what is still needed |
|---|---|---|---|---|---|
| E-1 | Worsening patient reported at the old severity | Lock returned early for any candidate | Catastrophic | Higher candidates bypass the lock and act on the first reading (policy tests, mutants killed) | None known. A first escalation from below still needs dwell |
| E-2 | Regime changes but no event | Bypass computed the flag from STABLE or CAUTION only | Catastrophic | Any move up is an escalation (engine and governed-path tests, mutant killed) | None known in the engine. Delivery to a human is separate |
| E-3 | Critical state lowered by a weaker later reading | Bypass overwrote the tracked regime | Catastrophic | Maximum of candidate and tracked regime (mutant killed after the first test proved vacuous) | None known |
| E-4 | Two escalations indistinguishable | No event kind or previous regime | Serious | `escalation_kind`, `previous_regime`, distinct audit hash per reading | The notification layer must carry the kind to the recipient |
| E-5 | Alert storm | Removing the lock; no dwell on a worsening | Serious | Same-severity repeats still suppressed; a worsening re-arms the lock; flapping candidates do not re-escalate (tests) | One noisy higher reading raises one event. Alert burden unmeasured |
| E-6 | Recovery inferred without a human | Automatic de-escalation after dwell | Serious | Recorded as `de_escalation` | A human must close the episode: not implemented |
| E-7 | Missing data downgrades a critical state | Unassessable reading | Catastrophic | Existing frozen-policy floor, now tested over repeated missing readings | None known |
| E-8 | Governance failure hides a severity increase | PERCEIVE crash | Catastrophic | Governance status `UNAVAILABLE` with an alert attempt; the escalation stands (test) | Alert delivery is a callback |
| E-9 | Escalation never reaches a person | No notification lifecycle | Catastrophic | Not addressed | The human escalation and receipt model |

## Adversarial retest (executed)

| Scenario | Test | Result |
|---|---|---|
| WARNING then CRITICAL during cooldown | `test_warning_then_critical_during_the_cooldown_...` and the dwell variant | New escalation, regime CRITICAL |
| WARNING then CRITICAL with a hard-rule bypass | `test_warning_then_critical_by_bypass_...`, governed-path test | Event raised, kind `severity_increase`, previous regime `warning`, distinct audit hash, `escalate_approved` |
| WARNING then CRITICAL without a bypass | Policy-level tests only | Not reachable by the engine on any swept input; reachable at the policy and fixed there: escalates on the first reading |
| Repeated WARNING, repeated CRITICAL | two tests | One event each |
| CRITICAL then WARNING (lower candidate or lower bypass) | policy test, bypass test | Stays CRITICAL, no event |
| CRITICAL then recovery | recovery test | `de_escalation` recorded, not an escalation event, STABLE after dwell |
| Missing data during CRITICAL | missing-data test | Stays CRITICAL, no event |
| Conflicting signals during cooldown | conflicting-signals test | No downgrade, no repeat event |
| Notification delivery failure for a severity increase | failed-alert test | Escalation stands, `human_alert="failed"` recorded |
| Receipt not confirmed, acknowledgement without resolution | none | **Not covered**: no such concepts exist yet |
