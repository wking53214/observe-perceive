"""
Deterministic replay (Phase 11 of the repair mission).

What determinism the chain actually offers, stated and tested rather than
assumed:

- The request commitment is a pure function of the artifact and context, so
  it is identical across runs.
- The execution id is identical when the caller fixes it via
  `context["execution_id"]`.
- The verdict content (regime, escalation) and the execution result are
  identical for identical inputs.
- Re-verifying a RECORDED chain is deterministic and idempotent: the same
  record yields the same checks every time, with or without the kernels.

What is NOT deterministic across re-executions, by design: PERCEIVE mints the
decision id from its audit-ledger entry hash, which covers the entry's own
time, so a re-evaluation is a new decision with a new id, and everything
chained below it (conservation receipt, approval, execution context, outcome
commitments) is new too. The test records that divergence explicitly and
checks that it is explained by the decision id alone.
"""
from conftest import make_perceive, run_chain
from conservation_kernel import ConservationKernel
from governance_chain import verify_result
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine


def _fresh():
    return GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine())


def test_canonical_scenario_replays_to_the_same_request_commitment_and_execution_id():
    a, _ = run_chain(_fresh(), "canon")
    b, _ = run_chain(_fresh(), "canon")
    assert a["status"] == b["status"] == "APPROVED_AND_EXECUTED"
    assert a["governance_request"].state_commitment == b["governance_request"].state_commitment
    assert a["execution_context"].execution_id == b["execution_context"].execution_id == "exec-canon"
    assert a["gsa815_result"] == b["gsa815_result"]
    assert a["observe_verdict"].regime == b["observe_verdict"].regime
    assert a["observe_verdict"].escalation_required == b["observe_verdict"].escalation_required
    assert a["governance_decision"].approval == b["governance_decision"].approval
    assert a["governance_decision"].violations == b["governance_decision"].violations


def test_divergence_between_replays_is_explained_by_the_decision_identity():
    a, _ = run_chain(_fresh(), "canon")
    b, _ = run_chain(_fresh(), "canon")
    # A re-evaluation is a new PERCEIVE event: new ledger entry, new decision id.
    assert a["governance_decision"].decision_id != b["governance_decision"].decision_id
    # Everything below chains from that id, so it moves with it and nothing else.
    assert a["conservation_decision"].governance_decision_id == a["governance_decision"].decision_id
    assert b["conservation_decision"].governance_decision_id == b["governance_decision"].decision_id
    assert a["execution_approval"].conservation_decision_id == a["governance_decision"].decision_id
    assert b["execution_approval"].conservation_decision_id == b["governance_decision"].decision_id


def test_reverifying_a_recorded_chain_is_deterministic_and_idempotent():
    orchestrator = _fresh()
    result, _ = run_chain(orchestrator, "canon")
    with_kernels = [verify_result(result, kernel=orchestrator.conservation_kernel, perceive=orchestrator.perceive).as_dict() for _ in range(3)]
    without = [verify_result(result).as_dict() for _ in range(3)]
    assert with_kernels[0] == with_kernels[1] == with_kernels[2]
    assert without[0] == without[1] == without[2]
    assert with_kernels[0]["valid"] and with_kernels[0]["complete"]
    assert without[0]["valid"]
    # The ledger checks are the only difference between the two modes.
    names_with = {c["name"] for c in with_kernels[0]["checks"]}
    names_without = {c["name"] for c in without[0]["checks"]}
    assert names_with - names_without == {
        "decision.in_perceive_ledger", "decision.matches_perceive_ledger",
        "perceive_ledger.integrity", "conservation.in_kernel_ledger",
    }


def test_reconstruction_from_the_record_alone_reproduces_every_commitment():
    """The record must be reconstructible without the process that made it:
    every commitment the verifier recomputes comes from fields in the result."""
    result, _ = run_chain(_fresh(), "canon")
    verification = verify_result(result).as_dict()
    recomputed = {c["name"] for c in verification["checks"] if c["name"].endswith("state_commitment")}
    assert recomputed == {
        "request.state_commitment", "approval.state_commitment",
        "execution_context.state_commitment", "outcome.state_commitment",
    }
    assert not verification["failed"]
