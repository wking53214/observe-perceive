# The escalation sweep runner and its watchdog

Status: implemented in `escalation_runner.py`, required by `profile="production"` (PR 9). Evidence class: EXECUTED by
`test_escalation_runner.py` (49 tests, including real threads) and `test_production_profile.py`, and a mutation check of 44
changes with none surviving. Whether the intervals suit a real unit is UNKNOWN until a hospital sets and drills them.

## The gap

`EscalationDesk` has no thread or timer. Its follow-up (a page nobody answers moves to the next tier; a ticket nobody could be
reached for is retried; a ticket stranded by a crash is picked up) happens only when something calls `sweep()`. Until now nothing
did, and nothing noticed that nothing did. A page could go unanswered for as long as the process ran, with every record
looking fine.

## What it is

**The sweeper** calls `desk.sweep()` every `sweep_interval_s`. An error in one sweep is logged and counted, and the next sweep still
runs (EXECUTED: `test_one_failing_sweep_does_not_stop_the_sweeper`).

**The watchdog** runs on its own thread every `watch_interval_s` and alarms through a channel the deployer gives it, separate from
the desk's own channels, because those may be the thing that broke:

| Alarm | Meaning |
|---|---|
| `sweep_stale` | No **clean** sweep for `max_silence_s`: a hung sweep, a sweep failing on a ticket, none yet since start, or a desk that does not answer within `alarm_timeout_s` (for example one stuck on a hung disk write). Repeated every `max_silence_s` while it continues. |
| `sweep_recovered` | The sweep is clean again. |
| `sweeper_dead` | The sweeper thread had stopped. It is restarted and the alarm says so. |
| `tickets_exhausted` | A ticket nobody could be reached for or nobody answered, once per ticket. This is the alarm of last resort: the desk's own channels already failed. |
| `log_failures` | The desk could not write its log (pages still went out), each time the count rises. |

Two things the first draft got wrong and the tests now pin:

- **The watchdog cannot hang on the desk.** The desk's lock is held across its log writes, so a hung disk would have hung a
  watchdog that read the desk directly, and a silent watchdog is its worst failure. The read runs on a thread with a timeout. A desk
  that does not answer counts as no sweep at all (`desk_unresponsive: true`), and a probe that is still stuck is not stacked up.
- **A lost alarm is retried.** An alarm that cannot be delivered, or whose channel hangs (it costs `alarm_timeout_s`, not the
  watchdog), stays pending and is tried again at the next check. It is delivered once.

## No defaults, and settings that cannot work together are refused

Every interval is the deployer's: `sweep_interval_s`, `watch_interval_s`, `max_silence_s`, `alarm_timeout_s`. A zero, negative, boolean
or non-finite value is a problem. So is a `max_silence_s` that a healthy sweep could exceed: it must be longer than
`sweep_interval_s` plus the desk's worst-case dispatch time (`desk.max_dispatch_seconds()`), or the system would alarm while healthy.
`start()` refuses to start with any problem.

## The production profile

`profile="production"` now needs `sweep_runner=`: a **running** `SweepRunner`, with no problems, **bound to the same desk** the
orchestrator pages through (a runner for another desk would leave the desk that pages never swept). Start it before building the
orchestrator.

## What it cannot do (stated, not hidden)

- **It cannot prove the alarm channel reaches a person.** `alarm` is a callable; "delivered" means it returned. Point it at something
  that is watched (an on-call tool, an operations desk), and drill it.
- **It cannot watch itself.** If the watchdog thread dies, nothing notices. `status()["running"]` and the profile check at start-up
  say so, and an external liveness check on the process is the deployer's. This is why the watchdog checks the sweeper but nothing
  checks the watchdog.
- **It lives in the process.** If the process dies, so do both threads. A separate supervisor is needed for that and is not here.
- **Wall clock.** The desk's deadlines use the wall clock; the runner's own timings use a monotonic clock.
- **The numbers.** The intervals are not provided. A reasonable starting point is a sweep interval well under the shortest tier
  deadline (otherwise escalation is only as prompt as the sweep), a silence limit a few sweeps long, and an alarm timeout shorter
  than the watch interval.
