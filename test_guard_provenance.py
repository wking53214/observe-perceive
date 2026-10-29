"""test_guard_provenance.py -- the orchestrator trusts a guard it can verify, not one an executor claims.

Found by auditing the execution seam on 2026-10-29. Before this, the orchestrator
skipped its own guard for any executor carrying an `execution_guard` attribute,
so a plain function that set that attribute ran with no authorization check
while the record still said `guard_executor: True`. And the strict profile built
happily over a kernel with no root guard.
"""
from __future__ import annotations

import pytest

from conftest import run_chain
from conservation_kernel import ConservationKernel
from execution_guard import ExecutionLedger, guarded
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine


def _marked(ran):
    """An executor that only CLAIMS to be guarded: a bare attribute, no check behind it."""
    def executor(ctx):
        ran.append(ctx)
        return {"status": "done"}
    executor.execution_guard = True
    return executor


def test_an_executor_that_only_claims_to_be_guarded_is_guarded_by_the_orchestrator(orchestrator):
    ran = []
    result, _ = run_chain(orchestrator, "gp1", func=_marked(ran), vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    # The orchestrator's own guard ran and said so.
    assert result["execution_authorization"]["guard"] is not None
    assert result["execution_authorization"]["consumed_by"] == "orchestrator"


def test_a_claiming_executor_cannot_run_a_replayed_context(orchestrator):
    ran = []
    claiming = _marked(ran)
    first, _ = run_chain(orchestrator, "gp2", func=claiming, vitals=False)
    assert first["status"] == "APPROVED_AND_EXECUTED"
    # The same issued context again, straight into the wrapped executor path: the single use is spent.
    again, _ = run_chain(orchestrator, "gp2-again", func=claiming, vitals=False, context={"execution_id": "exec-gp2"})
    assert again["status"] == "REJECTED" and len(ran) == 1


def test_an_executor_guarded_by_the_orchestrators_own_ledger_is_not_wrapped_twice(orchestrator, kernel):
    ran = []
    executor = guarded(lambda ctx: ran.append(ctx) or {"status": "done"}, orchestrator.execution_ledger, kernel=kernel)
    result, _ = run_chain(orchestrator, "gp3", func=executor, vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    # One authorization, made by the caller's wrapper, consumed the context.
    kinds = [e["kind"] for e in orchestrator.execution_ledger.entries if e["execution_id"] == "exec-gp3"]
    assert kinds == ["issued", "consumed"]


def test_an_executor_guarded_against_another_ledger_does_not_run(orchestrator):
    ran = []
    executor = guarded(lambda ctx: ran.append(ctx) or {"status": "done"}, ExecutionLedger())
    result, _ = run_chain(orchestrator, "gp4", func=executor, vitals=False)
    assert result["status"] == "EXECUTION_FAILED" and ran == []


def test_strict_profile_refuses_a_kernel_with_no_root_guard(perceive):
    with pytest.raises(ValueError, match="root guard"):
        GovernanceOrchestrator(perceive, ConservationKernel(allow_unguarded_roots=True), ObserveClinicalEngine(),
                               profile="strict")


def test_strict_profile_accepts_a_kernel_that_has_a_root_guard(perceive):
    kernel = ConservationKernel(root_guard=lambda artifact: False)
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), profile="strict")
    assert orchestrator.profile == "strict"


def test_advisory_profile_still_accepts_an_unguarded_kernel(perceive):
    orchestrator = GovernanceOrchestrator(perceive, ConservationKernel(allow_unguarded_roots=True),
                                          ObserveClinicalEngine(), profile="advisory")
    assert orchestrator.profile == "advisory"


def test_strict_profile_refuses_to_switch_the_pre_action_guard_off(perceive):
    # Strict is the protected profile, and its pre-action authorization is the executor guard. Turning that
    # off would leave only the after-the-fact consume, so strict refuses it at construction.
    with pytest.raises(ValueError, match="guard_executor"):
        GovernanceOrchestrator(perceive, ConservationKernel(root_guard=lambda artifact: True), ObserveClinicalEngine(),
                               profile="strict", guard_executor=False)


def test_advisory_profile_may_still_switch_the_guard_off(perceive, kernel):
    off = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), guard_executor=False)
    assert off.guard_executor is False
