"""Shared fixtures for the chain-level suites (adversarial, replay, failure paths).

Kept deliberately small: a PERCEIVE kernel with an empty manifest, a duck-typed
artifact shaped exactly as the chain's de facto input contract, vitals, and a
runner that returns the result plus whether the action ran.
"""
from datetime import datetime, timezone

import pytest

from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest


class _Status:
    def __init__(self, value):
        self.value = value


class _Metadata:
    def __init__(self, origin="SENTINEL", authority="SYSTEM", epistemic="INFERRED", parents=None):
        self.origin_status = _Status(origin)
        self.authority_status = _Status(authority)
        self.epistemic_status = _Status(epistemic)
        self.parent_artifact_ids = list(parents or [])


class ChainArtifact:
    def __init__(self, artifact_id, content="escalate P001", **meta):
        self.artifact_id = artifact_id
        self.content = content
        self.metadata = _Metadata(**meta)


def make_perceive():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="chain-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return kernel


def make_vitals():
    return VitalsSnapshot(
        patient_id="P001", timestamp=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        heart_rate=120, oxygen_saturation=95, respiratory_rate=20, temperature=37.5,
    )


@pytest.fixture
def perceive():
    return make_perceive()


@pytest.fixture
def kernel():
    return ConservationKernel()


@pytest.fixture
def orchestrator(perceive, kernel):
    return GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine())


def run_chain(orchestrator, artifact_id, operation="escalate", func=None, vitals=True, context=None, artifact=None):
    ran = []
    def default(ctx):
        ran.append(ctx)
        return {"status": "done", "artifact_id": ctx.artifact_id}
    ctx = {"patient_id": "P001", "execution_id": f"exec-{artifact_id}"}
    ctx.update(context or {})
    result = orchestrator.orchestrate_request(
        artifact or ChainArtifact(artifact_id), operation, func or default,
        vitals_snapshot=make_vitals() if vitals else None, context=ctx,
    )
    return result, ran
