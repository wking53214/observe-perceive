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
                 alarm=alarms if alarms is not None else Alarms(), clock=mono or Mono())
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
    desk, *_ = make_desk(dispatch_timeout=5.0)                            # a sweep can take up to 10s of paging
    assert desk.max_dispatch_seconds() == 10.0
    assert runner_for(desk, sweep_interval_s=30, max_silence_s=40).problems()          # 40 <= 30 + 10
    assert not runner_for(desk, sweep_interval_s=30, max_silence_s=41).problems()


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


def test_a_dead_sweeper_thread_is_restarted_and_alarmed(started):
    desk, *_ = make_desk()
    alarms = Alarms()
    runner = started(desk, alarms=alarms)
    gone = threading.Thread(target=lambda: None)
    gone.start()
    gone.join()
    runner._sweeper = gone                                                  # a sweeper thread that has died on its own
    assert not runner._sweeper.is_alive()
    assert [a["kind"] for a in runner.check()] == ["sweeper_dead"]
    assert runner._sweeper is not gone and runner._sweeper.is_alive() and runner.running


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
    before = threading.active_count()
    runner.check()
    runner.check()
    assert threading.active_count() <= before + 1                           # a stuck probe is not stacked up
    release.set()
    holder.join(2)


# ---- end to end, on real threads -----------------------------------------------------------------------------------

def test_a_sweep_that_hangs_is_noticed_by_the_watchdog_while_the_sweeper_is_stuck():
    from datetime import datetime, timezone
    desk, *_ = make_desk(dispatch_timeout=0.05, clock=lambda: datetime.now(timezone.utc))      # real time here
    alarms, release = Alarms(), threading.Event()
    runner = SweepRunner(desk, sweep_interval_s=0.02, watch_interval_s=0.02, max_silence_s=0.4, alarm_timeout_s=0.2,
                         alarm=alarms)
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
