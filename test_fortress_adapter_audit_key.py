"""FORTRESS's audit key: the adapter passes a configured key, never a literal, and production refuses none.

fortress-kernel before c361569 signed every audit record with the published default key "fortress-key", so anyone
could forge records that verify. From that commit the key comes from the caller or FORTRESS_AUDIT_KEY, and without
either the kernel signs with a random per-process key (`ledger.ephemeral_key` is True). Every key these tests sign
with is generated per run.
"""
import copy
import hashlib
import hmac
import secrets

import pytest

# The adapter raises a plain ImportError when the pack is absent; skip, as the other FORTRESS suites do.
try:
    from fortress_perceive_adapter import FortressPerceiveAdapter, ImmutableAuditLedger
except ImportError:  # pragma: no cover - fortress-kernel is an optional pack
    pytest.skip("fortress-kernel checkout not available", allow_module_level=True)

from production_profile import production_problems

# The default an earlier fortress-kernel signed with. Used only to show records do NOT verify under it.
OLD_PUBLISHED_DEFAULT = "fortress-key"
FORTRESS_PROBLEM = "FORTRESS is in the chain"


@pytest.fixture(autouse=True)
def _no_ambient_key(monkeypatch):
    """Each test starts with FORTRESS_AUDIT_KEY unset and sets it itself when it needs one."""
    monkeypatch.delenv("FORTRESS_AUDIT_KEY", raising=False)


def adapter(**kwargs):
    return FortressPerceiveAdapter(controller_mode="energy", enable_logging=False, **kwargs)


def record_one(a):
    a.process_governance_request(
        request_id="key-001", perceive_decision={"error_signal": 2.0, "live_signal": 100.0},
        artifact_content="audit key test", artifact_metadata={"origin": "sentinel", "signature": "sig"})
    return a


def verifies_elsewhere(a, key):
    """A second ledger holding `key` accepts the adapter's records: they can be checked outside it."""
    reader = ImmutableAuditLedger(audit_key=key)
    reader.ledger = copy.deepcopy(a.get_audit_trail())
    return reader.verify_integrity()


def test_a_configured_key_signs_the_ledger_and_its_records_verify_elsewhere():
    key = secrets.token_hex(32)
    a = record_one(adapter(audit_key=key))
    assert a.fortress.audit.ephemeral_key is False
    assert a.fortress.audit.key_id == ImmutableAuditLedger(audit_key=key).key_id
    assert a.verify_audit_integrity()
    assert verifies_elsewhere(a, key)
    assert not verifies_elsewhere(a, secrets.token_hex(32))


def test_the_environment_key_is_used_when_none_is_passed(monkeypatch):
    key = secrets.token_hex(32)
    monkeypatch.setenv("FORTRESS_AUDIT_KEY", key)
    a = record_one(adapter())
    assert a.fortress.audit.ephemeral_key is False
    assert verifies_elsewhere(a, key)


def test_a_passed_key_wins_over_the_environment(monkeypatch):
    monkeypatch.setenv("FORTRESS_AUDIT_KEY", secrets.token_hex(32))
    key = secrets.token_hex(32)
    a = record_one(adapter(audit_key=key))
    assert a.fortress.audit.key_id == ImmutableAuditLedger(audit_key=key).key_id
    assert verifies_elsewhere(a, key)


@pytest.mark.parametrize("env_value", [None, ""])
def test_without_a_key_the_ledger_is_per_process_and_not_the_old_default(monkeypatch, env_value):
    if env_value is not None:
        monkeypatch.setenv("FORTRESS_AUDIT_KEY", env_value)   # an empty value counts as unset
    first, second = record_one(adapter()), record_one(adapter())
    assert first.fortress.audit.ephemeral_key is True and second.fortress.audit.ephemeral_key is True
    assert first.fortress.audit.key_id != second.fortress.audit.key_id   # random each time, not a constant
    assert first.verify_audit_integrity()                                # tampering is still caught in-process
    record = dict(first.get_audit_trail()[0])
    signature = record.pop("hmac")
    forged = hmac.new(OLD_PUBLISHED_DEFAULT.encode(), ImmutableAuditLedger._canonical_json(record),
                      hashlib.sha256).hexdigest()
    assert not hmac.compare_digest(signature, forged)
    assert not verifies_elsewhere(first, OLD_PUBLISHED_DEFAULT)


