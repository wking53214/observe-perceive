"""The reconciler: an effect is unauthorized until a join finds its slip, and disagreements reach a person."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from conservation_kernel import HmacSigner

from conftest import run_chain
from execution_guard import ExecutionLedger, guarded
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
from reconciler import KINDS, QUERIES, ReconciliationRefused, escalate, reconcile

POLICY = dict(clock_skew_s=5, abandoned_after_s=3600)


def _ctx(execution_id, artifact_hash):
    approval = SimpleNamespace(state_commitment=f"ac-{execution_id}", timestamp=None, approval="APPROVED",
                               conservation_decision_id="d", conservation_receipt_id="r", conservation_audit_hash="h")
    return SimpleNamespace(execution_id=execution_id, request_id=f"req-{execution_id}",
                           artifact_id=f"art-{execution_id}", artifact_hash=artifact_hash, producer="p", lineage=[],
                           state_commitment=f"cc-{execution_id}", timestamp=None, approval=approval)


def _ledger(issued=(), consumed=()):
    ledger = ExecutionLedger()
    contexts = {}
    for execution_id in issued:
        contexts[execution_id] = _ctx(execution_id, f"hash-{execution_id}")
        ledger.issue(contexts[execution_id])
    for execution_id in consumed:
        ledger.consume(contexts[execution_id], consumer="test")
    return ledger


def _now(**delta):
    return datetime.now(timezone.utc) + timedelta(**delta)


def _effect(effect_id, execution_id=None, artifact_hash=None, at=None):
    if execution_id is not None and artifact_hash is None:
        artifact_hash = f"hash-{execution_id}"
    return {"effect_id": effect_id, "execution_id": execution_id, "artifact_hash": artifact_hash,
            "at": (at or _now(seconds=1)).isoformat()}


def _kinds(result):
    return sorted(v.kind for v in result.violations)


# ---- clean ----------------------------------------------------------------------------------------------------

def test_every_effect_matched_to_a_consumed_slip_is_clean():
    ledger = _ledger(issued=["a", "b"], consumed=["a", "b"])
    result = reconcile(ledger, [_effect("e1", "a"), _effect("e2", "b")], now=_now(), **POLICY)
    assert result.clean and (result.effects_checked, result.issued_checked, result.consumed_checked) == (2, 2, 2)


# ---- one test per join -----------------------------------------------------------------------------------------

def test_an_effect_with_no_slip_is_a_bypass():
    result = reconcile(_ledger(), [_effect("e1")], now=_now(), **POLICY)
    assert _kinds(result) == ["bypass"] and result.violations[0].effect_id == "e1"


def test_an_effect_naming_a_slip_never_issued_is_forged():
    result = reconcile(_ledger(), [_effect("e1", "never-issued")], now=_now(), **POLICY)
    assert _kinds(result) == ["forged"]


def test_an_effect_on_a_slip_the_guard_never_consumed_is_an_unconsumed_use():
    result = reconcile(_ledger(issued=["a"]), [_effect("e1", "a")], now=_now(), **POLICY)
    assert _kinds(result) == ["unconsumed_use"]


def test_an_effect_on_a_different_artifact_is_a_substitution():
    ledger = _ledger(issued=["a"], consumed=["a"])
    result = reconcile(ledger, [_effect("e1", "a", artifact_hash="hash-of-something-else")], now=_now(), **POLICY)
    assert _kinds(result) == ["substitution"]
    assert result.violations[0].detail == {"acted_on": "hash-of-something-else", "approved": "hash-a"}


def test_one_slip_used_twice_is_a_replay():
    ledger = _ledger(issued=["a"], consumed=["a"])
    result = reconcile(ledger, [_effect("e1", "a"), _effect("e2", "a")], now=_now(), **POLICY)
    assert _kinds(result) == ["replay"] and result.violations[0].detail == {"uses": 2, "effect_ids": ["e1", "e2"]}


def test_an_effect_before_its_slip_was_consumed_is_early_beyond_the_skew_only():
    ledger = _ledger(issued=["a", "b"], consumed=["a", "b"])
    effects = [_effect("e1", "a", at=_now(hours=-1)), _effect("e2", "b", at=_now(seconds=-2))]
    result = reconcile(ledger, effects, now=_now(), **POLICY)
    assert _kinds(result) == ["early"] and result.violations[0].effect_id == "e1"


def test_a_consumed_slip_with_no_effect_is_claimed_but_not_seen():
    result = reconcile(_ledger(issued=["a"], consumed=["a"]), [], now=_now(), **POLICY)
    assert _kinds(result) == ["claimed_no_effect"]


def test_an_old_unused_slip_is_abandoned_and_a_young_one_is_not():
    ledger = _ledger(issued=["a"])
    assert reconcile(ledger, [], now=_now(), **POLICY).clean
    result = reconcile(ledger, [], now=_now(hours=2), **POLICY)
    assert _kinds(result) == ["abandoned"] and result.violations[0].severity == "record"


def test_every_join_has_a_severity_and_a_test():
    assert set(QUERIES) == set(KINDS)
    assert {s for s in KINDS.values()} == {"page", "record"}


# ---- what it refuses ---------------------------------------------------------------------------------------------

def test_a_ledger_whose_chain_does_not_verify_is_refused():
    ledger = _ledger(issued=["a"], consumed=["a"])
    ledger._entries[0]["artifact_hash"] = "rewritten"
    with pytest.raises(ReconciliationRefused, match="does not verify"):
        reconcile(ledger, [_effect("e1", "a", artifact_hash="rewritten")], now=_now(), **POLICY)


@pytest.mark.parametrize("effect,match", [
    ({"execution_id": None, "at": "2026-11-21T00:00:00+00:00"}, "no effect_id"),
    ({"effect_id": "e1", "execution_id": "a", "at": "2026-11-21T00:00:00+00:00"}, "artifact_hash"),
    ({"effect_id": "e1", "execution_id": None, "at": "2026-11-21T00:00:00"}, "time zone"),
    ({"effect_id": "e1", "execution_id": None, "at": "yesterday"}, "ISO time"),
])
def test_a_malformed_effect_is_refused(effect, match):
    with pytest.raises(ReconciliationRefused, match=match):
        reconcile(_ledger(), [effect], now=_now(), **POLICY)


def test_a_duplicated_effect_id_is_refused():
    with pytest.raises(ReconciliationRefused, match="twice"):
        reconcile(_ledger(), [_effect("e1"), _effect("e1")], now=_now(), **POLICY)


@pytest.mark.parametrize("field,value", [("clock_skew_s", -1), ("abandoned_after_s", float("nan")),
                                         ("clock_skew_s", True), ("abandoned_after_s", None)])
def test_policy_numbers_have_no_default_and_must_be_sane(field, value):
    with pytest.raises(ReconciliationRefused, match=field):
        reconcile(_ledger(), [], now=_now(), **{**POLICY, field: value})
    with pytest.raises(TypeError):
        reconcile(_ledger(), [], now=_now())


# ---- to a person -------------------------------------------------------------------------------------------------

def _desk(sink):
    key = HmacSigner(b"reconciler-test-responder-key-0123456789", key_id="nurse")
    return EscalationDesk(NotificationPolicy((Tier("charge", sink.append, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": key})


def test_paging_violations_reach_a_person_once_and_recorded_ones_do_not_page():
    ledger = _ledger(issued=["old"])
    result = reconcile(ledger, [_effect("e1"), _effect("e2", "never-issued")], now=_now(hours=2), **POLICY)
    assert _kinds(result) == ["abandoned", "bypass", "forged"]
    sink, raised = [], set()
    desk = _desk(sink)
    first = escalate(result, desk, already_raised=raised)
    assert len(first) == 2 and sorted(p["kind"] for p in sink) == ["reconciliation:bypass", "reconciliation:forged"]
    again = reconcile(ledger, [_effect("e1"), _effect("e2", "never-issued")], now=_now(hours=2), **POLICY)
    assert escalate(again, desk, already_raised=raised) == [] and len(sink) == 2


# ---- end to end: the governed path is clean, the raw call is caught ----------------------------------------------

def test_the_governed_path_reconciles_clean_and_a_raw_call_is_a_bypass(orchestrator, kernel):
    effect_log = []

    def act(ctx=None):                                   # the real action; it writes the downstream record itself
        effect_log.append({"effect_id": f"e{len(effect_log) + 1}", "at": _now().isoformat(),
                           "execution_id": getattr(ctx, "execution_id", None),
                           "artifact_hash": getattr(ctx, "artifact_hash", None)})
        return {"status": "done"}

    result, _ = run_chain(orchestrator, "rc1", func=guarded(act, orchestrator.execution_ledger, kernel=kernel),
                          vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert reconcile(orchestrator.execution_ledger, effect_log, now=_now(), **POLICY).clean

    act()                                                # someone calls the action directly, around the guard
    caught = reconcile(orchestrator.execution_ledger, effect_log, now=_now(), **POLICY)
    assert _kinds(caught) == ["bypass"] and caught.violations[0].effect_id == "e2"
