"""test_observe_ledger_bounded.py -- OBSERVE's ledger can be bounded without losing a record (decision D11).

Measured on `main`: `ImmutableAuditLedger` is a list that only grows and lives in memory (docs/race/DATA_PRIVACY_AND_RESEARCH.md,
register C7). A bound drops old entries from memory; they are appended to an owner-only archive first, the chain continues across
the boundary, a verifier can still find an archived assessment, and an archive that cannot be written keeps the entries in memory
and says so.
"""
import json
import os
import stat
import threading
from datetime import datetime, timezone

import pytest

from observe_consolidated import ImmutableAuditLedger, ObserveClinicalEngine, VitalsSnapshot
from governance_chain import verify_result
from test_identity_binding import CRIT, orch, run, vit

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def fill(ledger, n, start=0):
    for i in range(start, start + n):
        ledger.append(f"P{i % 7}", "A", {"i": i})


def test_the_default_ledger_is_unchanged_unbounded_and_in_memory():
    ledger = ImmutableAuditLedger()
    fill(ledger, 50)
    assert len(ledger.entries) == 50 and ledger.archived_count == 0 and ledger.verify_integrity()
    assert ledger.anchor_hash == "0" * 64 and ledger.verify_integrity(full=True)


@pytest.mark.parametrize("bad", [0, -1, True, "3", 2.5])
def test_a_bad_bound_is_refused(bad, tmp_path):
    with pytest.raises(ValueError):
        ImmutableAuditLedger(max_entries=bad, archive_path=tmp_path / "a.jsonl")


def test_a_bound_without_an_archive_is_refused_so_nothing_is_dropped_silently():
    with pytest.raises(ValueError):
        ImmutableAuditLedger(max_entries=10)


def test_old_entries_leave_memory_for_the_archive_and_the_chain_continues(tmp_path):
    path = tmp_path / "observe.archive.jsonl"
    ledger = ImmutableAuditLedger(max_entries=10, archive_path=path)
    fill(ledger, 100)
    assert 10 <= len(ledger.entries) <= 11 and ledger.archived_count + len(ledger.entries) == 100   # bounded: 10 plus a tenth
    assert len(path.read_text().splitlines()) == ledger.archived_count
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert ledger.verify_integrity() and ledger.verify_integrity(full=True)
    everything = list(ledger.iter_all())
    assert [e["data"]["i"] for e in everything] == list(range(100))                                 # in order, none lost
    assert everything[0]["previous_hash"] == "0" * 64 and ledger.anchor_hash == ledger.entries[0]["previous_hash"]


def test_queries_and_exports_are_memory_only_unless_asked_for_the_archive(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=tmp_path / "a.jsonl")
    fill(ledger, 60)
    assert len(ledger.query_patient("P0")) < len(ledger.query_patient("P0", include_archive=True)) == len(range(0, 60, 7))
    assert len(json.loads(ledger.export_json())) < len(json.loads(ledger.export_json(include_archive=True))) == 60


def test_a_tampered_retained_entry_fails_both_checks(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=tmp_path / "a.jsonl")
    fill(ledger, 30)
    ledger.entries[1]["data"]["i"] = -1
    assert not ledger.verify_integrity() and not ledger.verify_integrity(full=True)


def test_a_tampered_archived_entry_is_caught_by_the_full_check_not_the_quick_one(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 30)
    lines = path.read_text().splitlines()
    raw = json.loads(lines[2])
    raw["data"]["i"] = -1
    lines[2] = json.dumps(raw, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    assert ledger.verify_integrity() is True            # the quick check covers the retained part from the anchor, and says so
    assert ledger.verify_integrity(full=True) is False  # the full check reads the archive


def test_an_unreadable_archive_makes_the_full_check_false_not_a_crash(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 30)
    path.write_text("not json\n")
    assert ledger.verify_integrity(full=True) is False


def test_an_archive_that_cannot_be_written_keeps_every_entry_and_reports_it(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    errors = []
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=blocker / "sub" / "a.jsonl", on_error=errors.append)
    fill(ledger, 30)
    assert len(ledger.entries) == 30 and ledger.archived_count == 0 and ledger.verify_integrity()   # nothing lost
    assert errors and errors[0]["kind"] == "ledger_archive_failed" and errors[0]["retained"] >= 7
    ledger.archive_path = str(tmp_path / "good.jsonl")                                              # the archive recovers
    fill(ledger, 1, start=30)
    assert len(ledger.entries) <= 11 and ledger.verify_integrity(full=True)
    assert [e["data"]["i"] for e in ledger.iter_all()] == list(range(31))


def test_a_raising_error_hook_does_not_break_the_append(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    ledger = ImmutableAuditLedger(max_entries=2, archive_path=blocker / "a.jsonl", on_error=lambda e: 1 / 0)
    fill(ledger, 10)
    assert len(ledger.entries) == 10


def test_concurrent_appends_keep_one_chain(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=20, archive_path=tmp_path / "a.jsonl")

    def work(k):
        fill(ledger, 100, start=k * 1000)
    threads = [threading.Thread(target=work, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert ledger.archived_count + len(ledger.entries) == 400 and ledger.verify_integrity(full=True)


# ---- the engine and the verifier -------------------------------------------------------------------------------------

def test_the_engine_takes_a_bound_and_an_archive(tmp_path):
    eng = ObserveClinicalEngine(ledger_max_entries=5, ledger_archive_path=tmp_path / "o.jsonl")
    for i in range(40):
        eng.evaluate(VitalsSnapshot(f"P{i}", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    assert len(eng.audit_ledger.entries) <= 6 and eng.audit_ledger.archived_count >= 30
    assert eng.audit_ledger.verify_integrity(full=True)


def test_a_verifier_still_finds_an_assessment_that_has_moved_to_the_archive(tmp_path):
    eng = ObserveClinicalEngine(ledger_max_entries=3, ledger_archive_path=tmp_path / "o.jsonl")
    o = orch(engine=eng)
    first = run(o, "arch-first", vit("P1", **CRIT))
    for i in range(12):
        run(o, f"arch-{i}", vit(f"Q{i}"))
    check = verify_result(first, kernel=o.conservation_kernel, perceive=o.perceive, observe=eng)
    assert check.valid, [c.name for c in check.failed]
    assert "assessment.in_observe_ledger" in [c.name for c in check.checks]
    assert not any(e["patient_id"] == "P1" for e in eng.audit_ledger.entries)                      # it really was archived


def test_a_failing_archive_inside_the_eviction_path_is_reported_without_deadlock(tmp_path):
    """The ledger can fail while the engine holds its state lock (an eviction record); reporting must not take that lock again."""
    blocker = tmp_path / "file"
    blocker.write_text("x")
    told = []
    eng = ObserveClinicalEngine(max_tracked_patients=1, ledger_max_entries=2, ledger_archive_path=blocker / "a.jsonl",
                                on_degraded=told.append)
    done = threading.Event()

    def work():
        for i in range(12):
            eng.evaluate(VitalsSnapshot(f"P{i}", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
        done.set()
    t = threading.Thread(target=work, daemon=True)
    t.start()
    assert done.wait(20), "deadlock: reporting a ledger failure from inside the state lock"
    assert any(e["kind"] == "ledger_archive_failed" for e in eng.degraded_events) and told


def test_the_archive_is_flushed_to_disk_before_entries_leave_memory(tmp_path, monkeypatch):
    synced = []
    real = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: (synced.append(fd), real(fd))[1])
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=tmp_path / "a.jsonl")
    fill(ledger, 20)
    assert synced and ledger.archived_count > 0
