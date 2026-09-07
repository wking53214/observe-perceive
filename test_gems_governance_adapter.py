"""GEMS handoffs entering the governance chain.

GEMS carries a constitutional rule its own guard enforces:

    AI/joint/uncertain material cannot silently become human authorization

This seam is exactly where that could be lost -- an adapter reading
`authority` and passing it along without checking `origin` would let an
artifact arrive downstream carrying human authority it never legitimately
acquired. Most of these tests are about the guard actually running.
"""

from datetime import datetime, timezone

import pytest

from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest

gems_available = True
try:
    # The adapter is imported first on purpose: it is what resolves the GEMS
    # sibling checkout onto sys.path, so importing gems' own types before it
    # fails even when GEMS is present.
    from gems_governance_adapter import GemsGovernanceAdapter
    from gems import Artifact, Authority, Handoff, Origin, Provenance
    from gems.contracts import EpistemicStatus
except (ModuleNotFoundError, ImportError):  # pragma: no cover
    gems_available = False

pytestmark = pytest.mark.skipif(not gems_available, reason="GEMS checkout not available")


def _artifact(artifact_id="art-1", origin=None, authority=None, status=None):
    return Artifact(
        artifact_id=artifact_id,
        kind="analysis",
        content="Servicing file shows three payments in the disputed window.",
        provenance=Provenance(
            source_id="doc-77",
            origin=origin or Origin.AI,
            epistemic_status=status or EpistemicStatus.INFERRED,
            authority=authority or Authority.ANALYSIS,
        ),
    )


def _handoff(artifacts=None, **overrides):
    base = dict(
        handoff_id="ho-1",
        task_id="task-9",
        sender="analyst-gem",
        recipient="reviewer-gem",
        artifacts=tuple(artifacts if artifacts is not None else [_artifact()]),
        routing_signal="servicing.dispute",
    )
    base.update(overrides)
    return Handoff(**base)


@pytest.fixture
def adapter():
    return GemsGovernanceAdapter()


@pytest.fixture
def orchestrator():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="gems-seam-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return GovernanceOrchestrator(kernel, ConservationKernel(), ObserveClinicalEngine())


# ---------------------------------------------------------------------------
# GEMS' constitutional rule, enforced not assumed
# ---------------------------------------------------------------------------

def test_ai_material_claiming_human_authorization_is_refused(adapter):
    """GEMS' own rule, run at this seam. An adapter that read `authority` and
    passed it along without checking `origin` would let an artifact arrive
    downstream carrying human authority it never legitimately acquired."""
    smuggled = _artifact(
        origin=Origin.AI, authority=Authority.HUMAN_AUTHORIZATION
    )
    refusal = adapter.check(_handoff([smuggled]))

    assert refusal is not None
    assert "constitutional violation" in refusal.reason.lower()
    assert refusal.artifact_id == "art-1"


@pytest.mark.parametrize("origin", [Origin.AI, Origin.JOINT, Origin.UNCERTAIN])
def test_no_non_human_origin_may_carry_human_authorization(adapter, origin):
    """"AI/joint/uncertain" -- all three, not just AI."""
    refusal = adapter.check(_handoff([
        _artifact(origin=origin, authority=Authority.HUMAN_AUTHORIZATION)
    ]))
    assert refusal is not None


def test_genuine_human_authorization_passes(adapter):
    assert adapter.check(_handoff([
        _artifact(origin=Origin.HUMAN, authority=Authority.HUMAN_AUTHORIZATION)
    ])) is None


def test_one_bad_artifact_refuses_the_whole_handoff(adapter):
    """Not just the offending artifact. A handoff missing one artifact is a
    different handoff, and nobody downstream would know which one went
    missing."""
    refusal = adapter.check(_handoff([
        _artifact("ok-1", origin=Origin.HUMAN, authority=Authority.OBSERVATION),
        _artifact("bad-1", origin=Origin.AI, authority=Authority.HUMAN_AUTHORIZATION),
        _artifact("ok-2", origin=Origin.HUMAN, authority=Authority.OBSERVATION),
    ]))
    assert refusal is not None
    assert refusal.artifact_id == "bad-1"


