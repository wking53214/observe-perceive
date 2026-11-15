"""test_escalation_runner.py -- something calls sweep(), and something notices when it stops.

The desk has no timer of its own. These tests drive the watchdog by hand with an injected clock where timing matters, and use
real threads with tiny intervals to prove the sweeper and the watchdog actually run.
"""
import threading
import time

import pytest

from conservation_kernel import HmacSigner
from escalation_runner import SweepRunner
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier

NURSE = HmacSigner(b"runner-test-responder-key-0123456789ab", key_id="nurse")


class Clock:
    """The desk's wall clock, advanced by hand."""
    def __init__(self):
        from datetime import datetime, timezone
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        from datetime import timedelta
        self.now += timedelta(seconds=seconds)


class Pager:
    def __init__(self, fail=False):
        self.calls, self.fail = [], fail

    def __call__(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise ConnectionError("pager down")


def make_desk(dispatch_timeout=0.05, fail=False, clock=None):
    pager = Pager(fail)
    clock = clock or Clock()
    desk = EscalationDesk(
        NotificationPolicy((Tier("charge", pager, ("nurse",), 60.0, dispatch_timeout),
                            Tier("physician", Pager(), ("nurse",), 120.0, dispatch_timeout))),
        EscalationLog(), responder_keys={"nurse": NURSE}, clock=clock)
    return desk, clock, pager


class Alarms:
    def __init__(self):
        self.got, self.fail = [], False

    def __call__(self, payload):
        if self.fail:
            raise ConnectionError("alarm channel down")
        self.got.append(payload)

    def kinds(self):
        return [a["kind"] for a in self.got]


class Mono:
    """The runner's monotonic clock, advanced by hand."""
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def runner_for(desk, alarms=None, mono=None, **override):
    parts = dict(sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200, alarm_timeout_s=0.5,
                 max_tickets_per_sweep=1, alarm=alarms if alarms is not None else Alarms(), clock=mono or Mono())
    parts.update(override)
    return SweepRunner(desk, **parts)


@pytest.fixture
def started():
    made = []

    def make(desk, **kw):
        runner = runner_for(desk, **kw)
        made.append(runner)
        return runner.start()
    yield make
    for r in made:
        r.stop(timeout=2)


def wait_for(predicate, seconds=3.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


# ---- configuration: no defaults, and settings that cannot work together are refused --------------------------------

def test_no_timing_has_a_default():
    desk, *_ = make_desk()
    with pytest.raises(TypeError):
        SweepRunner(desk, alarm=lambda p: None)                            # type: ignore[call-arg]
    with pytest.raises(TypeError):
        SweepRunner(desk, sweep_interval_s=1, watch_interval_s=1, max_silence_s=10, alarm=lambda p: None)  # type: ignore[call-arg]


@pytest.mark.parametrize("name", ["sweep_interval_s", "watch_interval_s", "max_silence_s", "alarm_timeout_s"])
@pytest.mark.parametrize("bad", [0, -1, float("nan"), float("inf"), True, None, "5"])
def test_an_unusable_interval_is_a_problem_and_the_runner_refuses_to_start(name, bad):
    desk, *_ = make_desk()
    runner = runner_for(desk, **{name: bad})
    assert any(name in p for p in runner.problems())
    with pytest.raises(ValueError, match="cannot start"):
        runner.start()
    assert not runner.running


def test_no_alarm_channel_is_a_problem():
    desk, *_ = make_desk()
    assert any("no alarm channel" in p for p in runner_for(desk, alarm=None).problems())


def test_a_silence_limit_a_healthy_sweep_could_exceed_is_a_problem():
    desk, *_ = make_desk(dispatch_timeout=5.0)                            # one ticket can take up to 10s of paging
    assert desk.max_dispatch_seconds() == 10.0
    assert runner_for(desk, sweep_interval_s=30, max_silence_s=40).problems()          # 40 <= 30 + 1 * 10
    assert not runner_for(desk, sweep_interval_s=30, max_silence_s=41).problems()


def test_the_silence_limit_is_checked_against_how_many_tickets_one_sweep_may_have_to_page():
    """A sweep pages its tickets one after another, so ten overdue tickets with slow pagers take ten times as long."""
    desk, *_ = make_desk(dispatch_timeout=5.0)
    assert runner_for(desk, sweep_interval_s=30, max_silence_s=100, max_tickets_per_sweep=10).problems()   # 30 + 10 * 10
    assert not runner_for(desk, sweep_interval_s=30, max_silence_s=131, max_tickets_per_sweep=10).problems()


@pytest.mark.parametrize("bad", [0, -1, 2.5, True, None, "10"])
def test_max_tickets_per_sweep_has_no_default_and_must_be_a_whole_number(bad):
    desk, *_ = make_desk()
    assert any("max_tickets_per_sweep" in p for p in runner_for(desk, max_tickets_per_sweep=bad).problems())
    with pytest.raises(TypeError):
        SweepRunner(desk, sweep_interval_s=1, watch_interval_s=1, max_silence_s=100, alarm_timeout_s=1,  # type: ignore[call-arg]
                    alarm=lambda p: None)


def test_a_runner_with_no_desk_is_a_problem_not_a_crash():
    runner = runner_for(None)                                               # type: ignore[arg-type]
    assert any("no escalation desk" in p for p in runner.problems())


# ---- the sweeper ----------------------------------------------------------------------------------------------------

def test_the_sweeper_really_moves_an_unanswered_ticket_up_without_anyone_calling_sweep(started):
    desk, clock, _ = make_desk()
    ticket_id, _ = desk.raise_and_dispatch(kind="stage_error", request_id="r1")
    clock.advance(61)                                                       # past the first tier's deadline
    started(desk, sweep_interval_s=0.01)
    assert wait_for(lambda: desk.ticket(ticket_id)["tier"] == "physician")


def test_one_failing_sweep_does_not_stop_the_sweeper(started, monkeypatch):
    desk, *_ = make_desk()
    real, calls = desk.sweep, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise OSError("sweep failed")
        return real()
    monkeypatch.setattr(desk, "sweep", flaky)
    runner = started(desk, sweep_interval_s=0.01)
    assert wait_for(lambda: runner.sweeps >= 5)
    assert runner.sweep_errors == 2 and runner.last_error.startswith("OSError")
    assert desk.seconds_since_sweep() is not None                           # the later sweeps were clean


def test_stop_ends_both_threads_and_a_runner_can_be_started_again():
    desk, *_ = make_desk()
    runner = runner_for(desk).start()
    assert runner.running
    runner.stop(timeout=2)
    assert not runner.running and not runner._sweeper.is_alive() and not runner._watchdog.is_alive()
    runner.start()
    assert runner.running
    runner.stop(timeout=2)


def test_a_runner_is_a_context_manager():
    desk, *_ = make_desk()
    with runner_for(desk) as runner:
        assert runner.running
    assert not runner.running


# ---- the watchdog (driven by hand) ----------------------------------------------------------------------------------

def never_sweeping_runner(started, **kw):
    """A runner whose sweeper does nothing, so the desk never records a clean sweep."""
    desk, clock, pager = make_desk()
    desk.sweep = lambda: []
    mono, alarms = Mono(), Alarms()
    return started(desk, alarms=alarms, mono=mono, **kw), desk, clock, mono, alarms, pager


def test_a_desk_that_has_never_been_swept_alarms_once_the_silence_limit_passes(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    assert runner.check() == []                                             # just started: not yet silent
    mono.t += 7199
    assert runner.check() == []
    mono.t += 1
    delivered = runner.check()
    assert [a["kind"] for a in delivered] == ["sweep_stale"] and delivered[0]["continuing"] is False
    assert delivered[0]["desk_unresponsive"] is False


def test_a_sweep_that_stopped_clean_alarms_with_how_long_and_does_not_repeat_until_the_limit_passes_again(started):
    desk, clock, _ = make_desk()
    mono, alarms = Mono(), Alarms()
    runner = started(desk, alarms=alarms, mono=mono)
    assert wait_for(lambda: desk.seconds_since_sweep() is not None)         # the sweeper's first, clean sweep
    desk.sweep = lambda: []                                                  # then the sweeping stops
    clock.advance(7300)
    first = runner.check()
    assert [a["kind"] for a in first] == ["sweep_stale"] and first[0]["seconds_since_clean_sweep"] >= 7300
    assert runner.check() == []                                             # deduplicated
    mono.t += 7199
    assert runner.check() == []
    mono.t += 1
    again = runner.check()
    assert [a["kind"] for a in again] == ["sweep_stale"] and again[0]["continuing"] is True


def test_a_recovery_is_announced_once_the_sweep_is_clean_again(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    mono.t += 7200
    runner.check()
    assert alarms.kinds() == ["sweep_stale"]
    desk.__dict__.pop("sweep")                                              # the real sweep is back
    desk.sweep()
    assert [a["kind"] for a in runner.check()] == ["sweep_recovered"]
    assert runner.check() == [] and runner.status()["alarmed"] is False


class Die(BaseException):
    """What kills a thread outright: not an Exception, so the sweep loop's own handler does not catch it."""


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")   # the thread is meant to die
def test_a_dead_sweeper_thread_is_restarted_and_alarmed_with_the_reason(started, monkeypatch):
    desk, *_ = make_desk()
    alarms = Alarms()
    real, calls = desk.sweep, {"n": 0}

    def dies_once():
        calls["n"] += 1
        if calls["n"] == 1:
            raise Die("the sweeper was killed")
        return real()
    monkeypatch.setattr(desk, "sweep", dies_once)
    runner = started(desk, alarms=alarms)
    first = runner._sweeper
    first.join(2)
    assert not first.is_alive()                                             # it really died
    out = runner.check()
    assert [a["kind"] for a in out] == ["sweeper_dead"] and out[0]["last_error"].startswith("Die")
    assert runner._sweeper is not first and runner._sweeper.is_alive() and runner.running
    assert runner.sweep_errors == 1


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")   # the thread is meant to die
def test_a_sweeper_that_dies_at_once_every_time_is_alarmed_once_not_once_per_check(started, monkeypatch):
    desk, *_ = make_desk()
    alarms = Alarms()

    def always_dies():
        raise Die("dies at once")
    monkeypatch.setattr(desk, "sweep", always_dies)
    runner = started(desk, alarms=alarms)
    for _ in range(25):
        runner._sweeper.join(1)
        runner.check()
    assert alarms.kinds().count("sweeper_dead") == 1                        # not 25: the restart is quiet within the limit


def test_a_ticket_nobody_could_be_reached_for_is_alarmed_once_and_a_new_one_is_alarmed_again(started):
    desk, clock, pager = make_desk(fail=True)
    desk.policy = NotificationPolicy((Tier("only", pager, ("nurse",), 60.0, 0.05),))
    alarms = Alarms()
    runner = started(desk, alarms=alarms)
    first, _ = desk.raise_and_dispatch(kind="stage_error", request_id="r1")
    out = runner.check()
    assert [a["kind"] for a in out] == ["tickets_exhausted"] and out[0]["tickets"] == [first]
    assert runner.check() == []                                             # once per ticket
    second, _ = desk.raise_and_dispatch(kind="stage_error", request_id="r2")
    assert [a["tickets"] for a in runner.check()] == [[second]]


def test_the_desks_own_log_failing_is_alarmed_only_when_it_gets_worse(started, monkeypatch):
    desk, *_ = make_desk()
    alarms = Alarms()
    runner = started(desk, alarms=alarms)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(desk.log, "append", broken)
    desk.raise_and_dispatch(kind="stage_error", request_id="r1")
    out = runner.check()
    assert [a["kind"] for a in out] == ["log_failures"] and out[0]["total"] == desk.log_failures
    assert runner.check() == []
    desk.raise_and_dispatch(kind="stage_error", request_id="r2")
    assert [a["kind"] for a in runner.check()] == ["log_failures"]


def test_an_alarm_that_cannot_be_delivered_is_retried_not_forgotten(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    alarms.fail = True
    mono.t += 7200
    assert runner.check() == [] and runner.status()["undelivered"] == 1 and runner.alarms_failed == 1
    alarms.fail = False
    out = runner.check()
    assert [a["kind"] for a in out] == ["sweep_stale"] and runner.status()["undelivered"] == 0
    assert alarms.kinds() == ["sweep_stale"] and runner.check() == []       # delivered exactly once


def test_a_hung_alarm_channel_costs_its_timeout_not_the_watchdog(started):
    release = threading.Event()
    desk, clock, _ = make_desk()
    mono = Mono()
    runner = started(desk, alarm=lambda payload: release.wait(10), mono=mono, alarm_timeout_s=0.2)
    assert wait_for(lambda: desk.seconds_since_sweep() is not None)
    desk.sweep = lambda: []
    clock.advance(8000)                                                     # long past the silence limit
    began = time.monotonic()
    assert runner.check() == []
    assert time.monotonic() - began < 3 and runner.status()["undelivered"] == 1
    release.set()


def test_a_desk_that_does_not_answer_is_itself_an_alarm_and_never_hangs_the_watchdog(started):
    desk, *_ = make_desk()
    alarms, mono = Alarms(), Mono()
    runner = started(desk, alarms=alarms, mono=mono, alarm_timeout_s=0.2)
    held, release = threading.Event(), threading.Event()

    def hold_the_desk_lock():
        with desk._lock:                                                     # as a hung disk write would
            held.set()
            release.wait(10)
    holder = threading.Thread(target=hold_the_desk_lock, daemon=True)
    holder.start()
    held.wait(2)
    began = time.monotonic()
    out = runner.check()
    assert time.monotonic() - began < 3
    assert [a["kind"] for a in out] == ["sweep_stale"] and out[0]["desk_unresponsive"] is True
    assert out[0]["seconds_since_clean_sweep"] is None
    probes = lambda: [t for t in threading.enumerate() if t.name == "escalation-probe" and t.is_alive()]   # noqa: E731
    assert len(probes()) == 1                                               # the one stuck probe
    runner.check()
    runner.check()
    assert len(probes()) == 1                                               # and it is not stacked up
    release.set()
    holder.join(2)


# ---- end to end, on real threads -----------------------------------------------------------------------------------

def test_a_sweep_that_hangs_is_noticed_by_the_watchdog_while_the_sweeper_is_stuck():
    from datetime import datetime, timezone
    desk, *_ = make_desk(dispatch_timeout=0.05, clock=lambda: datetime.now(timezone.utc))      # real time here
    alarms, release = Alarms(), threading.Event()
    runner = SweepRunner(desk, sweep_interval_s=0.02, watch_interval_s=0.02, max_silence_s=0.4, alarm_timeout_s=0.2,
                         max_tickets_per_sweep=1, alarm=alarms)
    assert not runner.problems()
    runner.start()
    try:
        assert wait_for(lambda: desk.seconds_since_sweep() is not None)      # healthy first
        desk.sweep = lambda: release.wait(10)                                # now the sweep hangs
        assert wait_for(lambda: "sweep_stale" in alarms.kinds(), 5)
        desk.__dict__.pop("sweep")
    finally:
        release.set()
        runner.stop(timeout=2)


def test_status_reports_what_the_runner_is_doing(started):
    desk, *_ = make_desk()
    runner = started(desk)
    assert wait_for(lambda: runner.sweeps >= 1)
    status = runner.status()
    assert status["running"] and status["sweeps"] >= 1 and status["sweep_errors"] == 0
    assert status["alarmed"] is False and status["alarms_delivered"] == 0 and status["undelivered"] == 0


# ---- gaps a mutation check found ---------------------------------------------------------------------------------------

def test_running_is_false_the_moment_either_thread_is_gone(started):
    desk, *_ = make_desk()
    runner = started(desk)
    assert runner.running
    gone = threading.Thread(target=lambda: None)
    gone.start()
    gone.join()
    real_sweeper, runner._sweeper = runner._sweeper, gone
    assert not runner.running                                               # a dead sweeper is not "running"
    runner._sweeper = real_sweeper
    real_watchdog, runner._watchdog = runner._watchdog, gone
    assert not runner.running                                               # nor is a dead watchdog
    runner._watchdog = real_watchdog
    assert runner.running


def test_the_watchdog_keeps_watching_after_one_of_its_own_checks_fails(started, monkeypatch):
    desk, *_ = make_desk()
    runner = runner_for(desk, watch_interval_s=0.01)
    real, calls = runner.check, {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] <= 2:
            raise RuntimeError("a bad check")
        return real()
    monkeypatch.setattr(runner, "check", flaky)
    runner.start()
    try:
        assert wait_for(lambda: calls["n"] >= 5)
        assert runner._watchdog.is_alive()
    finally:
        runner.stop(timeout=2)


def test_delivered_alarms_are_counted(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    mono.t += 7200
    runner.check()
    assert runner.status()["alarms_delivered"] == 1 and runner.alarms_delivered == 1


# ---- the review of this module ------------------------------------------------------------------------------------------

def alarm_threads():
    return [t for t in threading.enumerate() if t.name == "escalation-alarm" and t.is_alive()]


def test_a_hung_alarm_channel_costs_one_thread_however_many_checks_run(started):
    release = threading.Event()
    desk, clock, _ = make_desk()
    mono = Mono()
    runner = started(desk, alarm=lambda payload: release.wait(10), mono=mono, alarm_timeout_s=0.05)
    assert wait_for(lambda: desk.seconds_since_sweep() is not None)
    desk.sweep = lambda: []
    clock.advance(8000)
    for _ in range(12):
        mono.t += 7200                                                      # stale again every check
        runner.check()
    assert len(alarm_threads()) == 1                                        # one stuck delivery, not one per check
    assert runner.status()["delivery_stuck"] is True and runner.status()["undelivered"] == 1   # and one pending alarm
    release.set()
    assert wait_for(lambda: runner.status()["undelivered"] == 0 and not alarm_threads())


def test_alarms_are_coalesced_per_kind_so_a_long_outage_is_not_a_storm_when_the_channel_returns(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    alarms.fail = True
    for _ in range(10):
        mono.t += 7200
        runner.check()
    assert runner.status()["undelivered"] == 1                              # ten stale alarms are one
    alarms.fail = False
    out = runner.check()
    assert [a["kind"] for a in out] == ["sweep_stale"] and out[0]["coalesced"] == 9
    assert runner.check() == []


def test_exhausted_tickets_reported_during_an_outage_arrive_together_when_the_channel_returns(started):
    desk, clock, pager = make_desk(fail=True)
    desk.policy = NotificationPolicy((Tier("only", pager, ("nurse",), 60.0, 0.05),))
    alarms = Alarms()
    runner = started(desk, alarms=alarms)
    alarms.fail = True
    ids = [desk.raise_and_dispatch(kind="stage_error", request_id=f"r{i}")[0] for i in range(3)]
    for i in ids:
        runner.check()                                                      # each check sees the same ones, reports once
    desk.raise_and_dispatch(kind="stage_error", request_id="late")
    runner.check()
    alarms.fail = False
    out = runner.check()
    assert [a["kind"] for a in out] == ["tickets_exhausted"] and len(out[0]["tickets"]) == 4


def test_a_process_that_cannot_make_threads_neither_loses_alarms_nor_stops_the_watchdog(started, monkeypatch):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    mono.t += 7200
    real_start = threading.Thread.start

    def refuses(self):
        if self.name in ("escalation-alarm", "escalation-probe"):
            raise RuntimeError("can't start new thread")
        return real_start(self)
    monkeypatch.setattr(threading.Thread, "start", refuses)
    assert runner.check() == []                                             # must not raise
    assert runner.check() == []
    assert runner.status()["undelivered"] >= 1                              # the alarm is still waiting
    monkeypatch.setattr(threading.Thread, "start", real_start)
    assert "sweep_stale" in [a["kind"] for a in runner.check()]             # and goes out once threads can be made


def test_start_after_only_the_sweeper_died_does_not_add_a_second_watchdog(started, monkeypatch):
    desk, *_ = make_desk()
    runner = started(desk, sweep_interval_s=3600)
    gone = threading.Thread(target=lambda: None)
    gone.start()
    gone.join()
    runner._sweeper = gone
    assert not runner.running
    runner.start()
    watchdogs = [t for t in threading.enumerate() if t.name == "escalation-watchdog" and t.is_alive()]
    assert len(watchdogs) == 1 and runner.running


def test_a_stuck_sweeper_from_before_a_stop_never_sweeps_beside_the_new_one(monkeypatch):
    desk, *_ = make_desk()
    release, entered = threading.Event(), threading.Event()
    sweeping = []

    def sweeps(lock_name):
        def fn():
            sweeping.append(threading.current_thread().name + lock_name)
            entered.set()
            release.wait(10)
            return []
        return fn
    monkeypatch.setattr(desk, "sweep", sweeps("a"))
    runner = runner_for(desk, sweep_interval_s=0.01).start()
    assert entered.wait(2)
    runner.stop(timeout=0.1)                                                # the sweeper is stuck inside sweep()
    old = runner._sweeper
    assert old.is_alive()
    monkeypatch.setattr(desk, "sweep", sweeps("b"))
    entered.clear()
    runner.start()
    release.set()
    old.join(2)
    assert not old.is_alive()                                               # it saw ITS stop event and ended
    live = [t for t in threading.enumerate() if t.name == "escalation-sweeper" and t.is_alive()]
    assert len(live) == 1 and live[0] is runner._sweeper
    runner.stop(timeout=2)


def test_stop_called_from_inside_an_alarm_callback_does_not_wait_on_itself(started):
    desk, clock, _ = make_desk()
    mono = Mono()
    holder = {}
    runner = started(desk, alarm=lambda payload: holder["runner"].stop(timeout=5), mono=mono, alarm_timeout_s=2)
    holder["runner"] = runner
    assert wait_for(lambda: desk.seconds_since_sweep() is not None)
    desk.sweep = lambda: []
    clock.advance(8000)
    began = time.monotonic()
    out = runner.check()
    assert time.monotonic() - began < 3 and [a["kind"] for a in out] == ["sweep_stale"]
    assert not runner.running


def test_a_failure_while_reading_the_desk_is_logged_not_swallowed(started, caplog):
    desk, *_ = make_desk()
    runner = started(desk)

    def broken():
        raise RuntimeError("the desk is broken")
    desk.open_tickets = broken
    with caplog.at_level("CRITICAL"):
        out = runner.check()
    assert "reading the desk failed" in caplog.text and "the desk is broken" in caplog.text
    assert out == [] or [a["kind"] for a in out] == ["sweep_stale"]


def test_a_check_that_dies_after_deciding_to_alarm_loses_nothing(started, monkeypatch):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    mono.t += 7200
    real = runner._deliver_pending
    monkeypatch.setattr(runner, "_deliver_pending", lambda: (_ for _ in ()).throw(RuntimeError("delivery bug")))
    with pytest.raises(RuntimeError):
        runner.check()
    assert runner.status()["undelivered"] == 1
    monkeypatch.setattr(runner, "_deliver_pending", real)
    assert [a["kind"] for a in runner.check()] == ["sweep_stale"]


# ---- gaps a mutation check found in the second version -----------------------------------------------------------------

def named(name):
    return [t for t in threading.enumerate() if t.name == name and t.is_alive()]


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")   # the thread is meant to die
def test_start_after_only_the_watchdog_died_does_not_add_a_second_sweeper(started, monkeypatch):
    desk, *_ = make_desk()
    runner = started(desk, watch_interval_s=0.01)

    def kills_the_watchdog():
        raise Die("the watchdog was killed")
    monkeypatch.setattr(runner, "check", kills_the_watchdog)
    runner._watchdog.join(2)
    assert not runner._watchdog.is_alive() and runner._sweeper.is_alive()    # only the watchdog is gone
    assert not runner.running
    runner.start()
    assert len(named("escalation-sweeper")) == 1 and len(named("escalation-watchdog")) == 1 and runner.running


def test_stop_called_from_inside_a_sweep_does_not_try_to_join_its_own_thread(monkeypatch):
    desk, *_ = make_desk()
    runner = runner_for(desk, sweep_interval_s=0.01)
    done = threading.Event()
    errors = []

    def sweep_then_stop():
        try:
            runner.stop(timeout=1)
        except BaseException as exc:                                        # noqa: BLE001
            errors.append(exc)
        done.set()
        return []
    monkeypatch.setattr(desk, "sweep", sweep_then_stop)
    runner.start()
    assert done.wait(3) and errors == [] and not runner.running
    runner.stop(timeout=2)


def test_stop_from_an_alarm_during_the_watchdogs_own_check_does_not_wait_for_the_watchdog():
    """The watchdog is inside check() waiting for the delivery thread; if stop() (called by that very alarm) joined the
    watchdog, each would wait on the other until a timeout."""
    from datetime import datetime, timezone
    desk, *_ = make_desk(dispatch_timeout=0.02, clock=lambda: datetime.now(timezone.utc))
    waited = []
    holder = {}

    def alarm(payload):
        began = time.monotonic()
        holder["runner"].stop(timeout=5)
        waited.append(time.monotonic() - began)
    runner = SweepRunner(desk, sweep_interval_s=0.02, watch_interval_s=0.02, max_silence_s=0.3, alarm_timeout_s=2,
                         max_tickets_per_sweep=1, alarm=alarm)
    holder["runner"] = runner
    runner.start()
    try:
        assert wait_for(lambda: desk.seconds_since_sweep() is not None)
        desk.sweep = lambda: []                                              # the sweeping stops, so the watchdog alarms
        assert wait_for(lambda: bool(waited), 5)
        assert waited[0] < 1.0                                               # not the 5s (or 2s) a mutual wait would cost
    finally:
        runner.stop(timeout=2)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_sweeper_that_keeps_dying_is_alarmed_again_after_the_silence_limit(started, monkeypatch):
    desk, *_ = make_desk()
    alarms, mono = Alarms(), Mono()

    def always_dies():
        raise Die("dies at once")
    monkeypatch.setattr(desk, "sweep", always_dies)
    runner = started(desk, alarms=alarms, mono=mono)
    runner._sweeper.join(1)
    runner.check()
    runner._sweeper.join(1)
    runner.check()
    assert alarms.kinds().count("sweeper_dead") == 1
    mono.t += 7200                                                           # the silence limit has passed
    runner._sweeper.join(1)
    runner.check()
    assert alarms.kinds().count("sweeper_dead") == 2


def test_a_ticket_that_leaves_the_exhausted_state_and_comes_back_is_reported_again(started, monkeypatch):
    desk, clock, pager = make_desk(fail=True)
    desk.policy = NotificationPolicy((Tier("only", pager, ("nurse",), 60.0, 0.05),))
    alarms = Alarms()
    runner = started(desk, alarms=alarms)
    ticket_id, _ = desk.raise_and_dispatch(kind="stage_error", request_id="r1")
    assert [a["kind"] for a in runner.check()] == ["tickets_exhausted"]
    real_state = desk.state
    monkeypatch.setattr(desk, "state", lambda t: "dispatched")               # reached after all (a retry succeeded)
    assert runner.check() == []
    monkeypatch.setattr(desk, "state", real_state)                           # and exhausted again later
    again = runner.check()
    assert [a["kind"] for a in again] == ["tickets_exhausted"] and again[0]["tickets"] == [ticket_id]


def test_an_alarm_updated_while_it_is_being_delivered_is_not_lost(started):
    runner, desk, clock, mono, alarms, _ = never_sweeping_runner(started)
    mono.t += 7200
    first = {"done": False}

    def alarm(payload):
        if not first["done"]:
            first["done"] = True
            with runner._lock:                                               # the situation changed while it was sent
                runner._merge({"kind": "sweep_stale", "seconds_since_clean_sweep": 99999, "continuing": True})
        alarms.got.append(payload)
    runner.alarm = alarm
    out = runner.check()
    assert [a["kind"] for a in out] == ["sweep_stale", "sweep_stale"]        # the first, then the newer one, in one batch
    assert alarms.got[-1]["seconds_since_clean_sweep"] == 99999 and runner.status()["undelivered"] == 0
