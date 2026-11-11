"""test_human_escalation.py -- telling a person, with evidence of each step.

The claims under test: a returned callable is only DISPATCHED; only the channel's own receipt makes it DELIVERED; only a
responder's signature makes it ACKNOWLEDGED; a failed dispatch does not wait; an unanswered ticket moves up and ends
EXHAUSTED rather than quiet; the log verifies; and none of this alters a clinical outcome.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import run_chain
from conservation_kernel import HmacSigner
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog
from human_escalation import (AcknowledgementError, AlertOutcome, EscalationDesk, EscalationLog, EscalationLogError,
                              NotificationPolicy, Tier, sign_acknowledgement)
from observe_consolidated import ObserveClinicalEngine

LOG_KEY = HmacSigner(b"escalation-log-key-0123456789abcdef", key_id="log")
ALICE = HmacSigner(b"alice-responder-key-0123456789abcdef", key_id="alice")
BOB = HmacSigner(b"bob-responder-key-0123456789abcdefgh", key_id="bob")
EVE = HmacSigner(b"eve-the-impostor-key-0123456789abcd", key_id="alice")   # claims to be alice, is not


class Clock:
    def __init__(self):
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


class Channel:
    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.calls = answer, fail, []

    def __call__(self, payload):
        self.calls.append(payload)
        if self.fail:
            raise ConnectionError("pager unreachable")
        return self.answer


def T(name, channel, responders, ack=60.0, timeout=5.0):
    return Tier(name, channel, responders, ack, timeout)


def make(tmp_path=None, tiers=None, clock=None, signer=LOG_KEY):
    clock = clock or Clock()
    first, second = Channel(), Channel()
    tiers = tiers or (T("charge", first, ("alice",), 60.0), T("physician", second, ("bob",), 120.0))
    path = None if tmp_path is None else tmp_path / "esc.jsonl"
    desk = EscalationDesk(NotificationPolicy(tiers), EscalationLog(path, signer=signer),
                          responder_keys={"alice": ALICE, "bob": BOB}, clock=clock)
    return desk, clock, first, second


def raise_one(desk):
    return desk.raise_and_dispatch(kind="stage_error", request_id="r1", artifact_id="a1",
                                   context={"stage": "perceive", "stage_error": "boom"})


# ---- the policy has no defaults ---------------------------------------------------------------------------------

@pytest.mark.parametrize("seconds", [0, -5, float("nan"), float("inf"), True, None, "60"])
def test_a_tier_without_a_usable_deadline_is_a_problem(seconds):
    policy = NotificationPolicy((Tier("t", Channel(), ("alice",), seconds, 5.0),))
    assert any("ack_within_s" in p for p in policy.problems())
    policy = NotificationPolicy((Tier("t", Channel(), ("alice",), 60.0, seconds),))
    assert any("dispatch_timeout_s" in p for p in policy.problems())


def test_an_empty_or_malformed_policy_is_a_problem():
    assert NotificationPolicy(()).problems()
    bad = NotificationPolicy((Tier("", Channel(), (), 10, 5), Tier("x", None, ("a",), 10, 5), Tier("x", Channel(), ("a",), 10, 5)))
    text = " | ".join(bad.problems())
    assert "no name" in text and "no responder" in text and "no callable channel" in text and "used twice" in text


def test_tier_has_no_default_for_a_deadline_or_a_responder():
    with pytest.raises(TypeError):
        Tier("t", Channel(), ("alice",))                                     # type: ignore[call-arg]
    with pytest.raises(TypeError):
        Tier("t", Channel(), ("alice",), 60.0)                               # type: ignore[call-arg]


# ---- what each state does and does not prove --------------------------------------------------------------------

def test_a_callable_that_returns_is_dispatched_never_delivered():
    desk, _, first, _ = make()
    ticket_id, state = raise_one(desk)
    assert state == "dispatched" and desk.state(ticket_id) == "dispatched"
    assert first.calls[0]["ticket_id"] == ticket_id and first.calls[0]["stage"] == "perceive"
    assert first.calls[0]["request_id"] == "r1" and first.calls[0]["tier"] == "charge"


@pytest.mark.parametrize("answer", [True, "ok", "delivered", {"status": "sent"}, {"delivery_receipt": ""},
                                    {"delivery_receipt": "  "}, {"delivery_receipt": 7}])
def test_only_a_channel_receipt_string_makes_a_ticket_delivered(answer):
    desk, _, first, _ = make(tiers=(T("t", Channel(answer), ("alice",)),))
    assert raise_one(desk)[1] == "dispatched"


def test_a_channel_receipt_makes_it_delivered_and_is_logged():
    desk, *_ = make(tiers=(T("t", Channel({"delivery_receipt": "vendor-42"}), ("alice",)),))
    ticket_id, state = raise_one(desk)
    assert state == "delivered"
    assert desk.log.entries[1]["data"]["delivery_receipt"] == "vendor-42"


@pytest.mark.parametrize("answer", [{"acknowledged": True, "delivery_receipt": "x"}, {"state": "acknowledged"}, "acknowledged"])
def test_a_channel_can_never_acknowledge(answer):
    desk, *_ = make(tiers=(T("t", Channel(answer), ("alice",)),))
    ticket_id, state = raise_one(desk)
    assert state != "acknowledged" and desk.ticket(ticket_id)["acknowledged_by"] is None


# ---- acknowledgement: signed, bound, once -----------------------------------------------------------------------

def ack(desk, ticket_id, signer=ALICE, actor="alice", **kw):
    sig = sign_acknowledgement(signer, ticket_id, desk.ticket(ticket_id)["opened_hash"], actor)
    return desk.acknowledge(ticket_id, actor, sig, **kw)


def test_a_signed_acknowledgement_by_a_named_responder_is_recorded():
    desk, *_ = make()
    ticket_id, _ = raise_one(desk)
    assert ack(desk, ticket_id, note="on my way") == "acknowledged"
    info = desk.ticket(ticket_id)
    assert info["acknowledged_by"] == "alice" and desk.open_tickets() == []
    assert desk.log.entries[-1]["data"]["note"] == "on my way"


def test_every_forged_or_misdirected_acknowledgement_is_refused_and_records_nothing():
    desk, *_ = make()
    one, _ = raise_one(desk)
    two, _ = raise_one(desk)
    before = len(desk.log)
    opened = desk.ticket(one)["opened_hash"]
    cases = [
        ("no such ticket", lambda: desk.acknowledge("esc-999999", "alice", sign_acknowledgement(ALICE, "esc-999999", opened, "alice"))),
        ("unsigned", lambda: desk.acknowledge(one, "alice", None)),
        ("empty block", lambda: desk.acknowledge(one, "alice", {})),
        ("signed by another key", lambda: desk.acknowledge(one, "alice", sign_acknowledgement(EVE, one, opened, "alice"))),
        ("signed for another ticket", lambda: desk.acknowledge(one, "alice", sign_acknowledgement(ALICE, two, desk.ticket(two)["opened_hash"], "alice"))),
        ("signed as another actor", lambda: desk.acknowledge(one, "alice", sign_acknowledgement(BOB, one, opened, "bob"))),
        ("actor without a key", lambda: desk.acknowledge(one, "mallory", sign_acknowledgement(ALICE, one, opened, "mallory"))),
        ("responder of a tier not yet reached", lambda: desk.acknowledge(one, "bob", sign_acknowledgement(BOB, one, opened, "bob"))),
    ]
    for label, call in cases:
        with pytest.raises(AcknowledgementError):
            call()
        assert len(desk.log) == before, label
        assert desk.state(one) == "dispatched", label


def test_a_signature_made_for_one_responder_does_not_acknowledge_as_another_even_on_a_shared_key():
    desk, *_ = make(tiers=(T("t", Channel(), ("alice", "bob")),))
    desk.responder_keys["bob"] = ALICE                                       # a misconfiguration: one key, two people
    ticket_id, _ = raise_one(desk)
    forged = sign_acknowledgement(ALICE, ticket_id, desk.ticket(ticket_id)["opened_hash"], "alice")
    with pytest.raises(AcknowledgementError, match="signature refused"):
        desk.acknowledge(ticket_id, "bob", forged)


def test_an_acknowledgement_from_a_replaced_log_does_not_acknowledge_a_new_ticket_with_the_same_id(tmp_path):
    (tmp_path / "old").mkdir()
    (tmp_path / "new").mkdir()
    old, *_ = make(tmp_path / "old")
    new, *_ = make(tmp_path / "new")
    old_id, _ = raise_one(old)
    new_id, _ = new.raise_and_dispatch(kind="stage_error", request_id="other", artifact_id="other")
    assert old_id == new_id                                                  # ids restart when a log is replaced
    stale = sign_acknowledgement(ALICE, old_id, old.ticket(old_id)["opened_hash"], "alice")
    with pytest.raises(AcknowledgementError, match="signature refused"):
        new.acknowledge(new_id, "alice", stale)


def test_a_ticket_cannot_be_acknowledged_twice():
    desk, *_ = make()
    ticket_id, _ = raise_one(desk)
    ack(desk, ticket_id)
    with pytest.raises(AcknowledgementError, match="already acknowledged"):
        ack(desk, ticket_id)


def test_nothing_that_never_reached_a_person_can_be_acknowledged():
    desk, *_ = make(tiers=(T("t", Channel(fail=True), ("alice",)),))
    ticket_id, state = raise_one(desk)
    assert state == "exhausted"
    with pytest.raises(AcknowledgementError, match="never dispatched"):
        ack(desk, ticket_id)


# ---- failure does not wait; silence does not go quiet -----------------------------------------------------------

def test_a_failed_channel_moves_to_the_next_tier_at_once_without_waiting():
    desk, clock, first, second = make()
    first.fail = True
    ticket_id, state = raise_one(desk)
    assert state == "dispatched" and desk.ticket(ticket_id)["tier"] == "physician"
    assert len(second.calls) == 1 and clock.now == datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    assert desk.ticket(ticket_id)["history"] == ["raised", "charge:failed", "physician:dispatched"]


def test_every_channel_failing_is_exhausted_immediately_and_stays_open():
    desk, *_ = make(tiers=(T("a", Channel(fail=True), ("alice",)), T("b", Channel(fail=True), ("bob",))))
    ticket_id, state = raise_one(desk)
    assert state == "exhausted" and desk.open_tickets() == [ticket_id]


def test_sweep_does_nothing_before_a_deadline_and_escalates_after_it():
    desk, clock, first, second = make()
    ticket_id, _ = raise_one(desk)
    clock.advance(59)
    assert desk.sweep() == [] and second.calls == []
    clock.advance(1)
    assert desk.sweep() == [(ticket_id, "dispatched")]
    assert desk.ticket(ticket_id)["tier"] == "physician" and len(second.calls) == 1
    assert desk.ticket(ticket_id)["history"] == ["raised", "charge:dispatched", "charge:overdue", "physician:dispatched"]


def test_an_unanswered_last_tier_ends_exhausted_not_quiet():
    desk, clock, *_ = make()
    ticket_id, _ = raise_one(desk)
    clock.advance(60)
    desk.sweep()
    clock.advance(120)
    assert desk.sweep() == [(ticket_id, "exhausted")]
    assert desk.state(ticket_id) == "exhausted" and desk.open_tickets() == [ticket_id]
    clock.advance(10_000)
    assert desk.sweep() == []                                                # stays exhausted; does not re-page forever


def test_an_earlier_responder_may_still_acknowledge_after_escalation_and_after_exhaustion():
    desk, clock, *_ = make()
    ticket_id, _ = raise_one(desk)
    clock.advance(60)
    desk.sweep()
    assert ack(desk, ticket_id, BOB, "bob") == "acknowledged"
    other, _ = raise_one(desk)
    clock.advance(60)
    desk.sweep()
    clock.advance(120)
    desk.sweep()
    assert desk.state(other) == "exhausted"
    assert ack(desk, other) == "acknowledged"


def test_an_acknowledged_ticket_is_not_swept():
    desk, clock, _, second = make()
    ticket_id, _ = raise_one(desk)
    ack(desk, ticket_id)
    clock.advance(10_000)
    assert desk.sweep() == [] and second.calls == []


def test_a_sweep_failure_in_one_tier_falls_through_to_the_next_and_the_sweep_age_is_reported():
    desk, clock, _, second = make()
    assert desk.seconds_since_sweep() is None
    raise_one(desk)
    second.fail = True
    clock.advance(60)
    assert desk.sweep()[0][1] == "exhausted"
    clock.advance(30)
    assert desk.seconds_since_sweep() == 30


# ---- the log ----------------------------------------------------------------------------------------------------

def test_the_log_reloads_to_the_same_states(tmp_path):
    desk, clock, *_ = make(tmp_path)
    one, _ = raise_one(desk)
    two, _ = raise_one(desk)
    ack(desk, one)
    clock.advance(60)
    desk.sweep()
    desk.log.close()
    again, *_ = make(tmp_path, clock=clock)
    assert again.state(one) == "acknowledged" and again.state(two) == "dispatched"
    assert again.ticket(two)["tier"] == "physician" and again.ticket(two)["history"] == desk.ticket(two)["history"]
    assert raise_one(again)[0] == "esc-000002"


def _lines(tmp_path):
    return (tmp_path / "esc.jsonl").read_text().splitlines()


def test_an_edited_removed_or_foreign_signed_log_does_not_load(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    raise_one(desk)
    lines = _lines(tmp_path)
    desk.log.close()

    edited = json.loads(lines[1])
    edited["data"]["outcome"] = "delivered"
    (tmp_path / "esc.jsonl").write_text("\n".join([lines[0], json.dumps(edited), *lines[2:]]) + "\n")
    with pytest.raises(EscalationLogError, match="does not recompute"):
        make(tmp_path)

    (tmp_path / "esc.jsonl").write_text("\n".join([lines[0], *lines[2:]]) + "\n")
    with pytest.raises(EscalationLogError, match="chain link broken"):
        make(tmp_path)

    (tmp_path / "esc.jsonl").write_text("\n".join(lines) + "\n")
    with pytest.raises(EscalationLogError):
        make(tmp_path, signer=HmacSigner(b"another-log-key-0123456789abcdefgh", key_id="log"))


def test_a_tier_the_policy_no_longer_has_stops_the_desk_loudly(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    desk.log.close()
    with pytest.raises(EscalationLogError, match="current policy does not have"):
        make(tmp_path, tiers=(T("renamed", Channel(), ("alice",)),))


def test_the_log_file_is_owner_only_and_refuses_a_symlink(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    assert os.stat(tmp_path / "esc.jsonl").st_mode & 0o777 == 0o600
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    channel = Channel()
    bad = EscalationDesk(NotificationPolicy((T("t", channel, ("alice",)),)), EscalationLog(link),
                         responder_keys={"alice": ALICE})
    ticket_id, state = raise_one(bad)
    assert state == "dispatched" and len(channel.calls) == 1                 # the page went out regardless
    assert bad.log_failures >= 1 and bad.ticket(ticket_id)["logged"] is False
    assert target.read_text() == ""                                          # and the symlink was never written through


def test_problems_names_an_in_memory_unsigned_or_unkeyed_desk(tmp_path):
    desk, *_ = make()
    assert any("in memory" in p for p in desk.problems())
    unsigned, *_ = make(tmp_path, signer=None)
    assert any("unsigned" in p for p in unsigned.problems())
    keyless = EscalationDesk(NotificationPolicy((T("t", Channel(), ("zed",)),)), EscalationLog(tmp_path / "k.jsonl", signer=LOG_KEY),
                             responder_keys={})
    assert any("'zed'" in p and "no registered key" in p for p in keyless.problems())
    (tmp_path / "x").mkdir()
    clean, *_ = make(tmp_path / "x")
    assert clean.problems() == []


# ---- wiring into the orchestrator -------------------------------------------------------------------------------

def _orchestrator(kernel, desk, **kw):
    return GovernanceOrchestrator(None, kernel, ObserveClinicalEngine(), escalations=desk, **kw)


def test_both_a_legacy_callable_and_a_desk_is_refused(kernel):
    desk, *_ = make()
    with pytest.raises(ValueError, match="not both"):
        _orchestrator(kernel, desk, alert_human=lambda alert: None)


def test_a_crashed_stage_opens_a_ticket_and_the_record_names_it(kernel):
    desk, _, first, _ = make()
    result, ran = run_chain(_orchestrator(kernel, desk), "a1")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "dispatched" and isinstance(result["human_alert"], AlertOutcome)
    ticket_id = result["escalation_ticket"]
    assert desk.state(ticket_id) == "dispatched"                              # told, not acknowledged
    assert first.calls[0]["stage"] == "perceive" and first.calls[0]["artifact_id"] == "a1"
    assert first.calls[0]["request_id"] == result["governance_request"].request_id


def test_an_unreachable_desk_says_failed_and_the_outcome_is_unchanged(kernel):
    desk, *_ = make(tiers=(T("t", Channel(fail=True), ("alice",)),))
    result, ran = run_chain(_orchestrator(kernel, desk), "a2")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "failed" and desk.state(result["escalation_ticket"]) == "exhausted"


def test_a_desk_that_cannot_write_its_log_still_pages_and_says_so(kernel, monkeypatch):
    desk, _, first, _ = make()

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(desk.log, "append", broken)
    result, ran = run_chain(_orchestrator(kernel, desk), "a3")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "dispatched" and len(first.calls) == 1   # the page was not held up by the log
    assert desk.ticket(result["escalation_ticket"])["logged"] is False and desk.log_failures >= 1


def test_a_desk_that_itself_raises_says_failed_and_the_outcome_is_unchanged(kernel, monkeypatch):
    desk, *_ = make()

    def broken(**k):
        raise RuntimeError("desk bug")
    monkeypatch.setattr(desk, "raise_and_dispatch", broken)
    result, ran = run_chain(_orchestrator(kernel, desk), "a3b")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "failed" and "escalation_ticket" not in result


def test_a_delivery_receipt_is_the_only_way_the_record_says_delivered(kernel):
    desk, *_ = make(tiers=(T("t", Channel({"delivery_receipt": "vendor-1"}), ("alice",)),))
    result, _ = run_chain(_orchestrator(kernel, desk), "a3c")
    assert result["human_alert"] == "delivered" and desk.state(result["escalation_ticket"]) == "delivered"


def test_a_lost_receipt_after_an_action_opens_a_ticket_beside_the_stage_ticket(perceive, kernel, monkeypatch, tmp_path):
    desk, *_ = make()
    receipts = ReceiptLog(tmp_path / "r.jsonl")

    def broken(result):
        raise OSError("disk full")
    monkeypatch.setattr(receipts, "append", broken)
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk, receipts=receipts)
    result, _ = run_chain(orchestrator, "a4")
    assert result["receipt"] is None and result["human_alert"] == "dispatched"
    assert desk.ticket(result["escalation_ticket"])["kind"] == "stage_error"


def test_the_legacy_channel_is_unchanged_and_gains_no_ticket(kernel):
    seen = []
    result, _ = run_chain(GovernanceOrchestrator(None, kernel, ObserveClinicalEngine(), alert_human=seen.append), "a5")
    assert result["human_alert"] == "delivered" and "escalation_ticket" not in result and len(seen) == 1


# ---- review findings: the page goes out whatever else is broken -------------------------------------------------

def test_a_log_that_cannot_be_written_never_stops_the_page(tmp_path, monkeypatch):
    desk, _, first, _ = make(tmp_path)

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(desk.log, "append", broken)
    ticket_id, state = raise_one(desk)
    assert state == "dispatched" and len(first.calls) == 1
    assert desk.ticket(ticket_id)["logged"] is False and desk.log_failures >= 2


def test_an_unwritable_log_path_is_a_startup_problem(tmp_path):
    (tmp_path / "afile").write_text("x")
    desk = EscalationDesk(NotificationPolicy((T("t", Channel(), ("alice",)),)),
                          EscalationLog(tmp_path / "afile" / "log.jsonl", signer=LOG_KEY), responder_keys={"alice": ALICE})
    assert any("cannot be written" in p for p in desk.problems())


def test_a_missing_or_unusable_responder_key_is_a_problem(tmp_path):
    for key, text in ((None, "no registered key"), (object(), "cannot verify")):
        desk = EscalationDesk(NotificationPolicy((T("t", Channel(), ("alice",)),)), EscalationLog(tmp_path / "k.jsonl", signer=LOG_KEY),
                              responder_keys={"alice": key})
        assert any(text in p for p in desk.problems())
        desk.log.close()


def test_a_ticket_opened_but_never_dispatched_is_picked_up_by_the_next_sweep_and_after_a_restart(tmp_path, monkeypatch):
    desk, clock, first, _ = make(tmp_path)
    monkeypatch.setattr(desk, "_dispatch_from", lambda *a: None)               # a crash right after "opened"
    ticket_id, state = raise_one(desk)
    assert state == "raised" and first.calls == []
    desk.log.close()
    again, _, first2, _ = make(tmp_path, clock=clock)
    assert again.state(ticket_id) == "raised"
    assert again.sweep() == [(ticket_id, "dispatched")] and len(first2.calls) == 1


def test_a_ticket_stranded_in_overdue_is_picked_up_and_other_tickets_are_not_skipped(tmp_path, monkeypatch):
    desk, clock, _, second = make(tmp_path)
    one, _ = raise_one(desk)
    two, _ = raise_one(desk)
    clock.advance(60)
    real = desk._dispatch_from

    def explode_for_one(t, start):
        if t.ticket_id == one:
            raise OSError("transient")
        return real(t, start)
    monkeypatch.setattr(desk, "_dispatch_from", explode_for_one)
    desk.sweep()
    assert desk.state(one) == "overdue" and desk.state(two) == "dispatched" and len(second.calls) == 1
    assert desk.seconds_since_sweep() is None                                 # a sweep that failed is not "fresh"
    monkeypatch.setattr(desk, "_dispatch_from", real)
    desk.log.close()
    again, _, _, second2 = make(tmp_path, clock=clock)                        # and the same after a restart
    assert again.state(one) == "overdue"
    assert (one, "dispatched") in again.sweep() and len(second2.calls) == 1


def test_a_clean_sweep_resets_the_sweep_age_and_a_failed_one_does_not(tmp_path):
    desk, clock, *_ = make()
    assert desk.seconds_since_sweep() is None
    desk.sweep()
    clock.advance(40)
    assert desk.seconds_since_sweep() == 40


def test_nobody_reached_is_retried_on_each_sweep_and_logged_exhausted_once():
    channel = Channel(fail=True)
    desk, clock, *_ = make(tiers=(T("only", channel, ("alice",)),))
    ticket_id, state = raise_one(desk)
    assert state == "exhausted"
    desk.sweep()
    desk.sweep()
    assert len(channel.calls) == 3                                           # tried again every sweep
    assert [e["event"] for e in desk.log.entries].count("exhausted") == 1
    channel.fail = False
    assert desk.sweep() == [(ticket_id, "dispatched")] and desk.state(ticket_id) == "dispatched"


def test_only_a_tier_that_was_actually_reached_may_acknowledge():
    desk, _, first, second = make()
    first.fail = True
    ticket_id, _ = raise_one(desk)
    with pytest.raises(AcknowledgementError, match="not a responder on any tier that reached"):
        ack(desk, ticket_id)                                                  # alice's tier never got the page
    assert ack(desk, ticket_id, BOB, "bob") == "acknowledged"


def test_a_hung_channel_costs_its_timeout_and_holds_nobody_else_up():
    release = threading.Event()
    entered = threading.Event()

    def hung(payload):
        entered.set()
        release.wait(10)
    second = Channel()
    desk, *_ = make(tiers=(T("charge", hung, ("alice",), 60.0, 0.3), T("physician", second, ("bob",))))
    started = time.monotonic()
    result = {}
    worker = threading.Thread(target=lambda: result.update(r=raise_one(desk)))
    worker.start()
    entered.wait(2)
    probe_started = time.monotonic()
    desk.open_tickets()                                                      # the desk lock is not held by the page
    assert time.monotonic() - probe_started < 0.2
    worker.join(5)
    assert result["r"][1] == "dispatched" and len(second.calls) == 1
    assert time.monotonic() - started < 3
    release.set()


def test_a_channel_that_edits_its_payload_changes_nothing_that_is_recorded():
    def vandal(payload):
        payload["ticket_id"] = "forged"
        payload.clear()
    desk, *_ = make(tiers=(T("t", vandal, ("alice",)),))
    ticket_id, _ = raise_one(desk)
    assert desk.ticket(ticket_id)["ticket_id"] == ticket_id and ticket_id.startswith("esc-")


def test_the_payload_carries_what_a_responder_needs_to_sign():
    desk, _, first, _ = make()
    ticket_id, _ = raise_one(desk)
    payload = first.calls[0]
    sig = sign_acknowledgement(ALICE, payload["ticket_id"], payload["opened_hash"], "alice")
    assert desk.acknowledge(ticket_id, "alice", sig) == "acknowledged"


def test_ids_are_unique_when_tickets_are_raised_from_many_threads():
    desk, *_ = make()
    ids, lock = [], threading.Lock()

    def go():
        ticket_id, _ = raise_one(desk)
        with lock:
            ids.append(ticket_id)
    threads = [threading.Thread(target=go) for _ in range(20)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()
    assert len(set(ids)) == 20 and len(desk.open_tickets()) == 20


# ---- the log file: one writer, torn tail, forged acknowledgement ------------------------------------------------

def test_a_second_writer_on_the_same_log_is_refused_until_the_first_lets_go(tmp_path):
    first = EscalationLog(tmp_path / "w.jsonl")
    with pytest.raises(EscalationLogError, match="another writer"):
        EscalationLog(tmp_path / "w.jsonl")
    first.close()
    EscalationLog(tmp_path / "w.jsonl").close()


def test_a_torn_final_line_is_set_aside_and_the_desk_starts(tmp_path):
    desk, clock, *_ = make(tmp_path)
    ticket_id, _ = raise_one(desk)
    desk.log.close()
    path = tmp_path / "esc.jsonl"
    path.write_text(path.read_text() + '{"seq": 2, "previous": "abc", "tick')   # power cut mid-write
    again, *_ = make(tmp_path, clock=clock)
    assert again.state(ticket_id) == "dispatched" and (tmp_path / "esc.jsonl.torn").exists()
    again.log.close()
    assert path.read_text().endswith("\n")


def test_a_whole_final_line_that_only_lost_its_newline_is_kept(tmp_path):
    desk, clock, *_ = make(tmp_path)
    ticket_id, _ = raise_one(desk)
    desk.log.close()
    path = tmp_path / "esc.jsonl"
    path.write_text(path.read_text().rstrip("\n"))
    again, *_ = make(tmp_path, clock=clock)
    assert again.state(ticket_id) == "dispatched" and not (tmp_path / "esc.jsonl.torn").exists()
    again.log.close()


def test_a_damaged_line_that_is_not_the_last_is_an_error_not_a_repair(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    desk.log.close()
    path = tmp_path / "esc.jsonl"
    path.write_text("not json\n" + path.read_text())
    with pytest.raises(EscalationLogError, match="not valid JSON"):
        make(tmp_path)


def test_an_acknowledgement_written_into_an_unsigned_log_without_a_valid_signature_does_not_load(tmp_path):
    desk, *_ = make(tmp_path, signer=None)
    ticket_id, _ = raise_one(desk)
    desk.log.append(ticket_id, "acknowledged", "2026-01-01T12:00:00+00:00",
                    {"actor_id": "alice", "note": "", "signature": {"key_id": "alice", "signature": "00"}})
    desk.log.close()
    with pytest.raises(EscalationLogError, match="does not verify"):
        make(tmp_path, signer=None)


def test_an_opening_whose_hash_was_tampered_does_not_load(tmp_path):
    desk, *_ = make(tmp_path, signer=None)
    raise_one(desk)
    desk.log.close()
    path = tmp_path / "esc.jsonl"
    lines = path.read_text().splitlines()
    first = json.loads(lines[0])
    first["data"]["artifact_id"] = "someone-else"
    from human_escalation import _event_hash
    first["hash"] = _event_hash(first)                                       # a forger who can recompute the chain
    path.write_text(json.dumps(first) + "\n")
    with pytest.raises(EscalationLogError, match="does not match its hash"):
        make(tmp_path, signer=None)


def test_the_startup_probe_creates_the_log_owner_only(tmp_path):
    desk, *_ = make(tmp_path)
    desk.problems()
    assert os.stat(tmp_path / "esc.jsonl").st_mode & 0o777 == 0o600


def test_a_sweep_during_the_first_dispatch_does_not_page_the_same_tier_twice():
    release, entered = threading.Event(), threading.Event()
    calls = []

    def slow(payload):
        calls.append(payload["ticket_id"])
        entered.set()
        release.wait(5)
    desk, *_ = make(tiers=(T("charge", slow, ("alice",), 60.0, 5.0),))
    worker = threading.Thread(target=lambda: raise_one(desk))
    worker.start()
    entered.wait(2)
    desk.sweep()                                                             # the ticket is "raised" and mid-dispatch
    release.set()
    worker.join(5)
    assert len(calls) == 1
