# Human escalation and receipt model

Status: implemented in `human_escalation.py` (PR 5). Evidence class: the behaviour below is EXECUTED by
`test_human_escalation.py`; the clinical and operational adequacy of any policy is UNKNOWN until a hospital
configures one and runs a drill.

## The problem this closes

Before this change, "a person was told" meant one thing: the `alert_human` callable returned without raising. The
record said `human_alert: "delivered"`. That proves a function ran. It does not prove a page left the building, that
a phone received it, that a named person saw it, or that anyone took responsibility. A channel that silently drops
messages reported "delivered" every time, and nothing noticed when no one answered.

## What a ticket is

Every event that must reach a person opens a **ticket**. A ticket is an append-only list of events in a hash-chained,
optionally signed log (the same integrity pattern as `ReceiptLog`). Its state is derived by replaying those events;
nothing holds state that the log does not.

| State | What it proves | What it does NOT prove |
|---|---|---|
| `raised` | The need was recorded. | That anyone was contacted. |
| `dispatched` | A channel callable returned without raising. | That the message left, arrived, or was seen. |
| `delivered` | The channel itself returned a delivery receipt id. | That a person read it. It is the channel's claim. |
| `acknowledged` | A named responder, holding a registered key, signed an acknowledgement bound to this ticket. | That the right action was taken. |
| `overdue` | The tier's deadline passed with no acknowledgement. | n/a |
| `exhausted` | Every tier was tried and none could be dispatched or acknowledged in time. | n/a. It is a loud, open state that needs human-governed recovery. |

## Rules the code enforces

1. **No manufactured acknowledgement.** The only way to reach `acknowledged` is `EscalationDesk.acknowledge`, with a
   signature from a responder key registered on the desk, over a digest that names this ticket and this responder.
   A channel's return value can reach `delivered` at most. An unknown responder, a wrong key, a signature made for
   another ticket, a responder not named on any tier, a second acknowledgement, and an acknowledgement before any
   dispatch are all refused.
2. **No default timing.** A tier's `ack_within_s` has no default. A policy with a missing, zero, negative, boolean or
   non-finite deadline is invalid and the desk reports it as a problem. The numbers are the hospital's decision.
3. **A failed dispatch does not wait.** If a tier's channel raises, the next tier is tried immediately, in the same
   call. If every tier fails the ticket is `exhausted` at once and the caller is told. A person is never left waiting
   on a deadline for a message that was never sent.
4. **Overdue escalates, it does not go quiet.** `EscalationDesk.sweep()` moves every unacknowledged ticket whose
   deadline has passed to the next tier, or to `exhausted` if none is left. The desk has no thread or timer: an
   operator must call `sweep()` from a scheduler. That is a stated residual risk (see below).
5. **Failure never changes the clinical outcome.** Opening, dispatching and logging a ticket never alters a verdict.
   A desk whose log cannot be written still returns `failed` to the caller and logs at CRITICAL.
6. **The ticket carries identifiers, not clinical content.** The payload to a channel holds ticket id, kind, stage,
   error text, request id and artifact id: what the previous alert held. It adds nothing about a patient.

## Wiring

`GovernanceOrchestrator(escalations=EscalationDesk(...))` opens a ticket for every stage crash, lost receipt and
failed execution that previously called `alert_human`. The result keeps `human_alert` ("delivered" here means
dispatched or delivered, "failed", "no_channel") and gains `escalation_ticket`. Passing both `alert_human` and
`escalations` is refused, so there is never doubt about which channel told a person.

`profile="production"` now requires a desk whose policy is valid, whose responders all have keys, and whose log has a
path and a signer. The legacy `alert_human` check is replaced by that requirement.

## Residual risks and open decisions

- **Nothing calls `sweep()` for you.** If the scheduler dies, deadlines pass unnoticed. Mitigation to build later: a
  watchdog that alarms on `desk.seconds_since_sweep`. Decision needed: who owns the scheduler.
- **Channel `delivered` is the channel's claim.** A hospital paging vendor's delivery receipt is stronger evidence
  than a callable returning, and weaker than an acknowledgement.
- **HMAC responder keys are shared secrets.** A responder's device and the desk hold the same key. Asymmetric
  responder keys are a later hardening.
- **The tier, deadline and responder numbers are not provided.** Choosing them is a clinical-operations decision.
- **Clinical escalations (a CRITICAL regime) do not open a ticket yet.** This PR covers stage errors, receipt loss and
  execution failure. A `ClinicalGovernanceSystem` hook is the next intervention.
