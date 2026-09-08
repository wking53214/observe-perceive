"""test_governance_record.py -- the record that leaves the process.

Each test names the attack it defends against. H3 (2026-09-08): a
timestamp rewritten after the fact left `verify_result` valid, because
timestamps are outside the commitments by design. The receipt is where
that becomes detectable.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import make_perceive, run_chain
from conservation_kernel import ConservationKernel
from execution_guard import ExecutionLedger
from governance_orchestrator import GovernanceOrchestrator
from governance_record import (
    RecordIntegrityError, ReceiptLog, from_record, record_hash, to_record, verify_record,
)
from observe_consolidated import ObserveClinicalEngine


@pytest.fixture
def receipted(tmp_path, perceive, kernel):
    log = ReceiptLog(tmp_path / "receipts.jsonl")
    orch = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(),
                                  execution_ledger=ExecutionLedger(tmp_path / "executions.jsonl"), receipts=log)
    return orch, log, tmp_path


def test_a_record_verifies_with_no_access_to_the_process_that_made_it(orchestrator):
    result, _ = run_chain(orchestrator, "r1")
    assert result["audit_chain_valid"]
    record = json.loads(json.dumps(to_record(result)))        # through bytes
    verification = verify_record(record)                        # no kernels: structure and cryptography
    assert verification.valid and verification.complete, verification.as_dict()["failed"]
    rebuilt = from_record(record)
    assert rebuilt["execution_context"].state_commitment == result["execution_context"].state_commitment
    assert rebuilt["governance_request"].event_time == result["governance_request"].event_time


def test_a_record_verifies_against_the_kernels_too(orchestrator, kernel, perceive):
    result, _ = run_chain(orchestrator, "r2")
    record = json.loads(json.dumps(to_record(result)))
    verification = verify_record(record, kernel=kernel, perceive=perceive)
    assert verification.valid and verification.complete, verification.as_dict()["failed"]
    assert {"decision.in_perceive_ledger", "conservation.in_kernel_ledger"} <= {c["name"] for c in verification.as_dict()["checks"]}


def test_every_path_is_receipted_not_only_success(receipted):
    orch, log, _ = receipted
    run_chain(orch, "r3-ok", vitals=False)
    run_chain(orch, "r3-fail", func=lambda ctx: (_ for _ in ()).throw(IOError("x")), vitals=False)
    run_chain(orch, "r3-scope", vitals=False, context={"gateway_scope": "READ_ONLY"})
    orch.orchestrate_request(None, "escalate", lambda ctx: None)
    statuses = [e["receipt"]["status"] for e in log.entries]
    assert statuses == ["APPROVED_AND_EXECUTED", "EXECUTION_FAILED", "REJECTED", "INVALID_REQUEST"]


def test_receipts_survive_the_process_and_reverify_from_the_file_alone(receipted):
    orch, log, tmp_path = receipted
    run_chain(orch, "r4-a")
    run_chain(orch, "r4-b", vitals=False)
    reopened = ReceiptLog(tmp_path / "receipts.jsonl")           # "restart"
    assert [e["receipt"]["sequence"] for e in reopened.entries] == [0, 1]
    verdicts = reopened.verify_all()
    assert [v["valid"] for v in verdicts] == [True, True]
    assert [v["complete"] for v in verdicts] == [True, False]      # the second was not observed
    found = reopened.find(execution_id="exec-r4-a")
    assert len(found) == 1 and found[0]["record"]["status"] == "APPROVED_AND_EXECUTED"


def test_h3_a_timestamp_rewritten_after_receipting_no_longer_matches_the_receipt(receipted):
    orch, log, _ = receipted
    result, _ = run_chain(orch, "r5")
    assert log.matches(result)
    result["governance_request"].timestamp = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
    # The commitments still hold (by design) ...
    assert result["chain_verification"]["valid"]
    # ... and the receipt says this is not the record that was written.
    assert not log.matches(result)


def test_a_tampered_receipt_file_is_refused_outright(receipted):
    orch, log, tmp_path = receipted
    run_chain(orch, "r6")
    path = tmp_path / "receipts.jsonl"
    entry = json.loads(path.read_text().splitlines()[0])
    entry["record"]["status"] = "REJECTED"                        # rewrite history
    path.write_text(json.dumps(entry, sort_keys=True, default=str) + "\n")
    with pytest.raises(RecordIntegrityError, match="record hash"):
        ReceiptLog(path)


def test_the_record_hash_is_deterministic_and_covers_timestamps(orchestrator):
    result, _ = run_chain(orchestrator, "r7")
    a = record_hash(to_record(result))
    assert a == record_hash(json.loads(json.dumps(to_record(result))))
    result["execution_approval"].timestamp = dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)
    assert record_hash(to_record(result)) != a


def test_a_replayed_scenario_across_restarts_is_receipted_twice_and_refused_once(tmp_path):
    ledger_path, receipts_path = tmp_path / "executions.jsonl", tmp_path / "receipts.jsonl"
    first = GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine(),
                                   execution_ledger=ExecutionLedger(ledger_path), receipts=ReceiptLog(receipts_path))
    run_chain(first, "canon")
    second = GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine(),
                                    execution_ledger=ExecutionLedger(ledger_path), receipts=ReceiptLog(receipts_path))
    again, ran = run_chain(second, "canon")
    assert again["status"] == "REJECTED" and again["refused_by"] == "execution_ledger" and ran == []
    log = ReceiptLog(receipts_path)
    assert [e["receipt"]["status"] for e in log.entries] == ["APPROVED_AND_EXECUTED", "REJECTED"]
    # Both receipts reverify from the file; the refusal explains itself.
    assert all(v["valid"] for v in log.verify_all())
    assert "already issued" in log.records()[1]["reason"]
