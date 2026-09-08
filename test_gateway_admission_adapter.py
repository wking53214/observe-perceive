"""Governance Gateway as the chain's front door.

Two things under test, and the second is the one that mattered:

1. Admission -- a malformed or tampered artifact is refused before any
   governance stage spends work on it, and the refusal is distinguishable
   from a policy decision.
2. Scope -- the chain executed on approval without ever asking whether the
   artifact was scoped to be run. An artifact admitted READ_ONLY could be
   executed. That gap is closed in two places on purpose, and both are
   tested: at the door, and inside the orchestrator so a caller who bypasses
   the door still cannot get past it.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import List

import pytest

from conservation_kernel import ConservationKernel
from gateway_admission_adapter import GatewayAdmissionAdapter
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest

gateway_available = True
try:
    from governance_gateway.models import EpistemicStatus, Scope
except ModuleNotFoundError:  # pragma: no cover
    gateway_available = False

pytestmark = pytest.mark.skipif(
    not gateway_available, reason="Governance_Gateway checkout not available"
)


# --- the chain's own artifact shape (duck-typed, as the chain expects) -----

@dataclass
class _Status:
    value: str


@dataclass
class _Metadata:
    origin_status: _Status
    authority_status: _Status
    epistemic_status: _Status
    parent_artifact_ids: List[str]


@dataclass
class _ChainArtifact:
    artifact_id: str
    content: str
    metadata: _Metadata


def _chain_artifact(artifact_id="gw-001"):
    return _ChainArtifact(
        artifact_id=artifact_id,
        content="escalate: sustained tachycardia",
        metadata=_Metadata(
            origin_status=_Status("SENTINEL"),
            authority_status=_Status("SYSTEM"),
            epistemic_status=_Status("INFERRED"),
            parent_artifact_ids=[],
        ),
    )


def _sealed(artifact_id="gw-001", execute=True):
    return GatewayAdmissionAdapter.seal(
        artifact_id=artifact_id,
        payload={"action": "escalate", "subject": "P001"},
        provenance={"producer": "SENTINEL", "run": "r-1"},
        epistemic_status=EpistemicStatus.DECISION,
        authority_actor="sentinel",
        authority_grant="escalate",
        execute=execute,
    )


@pytest.fixture
def adapter():
    return GatewayAdmissionAdapter()


@pytest.fixture
def orchestrator():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="gateway-seam-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return GovernanceOrchestrator(kernel, ConservationKernel(), ObserveClinicalEngine())


# ---------------------------------------------------------------------------
# Admission
# ---------------------------------------------------------------------------

def test_a_well_formed_sealed_artifact_is_admitted(adapter):
    result = adapter.admit(_sealed())
    assert result.admitted is True
    assert result.scope == "EXECUTE"


def test_a_tampered_artifact_is_refused(adapter):
    """The check that makes admission worth doing. The digest covers
    artifact_id, payload, provenance, epistemic status, authority and scope,
    so a field altered after sealing no longer matches."""
    artifact = _sealed()
    tampered = replace(artifact, payload={"action": "escalate", "subject": "P999"})

    result = adapter.admit(tampered)
    assert result.admitted is False
    assert result.reason == "INTEGRITY_FAILURE"


def test_scope_escalation_after_sealing_is_caught(adapter):
    """The most valuable tamper case: an artifact sealed READ_ONLY, then
    promoted to EXECUTE. Scope is inside the digest, so the promotion breaks
    it."""
    read_only = _sealed(execute=False)
    promoted = replace(read_only, scope=Scope.EXECUTE)

    result = adapter.admit(promoted)
    assert result.admitted is False
    assert result.reason == "INTEGRITY_FAILURE"


def test_an_artifact_that_is_not_a_gateway_artifact_is_refused(adapter):
    result = adapter.admit(_chain_artifact())
    assert result.admitted is False
    assert result.reason == "INVALID_ARTIFACT"


def test_sealing_defaults_to_read_only():
    """A producer that wants an artifact runnable has to say so. Acquiring
    execution scope by omission is the wrong default for a governed system."""
    assert _sealed(execute=False).scope is Scope.READ_ONLY
    assert GatewayAdmissionAdapter.seal(
        artifact_id="x", payload={}, provenance={"p": 1},
        epistemic_status=EpistemicStatus.INFERENCE,
        authority_actor="a", authority_grant="g",
    ).scope is Scope.READ_ONLY


# ---------------------------------------------------------------------------
# Scope: the gap this seam closes
# ---------------------------------------------------------------------------

def test_read_only_admission_does_not_permit_execution(adapter):
    result = adapter.admit(_sealed(execute=False))
    assert result.admitted is True          # it is a valid artifact
    assert result.execution_permitted is False   # it is just not runnable


def test_a_refused_artifact_never_permits_execution(adapter):
    result = adapter.admit(_chain_artifact())
    assert result.execution_permitted is False


def test_a_read_only_artifact_is_stopped_before_the_chain_runs(adapter, orchestrator):
    ran = []
    result = adapter.govern_admitted(
        orchestrator,
        _sealed(execute=False),
        _chain_artifact(),
        operation_func=lambda ctx: ran.append(True),
        context={"patient_id": "P001"},
    )
    assert result["status"] == "NOT_ADMITTED"
    assert "EXECUTE" in result["reason"]
    assert not ran, "a READ_ONLY artifact was executed"


def test_the_orchestrator_refuses_read_only_even_without_the_door(orchestrator):
    """Defence in depth, and the point of enforcing in two places. A caller
    that bypasses the admission adapter and drives the orchestrator directly
    still cannot execute a read-only artifact."""
    ran = []
    result = orchestrator.orchestrate_request(
        _chain_artifact(),
        "escalate",
        lambda ctx: ran.append(True),
        context={"patient_id": "P001", "gateway_scope": "READ_ONLY"},
    )
    assert result["status"] == "REJECTED"
    assert "does not permit execution" in result["reason"]
    assert not ran, "the orchestrator executed a READ_ONLY artifact"


def test_callers_that_declare_no_scope_are_unaffected(orchestrator):
    """Every caller predating this seam passes no scope. Refusing those would
    be a silent breaking change dressed as a safety improvement."""
    ran = []
    result = orchestrator.orchestrate_request(
        _chain_artifact(),
        "escalate",
        lambda ctx: ran.append(True) or {"status": "executed"},
        context={"patient_id": "P001"},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert ran


# ---------------------------------------------------------------------------
# An undeclared scope is a decision, not an absence
# ---------------------------------------------------------------------------

def test_the_record_says_whether_a_scope_was_actually_checked(orchestrator):
    """The failure this closes.

    A READ_ONLY artifact is refused loudly. An artifact with no scope at all
    was executed with nothing recording that the check had done nothing -- so
    from outside, an approval that skipped the check and an approval that
    passed it were the same value. Measured when this was added: 15 of 16
    call sites declared no scope, so the check was reaching almost nothing
    while looking like a control.
    """
    # Distinct artifact ids: the same artifact run twice is a replay, and
    # the chain refuses replays -- which would mask what this is testing.
    unscoped = orchestrator.orchestrate_request(
        _chain_artifact("gw-unscoped"), "escalate",
        lambda ctx: {"status": "executed"},
        context={"patient_id": "P001"},
    )
    assert unscoped["status"] == "APPROVED_AND_EXECUTED"
    assert unscoped["scope_enforced"] is False
    assert unscoped["declared_scope"] is None

    scoped = orchestrator.orchestrate_request(
        _chain_artifact("gw-scoped"), "escalate",
        lambda ctx: {"status": "executed"},
        context={"patient_id": "P001", "gateway_scope": "EXECUTE"},
    )
    assert scoped["status"] == "APPROVED_AND_EXECUTED"
    assert scoped["scope_enforced"] is True
    assert scoped["declared_scope"] == "EXECUTE"


def test_require_declared_scope_refuses_an_unscoped_request():
    """Opt-in strict mode, for a deployment that has finished migrating its
    callers. Off by default: turning it on globally would refuse 15 of the 16
    existing call sites, which is a breaking change wearing a safety
    improvement's clothes."""
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="strict-scope-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    strict = GovernanceOrchestrator(
        kernel, ConservationKernel(), ObserveClinicalEngine(),
        require_declared_scope=True,
    )

    ran = []
    result = strict.orchestrate_request(
        _chain_artifact(), "escalate",
        lambda ctx: ran.append(True),
        context={"patient_id": "P001"},
    )
    assert result["status"] == "REJECTED"
    assert "no gateway scope declared" in result["reason"]
    assert result["scope_enforced"] is False
    assert not ran, "executed a request with no declared scope under strict mode"


