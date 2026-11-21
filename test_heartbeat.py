"""The heartbeat: something outside the process notices when it stops, because silence is the alarm."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from conservation_kernel import HmacSigner

from escalation_runner import SweepRunner
from heartbeat import HeartbeatWriter, check_heartbeat, main

KEY = HmacSigner(b"heartbeat-test-key-0123456789abcdef0123", key_id="hb")
OTHER = HmacSigner(b"heartbeat-test-other-key-0123456789abcdef", key_id="hb")
T0 = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
HEALTHY = {"running": True, "alarmed": False, "undelivered": 0, "delivery_stuck": False, "sweeps": 3,
           "sweep_errors": 0, "seconds_since_clean_sweep": 1.0}


def _beat(tmp_path, status=None, at=T0, signer=KEY, label="desk-1"):
    path = tmp_path / "heartbeat.json"
    assert HeartbeatWriter(path, signer, label=label, clock=lambda: at)({**HEALTHY, **(status or {})})
    return path


def test_a_fresh_healthy_beat_is_healthy(tmp_path):
    verdict = check_heartbeat(_beat(tmp_path), KEY, max_age_s=60, label="desk-1", now=T0 + timedelta(seconds=30))
    assert verdict.healthy and "healthy" in verdict.reason


def test_silence_is_the_alarm(tmp_path):
    verdict = check_heartbeat(tmp_path / "heartbeat.json", KEY, max_age_s=60)
    assert not verdict.healthy and "no heartbeat file" in verdict.reason


def test_an_old_beat_is_a_dead_or_hung_process(tmp_path):
    verdict = check_heartbeat(_beat(tmp_path), KEY, max_age_s=60, now=T0 + timedelta(seconds=61))
    assert not verdict.healthy and "dead, hung or paused" in verdict.reason


def test_a_beat_from_the_future_is_a_wrong_clock(tmp_path):
    verdict = check_heartbeat(_beat(tmp_path, at=T0 + timedelta(hours=1)), KEY, max_age_s=60, now=T0)
    assert not verdict.healthy and "future" in verdict.reason


def test_a_forged_or_edited_beat_is_refused(tmp_path):
    assert "signature" in check_heartbeat(_beat(tmp_path, signer=OTHER), KEY, max_age_s=60, now=T0).reason
    path = _beat(tmp_path, status={"running": False})
    beat = json.loads(path.read_text())
    beat["running"] = True                                     # make a dead runner look alive
    path.write_text(json.dumps(beat))
    verdict = check_heartbeat(path, KEY, max_age_s=60, now=T0)
    assert not verdict.healthy and "signature" in verdict.reason


def test_an_old_healthy_beat_cannot_be_replayed_to_look_fresh(tmp_path):
    path = _beat(tmp_path)
    beat = json.loads(path.read_text())
    beat["at"] = (T0 + timedelta(hours=2)).isoformat()
    path.write_text(json.dumps(beat))
    assert "signature" in check_heartbeat(path, KEY, max_age_s=60, now=T0 + timedelta(hours=2)).reason


def test_a_beat_from_another_runner_is_refused(tmp_path):
    verdict = check_heartbeat(_beat(tmp_path, label="desk-2"), KEY, max_age_s=60, label="desk-1", now=T0)
    assert not verdict.healthy and "not 'desk-1'" in verdict.reason


@pytest.mark.parametrize("status,reason", [
    ({"running": False}, "not running"),
    ({"alarmed": True}, "stale sweep"),
    ({"undelivered": 2}, "could not be delivered"),
    ({"delivery_stuck": True}, "stuck"),
])
def test_an_alive_but_unhealthy_runner_is_an_alarm(tmp_path, status, reason):
    verdict = check_heartbeat(_beat(tmp_path, status=status), KEY, max_age_s=60, now=T0)
    assert not verdict.healthy and reason in verdict.reason


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, None])
def test_max_age_has_no_default_and_must_be_sane(tmp_path, bad):
    with pytest.raises(ValueError, match="max_age_s"):
        check_heartbeat(tmp_path / "x", KEY, max_age_s=bad)


def test_the_writer_needs_a_signer_and_a_label(tmp_path):
    with pytest.raises(ValueError, match="signer"):
        HeartbeatWriter(tmp_path / "x", None, label="d")
    with pytest.raises(ValueError, match="label"):
        HeartbeatWriter(tmp_path / "x", KEY, label=" ")


def test_a_beat_that_cannot_be_written_is_counted_not_raised(tmp_path):
    writer = HeartbeatWriter(tmp_path / "missing-dir" / "heartbeat.json", KEY, label="d")
    assert writer(HEALTHY) is False and writer.failures == 1 and writer.last_error


def test_the_runner_beats_after_each_watchdog_pass(tmp_path):
    from conftest import make_test_desk
    path = tmp_path / "heartbeat.json"
    runner = SweepRunner(make_test_desk(), sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200,
                         alarm_timeout_s=5, max_tickets_per_sweep=10, alarm=lambda payload: None,
                         heartbeat=HeartbeatWriter(path, KEY, label="desk-1"))
    runner.check()
    first = json.loads(path.read_text())
    runner.check()
    second = json.loads(path.read_text())
    assert (first["seq"], second["seq"]) == (1, 2) and first["running"] is False    # never started: not healthy
    assert not check_heartbeat(path, KEY, max_age_s=60).healthy


def test_a_heartbeat_that_raises_does_not_stop_the_watchdog(tmp_path):
    from conftest import make_test_desk

    def broken(status):
        raise OSError("disk full")
    runner = SweepRunner(make_test_desk(), sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200,
                         alarm_timeout_s=5, max_tickets_per_sweep=10, alarm=lambda payload: None, heartbeat=broken)
    runner.check()                                                      # does not raise


def test_the_command_line_check_for_a_scheduler(tmp_path, monkeypatch, capsys):
    path = _beat(tmp_path, at=datetime.now(timezone.utc))
    monkeypatch.setenv("HB_KEY", "heartbeat-test-key-0123456789abcdef0123")
    assert main(["check", str(path), "--max-age", "60", "--key-env", "HB_KEY", "--key-id", "hb"]) == 0
    assert capsys.readouterr().out.startswith("OK:")
    assert main(["check", str(tmp_path / "gone.json"), "--max-age", "60", "--key-env", "HB_KEY", "--key-id", "hb"]) == 2
    monkeypatch.delenv("HB_KEY")
    assert main(["check", str(path), "--max-age", "60", "--key-env", "HB_KEY", "--key-id", "hb"]) == 3


def test_the_check_runs_as_a_separate_process(tmp_path):
    """Writer and checker are both real processes on the real clock, as they are when deployed (and unaffected by a
    clock a test run has frozen in this process)."""
    path = tmp_path / "heartbeat.json"
    env = {**os.environ, "HB_KEY": "heartbeat-test-key-0123456789abcdef0123"}
    here = os.path.dirname(os.path.abspath(__file__))
    writer = ("import os, sys; from conservation_kernel import HmacSigner; from heartbeat import HeartbeatWriter; "
              "ok = HeartbeatWriter(sys.argv[1], HmacSigner(os.environ['HB_KEY'].encode(), key_id='hb'), label='desk-1')("
              "{'running': True, 'alarmed': False, 'undelivered': 0, 'delivery_stuck': False}); sys.exit(0 if ok else 1)")
    assert subprocess.run([sys.executable, "-c", writer, str(path)], env=env, cwd=here).returncode == 0
    done = subprocess.run([sys.executable, "-m", "heartbeat", "check", str(path), "--max-age", "60",
                           "--key-env", "HB_KEY", "--key-id", "hb"], capture_output=True, text=True, env=env,
                          cwd=here)
    assert done.returncode == 0 and done.stdout.startswith("OK:")
