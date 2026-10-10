"""CNS connector for OBSERVE and PERCEIVE: one gate per end, fail-closed, bound to content."""

import importlib
from datetime import datetime, timezone

import pytest

pytest.importorskip("cns.gate", reason="needs the pinned CNS package")

import cns_connector  # noqa: E402
from cns.gate import GateOutcome, GatePosition  # noqa: E402
from cns_connector import (  # noqa: E402
    CnsNotInstalled, ObserveCnsGate, PerceiveCnsGate, cns_available,
)
from observe_consolidated import FusedVerdict, OperationalRegime, VitalsSnapshot, validate_vitals  # noqa: E402
from perceive_consolidated import PolicyGates, PolicyRequest, PolicyVerdict, Provenance  # noqa: E402


# ---------------------------------------------------------------- OBSERVE

def make_vitals(**overrides):
    defaults = dict(
        patient_id="P001",
        timestamp=datetime.now(timezone.utc),
        heart_rate=100,
        oxygen_saturation=97.0,
        respiratory_rate=24,
        temperature=37.0,
        context={"age_months": 24},
    )
    defaults.update(overrides)
    return VitalsSnapshot(**defaults)


def make_verdict(**overrides):
    defaults = dict(
        risk_score=0.3,
        regime=OperationalRegime.STABLE,
        confidence=0.9,
        entropy=0.1,
        active_engines=["heuristic"],
        triggered_rules=[],
        timestamp=datetime.now(timezone.utc),
        audit_hash="a" * 64,
        decision_fingerprint="f" * 64,
    )
    defaults.update(overrides)
    return FusedVerdict(**defaults)


def test_observe_alpha_passes_plausible_vitals():
    result = ObserveCnsGate("alpha").check(make_vitals())
    assert result.outcome is GateOutcome.PASS
    assert result.position is GatePosition.ALPHA
    assert result.gate == "observe.vitals_ingress"


def test_observe_alpha_asks_for_a_new_reading_on_a_sensor_fault():
    faulty = make_vitals(heart_rate=-5)
    assert validate_vitals(faulty), "fixture must actually be a sensor fault"
    result = ObserveCnsGate("alpha").check(faulty)
    assert result.outcome is GateOutcome.RETRY
    assert "re-read required" in result.reason


def test_observe_omega_passes_a_verdict_backed_by_evidence():
    result = ObserveCnsGate("omega").check(make_verdict())
    assert result.outcome is GateOutcome.PASS
    assert result.position is GatePosition.OMEGA
    assert result.gate == "observe.verdict_egress"


def test_observe_omega_refuses_a_partial_assessment():
    partial = make_verdict(unassessable=True, validation_faults=["heart_rate=-5"])
    result = ObserveCnsGate("omega").check(partial)
    assert result.outcome is GateOutcome.RETRY
    assert "partial assessment" in result.reason


def test_observe_omega_refuses_a_verdict_no_engine_backed():
    result = ObserveCnsGate("omega").check(make_verdict(active_engines=[]))
    assert result.outcome is GateOutcome.RETRY
    assert "no engine produced evidence" in result.reason


def test_observe_verdict_binds_to_the_content_it_judged():
    gate = ObserveCnsGate("alpha")
    result = gate.check(make_vitals(heart_rate=100))
    assert result.bound()
    assert result.binds(result.subject, result.subject_digest)
    other = gate.check(make_vitals(heart_rate=101))
    assert not result.binds(other.subject, other.subject_digest)


def test_observe_wrong_candidate_for_the_end_is_a_fault():
    result = ObserveCnsGate("alpha").check(make_verdict())
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "fault" in result.reason


def test_observe_raising_check_is_a_fault_not_a_pass(monkeypatch):
    def explode(vitals):
        raise RuntimeError("boom")

    monkeypatch.setattr(cns_connector, "validate_vitals", explode)
    result = ObserveCnsGate("alpha").check(make_vitals())
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "boom" in result.reason


# ---------------------------------------------------------------- PERCEIVE

def request(**overrides):
    defaults = dict(
        request_id="r1", request_type="escalate_patient",
        subject_id="P1", actor_id="A1", context={},
    )
    defaults.update(overrides)
    return PolicyRequest(**defaults)