def test_an_artifact_without_provenance_is_refused(adapter):
    bare = Artifact(artifact_id="no-prov", kind="analysis", content="x", provenance=None)
    refusal = adapter.check(_handoff([bare]))
    assert refusal is not None
    assert "provenance" in refusal.reason.lower()


def test_a_handoff_missing_sender_or_recipient_is_refused(adapter):
    assert adapter.check(_handoff(sender="")) is not None
    assert adapter.check(_handoff(recipient="")) is not None


# ---------------------------------------------------------------------------
# Mixed handoffs: the weakest claim wins
# ---------------------------------------------------------------------------

def test_one_ai_artifact_makes_the_handoff_non_human(adapter):
    """A handoff containing one AI-origin artifact is not a human-origin
    handoff. Taking the strongest, or the first, would let a single confident
    artifact speak for the rest."""
    origin, _status, _authority = GemsGovernanceAdapter._dominant_provenance(
        _handoff([
            _artifact("a", origin=Origin.HUMAN),
            _artifact("b", origin=Origin.AI),
        ])
    )
    assert origin == "ai"


def test_one_unknown_status_makes_the_handoff_not_explicit(adapter):
    _origin, status, _authority = GemsGovernanceAdapter._dominant_provenance(
        _handoff([
            _artifact("a", status=EpistemicStatus.EXPLICIT),
            _artifact("b", status=EpistemicStatus.UNKNOWN),
        ])
    )
    assert status == "unknown"


def test_conflicted_is_weaker_than_unknown(adapter):
    _origin, status, _authority = GemsGovernanceAdapter._dominant_provenance(
        _handoff([
            _artifact("a", status=EpistemicStatus.UNKNOWN),
            _artifact("b", status=EpistemicStatus.CONFLICTED),
        ])
    )
    assert status == "conflicted"


def test_the_weakest_authority_wins(adapter):
    _origin, _status, authority = GemsGovernanceAdapter._dominant_provenance(
        _handoff([
            _artifact("a", origin=Origin.HUMAN, authority=Authority.HUMAN_AUTHORIZATION),
            _artifact("b", origin=Origin.HUMAN, authority=Authority.OBSERVATION),
        ])
    )
    assert authority == "observation"


def test_human_review_needs_both_human_origin_and_human_authorization(adapter):
    """GEMS' rule restated so the chain's gates see it: authority alone is not
    enough, because that is precisely what the guard refuses."""
    reviewed = GemsGovernanceAdapter.handoff_context(_handoff([
        _artifact(origin=Origin.HUMAN, authority=Authority.HUMAN_AUTHORIZATION)
    ]))
    analysed = GemsGovernanceAdapter.handoff_context(_handoff([
        _artifact(origin=Origin.HUMAN, authority=Authority.ANALYSIS)
    ]))
    assert reviewed["human_reviewed"] is True
    assert analysed["human_reviewed"] is False
    assert analysed["requires_human_oversight"] is True


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_a_refused_handoff_never_reaches_the_chain(adapter, orchestrator):
    """NOT_ADMITTED, not REJECTED, and no governance_decision -- the chain
    never evaluated it, and reporting a verdict no gate reached would be
    inventing one."""
    result = adapter.govern_handoff(orchestrator, _handoff([
        _artifact(origin=Origin.AI, authority=Authority.HUMAN_AUTHORIZATION)
    ]), context={"reviewer": "w.king"})

    assert result["status"] == "NOT_ADMITTED"
    assert result["status"] != "REJECTED"
    assert result["governance_decision"] is None


def test_a_valid_handoff_runs_the_whole_chain(adapter, orchestrator):
    result = adapter.govern_handoff(
        orchestrator, _handoff(), context={"reviewer": "w.king"}
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["conservation_decision"].verified is True


def test_the_handoffs_provenance_reaches_the_governed_artifact(adapter):
    artifact = GemsGovernanceAdapter.handoff_to_artifact(_handoff())
    assert artifact.metadata.epistemic_status.value == "inferred"
    assert artifact.metadata.authority_status.value == "analysis"
    assert "art-1" in artifact.metadata.parent_artifact_ids
