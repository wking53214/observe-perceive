"""test_chain_closure.py -- the 1.1.0 closures to the chain itself.

Each test names the attack that succeeded before the closure (2026-09-08).
"""
from __future__ import annotations

import copy
import datetime as dt

from conftest import ChainArtifact, run_chain
from conservation_kernel import Actor, ActorKind, Artifact
from governance_chain import verify_result


def _failed(result, **kernels):
    return verify_result(result, **kernels).as_dict()["failed"]


# ---------------------------------------------------------------- H4: forged kernel membership

def test_a_root_registered_under_a_decision_id_does_not_count_as_a_kernel_decision(orchestrator, kernel):
    result, _ = run_chain(orchestrator, "c1")
    assert result["audit_chain_valid"]
    fake = "rooted-" + "f" * 8
    kernel.register_root(Artifact(artifact_id=f"decision-{fake}", content="forged",
                                  producer=Actor(actor_id="x", kind=ActorKind.SYSTEM), propositions=()))
    forged = dict(result)
    for key, attr in (("governance_decision", "decision_id"), ("conservation_decision", "governance_decision_id"),
                      ("execution_approval", "conservation_decision_id"), ("outcome_context", "governance_decision_id")):
        forged[key] = copy.copy(result[key])
        setattr(forged[key], attr, fake)
    assert "conservation.in_kernel_ledger" in _failed(forged, kernel=kernel)
    # And the genuine record still passes the strengthened check.
    assert "conservation.in_kernel_ledger" not in _failed(result, kernel=kernel)


# ---------------------------------------------------------------- outcome without OBSERVE

def test_an_execution_nobody_observed_still_has_a_committed_outcome(orchestrator, kernel, perceive):
    result, _ = run_chain(orchestrator, "c2", func=lambda ctx: {"status": "done", "moved": 5}, vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["observe_enforced"] is False
    outcome = result["outcome_context"]
    assert outcome is not None and outcome.outcome["observe_ran"] is False
    assert outcome.outcome["gsa815_result"] == {"status": "done", "moved": 5}
    verification = verify_result(result, kernel=kernel, perceive=perceive)
    assert verification.valid and not verification.complete   # verifiable, not observed
    assert result["audit_chain_valid"] is False                # unchanged meaning: no observed outcome
    # H3c': relabelling the execution now breaks two commitments even with no OBSERVE.
    result["execution_context"].execution_id = "someone-else"
    assert {"execution_context.state_commitment"} <= set(_failed(result, kernel=kernel, perceive=perceive))
    # And altering what was executed is caught.
    result2, _ = run_chain(orchestrator, "c2b", func=lambda ctx: {"status": "done", "moved": 5}, vitals=False)
    result2["gsa815_result"] = {"status": "done", "moved": 500}
    assert "outcome.result_artifact_hash" in _failed(result2)


def test_a_failed_execution_carries_a_committed_outcome_that_says_it_failed(orchestrator):
    def boom(ctx):
        raise IOError("downstream unavailable")
    result, _ = run_chain(orchestrator, "c3", func=boom)
    assert result["status"] == "EXECUTION_FAILED"
    outcome = result["outcome_context"]
    assert outcome.outcome["execution_status"] == "failed" and outcome.outcome["gsa815_result"] is None
    assert result["chain_verification"]["valid"] and not result["chain_verification"]["complete"]
    # Claiming it completed after the fact breaks the record.
    result["execution_status"] = "completed"
    assert "outcome.execution_status" in _failed(result)


# ---------------------------------------------------------------- event time vs ingestion time

class _TimedArtifact(ChainArtifact):
    def __init__(self, artifact_id, occurred_at):
        super().__init__(artifact_id)
        self.metadata.occurred_at = occurred_at


def test_event_time_is_taken_from_the_source_and_committed(orchestrator):
    occurred = dt.datetime(2026, 9, 1, 8, 30, tzinfo=dt.timezone.utc)
    result, _ = run_chain(orchestrator, "c4", artifact=_TimedArtifact("c4", occurred), vitals=False)
    request = result["governance_request"]
    assert request.event_time == occurred.isoformat()
    assert request.ingested_at is not None and request.ingested_at > request.event_time
    # Rewriting when the thing happened breaks the request commitment.
    request.event_time = dt.datetime(2026, 9, 7, tzinfo=dt.timezone.utc).isoformat()
    assert "request.state_commitment" in _failed(result)


def test_a_source_that_states_no_event_time_records_none_not_the_clock(orchestrator):
    result, _ = run_chain(orchestrator, "c5", vitals=False)
    request = result["governance_request"]
    assert request.event_time is None and request.ingested_at is not None


def test_ingestion_time_is_recorded_but_not_part_of_the_request_identity(perceive, kernel):
    from conftest import make_perceive
    from conservation_kernel import ConservationKernel
    from governance_orchestrator import GovernanceOrchestrator
    from observe_consolidated import ObserveClinicalEngine
    # Two processes each see the same artifact once (the kernel refuses a
    # second root under one id within a process, by design).
    a, _ = run_chain(GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine()), "c6", vitals=False)
    b, _ = run_chain(GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine()), "c6", vitals=False)
    # Same artifact seen twice: same request commitment, different ingestion times.
    assert a["governance_request"].state_commitment == b["governance_request"].state_commitment
    assert a["governance_request"].ingested_at != b["governance_request"].ingested_at
