"""test_execution_guard.py -- the executor's side of the authorization boundary.

Every test here is an attack that succeeded on 2026-09-08 before
execution_guard existed, or a second-order attack on the guard itself.
"""
from __future__ import annotations

import copy
import datetime as dt
import json

import pytest

from conftest import make_perceive, run_chain
from conservation_kernel import Actor, ActorKind, Artifact, ConservationKernel
from execution_guard import (
    ExecutionLedger, ExecutionRefusal, LedgerIntegrityError, authorize_execution, guarded,
)
from governance_chain import approval_state, execution_context_state
from governance_contracts import (
    ExecutionApproval, ExecutionContext, GovernanceApproval, compute_state_commitment,
)
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine


def _forge(issued: ExecutionContext, execution_id: str = "forged-1") -> ExecutionContext:
    """A context built entirely from public constructors and public hashing,
    self-consistent under every structural check."""
    approval = ExecutionApproval(
        request_id=execution_id, approval=GovernanceApproval.APPROVED,
        conservation_decision_id="never-decided", conservation_receipt_id="never-issued",
        governance_audit_hash="00" * 32, conservation_audit_hash="00" * 32,
        artifact_id=issued.artifact_id, artifact_hash=issued.artifact_hash,
        producer=issued.approval.producer, lineage=list(issued.lineage),
    )
    approval.state_commitment = compute_state_commitment(
        approval.conservation_audit_hash,
        approval_state(approval, approval.artifact_id, approval.artifact_hash, approval.producer, approval.lineage),
    )
    forged = ExecutionContext(
        request_id=execution_id, approval=approval, artifact_id=issued.artifact_id,
        artifact_hash=issued.artifact_hash, producer=issued.producer, lineage=list(issued.lineage),
        execution_id=execution_id,
    )
    forged.state_commitment = compute_state_commitment(approval.state_commitment, execution_context_state(forged))
    return forged


# ---------------------------------------------------------------- H1: a hand-built context

def test_a_forged_context_is_refused_by_the_guard_though_its_commitments_recompute(orchestrator, kernel):
    result, _ = run_chain(orchestrator, "g1", vitals=False)
    forged = _forge(result["execution_context"])
    # It passes every structural recomputation...
    assert forged.state_commitment == compute_state_commitment(
        forged.approval.state_commitment, execution_context_state(forged))
    # ...and the guard refuses it because it was never issued.
    with pytest.raises(ExecutionRefusal, match="never issued"):
        authorize_execution(forged, orchestrator.execution_ledger, kernel=kernel)


