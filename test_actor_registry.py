"""The actor registry: who may act, and how that changes, proved by the people who change it."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from conservation_kernel import HmacSigner

from actor_registry import ADMIN, ActorRegistry, RegistryError, sign_registry_action

T0 = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)
KEYS = {k: HmacSigner(f"actor-registry-test-key-{k}-0123456789abcdef".encode(), key_id=k)
        for k in ("k-root", "k-admin", "k-nurse", "k-locum", "k-other")}


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def registry(tmp_path=None, clock=None):
    return ActorRegistry(roots={"root": "k-root"}, keys=KEYS, clock=clock or Clock(),
                         path=None if tmp_path is None else tmp_path / "registry.jsonl")


def act(reg, kind, by, signer_key, **fields):
    """Make an action the way an actor would: sign it against the current head with their own key."""
    body = {"actor_id": fields["actor_id"], "role": fields["role"]}
    if kind == "revoke":
        body["reason"] = fields["reason"]
    else:
        body["key_id"] = fields["key_id"]
        exp = fields.get("expires_at")
        body["expires_at"] = None if exp is None else exp.isoformat()
    signature = sign_registry_action(KEYS[signer_key], reg.head, kind, by, body)
    return getattr(reg, kind)(by=by, signature=signature, **fields)


# ---- construction ------------------------------------------------------------------------------------------------

def test_there_is_no_default_root():
    with pytest.raises(RegistryError, match="no roots"):
        ActorRegistry(roots={}, keys=KEYS)


def test_a_root_without_a_key_is_refused():
    with pytest.raises(RegistryError, match="no key"):
        ActorRegistry(roots={"root": "k-missing"}, keys=KEYS)


def test_roots_hold_admin_and_nobody_else_holds_anything():
    reg = registry()
    assert reg.holds("root", ADMIN)
    assert not reg.holds("nurse", "responder") and "never given" in reg.why_not("nurse", "responder")


# ---- grant -------------------------------------------------------------------------------------------------------

def test_an_admin_grants_a_role_and_it_is_held():
    reg = registry()
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse")
    assert reg.holds("nurse", "responder") and reg.key_id_of("nurse") == "k-nurse"


def test_a_non_admin_cannot_grant():
    reg = registry()
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse")
    with pytest.raises(RegistryError, match="may not grant"):
        act(reg, "grant", "nurse", "k-nurse", actor_id="other", role="responder", key_id="k-other")


def test_nobody_grants_to_themselves():
    reg = registry()
    with pytest.raises(RegistryError, match="themselves"):
        act(reg, "grant", "root", "k-root", actor_id="root", role="responder", key_id="k-root")


def test_a_forged_signature_is_refused_and_nothing_is_recorded():
    reg = registry()
    body = {"actor_id": "nurse", "role": "responder", "key_id": "k-nurse", "expires_at": None}
    forged = sign_registry_action(KEYS["k-other"], reg.head, "grant", "root", body)
    with pytest.raises(RegistryError, match="signature"):
        reg.grant("nurse", "responder", "k-nurse", by="root", signature=forged)
    assert reg.events == [] and not reg.holds("nurse", "responder")


def test_a_signed_action_cannot_be_replayed_later():
    reg = registry()
    body = {"actor_id": "nurse", "role": "responder", "key_id": "k-nurse", "expires_at": None}
    signature = sign_registry_action(KEYS["k-root"], reg.head, "grant", "root", body)
    reg.grant("nurse", "responder", "k-nurse", by="root", signature=signature)
    act(reg, "revoke", "root", "k-root", actor_id="nurse", role="responder", reason="left the unit")
    with pytest.raises(RegistryError, match="signature"):
        reg.grant("nurse", "responder", "k-nurse", by="root", signature=signature)
    assert not reg.holds("nurse", "responder")


def test_one_key_cannot_belong_to_two_actors_and_an_actor_keeps_their_key():
    reg = registry()
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse")
    with pytest.raises(RegistryError, match="another actor"):
        act(reg, "grant", "root", "k-root", actor_id="other", role="responder", key_id="k-nurse")
    with pytest.raises(RegistryError, match="bound to key"):
        act(reg, "grant", "root", "k-root", actor_id="nurse", role="reviewer", key_id="k-other")


def test_a_grant_expires():
    clock = Clock()
    reg = registry(clock=clock)
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse",
        expires_at=T0 + timedelta(hours=8))
    clock.advance(hours=8)
    assert "expired" in reg.why_not("nurse", "responder")


def test_a_granted_admin_can_grant():
    reg = registry()
    act(reg, "grant", "root", "k-root", actor_id="admin", role=ADMIN, key_id="k-admin")
    act(reg, "grant", "admin", "k-admin", actor_id="nurse", role="responder", key_id="k-nurse")
    assert reg.holds("nurse", "responder")


# ---- delegate ----------------------------------------------------------------------------------------------------

def _with_nurse(clock=None, expires=None):
    reg = registry(clock=clock)
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse", expires_at=expires)
    return reg


def test_a_holder_lends_a_role_for_a_time():
    clock = Clock()
    reg = _with_nurse(clock)
    act(reg, "delegate", "nurse", "k-nurse", actor_id="locum", role="responder", key_id="k-locum",
        expires_at=T0 + timedelta(hours=1))
    assert reg.holds("locum", "responder")
    clock.advance(hours=1)
    assert "expired" in reg.why_not("locum", "responder")


def test_a_delegation_must_end_and_not_outlast_the_delegator():
    reg = _with_nurse(expires=T0 + timedelta(hours=2))
    with pytest.raises(RegistryError, match="must end"):
        reg.delegate("locum", "responder", "k-locum", by="nurse", signature={}, expires_at=None)
    with pytest.raises(RegistryError, match="outlast"):
        act(reg, "delegate", "nurse", "k-nurse", actor_id="locum", role="responder", key_id="k-locum",
            expires_at=T0 + timedelta(hours=3))


def test_a_delegated_role_cannot_be_delegated_again_and_admin_cannot_be_delegated():
    reg = _with_nurse()
    act(reg, "delegate", "nurse", "k-nurse", actor_id="locum", role="responder", key_id="k-locum",
        expires_at=T0 + timedelta(hours=1))
    with pytest.raises(RegistryError, match="direct grant"):
        act(reg, "delegate", "locum", "k-locum", actor_id="other", role="responder", key_id="k-other",
            expires_at=T0 + timedelta(minutes=30))
    with pytest.raises(RegistryError, match="cannot be delegated"):
        act(reg, "delegate", "root", "k-root", actor_id="admin", role=ADMIN, key_id="k-admin",
            expires_at=T0 + timedelta(hours=1))


def test_revoking_the_delegator_ends_what_they_lent():
    reg = _with_nurse()
    act(reg, "delegate", "nurse", "k-nurse", actor_id="locum", role="responder", key_id="k-locum",
        expires_at=T0 + timedelta(hours=1))
    act(reg, "revoke", "root", "k-root", actor_id="nurse", role="responder", reason="suspended")
    assert "no longer holds it" in reg.why_not("locum", "responder")


# ---- revoke ------------------------------------------------------------------------------------------------------

def test_a_delegator_can_take_back_what_they_lent_but_not_more():
    reg = _with_nurse()
    act(reg, "grant", "root", "k-root", actor_id="other", role="responder", key_id="k-other")
    act(reg, "delegate", "nurse", "k-nurse", actor_id="locum", role="responder", key_id="k-locum",
        expires_at=T0 + timedelta(hours=1))
    act(reg, "revoke", "nurse", "k-nurse", actor_id="locum", role="responder", reason="shift over")
    assert not reg.holds("locum", "responder") and reg.holds("nurse", "responder")
    with pytest.raises(RegistryError, match="may not revoke"):
        act(reg, "revoke", "nurse", "k-nurse", actor_id="other", role="responder", reason="dislike")


def test_a_revocation_must_say_why():
    reg = _with_nurse()
    with pytest.raises(RegistryError, match="say why"):
        reg.revoke("nurse", "responder", by="root", signature={}, reason=" ")


def test_holds_answers_for_a_past_moment():
    clock = Clock()
    reg = _with_nurse(clock)
    clock.advance(hours=1)
    act(reg, "revoke", "root", "k-root", actor_id="nurse", role="responder", reason="left")
    assert reg.holds("nurse", "responder", at=T0 + timedelta(minutes=30))
    assert not reg.holds("nurse", "responder")
    assert not reg.holds("nurse", "responder", at=T0 - timedelta(minutes=1))


# ---- the durable log ---------------------------------------------------------------------------------------------

def test_a_reloaded_registry_proves_and_keeps_its_history(tmp_path):
    reg = registry(tmp_path)
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse")
    again = registry(tmp_path)
    assert again.holds("nurse", "responder") and again.head == reg.head


def test_the_log_carries_no_key_material(tmp_path):
    reg = registry(tmp_path)
    act(reg, "grant", "root", "k-root", actor_id="nurse", role="responder", key_id="k-nurse")
    text = (tmp_path / "registry.jsonl").read_text()
    assert "actor-registry-test-key" not in text


@pytest.mark.parametrize("tamper", ["edit", "drop", "unsigned-insert", "backdate"])
def test_a_tampered_log_does_not_start(tmp_path, tamper):
    reg = registry(tmp_path)
    act(reg, "grant", "root", "k-root", actor_id="admin", role=ADMIN, key_id="k-admin")
    act(reg, "grant", "admin", "k-admin", actor_id="nurse", role="responder", key_id="k-nurse")
    path = tmp_path / "registry.jsonl"
    lines = path.read_text().splitlines()
    if tamper == "edit":
        event = json.loads(lines[1])
        event["body"]["role"] = ADMIN
        lines[1] = json.dumps(event, sort_keys=True)
    elif tamper == "drop":
        del lines[0]
    elif tamper == "backdate":                       # a root's grant, moved earlier: only the hash chain covers time
        event = json.loads(lines[0])
        event["at"] = (T0 - timedelta(days=30)).isoformat()
        lines[0] = json.dumps(event, sort_keys=True)
    else:
        event = json.loads(lines[1])
        event["body"]["actor_id"], event["signature"] = "intruder", {}
        from actor_registry import _digest
        event["hash"] = _digest({k: v for k, v in event.items() if k != "hash"})
        lines[0] = json.dumps(event, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(RegistryError):
        registry(tmp_path)


def test_responder_keys_and_authority_follow_the_registry():
    reg = _with_nurse()
    assert set(reg.responder_keys("responder")) == {"nurse"}
    check = reg.authority("responder")
    assert check("nurse") is True
    act(reg, "revoke", "root", "k-root", actor_id="nurse", role="responder", reason="left")
    assert check("nurse") is False and reg.responder_keys("responder") == {}
