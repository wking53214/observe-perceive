"""docs/KEY_CUSTODY.md says four slots only ever verify, so they can hold a key that cannot sign. These tests are that
claim: each slot is given an object whose `sign` raises, and still does its whole job."""
from datetime import datetime, timedelta, timezone

import pytest
from conservation_kernel import HmacSigner

from actor_registry import ActorRegistry, sign_registry_action
from heartbeat import HeartbeatWriter, check_heartbeat
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier, sign_acknowledgement
from sentinel_perceive_adapter import SentinelPerceiveAdapter


class VerifyOnly:
    """Stands in for a public key: it checks signatures and cannot make one."""

    def __init__(self, signer):
        self.key_id, self.algorithm, self._signer = signer.key_id, signer.algorithm, signer

    def verify(self, payload, signature):
        return self._signer.verify(payload, signature)

    def sign(self, payload):
        raise AssertionError("a verify-only slot tried to sign")


ROOT = HmacSigner(b"key-custody-test-root-key-0123456789abcd", key_id="root")
NURSE = HmacSigner(b"key-custody-test-nurse-key-0123456789abc", key_id="nurse")
FEED = HmacSigner(b"key-custody-test-feed-key-0123456789abcd", key_id="feed")
BEAT = HmacSigner(b"key-custody-test-beat-key-0123456789abcd", key_id="beat")


def test_the_registry_needs_only_to_verify_its_actors():
    reg = ActorRegistry(roots={"root": "root"}, keys={"root": VerifyOnly(ROOT), "nurse": VerifyOnly(NURSE)})
    body = {"actor_id": "nurse", "role": "responder", "key_id": "nurse", "expires_at": None}
    reg.grant("nurse", "responder", "nurse", by="root",
              signature=sign_registry_action(ROOT, reg.head, "grant", "root", body))   # signed on the actor's side
    assert reg.holds("nurse", "responder")


def test_the_desk_needs_only_to_verify_its_responders():
    desk = EscalationDesk(NotificationPolicy((Tier("charge", lambda payload: None, ("nurse",), 60.0, 5.0),)),
                          EscalationLog(), responder_keys={"nurse": VerifyOnly(NURSE)})
    ticket_id, _ = desk.raise_and_dispatch(kind="test")
    opened = desk.ticket(ticket_id)["opened_hash"]
    assert desk.acknowledge(ticket_id, "nurse", sign_acknowledgement(NURSE, ticket_id, opened, "nurse")) == "acknowledged"


def test_event_time_sources_need_only_to_be_verified():
    class Meta:
        event_time_attestation = SentinelPerceiveAdapter.attest_event_time(FEED, "a-1", "2026-11-21T12:00:00+00:00")
        occurred_at = "2026-11-21T12:00:00+00:00"

    class Art:
        artifact_id, metadata = "a-1", Meta()

    attested, key_id, problem = SentinelPerceiveAdapter.check_event_time_attestation(
        Art(), "2026-11-21T12:00:00+00:00", [VerifyOnly(FEED)])
    assert (attested, key_id, problem) == (True, "feed", None)


def test_the_heartbeat_checker_needs_only_to_verify(tmp_path):
    at = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
    path = tmp_path / "hb.json"
    HeartbeatWriter(path, BEAT, label="w", clock=lambda: at)(
        {"running": True, "alarmed": False, "undelivered": 0, "delivery_stuck": False})
    assert check_heartbeat(path, VerifyOnly(BEAT), max_age_s=60, now=at + timedelta(seconds=5)).healthy


def test_a_slot_that_signs_cannot_take_a_verify_only_key(tmp_path):
    """The other side of the rule: a writer signs, so it needs the signing half."""
    assert HeartbeatWriter(tmp_path / "hb.json", VerifyOnly(BEAT), label="w")({"running": True}) is False
    with pytest.raises(AssertionError, match="tried to sign"):
        VerifyOnly(BEAT).sign(b"x")
