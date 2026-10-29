"""test_orchestrator_alert_channel.py -- a person hears when a stage CRASHES.

`alert_human` is called only when a stage raises instead of reaching a verdict.
Ordinary refusals and successes never call it, and their records are unchanged.
"""
from __future__ import annotations

import logging

import pytest

from conftest import run_chain
from conservation_kernel import ConservationKernel
from execution_guard import ExecutionLedger
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog
from observe_consolidated import ObserveClinicalEngine


class Pager:
    def __init__(self):
        self.calls = []

    def __call__(self, alert):
        self.calls.append(alert)


def _broken_pager(alert):
    raise ConnectionError("pager unreachable")


def _crashed_perceive(kernel, **kw):
    return GovernanceOrchestrator(None, kernel, ObserveClinicalEngine(), **kw)


# --- a stage crashes: the person is told -------------------------------------------------

def test_a_crashed_perceive_stage_alerts_and_the_record_says_so(kernel):
    pager = Pager()
    result, ran = run_chain(_crashed_perceive(kernel, alert_human=pager), "a1")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "delivered"
    assert len(pager.calls) == 1
    alert = pager.calls[0]
    assert alert["stage"] == "perceive" and "AttributeError" in alert["stage_error"]
    assert alert["request_id"] == result["governance_request"].request_id
    assert alert["artifact_id"] == "a1"


def test_a_missing_conservation_kernel_alerts(perceive):
    pager = Pager()
    orchestrator = GovernanceOrchestrator(perceive, None, ObserveClinicalEngine(), alert_human=pager)
    result, ran = run_chain(orchestrator, "a2")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "delivered"
    assert [c["stage"] for c in pager.calls] == ["conservation"]


def test_a_crashed_execution_approval_stage_alerts(perceive, kernel, monkeypatch, tmp_path):
    pager = Pager()
    ledger = ExecutionLedger(tmp_path / "e.jsonl")
    orchestrator = GovernanceOrchestrator(
        perceive, kernel, ObserveClinicalEngine(), execution_ledger=ledger, alert_human=pager,
    )

    def broken(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(ledger, "_append", broken)
    result, ran = run_chain(orchestrator, "a3")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "delivered"
    assert [c["stage"] for c in pager.calls] == ["execution_approval"]
    assert "OSError" in pager.calls[0]["stage_error"]


def test_an_unavailable_receipt_store_alerts(perceive, kernel, monkeypatch, tmp_path):
    pager = Pager()
    log = ReceiptLog(tmp_path / "r.jsonl")
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), receipts=log, alert_human=pager)

    def broken():
        raise OSError("receipt volume gone")
    monkeypatch.setattr(log, "probe", broken)
    result, ran = run_chain(orchestrator, "a4")
    assert result["status"] == "REJECTED" and ran == []
    assert result["human_alert"] == "delivered"
    assert [c["stage"] for c in pager.calls] == ["receipt_store"]


def test_raise_on_stage_error_still_alerts_before_raising(kernel):
    pager = Pager()
    orchestrator = _crashed_perceive(kernel, raise_on_stage_error=True, alert_human=pager)
    with pytest.raises(AttributeError):
        run_chain(orchestrator, "a5")
    assert [c["stage"] for c in pager.calls] == ["perceive"]


def test_an_alert_failure_is_recorded_loudly_and_changes_nothing_else(kernel, caplog):
    plain, _ = run_chain(_crashed_perceive(kernel), "a6")
    with caplog.at_level(logging.CRITICAL, logger="GovernanceOrchestrator"):
        failed, ran = run_chain(_crashed_perceive(kernel, alert_human=_broken_pager), "a6")
    assert ran == []
    assert failed["human_alert"] == "failed"
    assert any("HUMAN ALERT FAILED" in r.getMessage() for r in caplog.records)
    for key in ("status", "reason", "refused_by", "stage_refused", "stage_error"):
        assert failed[key] == plain[key], key


def test_no_channel_is_recorded_and_logged_at_critical(kernel, caplog):
    with caplog.at_level(logging.CRITICAL, logger="GovernanceOrchestrator"):
        result, ran = run_chain(_crashed_perceive(kernel), "a7")
    assert ran == [] and result["status"] == "REJECTED"
    assert result["human_alert"] == "no_channel"
    assert any("HUMAN ALERT NOT SENT" in r.getMessage() for r in caplog.records)


def test_a_receipted_stage_error_record_still_verifies(kernel, tmp_path):
    log = ReceiptLog(tmp_path / "r.jsonl")
    orchestrator = _crashed_perceive(kernel, receipts=log, alert_human=Pager())
    result, _ = run_chain(orchestrator, "a8")
    assert result["human_alert"] == "delivered" and result["receipt"] is not None
    ReceiptLog(tmp_path / "r.jsonl")   # reopening verifies the chain; it must not raise


# --- nothing else changes ----------------------------------------------------------------

def test_a_successful_run_never_alerts_and_its_record_has_no_alert_field(perceive, kernel):
    pager = Pager()
    result, ran = run_chain(GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), alert_human=pager), "n1")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert pager.calls == [] and "human_alert" not in result


def test_an_ordinary_refusal_never_alerts_and_its_record_has_no_alert_field(kernel):
    from datetime import datetime, timezone
    from perceive_consolidated import PerceiveGovernanceKernel, PolicyEnforcementConfig, PolicyManifest
    enforcing = PerceiveGovernanceKernel(enforcement=PolicyEnforcementConfig(enforce_export_controls=True))
    enforcing.register_manifest(PolicyManifest(
        manifest_id="m", version="1.0.0", created_at=datetime.now(timezone.utc), policies={}))
    pager = Pager()
    orchestrator = GovernanceOrchestrator(enforcing, kernel, ObserveClinicalEngine(), alert_human=pager)
    result, ran = run_chain(
        orchestrator, "n2", operation="export",
        context={"export_type": "full_record", "has_consent": False, "will_encrypt": False},
    )
    assert result["status"] == "REJECTED" and not ran and result["stage_refused"] is True
    assert pager.calls == [] and "human_alert" not in result


def test_a_replay_refusal_by_the_kernel_never_alerts(perceive, kernel):
    pager = Pager()
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), alert_human=pager)
    run_chain(orchestrator, "n3")
    second, ran = run_chain(orchestrator, "n3")
    assert second["status"] == "REJECTED" and not ran and second["refused_by"] == "conservation"
    assert pager.calls == [] and "human_alert" not in second


def test_record_shapes_are_identical_with_or_without_a_channel():
    from conftest import make_perceive

    def shape(alert_human):
        # Fresh kernels per run: PERCEIVE and the Conservation Kernel both remember earlier requests.
        orchestrator = GovernanceOrchestrator(
            make_perceive(), ConservationKernel(allow_unguarded_roots=True),
            ObserveClinicalEngine(), alert_human=alert_human,
        )
        result, _ = run_chain(orchestrator, "s1")
        handoff = dict(result["handoff"])
        handoff.pop("issued_at")
        return sorted(result), handoff

    assert shape(None) == shape(Pager())


def test_the_handoff_stamp_does_not_mention_the_alert_channel(perceive, kernel):
    with_channel = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), alert_human=Pager())
    result, _ = run_chain(with_channel, "h1")
    assert "alert" not in repr(result["handoff"]).lower()
