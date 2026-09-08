"""FORTRESS as a stage in the orchestrated chain.

test_fortress_integration.py covers the adapter on its own. These cover the
thing that was actually missing: FORTRESS placed *between* PERCEIVE and the
Conservation Kernel inside a real orchestration, including the case that
matters most -- a request PERCEIVE permitted that FORTRESS refuses.
"""

import pytest
from datetime import datetime, timezone

from governance_orchestrator import GovernanceOrchestrator
from governance_contracts import GovernanceApproval
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest
from observe_consolidated import ObserveClinicalEngine
from conservation_kernel import ConservationKernel

try:
    import fortress_perceive_adapter  # noqa: F401
    fortress_available = True
except ImportError:  # pragma: no cover - fortress-kernel is an optional pack
    fortress_available = False
# Only the tests that ask for a controller need the pack; the absent-by-default
# test is exactly the minimal-install case and must keep running without it.
needs_fortress = pytest.mark.skipif(not fortress_available, reason="fortress-kernel checkout not available")


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
    def __init__(self, artifact_id="fortress-chain-001"):
        self.artifact_id = artifact_id
        self.content = "escalate: sustained tachycardia over three intervals"
        self.metadata = _Metadata()


@pytest.fixture
def perceive():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="fortress-chain-manifest",
        version="1.0.0",
        created_at=datetime.now(timezone.utc),
        policies={},
    ))
    return kernel


def _orchestrator(perceive, controller=None):
    return GovernanceOrchestrator(
        perceive,
        ConservationKernel(),
        ObserveClinicalEngine(),
        fortress_controller=controller,
    )


def _run(orchestrator, artifact=None):
    return orchestrator.orchestrate_request(
        artifact or _Artifact(),
        "escalate",
        lambda context: {"status": "executed", "result": "success"},
        context={"patient_id": "P001"},
    )


def test_fortress_is_absent_by_default(perceive):
    """The chain must behave exactly as it did before FORTRESS existed when
    no controller is asked for -- a missing fortress-kernel checkout is never
    a hard failure for the other four systems."""
    orchestrator = _orchestrator(perceive)
    assert orchestrator.fortress_adapter is None
    result = _run(orchestrator)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["fortress_result"] is None


@needs_fortress
@pytest.mark.parametrize("controller", ["energy", "lyapunov", "sage"])
def test_each_controller_runs_inside_the_full_chain(perceive, controller):
    orchestrator = _orchestrator(perceive, controller)
    result = _run(orchestrator)

    assert result["status"] == "APPROVED_AND_EXECUTED"
    fortress = result["fortress_result"]
    assert fortress is not None, "FORTRESS was configured but never ran"
    assert fortress.controller == controller.upper()
    assert fortress.decision == "APPROVED"
    # The chain continued past FORTRESS, so the later stages must be present.
    assert result["conservation_decision"] is not None
    assert result["execution_approval"] is not None


@needs_fortress
def test_fortress_runs_after_perceive_and_before_conservation(perceive):
    """Ordering is the reason this stage exists. PERCEIVE decides whether the
    request is permitted; FORTRESS decides whether acting on it stays inside
    safe bounds. If FORTRESS ran after the Conservation Kernel it would be
    verifying a decision that had already been sealed."""
    calls = []
    orchestrator = _orchestrator(perceive, "energy")

    real_fortress = orchestrator.fortress_adapter.process_governance_decision
    real_conservation = orchestrator.perceive_adapter.verify_perceive_decision

    def traced_fortress(*args, **kwargs):
        calls.append("fortress")
        return real_fortress(*args, **kwargs)

    def traced_conservation(*args, **kwargs):
        calls.append("conservation")
        return real_conservation(*args, **kwargs)

    orchestrator.fortress_adapter.process_governance_decision = traced_fortress
    orchestrator.perceive_adapter.verify_perceive_decision = traced_conservation

    _run(orchestrator)
    assert calls == ["fortress", "conservation"]


@needs_fortress
def test_a_fortress_refusal_stops_the_chain_and_fails_closed(perceive):
    """The case the layer exists for: PERCEIVE permitted the request, FORTRESS
    refuses it on safety grounds, and nothing downstream gets to re-approve
    what FORTRESS refused."""
    orchestrator = _orchestrator(perceive, "energy")

    conservation_ran = []
    real_conservation = orchestrator.perceive_adapter.verify_perceive_decision

    def traced(*args, **kwargs):
        conservation_ran.append(True)
        return real_conservation(*args, **kwargs)

    orchestrator.perceive_adapter.verify_perceive_decision = traced

    # Force a refusal at the containment layer without touching PERCEIVE's
    # own verdict -- the point is that an approved request can still be
    # stopped here.
    real_fortress = orchestrator.fortress_adapter.process_governance_decision

    def refusing(*args, **kwargs):
        result = real_fortress(*args, **kwargs)
        result.decision = "REJECTED"
        result.distortion = 0.95
        return result

    orchestrator.fortress_adapter.process_governance_decision = refusing

    result = _run(orchestrator)

    assert result["status"] == "REJECTED"
    assert "FORTRESS" in result["reason"]
    assert result["audit_chain"] is None
    assert not conservation_ran, "Conservation Kernel ran after FORTRESS refused"
    # PERCEIVE's own decision is still carried, so the refusal is auditable
    # against what was permitted.
    assert result["governance_decision"].approval is GovernanceApproval.APPROVED


@needs_fortress
def test_perceive_violations_reach_fortress_as_error_signal(perceive):
    """FORTRESS's error signal is derived from PERCEIVE's violation count, not
    invented -- the two systems' notions of "something is wrong" stay
    connected. A clean approval is zero error."""
    orchestrator = _orchestrator(perceive, "energy")
    seen = {}

    real = orchestrator.fortress_adapter.process_governance_request

    def capture(request_id, perceive_decision, *args, **kwargs):
        seen.update(perceive_decision)
        return real(request_id, perceive_decision, *args, **kwargs)

    orchestrator.fortress_adapter.process_governance_request = capture
    _run(orchestrator)

    assert "error_signal" in seen
    assert seen["error_signal"] == 0.0, "a clean unanimous approval should carry no error"
    assert seen["approved"] is True


@needs_fortress
def test_fortress_keeps_its_own_verifiable_audit_ledger(perceive):
    orchestrator = _orchestrator(perceive, "energy")
    result = _run(orchestrator)

    fortress = result["fortress_result"]
    assert fortress.audit_ledger_size > 0, "FORTRESS recorded nothing"
    assert fortress.audit_verified is True, "FORTRESS audit chain does not verify"
    assert fortress.forensic_trace["audit_chain_valid"] is True
