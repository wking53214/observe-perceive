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


# ---- review findings (independent review of the first version of this change) ----------------------------------------------

def archived_lines(path):
    return [json.loads(line) for line in open(path, encoding="utf-8") if line.strip()]


def test_a_restart_over_an_existing_archive_continues_the_chain(tmp_path):
    path = tmp_path / "a.jsonl"
    first = ImmutableAuditLedger(max_entries=10, archive_path=path)
    fill(first, 12)
    first.flush_to_archive()                                            # a clean shutdown writes the retained tail
    second = ImmutableAuditLedger(max_entries=10, archive_path=path)
    assert second.archived_count == 12 and second.chain_head == first.chain_head and not second.entries
    assert [e["data"]["i"] for e in second.iter_all()] == list(range(12)) and second.verify_integrity(full=True)   # before any append
    fill(second, 12, start=12)
    assert second.verify_integrity() and second.verify_integrity(full=True)
    assert [e["data"]["i"] for e in second.iter_all()] == list(range(24))


def test_a_restart_after_a_crash_continues_from_what_was_archived(tmp_path):
    path = tmp_path / "a.jsonl"
    first = ImmutableAuditLedger(max_entries=10, archive_path=path)
    fill(first, 12)                                                     # no flush: the retained tail dies with the process
    second = ImmutableAuditLedger(max_entries=10, archive_path=path)
    assert second.archived_count == first.archived_count > 0
    fill(second, 12, start=100)
    assert second.verify_integrity(full=True)                           # the chain is whole; the lost tail is the documented limit


def test_a_half_written_last_line_is_set_aside_and_the_ledger_continues(tmp_path):
    path = tmp_path / "a.jsonl"
    first = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(first, 20)
    kept = first.archived_count
    with open(path, "ab") as fh:
        fh.write(b'{"timestamp": "2026-11-04T08:00:00+00:00", "patient_')
    errors = []
    second = ImmutableAuditLedger(max_entries=5, archive_path=path, on_error=errors.append)
    assert second.recovered["kind"] == "ledger_archive_torn_tail" and second.recovered["kept_entries"] == kept
    assert second.recovered["torn_bytes"] > 0 and (tmp_path / "a.jsonl.torn").exists()
    assert stat.S_IMODE(os.stat(tmp_path / "a.jsonl.torn").st_mode) == 0o600
    fill(second, 20, start=50)
    assert second.verify_integrity(full=True) and len(archived_lines(path)) == second.archived_count
    assert list(second.iter_all())                                      # readable end to end, no JSON error


def test_a_torn_tail_is_reported_by_the_engine_as_degraded(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    with open(path, "ab") as fh:
        fh.write(b'{"half')
    engine = ObserveClinicalEngine(ledger_max_entries=5, ledger_archive_path=path)
    assert [e["kind"] for e in engine.degraded_events] == ["ledger_archive_torn_tail"]


@pytest.mark.parametrize("damage", ["tamper", "garbage", "symlink"])
def test_a_damaged_archive_is_refused_at_start_not_continued(tmp_path, damage):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    lines = open(path, encoding="utf-8").read().splitlines()
    if damage == "tamper":
        entry = json.loads(lines[1])
        entry["data"]["i"] = 999
        lines[1] = json.dumps(entry, sort_keys=True)
        path.write_text("\n".join(lines) + "\n")
    elif damage == "garbage":
        lines[1] = "not json at all"
        path.write_text("\n".join(lines) + "\n")
    else:
        real = tmp_path / "real.jsonl"
        path.rename(real)
        os.symlink(real, path)
    from observe_consolidated import LedgerArchiveError
    with pytest.raises(LedgerArchiveError):
        ImmutableAuditLedger(max_entries=5, archive_path=path)


def test_an_fsync_failure_after_the_write_does_not_duplicate_entries(tmp_path, monkeypatch):
    path = tmp_path / "a.jsonl"
    errors = []
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path, on_error=errors.append)
    real, failed = os.fsync, []

    def flaky(fd):
        if not failed:
            failed.append(1)
            raise OSError("fsync failed once")
        return real(fd)
    monkeypatch.setattr(os, "fsync", flaky)
    fill(ledger, 7)                                                     # the first rotation writes, then fsync fails
    assert errors and errors[0]["kind"] == "ledger_archive_failed" and ledger.archived_count == 0
    assert [e["data"]["i"] for e in ledger.iter_all()] == list(range(7))   # the uncommitted bytes are not read twice
    fill(ledger, 20, start=7)
    assert ledger.verify_integrity(full=True)
    assert [e["data"]["i"] for e in ledger.iter_all()] == list(range(27))
    assert len(archived_lines(path)) == ledger.archived_count          # every archived entry once, none twice


