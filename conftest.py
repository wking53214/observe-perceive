"""Shared fixtures for the chain-level suites (adversarial, replay, failure paths).

Kept deliberately small: a PERCEIVE kernel with an empty manifest, a duck-typed
artifact shaped exactly as the chain's de facto input contract, vitals, and a
runner that returns the result plus whether the action ran.

Also ``importorskip_cns``, the one rule for tests that need CNS or a library's
CNS connector: they skip where it is missing, unless
OBSERVE_PERCEIVE_REQUIRE_CNS=1, which turns a missing module into an error.
"""
import importlib
import os
from datetime import datetime, timezone

import pytest

from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest


def importorskip_cns(modname, reason=None):
    """``pytest.importorskip``, except that OBSERVE_PERCEIVE_REQUIRE_CNS=1 makes a missing module an error.

    CI's CNS job installs requirements-demo.txt and sets the variable, so there
    the CNS layer and its demo cannot silently skip.
    """
    if os.environ.get("OBSERVE_PERCEIVE_REQUIRE_CNS") == "1":  # CI: a missing module is a failure, not a skip
        return importlib.import_module(modname)
    return pytest.importorskip(modname, reason=reason)


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
    return ConservationKernel(allow_unguarded_roots=True)


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


def make_test_desk(alerts=None):
    """A minimal escalation desk for tests that build a strict orchestrator (strict refuses to start without one).
    Everything the desk dispatches is appended to `alerts` (a list) when one is given."""
    from conservation_kernel import HmacSigner
    from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier
    sink = alerts if alerts is not None else []
    key = HmacSigner(b"test-strict-desk-responder-key-0123456789", key_id="nurse")
    return EscalationDesk(NotificationPolicy((Tier("charge", sink.append, ("nurse",), 60.0, 5.0),)), EscalationLog(),
                          responder_keys={"nurse": key})


_TEST_RUNNERS: list = []


@pytest.fixture(autouse=True)
def _stop_test_runners():
    yield
    while _TEST_RUNNERS:
        _TEST_RUNNERS.pop().stop(timeout=2)


def make_test_runner(desk):
    """A started sweep runner bound to `desk`, with intervals long enough that it never fires during a test.
    It is stopped when the test ends."""
    from escalation_runner import SweepRunner
    runner = SweepRunner(desk, sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200, alarm_timeout_s=5,
                         max_tickets_per_sweep=10, alarm=lambda payload: None).start()
    _TEST_RUNNERS.append(runner)
    return runner


def strict_wiring(alerts=None):
    """The desk and the running sweep runner a strict orchestrator needs, as keyword arguments."""
    desk = make_test_desk(alerts)
    return {"escalations": desk, "sweep_runner": make_test_runner(desk)}