def test_a_guarded_executor_never_runs_on_a_forged_context(orchestrator, kernel):
    ran = []
    executor = guarded(lambda ctx: ran.append(ctx.execution_id) or {"status": "done"},
                       orchestrator.execution_ledger, kernel=kernel)
    result, _ = run_chain(orchestrator, "g2", func=executor, vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and ran == ["exec-g2"]
    with pytest.raises(ExecutionRefusal):
        executor(_forge(result["execution_context"]))
    assert ran == ["exec-g2"]


# ---------------------------------------------------------------- H2: replay

def test_an_issued_context_executes_exactly_once(orchestrator, kernel):
    ran = []
    executor = guarded(lambda ctx: ran.append(ctx.execution_id) or {"status": "done"},
                       orchestrator.execution_ledger, kernel=kernel)
    result, _ = run_chain(orchestrator, "g3", func=executor, vitals=False)
    issued = result["execution_context"]
    with pytest.raises(ExecutionRefusal, match="already consumed"):
        executor(issued)
    with pytest.raises(ExecutionRefusal, match="already consumed"):
        executor(copy.deepcopy(issued))
    assert ran == ["exec-g3"]
    # The replay attempts are themselves evidence.
    kinds = [e["kind"] for e in orchestrator.execution_ledger.entries if e["execution_id"] == "exec-g3"]
    assert kinds == ["issued", "consumed", "refused", "refused"]


def test_the_orchestrator_refuses_to_reissue_an_execution_id(orchestrator):
    first, ran = run_chain(orchestrator, "g4", vitals=False)
    assert first["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    # Same execution id, second time through the whole chain: the approval
    # stages all say yes again; the ledger says this identity was already used.
    second, ran2 = run_chain(orchestrator, "g4-again", vitals=False, context={"execution_id": "exec-g4"})
    assert second["status"] == "REJECTED" and second["refused_by"] == "execution_ledger"
    assert "already issued" in second["reason"] and ran2 == []


def test_the_orchestrator_guards_a_plain_executor_itself(orchestrator):
    """1.3.0: the caller's plain callable is wrapped by the orchestrator, so
    the guard's checks run and are recorded even when nobody wrapped it."""
    result, _ = run_chain(orchestrator, "g5", vitals=False)
    auth = result["execution_authorization"]
    assert auth["consumed_by"] == "orchestrator" and auth["guard"] is not None
    assert [c["name"] for c in auth["guard"]["checks"]][:2] == ["context.has_approval", "context.commitment"] or auth["guard"]["checks"]
    assert orchestrator.execution_ledger.consumed("exec-g5")["consumer"] == "orchestrator"
    # The authorization is recorded beside the result, not inside it.
    assert "_authorization" not in result["gsa815_result"]


def test_an_unguarded_executor_is_still_consumed_when_the_orchestrator_guard_is_off(perceive, kernel):
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), guard_executor=False)
    result, _ = run_chain(orchestrator, "g5b", vitals=False)
    auth = result["execution_authorization"]
    assert auth["consumed_by"] == "orchestrator" and auth["guard"] is None
    assert result["handoff"]["strict"]["guard_executor"] is False
    with pytest.raises(ExecutionRefusal, match="already consumed"):
        authorize_execution(result["execution_context"], orchestrator.execution_ledger)


def test_a_guarded_executor_records_its_authorization_in_the_result(orchestrator, kernel):
    executor = guarded(lambda ctx: {"status": "done"}, orchestrator.execution_ledger, kernel=kernel, consumer="reference-executor")
    result, _ = run_chain(orchestrator, "g6", func=executor, vitals=True)
    auth = result["execution_authorization"]
    assert auth["consumed_by"] == "reference-executor"
    assert auth["guard"]["execution_id"] == "exec-g6"
    assert [c["name"] for c in auth["guard"]["checks"] if c["name"].startswith("kernel")] == ["kernel.decision_in_ledger"]
    assert all(c["ok"] for c in auth["guard"]["checks"])
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]


# ---------------------------------------------------------------- tampering after issuance

@pytest.mark.parametrize("field,value", [
    ("timestamp", dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)),
    ("producer", "someone-else"),
    ("lineage", ["injected-parent"]),
    ("artifact_hash", "ff" * 32),
    ("execution_id", "relabelled"),
])
def test_a_context_altered_after_issuance_is_refused(orchestrator, field, value):
    ran = []
    # Issue without executing: capture the context the orchestrator hands over.
    captured = {}
    def capture(ctx):
        captured["ctx"] = ctx
        raise RuntimeError("do not run")
    result, _ = run_chain(orchestrator, f"g7-{field}", func=capture, vitals=False)
    assert result["status"] == "EXECUTION_FAILED"
    # The failed attempt consumed it; forge a fresh issuance to isolate the tamper check.
    ledger = ExecutionLedger()
    ctx = copy.deepcopy(captured["ctx"])
    ledger.issue(ctx)
    tampered = copy.deepcopy(ctx)
    setattr(tampered, field, value)
    with pytest.raises(ExecutionRefusal):
        guarded(lambda c: ran.append(1) or {}, ledger)(tampered)
    assert ran == []
    # The untampered original still authorizes exactly once.
    guarded(lambda c: ran.append(1) or {}, ledger)(ctx)
    assert ran == [1]


