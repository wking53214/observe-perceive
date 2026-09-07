"""
The record must say what the chain did, not only what it decided.

Three places where a check was skipped, absent, or crashed and the result
looked the same as if it had run:

- PERCEIVE's policy gates default to advisory mode and dropped what they
  would have refused: an export with no consent came back approved with
  `violations == []`.
- OBSERVE is skipped whenever no vitals are passed; the result carried
  `observe_verdict: None` and nothing else.
- The Conservation and execution-approval stages turn *any* exception into
  REJECTED, so "the kernel refused" and "there was no kernel" were the same
  record. A PERCEIVE refusal was filed in the approval gate's words.

Each now carries a field, logs a warning naming the remedy, and has an
opt-in strict flag. Defaults are unchanged.
"""

from datetime import datetime, timezone

import pytest

from conservation_kernel import ConservationKernel
from governance_contracts import GovernanceApproval
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from perceive_consolidated import (
    PerceiveGovernanceKernel, PolicyEnforcementConfig, PolicyManifest, PolicyRequest,
)
from sentinel_perceive_adapter import SentinelPerceiveAdapter


class _Status:
    def __init__(self, value):
        self.value = value


class _Metadata:
    def __init__(self):
        self.origin_status = _Status("SENTINEL")
        self.authority_status = _Status("SYSTEM")
        self.epistemic_status = _Status("INFERRED")
        self.parent_artifact_ids = []


class _Artifact:
    def __init__(self, artifact_id):
        self.artifact_id = artifact_id
        self.content = "escalate: sustained tachycardia"
        self.metadata = _Metadata()


def _kernel(with_manifest=True, enforcement=None):
    kernel = PerceiveGovernanceKernel(enforcement=enforcement)
    if with_manifest:
        kernel.register_manifest(PolicyManifest(
            manifest_id="visibility-manifest", version="1.0.0",
            created_at=datetime.now(timezone.utc), policies={},
        ))
    return kernel


def _orchestrator(conservation=ConservationKernel, perceive=None, **kw):
    return GovernanceOrchestrator(
        perceive or _kernel(),
        conservation() if conservation else None,
        ObserveClinicalEngine(),
        **kw,
    )


def _vitals():
    return VitalsSnapshot(
        patient_id="P001", timestamp=datetime.now(timezone.utc),
        heart_rate=120, oxygen_saturation=95, respiratory_rate=20, temperature=37.5,
    )


def _run(orchestrator, artifact_id, **kw):
    ran = []
    result = orchestrator.orchestrate_request(
        _Artifact(artifact_id), "escalate",
        lambda ctx: ran.append(True) or {"status": "executed"},
        context={"patient_id": "P001"}, **kw,
    )
    return result, ran


# ---------------------------------------------------------------------------
# PERCEIVE advisory gates
# ---------------------------------------------------------------------------

def _export_request(request_id):
    return PolicyRequest(
        request_id=request_id, request_type="export_data", subject_id="P001",
        actor_id="SYSTEM_export",
        context={"export_type": "full_record", "has_consent": False, "will_encrypt": False},
        timestamp=datetime.now(timezone.utc),
    )


def test_an_advisory_refusal_is_visible_on_the_verdict():
    """Default enforcement is advisory: the export is approved, but the
    verdict now says what the export gate would have refused."""
    verdict = _kernel().evaluate_request(_export_request("adv-001"))
    assert verdict.approved is True
    assert verdict.violations == []
    assert verdict.advisory_violations, "the unenforced refusal was dropped"
    assert all(v.startswith("data_export_policy:") for v in verdict.advisory_violations)


def test_enforcing_the_flag_turns_the_advisory_into_a_refusal():
    """The strict mode already existed; this pins that it is the remedy the
    warning names."""
    kernel = _kernel(enforcement=PolicyEnforcementConfig(enforce_export_controls=True))
    verdict = kernel.evaluate_request(_export_request("adv-002"))
    assert verdict.approved is False
    assert any(v.startswith("data_export_policy:") for v in verdict.violations)
    assert verdict.advisory_violations == []


def test_a_clean_approval_carries_no_advisory_violations():
    request = PolicyRequest(
        request_id="adv-003", request_type="escalate_patient", subject_id="P001",
        actor_id="sentinel", context={"justification": "sustained tachycardia over threshold"},
        timestamp=datetime.now(timezone.utc),
    )
    verdict = _kernel().evaluate_request(request)
    assert verdict.approved is True
    assert verdict.advisory_violations == []


def test_advisory_violations_travel_into_the_governance_decision():
    verdict = _kernel().evaluate_request(_export_request("adv-004"))
    request = SentinelPerceiveAdapter.sentinel_artifact_to_governance_request(
        _Artifact("adv-004"), "export", {},
    )
    decision = SentinelPerceiveAdapter.policy_verdict_to_governance_decision(verdict, request)
    assert decision.approval is GovernanceApproval.APPROVED
    assert decision.advisory_violations == verdict.advisory_violations


# ---------------------------------------------------------------------------
# OBSERVE skip
# ---------------------------------------------------------------------------

def test_the_record_says_whether_observe_ran():
    without, _ = _run(_orchestrator(), "obs-001")
    assert without["status"] == "APPROVED_AND_EXECUTED", without.get("reason")
    assert without["observe_enforced"] is False
    assert without["observe_verdict"] is None

    with_vitals, _ = _run(_orchestrator(), "obs-002", vitals_snapshot=_vitals())
    assert with_vitals["status"] == "APPROVED_AND_EXECUTED", with_vitals.get("reason")
    assert with_vitals["observe_enforced"] is True
    assert with_vitals["observe_verdict"] is not None


def test_require_vitals_refuses_before_anything_executes():
    result, ran = _run(_orchestrator(require_vitals=True), "obs-003")
    assert result["status"] == "REJECTED"
    assert "vitals" in result["reason"]
    assert result["observe_enforced"] is False
    assert ran == [], "strict mode executed the operation before refusing"


def test_require_vitals_still_allows_a_request_that_supplies_them():
    result, ran = _run(_orchestrator(require_vitals=True), "obs-004", vitals_snapshot=_vitals())
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["observe_enforced"] is True
    assert ran == [True]