def _problems(fortress_adapter):
    return production_problems(conservation_kernel=None, root_admission=None, observe_engine=None, receipts=None,
                               execution_ledger=None, alert_human=None, source_signers=None, guard_executor=False,
                               fortress_adapter=fortress_adapter)


def test_production_refuses_fortress_without_a_configured_key():
    problems = [p for p in _problems(adapter()) if FORTRESS_PROBLEM in p]
    assert len(problems) == 1 and "FORTRESS_AUDIT_KEY" in problems[0]


def test_production_accepts_fortress_with_a_configured_key(monkeypatch):
    assert not [p for p in _problems(adapter(audit_key=secrets.token_hex(32))) if FORTRESS_PROBLEM in p]
    monkeypatch.setenv("FORTRESS_AUDIT_KEY", secrets.token_hex(32))
    assert not [p for p in _problems(adapter()) if FORTRESS_PROBLEM in p]


def test_production_without_fortress_has_no_fortress_problem():
    assert not [p for p in _problems(None) if FORTRESS_PROBLEM in p]


def test_production_refuses_a_fortress_ledger_that_cannot_say_whether_its_key_is_ephemeral():
    """A ledger without `ephemeral_key` is a kernel older than c361569; production cannot tell its key is real."""
    class OldLedger:
        pass

    class OldAdapter:
        class fortress:
            audit = OldLedger()

    assert [p for p in _problems(OldAdapter()) if FORTRESS_PROBLEM in p]


def _from(source, key, monkeypatch):
    """An adapter given `key` as its argument, or through FORTRESS_AUDIT_KEY."""
    if source == "env":
        monkeypatch.setenv("FORTRESS_AUDIT_KEY", key)
        return adapter()
    return adapter(audit_key=key)


@pytest.mark.parametrize("source", ["env", "arg"])
@pytest.mark.parametrize("value", [OLD_PUBLISHED_DEFAULT, f"  {OLD_PUBLISHED_DEFAULT}  "])
def test_production_refuses_the_old_published_key_configured_on_purpose(monkeypatch, source, value):
    a = _from(source, value, monkeypatch)
    assert a.fortress.audit.ephemeral_key is False   # it is configured, so only the key check can catch it
    problems = [p for p in _problems(a) if FORTRESS_PROBLEM in p]
    assert len(problems) == 1 and "published key" in problems[0] and "FORTRESS_AUDIT_KEY" in problems[0]


def test_production_refuses_a_whitespace_only_environment_key(monkeypatch):
    monkeypatch.setenv("FORTRESS_AUDIT_KEY", "   ")
    problems = [p for p in _problems(adapter()) if FORTRESS_PROBLEM in p]
    assert len(problems) == 1 and "FORTRESS_AUDIT_KEY" in problems[0]


def test_a_whitespace_only_passed_key_counts_as_unset():
    a = adapter(audit_key="   ")
    assert a.fortress.audit.ephemeral_key is True
    problems = [p for p in _problems(a) if FORTRESS_PROBLEM in p]
    assert len(problems) == 1 and "no configured key" in problems[0]


@pytest.mark.parametrize("source", ["env", "arg"])
def test_production_refuses_a_short_key_and_does_not_print_it(monkeypatch, source):
    key = secrets.token_hex(16)[:31]   # 31 bytes, generated per run
    a = _from(source, key, monkeypatch)
    problems = _problems(a)
    fortress = [p for p in problems if FORTRESS_PROBLEM in p]
    assert len(fortress) == 1 and "shorter than 32 bytes" in fortress[0] and "FORTRESS_AUDIT_KEY" in fortress[0]
    assert key not in " ".join(problems)


@pytest.mark.parametrize("source", ["env", "arg"])
def test_production_accepts_a_key_of_exactly_32_bytes(monkeypatch, source):
    a = _from(source, secrets.token_hex(16), monkeypatch)   # 32 bytes, generated per run
    assert not [p for p in _problems(a) if FORTRESS_PROBLEM in p]
