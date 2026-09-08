"""test_scope_binding.py -- a declared scope is only as good as its seal.

Measured 2026-09-08: `context["gateway_scope"] = "EXECUTE"` was enough to
pass the scope check; the Gateway's sealed artifact was never consulted by
the orchestrator, only by the admission adapter, which a caller can skip.
"""
from __future__ import annotations

from dataclasses import replace

import pytest

from conftest import make_perceive, run_chain
from conservation_kernel import ConservationKernel
from gateway_admission_adapter import GatewayAdmissionAdapter
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine

gw = pytest.importorskip("governance_gateway.models")


def _sealed(artifact_id="s1", execute=True):
    return GatewayAdmissionAdapter.seal(
        artifact_id=artifact_id, payload={"content": "escalate P001"},
        provenance={"source": "SENTINEL"}, epistemic_status=gw.EpistemicStatus.INFERENCE,
        authority_actor="CHARGE_NURSE", authority_grant="unit-escalation", execute=execute,
    )


def test_a_bare_scope_claim_is_honoured_as_a_claim_by_default_and_says_so(orchestrator):
    result, ran = run_chain(orchestrator, "s1", vitals=False, context={"gateway_scope": "EXECUTE"})
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["scope_enforced"] is True and result["scope_sealed"] is False
    assert result["gateway_admission"] is None


def test_strict_mode_refuses_a_bare_scope_claim(perceive, kernel):
    strict = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), require_declared_scope=True)
    result, ran = run_chain(strict, "s2", vitals=False, context={"gateway_scope": "EXECUTE"})
    assert result["status"] == "REJECTED" and ran == []
    assert "without a sealed Gateway admission" in result["reason"] and result["scope_sealed"] is False


def test_a_sealed_admission_backs_the_scope_in_strict_mode(perceive, kernel):
    strict = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), require_declared_scope=True)
    sealed = _sealed("s3")
    result, ran = run_chain(strict, "s3", vitals=False, context={"gateway_scope": "EXECUTE", "gateway_admission": sealed})
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["scope_sealed"] is True
    assert result["gateway_admission"] == {"artifact_id": "s3", "scope": "EXECUTE", "integrity": sealed.integrity}
    # The admission object itself did not enter the committed request.
    assert "gateway_admission" not in result["governance_request"].context
    assert result["governance_request"].context["gateway_admission_integrity"] == sealed.integrity


@pytest.mark.parametrize("strict", [False, True])
def test_an_admission_whose_scope_was_escalated_after_sealing_is_refused_in_every_mode(strict):
    orch = GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine(), require_declared_scope=strict)
    tampered = replace(_sealed("s4", execute=False), scope=gw.Scope.EXECUTE)     # integrity no longer recomputes
    result, ran = run_chain(orch, "s4", vitals=False, context={"gateway_scope": "EXECUTE", "gateway_admission": tampered})
    assert result["status"] == "REJECTED" and ran == []
    assert "does not recompute" in result["reason"]


def test_an_admission_sealed_read_only_does_not_back_an_execute_claim(orchestrator):
    result, ran = run_chain(orchestrator, "s5", vitals=False,
                            context={"gateway_scope": "EXECUTE", "gateway_admission": _sealed("s5", execute=False)})
    assert result["status"] == "REJECTED" and ran == [] and "sealed scope is READ_ONLY" in result["reason"]


def test_an_admission_for_another_artifact_does_not_back_this_one(orchestrator):
    result, ran = run_chain(orchestrator, "s6", vitals=False,
                            context={"gateway_scope": "EXECUTE", "gateway_admission": _sealed("someone-else")})
    assert result["status"] == "REJECTED" and ran == [] and "admission is for artifact someone-else" in result["reason"]


def test_the_admission_adapter_passes_the_seal_through(orchestrator):
    from test_gateway_admission_adapter import _chain_artifact
    adapter = GatewayAdmissionAdapter()
    result = adapter.govern_admitted(orchestrator, _sealed("gw-001"), _chain_artifact("gw-001"),
                                     operation_type="escalate", context={"patient_id": "P001"})
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["scope_sealed"] is True