def test_strict_mode_still_allows_a_properly_scoped_request():
    """Strict mode must refuse the undeclared case only. If it also blocked
    correctly-scoped work it would be a denial of service, not a control."""
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="strict-scope-manifest-2", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    strict = GovernanceOrchestrator(
        kernel, ConservationKernel(), ObserveClinicalEngine(),
        require_declared_scope=True,
    )

    # 1.1.0: "properly scoped" means sealed. A bare claim is refused in
    # strict mode (see test_scope_binding.py); a sealed admission backs it.
    result = strict.orchestrate_request(
        _chain_artifact(), "escalate",
        lambda ctx: {"status": "executed"},
        context={"patient_id": "P001", "gateway_scope": "EXECUTE", "gateway_admission": _sealed(execute=True)},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["scope_enforced"] is True and result["scope_sealed"] is True


# ---------------------------------------------------------------------------
# Refusal is not a policy decision
# ---------------------------------------------------------------------------

def test_a_door_refusal_is_not_reported_as_a_governance_rejection(adapter, orchestrator):
    """A tampered artifact is not a policy refusal -- nothing was evaluated.
    Reporting it as REJECTED would put a governance verdict on a decision no
    gate ever made."""
    tampered = replace(_sealed(), payload={"action": "delete_everything"})
    result = adapter.govern_admitted(
        orchestrator, tampered, _chain_artifact(), context={"patient_id": "P001"}
    )
    assert result["status"] == "NOT_ADMITTED"
    assert result["status"] != "REJECTED"
    assert result["governance_decision"] is None, (
        "a governance decision was reported for an artifact no gate evaluated"
    )


def test_an_admitted_execute_artifact_runs_the_whole_chain(adapter, orchestrator):
    result = adapter.govern_admitted(
        orchestrator, _sealed(execute=True), _chain_artifact(),
        operation_type="escalate", context={"patient_id": "P001"},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["admission"].admitted is True
    assert result["conservation_decision"].verified is True
