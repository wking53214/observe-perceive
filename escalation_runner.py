"""escalation_runner.py -- something calls `EscalationDesk.sweep()`, and something notices when it stops.

The desk has no thread or timer of its own. Without a caller, a page nobody answers never moves to the next tier and an
exhausted ticket stays silent. This module is that caller (the sweeper) and the thing that watches it (the watchdog), and it
alarms through a channel of its own, because the desk's own channels may be the thing that broke.

  sweeper    calls `desk.sweep()` every `sweep_interval_s`. An error in one sweep is logged and the next one still runs.
  watchdog   every `watch_interval_s` asks, and alarms when the answer is bad:
               sweep_stale          no CLEAN sweep for `max_silence_s` (a hung sweep, a sweep failing on a ticket, none yet, or a
                                    desk that does not answer within `alarm_timeout_s`, for example one stuck on a hung disk)
               sweeper_dead         the sweeper thread is gone (it is restarted; the alarm says why if it can)
               tickets_exhausted    a ticket nobody could be reached for or who never answered (once per ticket)
               log_failures         the desk could not write its log (pages still went out)
               sweep_recovered      the sweep is clean again after a sweep_stale

Nothing here has a default timing: every interval is the deployer's, and `problems()` says when they cannot work together (a
silence limit shorter than a sweep can legitimately take would alarm on a healthy system, and an alarm that cries wolf is ignored).

Alarms are kept until delivered. They are coalesced (one pending alarm per kind, so a long outage is one alarm and not a storm when
the channel returns), delivered by ONE delivery thread at a time (a hung channel costs one thread, not one per check), and a failed
delivery stops the batch and is retried at the next check. See docs/race/ESCALATION_SWEEP_RUNNER.md.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from typing import Any, Callable, Dict, List, Optional, Set

__all__ = ["SweepRunner"]

logger = logging.getLogger(__name__)


def _positive_finite(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0


def _positive_int(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, int) and value > 0


class SweepRunner:
    def __init__(self, desk: Any, *, sweep_interval_s: float, watch_interval_s: float, max_silence_s: float,
                 alarm_timeout_s: float, max_tickets_per_sweep: int, alarm: Callable[[Dict[str, Any]], Any],
                 clock: Callable[[], float] = time.monotonic,
                 heartbeat: Optional[Callable[[Dict[str, Any]], Any]] = None) -> None:
        self.desk = desk
        #: Called with `status()` after every watchdog pass, so something OUTSIDE this process can notice when the
        #: beats stop (heartbeat.HeartbeatWriter and check_heartbeat). None writes nothing, as before.
        self.heartbeat = heartbeat
        self.sweep_interval_s = sweep_interval_s
        self.watch_interval_s = watch_interval_s
        self.max_silence_s = max_silence_s
        self.alarm_timeout_s = alarm_timeout_s
        #: The most tickets one sweep may have to page (including ones nobody can be reached for, which are retried every
        #: sweep). The deployer's estimate; `max_silence_s` is checked against it so a healthy busy sweep does not alarm.
        self.max_tickets_per_sweep = max_tickets_per_sweep
        self.alarm = alarm
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._sweeper: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._probe_thread: Optional[threading.Thread] = None
        self._delivery_thread: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._alarmed_since: Optional[float] = None          # when sweep_stale was last raised, None while healthy
        self._dead_alarmed_at: Optional[float] = None        # when sweeper_dead was last raised
        self._reported_exhausted: Set[str] = set()
        self._logged_failures = 0
        self._pending: Dict[str, Dict[str, Any]] = {}         # one undelivered alarm per kind, retried each check
        self.sweeps = 0                                       # sweeps that finished (clean or not)
        self.sweep_errors = 0
        self.last_error: Optional[str] = None
        self.alarms_delivered = 0
        self.alarms_failed = 0

    # -- what the production profile asks --
    def problems(self) -> List[str]:
        out: List[str] = []
        for name in ("sweep_interval_s", "watch_interval_s", "max_silence_s", "alarm_timeout_s"):
            if not _positive_finite(getattr(self, name)):
                out.append(f"{name} must be a positive finite number of seconds (there is no default)")
        if not _positive_int(self.max_tickets_per_sweep):
            out.append("max_tickets_per_sweep must be a whole number, 1 or more (there is no default)")
        if not callable(self.alarm):
            out.append("no alarm channel: a stopped sweep would alarm nobody")
        dispatch = getattr(self.desk, "max_dispatch_seconds", None)
        if not callable(dispatch):
            out.append("the runner has no escalation desk to sweep")
        if not out:
            # One sweep pages its tickets one after another, so its worst case is every ticket hanging for the desk's
            # longest dispatch. A silence limit inside that would raise the alarm on a healthy, busy system.
            slowest_sweep = self.sweep_interval_s + self.max_tickets_per_sweep * dispatch()
            if self.max_silence_s <= slowest_sweep:
                out.append(f"max_silence_s ({self.max_silence_s}) must exceed sweep_interval_s plus max_tickets_per_sweep "
                           f"({self.max_tickets_per_sweep}) times the desk's worst-case dispatch time "
                           f"({slowest_sweep:g}s in all), or a healthy busy sweep would raise the alarm")
        return out

    @property
    def running(self) -> bool:
        return bool(self._sweeper and self._sweeper.is_alive() and self._watchdog and self._watchdog.is_alive()
                    and not self._stop.is_set())

    def status(self) -> Dict[str, Any]:
        return {"running": self.running, "sweeps": self.sweeps, "sweep_errors": self.sweep_errors,
                "last_error": self.last_error, "seconds_since_clean_sweep": self._try(self.desk.seconds_since_sweep),
                "alarmed": self._alarmed_since is not None, "alarms_delivered": self.alarms_delivered,
                "alarms_failed": self.alarms_failed, "undelivered": len(self._pending),
                "delivery_stuck": bool(self._delivery_thread and self._delivery_thread.is_alive())}

    @staticmethod
    def _try(fn: Callable[[], Any]) -> Any:
        try:
            return fn()
        except Exception:  # noqa: BLE001 - status is for looking, never for failing
            return None

    # -- running --
    def start(self) -> "SweepRunner":
        bad = self.problems()
        if bad:
            raise ValueError("the sweep runner cannot start: " + "; ".join(bad))
        with self._lock:
            if self.running:
                return self
            if self._stop.is_set():                       # after a stop: a fresh run, so a thread still stuck from the old
                self._stop = threading.Event()            # one sees ITS event set and ends, and never sweeps beside the new
                self._sweeper = self._watchdog = None
            if self._sweeper is None and self._watchdog is None:
                self._started_at = self._clock()          # a fresh run; a partial restart keeps the original start
            if self._sweeper is None or not self._sweeper.is_alive():
                self._spawn_sweeper()
            if self._watchdog is None or not self._watchdog.is_alive():
                self._watchdog = threading.Thread(target=self._watch_loop, args=(self._stop,),
                                                  name="escalation-watchdog", daemon=True)
                self._watchdog.start()
        return self

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        here = threading.current_thread()
        for thread in (self._sweeper, self._watchdog):
            if thread is None or thread is here:
                continue
            if thread is self._watchdog and here is self._delivery_thread:
                continue          # called from an alarm callback: the watchdog is waiting on this very thread
            thread.join(timeout)

    def __enter__(self) -> "SweepRunner":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop(timeout=5)

    def _spawn_sweeper(self) -> None:
        self._sweeper = threading.Thread(target=self._sweep_loop, args=(self._stop,), name="escalation-sweeper", daemon=True)
        self._sweeper.start()

    def _sweep_loop(self, stop: threading.Event) -> None:
        try:
            while not stop.is_set():
                self.sweep_once()
                if stop.wait(self.sweep_interval_s):
                    return
        except BaseException as exc:  # noqa: BLE001 - say why the sweeper died, then let it die (the watchdog restarts it)
            self.sweep_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.critical("[SweepRunner] the sweeper thread died (%s)", self.last_error)
            raise

    def sweep_once(self) -> bool:
        """One sweep. An error is recorded and logged, never raised: the next sweep must still run."""
        try:
            self.desk.sweep()
        except Exception as exc:  # noqa: BLE001 - the sweeper outlives any one failure
            self.sweep_errors += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            logger.critical("[SweepRunner] a sweep failed (%s); the next one will still run", self.last_error)
            return False
        finally:
            self.sweeps += 1
        return True

    def _watch_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.watch_interval_s):
            try:
                self.check()
            except Exception as exc:  # noqa: BLE001 - the watchdog must outlive a bad check
                logger.critical("[SweepRunner] the watchdog check failed: %s: %s", type(exc).__name__, exc)

    # -- the watchdog --
    def _probe_desk(self) -> Optional[Dict[str, Any]]:
        """Read what the watchdog needs from the desk on a thread of its own. The desk's lock is held across its log writes,
        so a hung disk could otherwise hang the watchdog too, and a silent watchdog is the worst failure it has. None means
        the desk did not answer in `alarm_timeout_s` (or the last probe is still stuck, or no thread could be made)."""
        with self._lock:
            if self._probe_thread is not None and self._probe_thread.is_alive():
                return None
            box: Dict[str, Any] = {}

            def run() -> None:
                try:
                    tickets = self.desk.open_tickets()
                    box["v"] = {"silence": self.desk.seconds_since_sweep(),
                                "exhausted": [t for t in tickets if self.desk.state(t) == "exhausted"],
                                "failures": getattr(self.desk, "log_failures", 0)}
                except BaseException as exc:  # noqa: BLE001
                    logger.critical("[SweepRunner] reading the desk failed: %s: %s", type(exc).__name__, exc)
                    box["e"] = exc
            worker = threading.Thread(target=run, name="escalation-probe", daemon=True)
            try:
                worker.start()
            except RuntimeError as exc:  # the process cannot make a thread: that is itself worth an alarm
                logger.critical("[SweepRunner] cannot start a probe thread: %s", exc)
                return None
            self._probe_thread = worker
        worker.join(self.alarm_timeout_s)
        return None if worker.is_alive() or "v" not in box else box["v"]

    def _merge(self, alarm: Dict[str, Any]) -> None:
        """Keep ONE pending alarm per kind: a long outage is one alarm, not a storm when the channel comes back."""
        kind = alarm["kind"]
        old = self._pending.get(kind)
        if old is None:
            self._pending[kind] = alarm
            return
        if kind == "tickets_exhausted":
            alarm = dict(alarm, tickets=sorted(set(old["tickets"]) | set(alarm["tickets"])))
        alarm["coalesced"] = old.get("coalesced", 0) + 1
        self._pending[kind] = alarm

    def check(self) -> List[Dict[str, Any]]:
        """One watchdog pass. Returns the alarms DELIVERED by it. Safe to call by hand (and the tests do)."""
        now = self._clock()
        seen = self._probe_desk()
        with self._lock:
            # 1. the sweeper thread itself (restarted at most once per check, and alarmed at most once per silence limit)
            if self._sweeper is not None and not self._sweeper.is_alive() and not self._stop.is_set():
                if self._dead_alarmed_at is None or now - self._dead_alarmed_at >= self.max_silence_s:
                    self._merge({"kind": "sweeper_dead", "last_error": self.last_error,
                                 "detail": "the sweeper thread had stopped; it was restarted"})
                    self._dead_alarmed_at = now
                self._spawn_sweeper()
            # 2. a clean sweep recently enough (an unresponsive desk counts as no sweep at all)
            if seen is None:
                silence = math.inf
            elif seen["silence"] is None:
                silence = now - (self._started_at if self._started_at is not None else now)
            else:
                silence = seen["silence"]
            stale = silence >= self.max_silence_s
            if stale and (self._alarmed_since is None or now - self._alarmed_since >= self.max_silence_s):
                self._merge({"kind": "sweep_stale",
                             "seconds_since_clean_sweep": None if math.isinf(silence) else round(silence, 3),
                             "desk_unresponsive": seen is None, "max_silence_s": self.max_silence_s,
                             "last_error": self.last_error, "continuing": self._alarmed_since is not None})
                self._alarmed_since = now
            elif not stale and self._alarmed_since is not None:
                self._merge({"kind": "sweep_recovered", "seconds_since_clean_sweep": round(silence, 3)})
                self._alarmed_since = None
            if seen is not None:
                # 3. tickets nobody could be reached for
                exhausted = seen["exhausted"]
                fresh = [t for t in exhausted if t not in self._reported_exhausted]
                if fresh:
                    self._merge({"kind": "tickets_exhausted", "tickets": fresh,
                                 "detail": "no tier could be reached or answered; a person must recover these"})
                    self._reported_exhausted.update(fresh)
                self._reported_exhausted &= set(exhausted)    # a ticket that left the state may be reported again
                # 4. the desk's own log
                if seen["failures"] > self._logged_failures:
                    self._merge({"kind": "log_failures", "total": seen["failures"],
                                 "detail": "the escalation log could not be written; pages still went out"})
                    self._logged_failures = seen["failures"]
        delivered = self._deliver_pending()
        if self.heartbeat is not None:
            try:
                self.heartbeat(self.status())
            except Exception as exc:  # noqa: BLE001 - a missed beat is silence, which the outside checker sees
                logger.critical("[SweepRunner] the heartbeat could not be written: %s: %s", type(exc).__name__, exc)
        return delivered

    def _deliver_pending(self) -> List[Dict[str, Any]]:
        """Deliver what is pending on ONE delivery thread. If the last one is still stuck in the channel, do nothing: the
        alarms stay pending (bounded: one per kind) and a hung channel costs one thread, not one per check. A failure stops
        the batch (the channel is down; do not hammer it) and everything undelivered is retried at the next check."""
        delivered: List[Dict[str, Any]] = []
        with self._lock:
            if not self._pending or (self._delivery_thread is not None and self._delivery_thread.is_alive()):
                return []

            def run() -> None:
                while True:
                    with self._lock:
                        if not self._pending:
                            return
                        kind = next(iter(self._pending))
                        payload = dict(self._pending[kind])
                    try:
                        self.alarm(dict(payload))
                    except BaseException as exc:  # noqa: BLE001
                        with self._lock:
                            self.alarms_failed += 1
                        logger.critical("[SweepRunner] ALARM NOT DELIVERED (%s): %s: %s; it will be retried",
                                        kind, type(exc).__name__, exc)
                        return
                    with self._lock:
                        if self._pending.get(kind) == payload:   # not replaced by a newer one while it was being sent
                            del self._pending[kind]
                        self.alarms_delivered += 1
                        delivered.append(payload)
            worker = threading.Thread(target=run, name="escalation-alarm", daemon=True)
            try:
                worker.start()
            except RuntimeError as exc:                        # no thread: the alarms stay pending, and that is logged
                self.alarms_failed += 1
                logger.critical("[SweepRunner] cannot start the alarm thread: %s; %d alarm(s) pending", exc,
                                len(self._pending))
                return []
            self._delivery_thread = worker
        worker.join(self.alarm_timeout_s)
        if worker.is_alive():
            with self._lock:
                self.alarms_failed += 1
            logger.critical("[SweepRunner] the alarm channel did not answer in %ss; alarms stay pending", self.alarm_timeout_s)
        with self._lock:
            return list(delivered)