def test_an_altered_approval_timestamp_is_refused(orchestrator):
    captured = {}
    def capture(ctx):
        captured["ctx"] = ctx
        return {}
    run_chain(orchestrator, "g8", func=capture, vitals=False)
    ledger = ExecutionLedger()
    ctx = copy.deepcopy(captured["ctx"])
    ledger.issue(ctx)
    ctx.approval.timestamp = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    with pytest.raises(ExecutionRefusal, match="approval_timestamp"):
        authorize_execution(ctx, ledger)


# ---------------------------------------------------------------- restart: the ledger on disk

def test_a_replay_after_restart_is_still_a_replay(tmp_path, perceive, kernel):
    path = tmp_path / "executions.jsonl"
    first = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), execution_ledger=ExecutionLedger(path))
    result, _ = run_chain(first, "g9", vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    issued = result["execution_context"]
    # "Restart": a new process opens the same ledger file.
    reopened = ExecutionLedger(path)
    assert reopened.verify_chain() and reopened.consumed("exec-g9") is not None
    with pytest.raises(ExecutionRefusal, match="already consumed"):
        authorize_execution(issued, reopened)
    # And a new orchestrator over the same file cannot reissue the id.
    second = GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine(), execution_ledger=reopened)
    again, ran = run_chain(second, "g9-again", vitals=False, context={"execution_id": "exec-g9"})
    assert again["status"] == "REJECTED" and again["refused_by"] == "execution_ledger" and ran == []


def test_a_tampered_ledger_file_is_refused_outright(tmp_path, perceive, kernel):
    path = tmp_path / "executions.jsonl"
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), execution_ledger=ExecutionLedger(path))
    run_chain(orchestrator, "g10", vitals=False)
    lines = path.read_text().splitlines()
    consumed = json.loads(lines[1])
    assert consumed["kind"] == "consumed"
    # Erase the consumption to make the context look unused again.
    path.write_text(lines[0] + "\n")
    reopened = ExecutionLedger(path)          # a truncated chain still recomputes...
    assert reopened.consumed("exec-g10") is None
    # ...so truncation is the one edit a hash chain cannot see. Record it:
    # the defence is the append-only file plus the consumed entry being
    # required by any executor that already ran. Now alter an entry instead:
    issued = json.loads(lines[0])
    issued["context_timestamp"] = "1999-01-01T00:00:00+00:00"
    path.write_text(json.dumps(issued, sort_keys=True) + "\n" + lines[1] + "\n")
    with pytest.raises(LedgerIntegrityError):
        ExecutionLedger(path)


# ---------------------------------------------------------------- the kernel is consulted

def test_the_guard_refuses_a_decision_that_is_only_a_root_in_the_kernel(orchestrator, kernel):
    """H4: registering a root named decision-<id> used to satisfy the
    membership check. The guard, like the verifier, demands a decision that
    was DERIVED by a transformation from the request's artifact."""
    captured = {}
    def capture(ctx):
        captured["ctx"] = ctx
        return {}
    run_chain(orchestrator, "g11", func=capture, vitals=False)
    ledger = ExecutionLedger()
    ctx = copy.deepcopy(captured["ctx"])
    # Re-point the approval at a decision id that exists in the kernel only as a root.
    kernel.register_root(Artifact(artifact_id="decision-rooted", content="forged",
                                  producer=Actor(actor_id="x", kind=ActorKind.SYSTEM), propositions=()))
    ctx.approval.conservation_decision_id = "rooted"
    ctx.approval.state_commitment = compute_state_commitment(
        ctx.approval.conservation_audit_hash,
        approval_state(ctx.approval, ctx.approval.artifact_id, ctx.approval.artifact_hash,
                       ctx.approval.producer, ctx.approval.lineage))
    ctx.state_commitment = compute_state_commitment(ctx.approval.state_commitment, execution_context_state(ctx))
    ledger.issue(ctx)
    with pytest.raises(ExecutionRefusal, match="kernel.decision_in_ledger"):
        authorize_execution(ctx, ledger, kernel=kernel)
