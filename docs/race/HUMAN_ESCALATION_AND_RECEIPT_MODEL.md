# Human escalation and receipt model

Status: implemented in `human_escalation.py` (PR 5). Evidence class: the behaviour below is EXECUTED by
`test_human_escalation.py` (67 tests; a mutation check of the module's safety conditions leaves no surviving mutant
except two argued equivalents, see "How it was checked"). The clinical and operational adequacy of any policy is UNKNOWN
until a hospital configures one and runs a drill.

## The problem this closes

Before this change, "a person was told" meant one thing: the `alert_human` callable returned without raising. The
record said `human_alert: "delivered"`. That proves a function ran. It does not prove a page left the building, that
a phone received it, that a named person saw it, or that anyone took responsibility. A channel that silently drops
messages reported "delivered" every time, and nothing noticed when no one answered.

## What a ticket is

Every event that must reach a person opens a **ticket**. A ticket is an append-only list of events in a hash-chained,
optionally signed log (the same integrity pattern as `ReceiptLog`). While the process runs, the desk keeps the
working state in memory and writes each step to the log; after a restart the state is rebuilt from the log and
anything the log is missing is picked up again by `sweep()` (see rule 5).

| State | What it proves | What it does NOT prove |
|---|---|---|
| `raised` | The need was recorded. | That anyone was contacted. |
| `dispatched` | A channel callable returned without raising. | That the message left, arrived, or was seen. |
| `delivered` | The channel itself returned a delivery receipt id. | That a person read it. It is the channel's claim. |
| `acknowledged` | A named responder, holding a registered key, signed an acknowledgement bound to this ticket. | That the right action was taken. |
| `overdue` | The tier's deadline passed with no acknowledgement. | n/a |
| `exhausted` | Every tier was tried and none could be dispatched or acknowledged in time. | n/a. It is a loud, open state that needs human-governed recovery. |

The orchestrator's `human_alert` field says `delivered` only for the `delivered` state, `dispatched` for a callable
that merely returned, and `failed` when nobody could be reached. It never says anything about acknowledgement: that
lives on the ticket (`escalation_ticket`).

## Rules the code enforces

1. **No manufactured acknowledgement.** The only way to `acknowledged` is `EscalationDesk.acknowledge`, with a
   signature from a responder key registered on the desk, over a digest of the ticket id, the hash of the ticket's
   opening, and the responder id. A channel's return value can reach `delivered` at most. Refused: an unknown ticket,
   an unknown responder, a wrong key, a signature made for another ticket, another log with the same ticket id,
   another responder (even on a shared key), a responder whose tier never actually received the page, a second
   acknowledgement, an acknowledgement before any dispatch. On reload, every recorded acknowledgement is verified
   again, so a hand-edited unsigned log cannot invent one.
2. **No default timing.** `ack_within_s` and `dispatch_timeout_s` have no default. A missing, zero, negative, boolean or
   non-finite value is a problem. The numbers are the hospital's decision.
3. **A failed or hung channel does not wait.** A channel that raises, or does not return within `dispatch_timeout_s`,
   is skipped and the next tier is tried at once, in the same call. If every tier fails the ticket is `exhausted`
   immediately. Channels run on their own thread without the desk lock held, so a hung pager costs its timeout and
   blocks no other ticket and no acknowledgement. A channel gets a copy of the payload and cannot alter the record.
4. **A log that cannot be written never stops a page.** If appending to the log fails, the page is still sent, the
   ticket is marked `logged: false`, `desk.log_failures` counts it, and CRITICAL is logged. The profile refuses to
   start on a log path it cannot write.
5. **Nothing stays stranded.** `sweep()` also picks up a ticket left in `raised` (crash right after opening) or
   `overdue` (crash between tiers), and retries a ticket that nobody was ever reached for (a channel may have come
   back), logging `exhausted` once. One ticket failing never skips the others, and `seconds_since_sweep()` only
   advances after a sweep that finished without an error, so a watchdog on it catches a broken sweep.
6. **Overdue escalates, it does not go quiet.** A ticket past its deadline moves up a tier; past the last tier it is
   `exhausted` and stays in `open_tickets()`. The price of rules 4 and 5 is that a page can be sent twice. That is
   chosen over a page not sent.
7. **One writer.** A second process opening the same log is refused (POSIX advisory lock). A torn final line from a
   crash is moved to `<log>.torn` and the log resumes; damage anywhere else is an error, not a repair.
8. **The ticket carries identifiers, not clinical content.** The payload to a channel holds ticket id, the opening hash
   a responder needs to sign, kind, stage, error text, request id and artifact id. It adds nothing about a patient.

## Wiring

`GovernanceOrchestrator(escalations=EscalationDesk(...))` opens a ticket for every stage crash, lost receipt and
failed execution that previously called `alert_human`. Passing both is refused, so there is never doubt which channel
tells a person. A desk that itself raises yields `human_alert: "failed"` and never alters the verdict.

`profile="production"` requires a desk whose policy is valid, whose responders all have keys that can verify, and whose
log has a signer and a path that can be written. A bare `alert_human` beside a desk is refused.

## How it was checked

- EXECUTED: 67 tests, including a reproduction of each defect an independent Opus review found (log failure silencing
  the page, tickets stranded in `raised` or `overdue`, a failed sweep looking fresh, a hung channel holding the lock,
  an unkeyed responder passing the profile, two writers on one log, a torn tail).
- EXECUTED: a mutation check of 35 changes to the module's safety conditions. All are caught except two arguments for
  equivalence: the ticket id inside the signed digest (it is already inside the opening hash, which is unique per
  ticket), and `fsync` (not portably observable).
- NOT done: a real paging vendor, real responder devices, a drill, multi-process behaviour beyond the writer lock.

## Residual risks and open decisions

- **Nothing calls `sweep()` for you.** If the scheduler dies, deadlines pass unnoticed. Alarm on
  `desk.seconds_since_sweep()`. Decision needed: who owns the scheduler and the watchdog.
- **The deadline uses the wall clock.** A clock step can delay or bring forward an escalation. A monotonic clock is a
  later hardening.
- **Duplicate pages are possible** after a log failure, a timeout that actually delivered late, or a restart.
- **Channel `delivered` is the channel's claim.** A paging vendor's delivery receipt is stronger evidence than a callable
  returning, and weaker than an acknowledgement.
- **HMAC responder keys are shared secrets.** A responder's device and the desk hold the same key. Asymmetric
  responder keys are a later hardening. The profile cannot prove a real person holds each key.
- **A ticket exhausted after delivery has no resolve API.** A late acknowledgement is accepted; closing a ticket as
  "handled outside the system" is a human-governed recovery path that does not exist yet.
- **The numbers are not provided.** Tiers, deadlines, timeouts and responders are a clinical-operations decision.
- **POSIX only for the single-writer lock.** Elsewhere it is skipped.
- **Clinical escalations (a CRITICAL regime) do not open a ticket yet.** This PR covers stage errors, receipt loss and
  execution failure. A `ClinicalGovernanceSystem` hook is the next intervention.
