"""test_human_escalation.py -- telling a person, with evidence of each step.

The claims under test: a returned callable is only DISPATCHED; only the channel's own receipt makes it DELIVERED; only a
responder's signature makes it ACKNOWLEDGED; a failed dispatch does not wait; an unanswered ticket moves up and ends
EXHAUSTED rather than quiet; the log verifies; and none of this alters a clinical outcome.
"""
from __future__ import annotations

import json
import os
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


def make(tmp_path=None, tiers=None, clock=None, signer=LOG_KEY):
    clock = clock or Clock()
    first, second = Channel(), Channel()
    tiers = tiers or (Tier("charge", first, ("alice",), 60.0), Tier("physician", second, ("bob",), 120.0))
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
    policy = NotificationPolicy((Tier("t", Channel(), ("alice",), seconds),))
    assert any("ack_within_s" in p for p in policy.problems())


def test_an_empty_or_malformed_policy_is_a_problem():
    assert NotificationPolicy(()).problems()
    bad = NotificationPolicy((Tier("", Channel(), (), 10), Tier("x", None, ("a",), 10), Tier("x", Channel(), ("a",), 10)))
    text = " | ".join(bad.problems())
    assert "no name" in text and "no responder" in text and "no callable channel" in text and "used twice" in text


def test_tier_has_no_default_for_a_deadline_or_a_responder():
    with pytest.raises(TypeError):
        Tier("t", Channel())                                                 # type: ignore[call-arg]


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
    desk, _, first, _ = make(tiers=(Tier("t", Channel(answer), ("alice",), 60),))
    assert raise_one(desk)[1] == "dispatched"


def test_a_channel_receipt_makes_it_delivered_and_is_logged():
    desk, *_ = make(tiers=(Tier("t", Channel({"delivery_receipt": "vendor-42"}), ("alice",), 60),))
    ticket_id, state = raise_one(desk)
    assert state == "delivered"
    assert desk.log.entries[1]["data"]["delivery_receipt"] == "vendor-42"


@pytest.mark.parametrize("answer", [{"acknowledged": True, "delivery_receipt": "x"}, {"state": "acknowledged"}, "acknowledged"])
def test_a_channel_can_never_acknowledge(answer):
    desk, *_ = make(tiers=(Tier("t", Channel(answer), ("alice",), 60),))
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
    desk, *_ = make(tiers=(Tier("t", Channel(), ("alice", "bob"), 60),))
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
    desk, *_ = make(tiers=(Tier("t", Channel(fail=True), ("alice",), 60),))
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
    desk, *_ = make(tiers=(Tier("a", Channel(fail=True), ("alice",), 60), Tier("b", Channel(fail=True), ("bob",), 60)))
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
    again, *_ = make(tmp_path, clock=clock)
    assert again.state(one) == "acknowledged" and again.state(two) == "dispatched"
    assert again.ticket(two)["tier"] == "physician" and again.ticket(two)["history"] == desk.ticket(two)["history"]
    assert raise_one(again)[0] == "esc-%06d" % len(desk.log)


def _lines(tmp_path):
    return (tmp_path / "esc.jsonl").read_text().splitlines()


def test_an_edited_removed_or_foreign_signed_log_does_not_load(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    raise_one(desk)
    lines = _lines(tmp_path)

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
    with pytest.raises(EscalationLogError, match="current policy does not have"):
        make(tmp_path, tiers=(Tier("renamed", Channel(), ("alice",), 60),))


def test_the_log_file_is_owner_only_and_refuses_a_symlink(tmp_path):
    desk, *_ = make(tmp_path)
    raise_one(desk)
    assert os.stat(tmp_path / "esc.jsonl").st_mode & 0o777 == 0o600
    target = tmp_path / "elsewhere.jsonl"
    target.write_text("")
    link = tmp_path / "link.jsonl"
    link.symlink_to(target)
    bad = EscalationDesk(NotificationPolicy((Tier("t", Channel(), ("alice",), 60),)), EscalationLog(link),
                         responder_keys={"alice": ALICE})
    with pytest.raises(OSError):
        raise_one(bad)


def test_problems_names_an_in_memory_unsigned_or_unkeyed_desk(tmp_path):
    desk, *_ = make()
    assert any("in memory" in p for p in desk.problems())
    unsigned, *_ = make(tmp_path, signer=None)
    assert any("unsigned" in p for p in unsigned.problems())
    keyless = EscalationDesk(NotificationPolicy((Tier("t", Channel(), ("zed",), 60),)), EscalationLog(tmp_path / "k.jsonl", signer=LOG_KEY),
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
    assert result["human_alert"] == "delivered" and isinstance(result["human_alert"], AlertOutcome)
    ticket_id = result["escalation_ticket"]
    assert desk.state(ticket_id) == "dispatched"                              # told, not acknowledged
    assert first.calls[0]["stage"] == "perceive" and first.calls[0]["artifact_id"] == "a1"
    assert first.calls[0]["request_id"] == result["governance_request"].request_id


def test_an_unreachable_desk_says_failed_and_the_outcome_is_unchanged(kernel):
    desk, *_ = make(tiers=(Tier("t", Channel(fail=True), ("alice",), 60),))
    result, ran = run_chain(_orchestrator(kernel, desk), "a2")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "failed" and desk.state(result["escalation_ticket"]) == "exhausted"


def test_a_desk_that_cannot_write_its_log_says_failed_and_the_outcome_is_unchanged(kernel, monkeypatch):
    desk, *_ = make()

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(desk.log, "append", broken)
    result, ran = run_chain(_orchestrator(kernel, desk), "a3")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "failed" and "escalation_ticket" not in result


def test_a_lost_receipt_after_an_action_opens_a_ticket_beside_the_stage_ticket(perceive, kernel, monkeypatch, tmp_path):
    desk, *_ = make()
    receipts = ReceiptLog(tmp_path / "r.jsonl")

    def broken(result):
        raise OSError("disk full")
    monkeypatch.setattr(receipts, "append", broken)
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), escalations=desk, receipts=receipts)
    result, _ = run_chain(orchestrator, "a4")
    assert result["receipt"] is None and result["human_alert"] == "delivered"
    assert desk.ticket(result["escalation_ticket"])["kind"] == "stage_error"


def test_the_legacy_channel_is_unchanged_and_gains_no_ticket(kernel):
    seen = []
    result, _ = run_chain(GovernanceOrchestrator(None, kernel, ObserveClinicalEngine(), alert_human=seen.append), "a5")
    assert result["human_alert"] == "delivered" and "escalation_ticket" not in result and len(seen) == 1
