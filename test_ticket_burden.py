"""test_ticket_burden.py -- the ticket-log report counts what the desk really recorded, and says so when the log cannot be trusted.

The log under test is written by a REAL EscalationDesk on a controlled clock, and every expected number is worked out by hand from the
timeline in `busy_ward`, not read back from the module.
"""
import hashlib
import json

import pytest

import ticket_burden as tb
from test_human_escalation import ALICE, BOB, LOG_KEY, Channel, Clock, EscalationDesk, EscalationLog, NotificationPolicy, T, ack

P1, P2, P3, P4 = "MRN-ONE", "MRN-TWO", "MRN-THREE", "MRN-FOUR"


def desk_on(path, signer=LOG_KEY):
    clock, first, second = Clock(), Channel(), Channel()
    desk = EscalationDesk(NotificationPolicy((T("charge", first, ("alice",), 60.0), T("physician", second, ("bob",), 120.0))),
                          EscalationLog(path, signer=signer), responder_keys={"alice": ALICE, "bob": BOB}, clock=clock)
    return desk, clock, first, second


def clinical(desk, patient, regime="critical", reason="regime_at_or_above_threshold"):
    return desk.raise_and_dispatch(kind="clinical_escalation", request_id="r", artifact_id="a",
                                   context={"patient_id": patient, "regime": regime, "reason": reason})[0]


def busy_ward(path):
    """t=0 T1 (P1) acknowledged by alice at 30. t=120 T2 (P2): swept at 181 to the physician, acknowledged by bob at 200.
    t=500 T3 (P1 again, open). t=600 T4 (P3: charge fails, physician takes it, open), T5 (P4: nobody reachable), T6 (a stage error, open)."""
    desk, clock, first, second = desk_on(path)
    t1 = clinical(desk, P1)
    clock.advance(30)
    ack(desk, t1)
    clock.advance(90)
    t2 = clinical(desk, P2)
    clock.advance(61)
    desk.sweep()
    clock.advance(19)
    ack(desk, t2, signer=BOB, actor="bob")
    clock.advance(300)
    clinical(desk, P1, regime="warning", reason="severity_increase")                       # T3
    clock.advance(100)
    first.fail = True
    clinical(desk, P3)                                                                       # T4
    second.fail = True
    clinical(desk, P4)                                                                       # T5
    first.fail = second.fail = False
    desk.raise_and_dispatch(kind="stage_error", context={"stage": "perceive"})              # T6
    desk.log.close()
    return desk


def report(path, **kw):
    entries, problems = tb.read_log(path)
    return tb.analyse(entries, source="unit test", chain_problems=problems, **kw)


def test_the_counts_on_a_hand_built_ward(tmp_path):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    r = report(path)
    assert r.chain_intact and r.signed and r.tickets == 6
    assert r.tickets_by_kind == {"clinical_escalation": 5, "stage_error": 1}
    assert r.tickets_by_reason == {"none": 1, "regime_at_or_above_threshold": 4, "severity_increase": 1}
    assert r.tickets_by_regime == {"critical": 4, "none": 1, "warning": 1}
    assert (r.acknowledged, r.exhausted, r.open_at_end) == (2, 1, 3) and r.share_acknowledged == round(2 / 6, 4)
    assert r.days_with_a_ticket == 1 and r.tickets_per_day_with_any == {"mean": 6.0, "median": 6, "p90": 6, "max": 6}
    assert r.patient_days_with_a_ticket == 4 and r.tickets_per_patient_day_with_any == 1.25      # 5 patient tickets over 4 patient-days
    assert r.seconds_to_first_dispatch == {"median": 0.0, "p90": 0.0, "max": 0.0}                # T1 T2 T3 T4 T6; T5 never dispatched
    assert r.seconds_to_acknowledge == {"median": 30.0, "p90": 80.0, "max": 80.0}                # T1 30 s, T2 200 - 120 = 80 s
    assert r.oldest_open_seconds == 100.0                                                        # T3, opened at 500; the log ends at 600
    assert (r.needed_more_than_one_tier, r.overdue_events, r.failed_dispatches) == (1, 1, 3)    # T2; T2; T4 once, T5 twice
    assert r.repeat_tickets_within_1h == 1                                                       # T3 for P1, 500 s after T1
    assert r.share_of_tickets_from_busiest_5pct_of_patients == 0.4                               # P1's 2 of the 5 patient tickets


