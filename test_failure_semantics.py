"""test_failure_semantics.py -- what each boundary does when it cannot do its job.

The matrix these tests pin down is in docs/closure/FAILURE_SEMANTICS_MATRIX.md.
Every row there is either exercised here or in test_chain_failure_paths.py.
"""
from __future__ import annotations

import datetime as dt

import pytest

from conftest import ChainArtifact, make_perceive, run_chain
from conservation_kernel import ConservationKernel
from execution_guard import ExecutionLedger
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog
from observe_consolidated import ObserveClinicalEngine


def test_a_missing_perceive_is_a_recorded_refusal_not_a_crash(kernel):
    orchestrator = GovernanceOrchestrator(None, kernel, ObserveClinicalEngine())
    result, ran = run_chain(orchestrator, "f1")
    assert result["status"] == "REJECTED" and ran == []
    assert result["stage_refused"] is False and "AttributeError" in result["stage_error"]
    assert result["governance_request"] is not None            # the request it could not evaluate is kept


def test_a_missing_perceive_raises_on_request(kernel):
    strict = GovernanceOrchestrator(None, kernel, ObserveClinicalEngine(), raise_on_stage_error=True)
    with pytest.raises(AttributeError):
        run_chain(strict, "f2")


def test_a_missing_kernel_is_a_recorded_refusal_that_says_no_kernel_ran(perceive):
    orchestrator = GovernanceOrchestrator(perceive, None, ObserveClinicalEngine())
    result, ran = run_chain(orchestrator, "f3")
    assert result["status"] == "REJECTED" and ran == []
    assert result["refused_by"] is None and result["stage_refused"] is False and result["conservation_enforced"] is False


def test_an_unwritable_execution_ledger_refuses_before_execution(perceive, kernel, monkeypatch, tmp_path):
    ledger = ExecutionLedger(tmp_path / "e.jsonl")
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), execution_ledger=ledger)
    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ledger, "_append", broken)
    result, ran = run_chain(orchestrator, "f4")
    assert result["status"] == "REJECTED" and ran == []
    assert "OSError" in result["stage_error"] and result["stage_refused"] is False


def test_an_unwritable_receipt_log_degrades_explicitly_after_the_action(perceive, kernel, monkeypatch, tmp_path):
    log = ReceiptLog(tmp_path / "r.jsonl")
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), receipts=log)
    def broken(result):
        raise OSError("disk full")
    monkeypatch.setattr(log, "append", broken)
    result, ran = run_chain(orchestrator, "f5")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["receipt"] is None and "OSError" in result["receipt_error"]


def test_a_receipt_log_whose_chain_is_broken_refuses_to_open(perceive, kernel, tmp_path):
    log = ReceiptLog(tmp_path / "r.jsonl")
    run_chain(GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), receipts=log), "f6", vitals=False)
    path = tmp_path / "r.jsonl"
    path.write_text(path.read_text().replace('"APPROVED_AND_EXECUTED"', '"REJECTED"'))
    from governance_record import RecordIntegrityError
    with pytest.raises(RecordIntegrityError):
        ReceiptLog(path)


class _Timed(ChainArtifact):
    def __init__(self, artifact_id, when):
        super().__init__(artifact_id)
        self.metadata.occurred_at = when


def test_a_future_event_time_is_recorded_as_an_anomaly_not_refused(orchestrator):
    result, ran = run_chain(orchestrator, "f7", artifact=_Timed("f7", dt.datetime(2099, 1, 1, tzinfo=dt.timezone.utc)), vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["temporal_anomalies"] and "after ingestion" in result["temporal_anomalies"][0]


def test_a_stale_event_time_is_recorded_as_an_anomaly(orchestrator):
    result, _ = run_chain(orchestrator, "f8", artifact=_Timed("f8", dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc)), vitals=False)
    assert "more than 30 days before ingestion" in result["temporal_anomalies"][0]


def test_a_naive_event_time_is_an_anomaly_too(orchestrator):
    result, _ = run_chain(orchestrator, "f9", artifact=_Timed("f9", dt.datetime(2026, 9, 8, 12, 0)), vitals=False)
    assert "no timezone" in result["temporal_anomalies"][0]


def test_a_duplicate_artifact_in_one_process_is_refused_by_the_kernel_not_executed_twice(perceive, kernel):
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine())
    first, ran1 = run_chain(orchestrator, "f10", vitals=False)
    second, ran2 = run_chain(orchestrator, "f10", vitals=False, context={"execution_id": "exec-f10-b"})
    assert first["status"] == "APPROVED_AND_EXECUTED" and len(ran1) == 1
    assert second["status"] == "REJECTED" and second["refused_by"] == "conservation" and ran2 == []
    assert "duplicate artifact ID" in second["reason"]


def test_the_same_artifact_seen_by_two_processes_is_two_decisions_not_one():
    a, _ = run_chain(GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine()), "f11", vitals=False)
    b, _ = run_chain(GovernanceOrchestrator(make_perceive(), ConservationKernel(), ObserveClinicalEngine()), "f11", vitals=False)
    assert a["governance_decision"].decision_id != b["governance_decision"].decision_id
    assert a["governance_request"].state_commitment == b["governance_request"].state_commitment
