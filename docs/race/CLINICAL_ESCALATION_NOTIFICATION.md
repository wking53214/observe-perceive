# Clinical escalation notification

Status: implemented in `clinical_notification.py`, wired into `ClinicalGovernanceSystem` and `GovernanceOrchestrator`
(PR 6). Evidence class: the behaviour below is EXECUTED by `test_clinical_notification.py` (42 tests, 39 mutants of the
rule and its wiring, none surviving). Whether the defaults suit a real unit is UNKNOWN until a clinician reviews them.

## The gap this closes

OBSERVE says an escalation happened. PERCEIVE says whether it is approved. Neither told anyone. Before this change:

- An **approved** CRITICAL returned `escalate_approved` to the caller and nobody was paged unless the caller looked.
- A CRITICAL that governance **blocked** returned `escalate_blocked` and nobody was paged, on either entry point.
  Measured on the default configuration: a first CRITICAL for a patient is blocked unless the request context carries
  `physician_approved`, because the emergency gate (`micropatch`) requires it. So by default the worst events went
  silent. EXECUTED: `test_a_blocked_critical_opens_a_ticket_that_says_why_it_is_blocked`.
- Only "governance crashed" (`UNAVAILABLE`) alerted anyone, and through a callable whose "delivered" meant only that it
  returned (see `HUMAN_ESCALATION_AND_RECEIPT_MODEL.md`).

## The rule

One function, `notification_reason`, decides, and both entry points use it. Only an escalation **event** is ever a
reason (OBSERVE sets `escalation_required` once per new escalation or severity increase, so a patient who stays critical
is not paged on every reading; EXECUTED). The first rule that applies wins:

| Reason | When | Adjustable? |
|---|---|---|
| `governance_unavailable` | an escalation event whose governance verdict does not exist | No: this has always alerted |
| `blocked_escalation` | an escalation event governance blocked | `notify_on_blocked_escalation` (default on) |
| `unassessable_reading` | an escalation event on a reading with a failed channel (a blind spot) | `notify_on_unassessable` (default on) |
| `severity_increase` | an already-escalated patient got worse: a new event | `notify_on_severity_increase` (default on) |
| `regime_at_or_above_threshold` | a new escalation at or above `min_regime` | `min_regime`: `caution`, `warning`, `critical` (default), or `none` |

An escalation whose regime this code does not recognise ranks above every known one, so it is told, not dropped.
`ClinicalNotificationSettings` is validated at construction; a bad value refuses to start.

**The default is quiet below CRITICAL on purpose.** A new WARNING that governance approves opens no ticket unless the
clinician lowers `min_regime`. That limits alarm fatigue and is the clinician's call, not mine. A WARNING governance
blocks still opens one.

## What happens

With an `EscalationDesk` (`escalations=`), the event opens a ticket with the lifecycle from
`HUMAN_ESCALATION_AND_RECEIPT_MODEL.md`: dispatched, delivered, acknowledged, followed up by `sweep()`. The decision (or
the orchestrator record) gains `clinical_notification = {reason, status, ticket}`. `status` is `delivered` only when the
channel returned its own receipt, `dispatched` when a callable merely returned, `failed` when nobody could be reached.
On the orchestrator the field is set before the receipt is written, so the receipt records the notification.
Telling a person never changes an action or an outcome, and a failing desk, channel, or internal error is recorded as
`failed`, not raised (EXECUTED).

Without a desk, `ClinicalGovernanceSystem` keeps its `alert_human` callable and now calls it for a **blocked**
escalation as well as an unavailable one (switchable with `notify_on_blocked_escalation`). An approved escalation reaches
a person only through a desk; the legacy callable's contract ("called when an escalation cannot be governed") is
otherwise unchanged. Giving both a callable and a desk is refused. The orchestrator requires a desk for any notification.

## What the ticket carries

Patient identifier, regime and why (kind, previous regime, risk score, triggered rules, unassessable flag), what
governance said (status, violations), and the OBSERVE and PERCEIVE audit hashes that locate the full record. No raw vitals
(EXECUTED). **The patient identifier is in the ticket log and in the page**, because a page that cannot name the patient
cannot be acted on. Classify and retain that log accordingly; that is an open item for the data-classification document.

## Limitations and open decisions

- **A request refused before OBSERVE ran has no verdict, so no notification.** The orchestrator evaluates a patient's
  vitals only after the action's governance and Conservation checks pass; a request refused earlier never assesses the
  vitals attached to it. Closing this means running OBSERVE on every request that carries vitals, which reorders a
  1,000-line pipeline and is its own change.
- **Whether a CRITICAL may bypass the `micropatch` physician gate is a governance decision, not made here.** This change
  makes a blocked CRITICAL loud. It does not make it unblocked.
- **The legacy callable still does not announce an approved escalation.** Use a desk.
- **Defaults are mine, not a clinician's.** `min_regime=critical` and the three switches are a starting point to be
  confirmed per unit. Alarm-fatigue effects are not measured.
- **Not exercised:** a real paging vendor, real responders, a drill.