def test_a_ticket_that_needs_a_second_look_is_measured_from_opening_not_from_the_last_dispatch(tmp_path):
    desk, clock, first, second = desk_on(tmp_path / "esc.jsonl")
    first.fail = second.fail = True
    ticket = clinical(desk, P1)                                    # nobody reachable: exhausted at t=0
    assert desk.state(ticket) == "exhausted"
    clock.advance(300)
    first.fail = second.fail = False
    desk.sweep()                                                   # the channels are back: paged for the first time at t=300
    clock.advance(30)
    ack(desk, ticket)                                              # acknowledged at t=330
    r = report(tmp_path / "esc.jsonl")
    assert r.seconds_to_first_dispatch == {"median": 300.0, "p90": 300.0, "max": 300.0}
    assert r.seconds_to_acknowledge == {"median": 330.0, "p90": 330.0, "max": 330.0}
    assert (r.acknowledged, r.exhausted, r.open_at_end) == (1, 0, 0)           # exhausted earlier, but acknowledged in the end


def test_an_unsigned_log_is_reported_as_unsigned(tmp_path):
    desk, clock, *_ = desk_on(tmp_path / "esc.jsonl", signer=None)
    clinical(desk, P1)
    assert report(tmp_path / "esc.jsonl").signed is False


def test_an_empty_log_is_a_zero_report_and_a_source_is_required(tmp_path):
    (tmp_path / "e.jsonl").write_text("")
    r = report(tmp_path / "e.jsonl")
    assert (r.events, r.tickets, r.acknowledged, r.chain_intact) == (0, 0, 0, True)
    with pytest.raises(ValueError, match="where the log came from"):
        tb.analyse([], source=" ")


def test_a_tampered_line_stops_the_figures_and_the_report_says_so(tmp_path):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    lines = path.read_text().splitlines()
    entry = json.loads(lines[3])
    entry["at"] = "2020-01-01T00:00:00+00:00"                      # rewrite history; the stored hash no longer matches
    lines[3] = json.dumps(entry, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    r = report(path)
    assert not r.chain_intact and r.events == 3 and "line 4" in r.chain_problems[0]
    assert "NOT INTACT" in tb.render_text(r)


def test_a_dropped_event_breaks_the_chain(tmp_path):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    lines = path.read_text().splitlines()
    del lines[2]
    path.write_text("\n".join(lines) + "\n")
    r = report(path)
    assert not r.chain_intact and r.events == 2 and "line 3" in r.chain_problems[0]


def test_a_half_written_last_line_is_reported_and_everything_before_it_is_used(tmp_path):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    whole = report(path).events
    with open(path, "ab") as fh:
        fh.write(b'{"seq": 99, "prev')
    r = report(path)
    assert r.events == whole and not r.chain_intact and "half-written" in r.chain_problems[0]


def test_reading_a_log_changes_nothing_and_takes_no_lock(tmp_path):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    with open(path, "ab") as fh:
        fh.write(b'{"torn')                                       # a desk opening this would move it aside and repair the file
    before = (hashlib.sha256(path.read_bytes()).hexdigest(), sorted(p.name for p in tmp_path.iterdir()))
    report(path)
    assert (hashlib.sha256(path.read_bytes()).hexdigest(), sorted(p.name for p in tmp_path.iterdir())) == before


def test_the_command_line_prints_the_source_never_a_patient_id_and_exits_2_on_a_broken_chain(tmp_path, capsys):
    path = tmp_path / "esc.jsonl"
    busy_ward(path)
    assert tb.main([str(path), "--source", "a labelled test source"]) == 0
    text = capsys.readouterr().out
    assert "a labelled test source" in text and "not a measure of whether any page was needed" in text
    assert all(p not in text for p in (P1, P2, P3, P4))
    assert tb.main([str(path), "--source", "s", "--json"]) == 0
    assert all(p not in capsys.readouterr().out for p in (P1, P2, P3, P4))
    lines = path.read_text().splitlines()
    path.write_text("\n".join(lines[:3] + ["not json"] + lines[4:]) + "\n")
    assert tb.main([str(path), "--source", "s"]) == 2
    assert "NOT INTACT" in capsys.readouterr().out


# ---- forged but well-formed logs: each check is isolated from the others ----------------------------------------------------

def forge(path, ticket_id, event, at, data, *, seq=None, previous=None, signature=True):
    """Append one event with a CORRECT hash. `seq` / `previous` default to the true next values; passing a wrong one isolates that check."""
    from human_escalation import _event_hash
    lines = path.read_text().splitlines() if path.exists() else []
    last = json.loads(lines[-1]) if lines else None
    entry = {"seq": len(lines) if seq is None else seq, "previous": (last["hash"] if last else "") if previous is None else previous,
             "ticket_id": ticket_id, "event": event, "at": at, "data": data}
    entry["hash"] = _event_hash(entry)
    if signature:
        entry["signature"] = {"key_id": "forged"}
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, sort_keys=True) + "\n")