def test_a_write_that_fails_part_way_leaves_no_half_line(tmp_path, monkeypatch):
    path = tmp_path / "a.jsonl"
    errors = []
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path, on_error=errors.append)
    fill(ledger, 5)                                                     # below the bound: nothing archived yet
    real_fdopen, armed = os.fdopen, [True]

    class Half:
        def __init__(self, fh):
            self._fh = fh

        def write(self, data):
            if armed[0]:
                self._fh.write(data[: len(data) // 2])
                self._fh.flush()
                raise OSError("disk full")
            return self._fh.write(data)

        def __getattr__(self, name):
            return getattr(self._fh, name)

    monkeypatch.setattr(os, "fdopen", lambda fd, mode="r", *a, **k: Half(real_fdopen(fd, mode, *a, **k)) if mode == "ab" else real_fdopen(fd, mode, *a, **k))
    fill(ledger, 3, start=5)
    assert errors and os.path.getsize(path) == 0                       # truncated back: no half line, nothing duplicated later
    armed[0] = False
    fill(ledger, 20, start=8)
    assert ledger.verify_integrity(full=True) and len(archived_lines(path)) == ledger.archived_count


def test_reading_the_whole_ledger_while_it_rotates_is_always_consistent(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=10, archive_path=tmp_path / "a.jsonl")
    stop, results = threading.Event(), []

    def writer():
        i = 0
        while not stop.is_set() and i < 3000:
            ledger.append("P", "A", {"i": i})
            i += 1
    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(60):
            seen = [e["data"]["i"] for e in ledger.iter_all()]
            results.append(seen == list(range(len(seen))) and ledger.verify_integrity(full=True))
    finally:
        stop.set()
        t.join()
    assert all(results) and ledger.verify_integrity(full=True)


def test_an_archive_swapped_for_a_symlink_is_not_read(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    forged = tmp_path / "forged.jsonl"
    forged.write_text(path.read_text())
    path.unlink()
    os.symlink(forged, path)
    assert ledger.verify_integrity() and not ledger.verify_integrity(full=True)
    with pytest.raises(OSError):
        list(ledger.iter_all())


def test_an_archive_line_that_is_not_an_entry_fails_the_check_instead_of_raising(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    lines = open(path, encoding="utf-8").read().split("\n")
    lines[0] = "[]" + " " * (len(lines[0]) - 2)                        # same length, so the committed boundary still holds
    path.write_text("\n".join(lines))
    assert ledger.verify_integrity(full=True) is False
    with pytest.raises(ValueError):
        list(ledger.iter_all())

    class Odd(ImmutableAuditLedger):                                    # a source that yields a non-entry: still False, not a raise
        def iter_all(self, retained_first=False):
            return iter([[]])
    assert Odd().verify_integrity(full=True) is False


def test_an_archive_cut_short_fails_the_check_and_the_read(tmp_path):
    path = tmp_path / "a.jsonl"
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    lines = open(path, encoding="utf-8").read().splitlines(keepends=True)
    path.write_text("".join(lines[:2]))                                  # whole lines, but fewer than the ledger committed
    assert ledger.verify_integrity() and ledger.verify_integrity(full=True) is False
    with pytest.raises(ValueError):
        list(ledger.iter_all())


def test_an_archive_that_was_looser_than_owner_only_is_tightened(tmp_path):
    path = tmp_path / "a.jsonl"
    path.write_text("")
    os.chmod(path, 0o644)
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=path)
    fill(ledger, 20)
    assert ledger.archived_count and stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_entries_with_integer_keys_verify_after_they_are_archived(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=5, archive_path=tmp_path / "a.jsonl")
    for i in range(20):
        ledger.append("P", "A", {9: i, 10: i, "s": (1, 2)})
    assert ledger.archived_count and ledger.verify_integrity() and ledger.verify_integrity(full=True)


def test_the_hash_of_an_ordinary_entry_is_what_it_was_before_this_change():
    import hashlib
    ledger = ImmutableAuditLedger()
    ledger.append("P", "A", {"b": 1.5, "a": [1, 2], "t": None})
    entry = dict(ledger.entries[0])
    stored = entry.pop("immutable_hash")
    assert stored == hashlib.sha256(json.dumps(entry, sort_keys=True, default=str).encode()).hexdigest()


def test_flush_to_archive_writes_everything_and_a_failure_keeps_it(tmp_path):
    ledger = ImmutableAuditLedger(max_entries=50, archive_path=tmp_path / "a.jsonl")
    fill(ledger, 8)
    assert ledger.flush_to_archive() == 8 and not ledger.entries and ledger.verify_integrity(full=True)
    blocker = tmp_path / "file"
    blocker.write_text("x")
    bad = ImmutableAuditLedger(max_entries=50, archive_path=blocker / "a.jsonl")
    fill(bad, 3)
    bad.flush_to_archive()
    assert len(bad.entries) == 3 and bad.archived_count == 0


def test_the_exports_include_what_has_been_archived(tmp_path):
    from clinical_governance_system import ClinicalGovernanceSystem
    engine = ObserveClinicalEngine(ledger_max_entries=5, ledger_archive_path=tmp_path / "a.jsonl")
    fill(engine.audit_ledger, 30)
    holder = type("S", (), {"observe": engine})()
    assert len(ClinicalGovernanceSystem.export_observe_audit(holder)) == 30
    assert len(engine.audit_ledger.entries) < 30


def test_a_current_record_is_verified_without_reading_the_archive(tmp_path, monkeypatch):
    engine = ObserveClinicalEngine(ledger_max_entries=5, ledger_archive_path=tmp_path / "a.jsonl")
    fill(engine.audit_ledger, 30)
    reads = []
    monkeypatch.setattr(ImmutableAuditLedger, "_read_archive", lambda self, size: reads.append(size) or iter(()))
    first = next(iter(engine.audit_ledger.iter_all(retained_first=True)))
    assert first is engine.audit_ledger.entries[0] and not reads      # nothing is read until the retained part is exhausted


def test_a_verifier_reports_an_unreadable_archive_as_a_failed_check_not_an_exception(tmp_path):
    eng = ObserveClinicalEngine(ledger_max_entries=3, ledger_archive_path=tmp_path / "o.jsonl")
    o = orch(engine=eng)
    first = run(o, "arch-first", vit("P1", **CRIT))
    for i in range(12):
        run(o, f"arch-{i}", vit(f"Q{i}"))
    (tmp_path / "o.jsonl").write_text("this is not an archive\n" * 40)
    check = verify_result(first, kernel=o.conservation_kernel, perceive=o.perceive, observe=eng)    # must not raise
    assert not check.valid and "assessment.in_observe_ledger" in [c.name for c in check.failed]


def test_a_current_record_still_verifies_when_the_archive_is_unreadable(tmp_path):
    eng = ObserveClinicalEngine(ledger_max_entries=3, ledger_archive_path=tmp_path / "o.jsonl")
    o = orch(engine=eng)
    for i in range(12):
        run(o, f"arch-{i}", vit(f"Q{i}"))
    latest = run(o, "arch-latest", vit("P9", **CRIT))
    (tmp_path / "o.jsonl").write_text("garbage\n" * 40)
    check = verify_result(latest, kernel=o.conservation_kernel, perceive=o.perceive, observe=eng)
    assert "assessment.in_observe_ledger" not in [c.name for c in check.failed]                      # found in memory, archive untouched