def verdict(**overrides):
    defaults = dict(
        request_id="r1", approved=True, confidence=0.9, violations=[],
        applied_gates=["boundary_gate", "sentinel"], policy_version="1.0.0",
        provenance=Provenance("system", "test", "fixture"), audit_hash="b" * 64,
    )
    defaults.update(overrides)
    return PolicyVerdict(**defaults)


def test_perceive_alpha_passes_a_well_formed_request():
    result = PerceiveCnsGate("boundary_gate", "alpha").check(request())
    assert result.outcome is GateOutcome.PASS
    assert result.position is GatePosition.ALPHA
    assert result.gate == "perceive.boundary_gate"


def test_perceive_alpha_refuses_a_request_its_gate_refuses():
    result = PerceiveCnsGate("boundary_gate", "alpha").check(request(actor_id=""))
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "Missing actor_id" in result.reason


def test_perceive_alpha_sentinel_refuses_a_volume_spike():
    result = PerceiveCnsGate("sentinel", "alpha").check(
        request(context={"operation_count_today": 500})
    )
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "operation frequency" in result.reason


def test_perceive_omega_passes_an_approval_that_ran_its_gate():
    result = PerceiveCnsGate("sentinel", "omega").check(verdict())
    assert result.outcome is GateOutcome.PASS
    assert result.position is GatePosition.OMEGA


def test_perceive_omega_refuses_an_approval_where_its_gate_never_ran():
    result = PerceiveCnsGate("micropatch", "omega").check(verdict())
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "micropatch having run" in result.reason


def test_perceive_omega_refuses_an_approval_with_no_audit_record():
    result = PerceiveCnsGate("sentinel", "omega").check(verdict(audit_hash=""))
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "no audit record" in result.reason


def test_perceive_omega_refuses_an_approval_that_carries_violations():
    result = PerceiveCnsGate("sentinel", "omega").check(verdict(violations=["x"]))
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "carries violations" in result.reason


def test_perceive_omega_lets_a_refusal_leave_as_a_valid_result():
    refused = verdict(approved=False, violations=["x"])
    result = PerceiveCnsGate("sentinel", "omega").check(refused)
    assert result.outcome is GateOutcome.PASS


def test_perceive_verdict_binds_to_the_content_it_judged():
    gate = PerceiveCnsGate("boundary_gate", "alpha")
    result = gate.check(request(request_id="r1"))
    assert result.bound()
    assert result.binds(result.subject, result.subject_digest)
    other = gate.check(request(request_id="r2"))
    assert not result.binds(other.subject, other.subject_digest)


def test_perceive_unknown_gate_name_is_rejected():
    with pytest.raises(ValueError):
        PerceiveCnsGate("escalation_rate_policy", "alpha")


def test_perceive_wrong_candidate_for_the_end_is_a_fault():
    result = PerceiveCnsGate("sentinel", "alpha").check(verdict())
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "fault" in result.reason


def test_perceive_raising_check_is_a_fault_not_a_pass(monkeypatch):
    def explode(req):
        raise RuntimeError("boom")

    monkeypatch.setattr(PolicyGates, "sentinel", staticmethod(explode))
    result = PerceiveCnsGate("sentinel", "alpha").check(request())
    assert result.outcome is GateOutcome.TERMINAL_BREACH
    assert "boom" in result.reason


# ---------------------------------------------------------------- shared

def test_unknown_position_is_rejected():
    with pytest.raises(ValueError):
        ObserveCnsGate("middle")
    with pytest.raises(ValueError):
        PerceiveCnsGate("sentinel", "middle")


def test_missing_cns_raises_with_the_install_command(monkeypatch):
    real_import = importlib.import_module

    def no_cns(name, *args, **kwargs):
        if name == "cns.gate":
            raise ModuleNotFoundError("No module named 'cns'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(importlib, "import_module", no_cns)
    assert cns_available() is False
    with pytest.raises(CnsNotInstalled, match="observe-perceive\\[cns\\]"):
        ObserveCnsGate("alpha")
    with pytest.raises(CnsNotInstalled, match="observe-perceive\\[cns\\]"):
        PerceiveCnsGate("sentinel", "alpha")