def opened(path, ticket_id, at, patient, **kw):
    forge(path, ticket_id, "opened", at, {"kind": "clinical_escalation", "request_id": None, "artifact_id": None,
                                          "context": {"patient_id": patient, "regime": "critical", "reason": "r"}, "opened_hash": "x"}, **kw)


def test_a_wrong_sequence_number_alone_breaks_the_chain(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "t1", "2026-01-01T12:00:00+00:00", P1)
    opened(path, "t2", "2026-01-01T12:01:00+00:00", P2, seq=7)               # right previous, right hash, wrong seq
    r = report(path)
    assert r.events == 1 and not r.chain_intact and "line 2" in r.chain_problems[0]


def test_a_wrong_previous_link_alone_breaks_the_chain(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "t1", "2026-01-01T12:00:00+00:00", P1)
    opened(path, "t2", "2026-01-01T12:01:00+00:00", P2, previous="0" * 64)   # right seq, right hash, wrong previous
    r = report(path)
    assert r.events == 1 and not r.chain_intact and "line 2" in r.chain_problems[0]


def test_one_unsigned_event_makes_the_log_unsigned(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "t1", "2026-01-01T12:00:00+00:00", P1)
    assert report(path).signed is True
    opened(path, "t2", "2026-01-01T12:01:00+00:00", P2, signature=False)
    assert report(path).signed is False


def test_a_second_acknowledgement_does_not_move_the_first(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "t1", "2026-01-01T12:00:00+00:00", P1)
    forge(path, "t1", "dispatch", "2026-01-01T12:00:00+00:00", {"tier": "charge", "outcome": "dispatched"})
    forge(path, "t1", "acknowledged", "2026-01-01T12:00:20+00:00", {"actor_id": "alice"})
    forge(path, "t1", "acknowledged", "2026-01-01T12:09:00+00:00", {"actor_id": "bob"})
    assert report(path).seconds_to_acknowledge == {"median": 20.0, "p90": 20.0, "max": 20.0}


def test_events_for_a_ticket_that_was_never_opened_are_not_tickets(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "t1", "2026-01-01T12:00:00+00:00", P1)
    forge(path, "ghost", "dispatch", "2026-01-01T12:00:05+00:00", {"tier": "charge", "outcome": "dispatched"})
    r = report(path)
    assert r.tickets == 1 and r.events == 2 and r.open_at_end == 1


def test_the_repeat_window_is_one_hour_inclusive(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "a", "2026-01-01T10:00:00+00:00", P1)
    opened(path, "b", "2026-01-01T11:00:00+00:00", P1)                       # exactly 3600 s after a: a repeat
    opened(path, "c", "2026-01-01T12:00:01+00:00", P1)                       # 3601 s after b: not
    opened(path, "d", "2026-01-01T13:30:00+00:00", P2)                       # another patient: not
    opened(path, "e", "2026-01-01T14:30:00+00:00", P2)                       # 3600 s after d: a repeat
    opened(path, "f", "2026-01-01T15:45:00+00:00", P2)                       # 4500 s after e: not (and not within 2 hours of 1)
    assert report(path).repeat_tickets_within_1h == 2


def test_a_day_is_a_utc_day_whatever_offset_the_event_carries(tmp_path):
    path = tmp_path / "e.jsonl"
    opened(path, "a", "2026-01-01T23:00:00-05:00", P1)                       # 04:00Z on the 2nd
    opened(path, "b", "2026-01-02T01:00:00+00:00", P1)                       # 01:00Z on the 2nd
    r = report(path)
    assert r.days_with_a_ticket == 1 and r.patient_days_with_a_ticket == 1 and r.tickets_per_patient_day_with_any == 2.0


def test_one_patient_on_two_days_is_two_patient_days(tmp_path):
    path = tmp_path / "e.jsonl"
    desk, clock, *_ = desk_on(path)
    clinical(desk, P1)
    clock.advance(25 * 3600)
    clinical(desk, P1)
    desk.log.close()
    r = report(path)
    assert (r.days_with_a_ticket, r.patient_days_with_a_ticket, r.tickets_per_patient_day_with_any) == (2, 2, 1.0)
    assert r.repeat_tickets_within_1h == 0


def test_the_busiest_five_percent_rounds_up_to_whole_patients(tmp_path):
    path = tmp_path / "e.jsonl"
    stamp = "2026-01-0{}T12:00:00+00:00"
    n = 0
    for patient, count in [("P%02d" % i, 3 if i == 0 else 2 if i == 1 else 1) for i in range(30)]:
        for _ in range(count):
            n += 1
            opened(path, f"t{n}", stamp.format(1 + n % 8), patient)
    r = report(path)                                                          # 30 patients, 33 tickets; busiest 5% is ceil(1.5) = 2 patients
    assert r.tickets == 33 and r.share_of_tickets_from_busiest_5pct_of_patients == round((3 + 2) / 33, 4)
