"""The escalation desk with a live authority check: a responder whose role is revoked cannot acknowledge."""
import pytest
from conservation_kernel import HmacSigner

from actor_registry import ActorRegistry, sign_registry_action
from human_escalation import (AcknowledgementError, EscalationDesk, EscalationLog, NotificationPolicy, Tier,
                              sign_acknowledgement)

ROOT = HmacSigner(b"desk-authority-test-root-key-0123456789ab", key_id="k-root")
NURSE = HmacSigner(b"desk-authority-test-nurse-key-0123456789a", key_id="k-nurse")
KEYS = {"k-root": ROOT, "k-nurse": NURSE}


def _act(reg, kind, **body):
    full = {"actor_id": body["actor_id"], "role": body["role"]}
    if kind == "revoke":
        full["reason"] = body["reason"]
    else:
        full.update(key_id=body["key_id"], expires_at=None)
    return getattr(reg, kind)(by="root", signature=sign_registry_action(ROOT, reg.head, kind, "root", full), **body)


def _wired(authority=True):
    reg = ActorRegistry(roots={"root": "k-root"}, keys=KEYS)
    _act(reg, "grant", actor_id="nurse", role="responder", key_id="k-nurse")
    desk = EscalationDesk(NotificationPolicy((Tier("charge", lambda payload: None, ("nurse",), 60.0, 5.0),)),
                          EscalationLog(), responder_keys=reg.responder_keys("responder"),
                          authority=reg.authority("responder") if authority else None)
    ticket_id, _ = desk.raise_and_dispatch(kind="test")
    return reg, desk, ticket_id


def _ack(desk, ticket_id):
    opened = desk.ticket(ticket_id)["opened_hash"]
    return desk.acknowledge(ticket_id, "nurse", sign_acknowledgement(NURSE, ticket_id, opened, "nurse"))


def test_a_responder_who_holds_the_role_acknowledges():
    _, desk, ticket_id = _wired()
    assert _ack(desk, ticket_id) == "acknowledged"


def test_a_revoked_responder_is_refused_although_their_key_is_still_on_file():
    reg, desk, ticket_id = _wired()
    _act(reg, "revoke", actor_id="nurse", role="responder", reason="suspended")
    assert "nurse" in desk.responder_keys
    with pytest.raises(AcknowledgementError, match="does not currently hold"):
        _ack(desk, ticket_id)
    assert desk.state(ticket_id) != "acknowledged"


def test_without_an_authority_the_fixed_map_decides_as_before():
    reg, desk, ticket_id = _wired(authority=False)
    _act(reg, "revoke", actor_id="nurse", role="responder", reason="suspended")
    assert _ack(desk, ticket_id) == "acknowledged"


@pytest.mark.parametrize("answer", [False, None, 1, "yes"])
def test_anything_but_true_from_the_authority_is_a_refusal(answer):
    desk = EscalationDesk(NotificationPolicy((Tier("charge", lambda payload: None, ("nurse",), 60.0, 5.0),)),
                          EscalationLog(), responder_keys={"nurse": NURSE}, authority=lambda actor: answer)
    ticket_id, _ = desk.raise_and_dispatch(kind="test")
    with pytest.raises(AcknowledgementError):
        _ack(desk, ticket_id)


def test_an_authority_that_raises_is_a_refusal():
    def broken(actor):
        raise RuntimeError("registry unreachable")
    desk = EscalationDesk(NotificationPolicy((Tier("charge", lambda payload: None, ("nurse",), 60.0, 5.0),)),
                          EscalationLog(), responder_keys={"nurse": NURSE}, authority=broken)
    ticket_id, _ = desk.raise_and_dispatch(kind="test")
    with pytest.raises(AcknowledgementError, match="authority check failed"):
        _ack(desk, ticket_id)
