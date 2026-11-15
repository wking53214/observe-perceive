"""escalation_runner.py -- something calls `EscalationDesk.sweep()`, and something notices when it stops.

The desk has no thread or timer of its own. Without a caller, a page nobody answers never moves to the next tier and an
exhausted ticket stays silent. This module is that caller (the sweeper) and the thing that watches it (the watchdog), and it
alarms through a channel of its own, because the desk's own channels may be the thing that broke.

  sweeper    calls `desk.sweep()` every `sweep_interval_s`. An error in one sweep is logged and the next one still runs.
  watchdog   every `watch_interval_s` asks, and alarms when the answer is bad:
               sweep_stale          no CLEAN sweep for `max_silence_s` (a hung sweep, a sweep failing on a ticket, none yet, or a
                                    desk that does not answer within `alarm_timeout_s`, for example one stuck on a hung disk)
               sweeper_dead         the sweeper thread is gone (it is restarted and the alarm says so)
               tickets_exhausted    a ticket nobody could be reached for or who never answered (once per ticket)
               log_failures         the desk could not write its log (pages still went out)
               sweep_recovered      the sweep is clean again after a sweep_stale

Nothing here has a default timing: every interval is the deployer's, and `problems()` says when they cannot work together
(a silence limit shorter than a sweep can legitimately take would alarm on a healthy system). An alarm that cannot be
delivered is retried at the next check, not forgotten, and a hung alarm channel costs `alarm_timeout_s`, not the watchdog.
See docs/race/ESCALATION_SWEEP_RUNNER.md.
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


class SweepRunner:
    def __init__(self, desk: Any, *, sweep_interval_s: float, watch_interval_s: float, max_silence_s: float,
                 alarm_timeout_s: float, alarm: Callable[[Dict[str, Any]], Any],
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.desk = desk
        self.sweep_interval_s = sweep_interval_s
        self.watch_interval_s = watch_interval_s
        self.max_silence_s = max_silence_s
        self.alarm_timeout_s = alarm_timeout_s
        self.alarm = alarm
        self._clock = clock
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self._sweeper: Optional[threading.Thread] = None
        self._watchdog: Optional[threading.Thread] = None
        self._started_at: Optional[float] = None
        self._alarmed_since: Optional[float] = None          # when sweep_stale was last raised, None while healthy
        self._reported_exhausted: Set[str] = set()
        self._logged_failures = 0
        self._probe_thread: Optional[threading.Thread] = None
        self._pending: List[Dict[str, Any]] = []              # alarms not yet delivered, retried each check
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
        if not callable(self.alarm):
            out.append("no alarm channel: a stopped sweep would alarm nobody")
        if not out:
            slowest_sweep = self.sweep_interval_s + self.desk.max_dispatch_seconds()
            if self.max_silence_s <= slowest_sweep:
                out.append(f"max_silence_s ({self.max_silence_s}) must exceed sweep_interval_s plus the desk's worst-case "
                           f"dispatch time ({slowest_sweep:g}s), or a healthy sweep would raise the alarm")
        return out

    @property
    def running(self) -> bool:
        return bool(self._sweeper and self._sweeper.is_alive() and self._watchdog and self._watchdog.is_alive()
                    and not self._stop.is_set())

    def status(self) -> Dict[str, Any]:
        return {"running": self.running, "sweeps": self.sweeps, "sweep_errors": self.sweep_errors,
                "last_error": self.last_error, "seconds_since_clean_sweep": self.desk.seconds_since_sweep(),
                "alarmed": self._alarmed_since is not None, "alarms_delivered": self.alarms_delivered,
                "alarms_failed": self.alarms_failed, "undelivered": len(self._pending)}

    # -- running --
    def start(self) -> "SweepRunner":
        bad = self.problems()
        if bad:
            raise ValueError("the sweep runner cannot start: " + "; ".join(bad))
        with self._lock:
            if self.running:
                return self
            self._stop.clear()
            self._started_at = self._clock()
            self._spawn_sweeper()
            self._watchdog = threading.Thread(target=self._watch_loop, name="escalation-watchdog", daemon=True)
            self._watchdog.start()
        return self

    def stop(self, timeout: Optional[float] = None) -> None:
        self._stop.set()
        for thread in (self._sweeper, self._watchdog):
            if thread is not None and thread is not threading.current_thread():
                thread.join(timeout)

    def __enter__(self) -> "SweepRunner":
        return self.start()

    def __exit__(self, *exc: Any) -> None:
        self.stop(timeout=5)

    def _spawn_sweeper(self) -> None:
        self._sweeper = threading.Thread(target=self._sweep_loop, name="escalation-sweeper", daemon=True)
        self._sweeper.start()

    def _sweep_loop(self) -> None:
        while not self._stop.is_set():
            self.sweep_once()
            if self._stop.wait(self.sweep_interval_s):
                return

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

    def _watch_loop(self) -> None:
        while not self._stop.wait(self.watch_interval_s):
            try:
                self.check()
            except Exception as exc:  # noqa: BLE001 - the watchdog must outlive a bad check
                logger.critical("[SweepRunner] the watchdog check failed: %s: %s", type(exc).__name__, exc)

    # -- the watchdog --
    def _probe_desk(self) -> Optional[Dict[str, Any]]:
        """Read what the watchdog needs from the desk on a thread of its own. The desk's lock is held across its log writes,
        so a hung disk could otherwise hang the watchdog too, and a silent watchdog is the worst failure it has. None means
        the desk did not answer in `alarm_timeout_s` (or the last probe is still stuck)."""
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
                    box["e"] = exc
            worker = threading.Thread(target=run, name="escalation-probe", daemon=True)
            self._probe_thread = worker
            worker.start()
        worker.join(self.alarm_timeout_s)
        return None if worker.is_alive() or "v" not in box else box["v"]

    def check(self) -> List[Dict[str, Any]]:
        """One watchdog pass. Returns the alarms DELIVERED by it. Safe to call by hand (and the tests do)."""
        now = self._clock()
        seen = self._probe_desk()
        new: List[Dict[str, Any]] = []
        with self._lock:
            # 1. the sweeper thread itself
            if self._sweeper is not None and not self._sweeper.is_alive() and not self._stop.is_set():
                new.append({"kind": "sweeper_dead", "detail": "the sweeper thread had stopped; it was restarted"})
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
                new.append({"kind": "sweep_stale",
                            "seconds_since_clean_sweep": None if math.isinf(silence) else round(silence, 3),
                            "desk_unresponsive": seen is None, "max_silence_s": self.max_silence_s,
                            "last_error": self.last_error, "continuing": self._alarmed_since is not None})
                self._alarmed_since = now
            elif not stale and self._alarmed_since is not None:
                new.append({"kind": "sweep_recovered", "seconds_since_clean_sweep": round(silence, 3)})
                self._alarmed_since = None
            if seen is not None:
                # 3. tickets nobody could be reached for
                exhausted = seen["exhausted"]
                fresh = [t for t in exhausted if t not in self._reported_exhausted]
                if fresh:
                    new.append({"kind": "tickets_exhausted", "tickets": fresh,
                                "detail": "no tier could be reached or answered; a person must recover these"})
                    self._reported_exhausted.update(fresh)
                self._reported_exhausted &= set(exhausted)    # a ticket that left the state may be reported again
                # 4. the desk's own log
                if seen["failures"] > self._logged_failures:
                    new.append({"kind": "log_failures", "total": seen["failures"],
                                "detail": "the escalation log could not be written; pages still went out"})
                    self._logged_failures = seen["failures"]
            self._pending.extend(new)
            batch, self._pending = self._pending, []
        delivered, failed = [], []
        for alarm in batch:
            (delivered if self._deliver(alarm) else failed).append(alarm)
        if failed:
            with self._lock:
                self._pending = failed + self._pending         # retried at the next check, not forgotten
        return delivered

    def _deliver(self, payload: Dict[str, Any]) -> bool:
        """Deliver one alarm on its own thread so a hung channel costs `alarm_timeout_s`, not the watchdog."""
        box: Dict[str, Any] = {}

        def run() -> None:
            try:
                self.alarm(dict(payload))
                box["ok"] = True
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc
        worker = threading.Thread(target=run, name="escalation-alarm", daemon=True)
        worker.start()
        worker.join(self.alarm_timeout_s)
        if worker.is_alive() or "error" in box or not box.get("ok"):
            self.alarms_failed += 1
            reason = "timed out" if worker.is_alive() else f"{type(box.get('error')).__name__}: {box.get('error')}"
            logger.critical("[SweepRunner] ALARM NOT DELIVERED (%s): %s; it will be retried", payload.get("kind"), reason)
            return False
        self.alarms_delivered += 1
        return True
