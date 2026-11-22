"""`python -m reconciler run`: reconciliation on a schedule, with what was already raised kept on disk."""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conservation_kernel import HmacSigner

from execution_guard import ExecutionLedger
from reconciler import load_raised, main

SECRET = "reconciler-run-test-ledger-key-0123456789"
KEY = HmacSigner(SECRET.encode(), key_id="ledger")


def _ctx(execution_id):
    approval = SimpleNamespace(state_commitment=f"ac-{execution_id}", timestamp=None, approval="APPROVED",
                               conservation_decision_id="d", conservation_receipt_id="r", conservation_audit_hash="h")
    return SimpleNamespace(execution_id=execution_id, request_id=f"req-{execution_id}", artifact_id=f"art-{execution_id}",
                           artifact_hash=f"hash-{execution_id}", producer="p", lineage=[],
                           state_commitment=f"cc-{execution_id}", timestamp=None, approval=approval)


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.setenv("RECON_KEY", SECRET)
    ledger = ExecutionLedger(tmp_path / "ledger.jsonl", signer=KEY)
    ctx = _ctx("a")
    ledger.issue(ctx)
    ledger.consume(ctx, consumer="test")
    effects = tmp_path / "effects.jsonl"
    effects.write_text(json.dumps({"effect_id": "e1", "execution_id": "a", "artifact_hash": "hash-a",
                                   "at": (datetime.now(timezone.utc) + timedelta(seconds=1)).isoformat()}) + "\n")
    return SimpleNamespace(ledger=tmp_path / "ledger.jsonl", effects=effects, state=tmp_path / "state.json",
                           tmp=tmp_path, issue=ledger.issue)


def _args(f, **override):
    parts = {"--ledger": f.ledger, "--effects": f.effects, "--state": f.state, "--clock-skew": 5,
             "--abandoned-after": 3600, "--key-env": "RECON_KEY", "--key-id": "ledger"}
    parts.update(override)
    return ["run"] + [str(x) for kv in parts.items() for x in kv]


def _add_effect(f, effect_id, execution_id=None):
    with f.effects.open("a") as fh:
        fh.write(json.dumps({"effect_id": effect_id, "execution_id": execution_id, "artifact_hash": None,
                             "at": datetime.now(timezone.utc).isoformat()}) + "\n")


def _lines(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_a_clean_run_exits_0_prints_nothing_and_writes_no_state(files, capsys):
    assert main(_args(files)) == 0 and _lines(capsys) == [] and not files.state.exists()


def test_a_bypass_pages_once_and_the_next_run_does_not_page_it_again(files, capsys):
    _add_effect(files, "e2")
    assert main(_args(files)) == 2
    out = _lines(capsys)
    assert [o["kind"] for o in out] == ["bypass"] and out[0]["effect_id"] == "e2"
    assert load_raised(files.state) == {out[0]["violation_id"]}
    assert main(_args(files)) == 0 and _lines(capsys) == []
    _add_effect(files, "e3")                                           # a new one still pages
    assert main(_args(files)) == 2 and [o["effect_id"] for o in _lines(capsys)] == ["e3"]


def test_a_record_only_violation_does_not_page(files, capsys):
    files.issue(_ctx("never-used"))
    later = datetime.now(timezone.utc) + timedelta(hours=2)
    assert main(_args(files), now=later) == 0 and _lines(capsys) == []


@pytest.mark.parametrize("damage,alarm", [
    ("wrong-key", "ledger_unreadable"),
    ("edited-ledger", "ledger_unreadable"),
    ("no-ledger", "refused"),
    ("no-effects", "effect_log_missing"),
    ("bad-effect", "refused"),
])
def test_what_cannot_be_seen_is_an_alarm(files, capsys, monkeypatch, damage, alarm):
    if damage == "wrong-key":
        monkeypatch.setenv("RECON_KEY", "some-other-key-of-enough-length-0123")
    elif damage == "edited-ledger":
        lines = files.ledger.read_text().splitlines()
        entry = json.loads(lines[0])
        entry["artifact_hash"] = "rewritten"
        lines[0] = json.dumps(entry, sort_keys=True)
        files.ledger.write_text("\n".join(lines) + "\n")
    elif damage == "no-ledger":
        files.ledger.unlink()
    elif damage == "no-effects":
        files.effects.unlink()
    else:
        files.effects.write_text("not json\n")
    assert main(_args(files)) == 2
    assert _lines(capsys)[0]["alarm"] == alarm


def test_misconfiguration_exits_3(files, monkeypatch):
    files.state.write_text("[]")
    assert main(_args(files)) == 3                                     # a state file in the wrong shape
    files.state.unlink()
    monkeypatch.delenv("RECON_KEY")
    assert main(_args(files)) == 3                                     # no key
    with pytest.raises(SystemExit):
        main(["run", "--ledger", str(files.ledger)])                   # no policy numbers: there is no default


def test_it_runs_as_its_own_process(files):
    _add_effect(files, "e2")
    done = subprocess.run([sys.executable, "-m", "reconciler"] + _args(files), capture_output=True, text=True,
                          env={**os.environ, "RECON_KEY": SECRET}, cwd=os.path.dirname(os.path.abspath(__file__)))
    assert done.returncode == 2, done.stdout + done.stderr
    assert json.loads(done.stdout.splitlines()[0])["kind"] == "bypass"


def test_a_state_file_that_cannot_be_written_still_pages_and_says_it_will_repeat(files, capsys):
    _add_effect(files, "e2")
    unwritable = files.tmp / "no-such-dir" / "state.json"
    assert main(_args(files, **{"--state": unwritable})) == 2
    out = _lines(capsys)
    assert [o.get("kind") for o in out[:1]] == ["bypass"] and out[-1]["alarm"] == "state_unwritable"
    assert "printed again" in out[-1]["detail"]
