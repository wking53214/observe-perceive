"""TIE handoffs entering the governance chain.

TIE is a source-preserving engine: it holds the line between what was
present, extracted, inferred, reconstructed, and still unknown. Everything
downstream can only degrade that, and the failure mode is not a crash -- it
is a partial reading arriving as though it were complete, because the
uncertainty was dropped in transit.

So these tests are mostly about what survives the seam.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Tuple

import pytest

from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest
from tie_governance_adapter import TieGovernanceAdapter, NO_COVERAGE_STATEMENT


# --- structural stand-ins for TIE's models (not imported: duck-typed seam) --

@dataclass
class _Enum:
    value: str


@dataclass
class _Provenance:
    origin: _Enum = field(default_factory=lambda: _Enum("AI"))
    lineage_id: Optional[str] = "lin-1"
    extraction_origin: Optional[str] = "tie.evidence.extract"


@dataclass
class _Segment:
    segment_id: str
    status: _Enum


@dataclass
class _Coverage:
    source_id: str
    segments: Tuple[_Segment, ...] = ()

    @property
    def complete(self) -> bool:
        return bool(self.segments) and all(
            s.status.value == "INSPECTED" for s in self.segments
        )


@dataclass
class _TypedHandoff:
    objective: str
    source_id: str
    evidence_ids: Tuple[str, ...]
    artifact_ids: Tuple[str, ...]
    known_uncertainty: Tuple[str, ...]
    routing_signal: Optional[str] = None
    provenance: _Provenance = field(default_factory=_Provenance)


def _handoff(**overrides):
    base = dict(
        objective="Establish the payment schedule stated in the servicing file.",
        source_id="doc-77",
        evidence_ids=("ev-1", "ev-2"),
        artifact_ids=("art-1",),
        known_uncertainty=("Payment 3 has no stated date in the source.",),
        routing_signal="servicing.schedule",
    )
    base.update(overrides)
    return _TypedHandoff(**base)


def _coverage(inspected=8, not_inspected=2, missing=0):
    segments = (
        tuple(_Segment(f"s{i}", _Enum("INSPECTED")) for i in range(inspected))
        + tuple(_Segment(f"n{i}", _Enum("NOT_INSPECTED")) for i in range(not_inspected))
        + tuple(_Segment(f"m{i}", _Enum("MISSING")) for i in range(missing))
    )
    return _Coverage(source_id="doc-77", segments=segments)


@pytest.fixture
def orchestrator():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="tie-seam-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return GovernanceOrchestrator(kernel, ConservationKernel(), ObserveClinicalEngine())


# ---------------------------------------------------------------------------
# Uncertainty must survive
# ---------------------------------------------------------------------------

def test_stated_uncertainty_travels_verbatim():
    """Not summarised, not counted -- the strings themselves. A reader who
    gets "1 uncertainty" instead of what it was cannot act on it."""
    context = TieGovernanceAdapter.handoff_context(_handoff())
    assert "Payment 3 has no stated date in the source." in context["known_uncertainty"]


def test_uninspected_segments_become_a_stated_uncertainty():
    """Silence reads as "nothing to report", which is exactly wrong. A reader
    not told that 2 of 10 segments went uninspected will read the handoff as
    covering the source."""
    context = TieGovernanceAdapter.handoff_context(_handoff(), _coverage(8, 2))
    assert any("2 of 10 source segments not inspected" in u
               for u in context["known_uncertainty"])


def test_a_coverage_gap_tie_already_stated_is_not_said_twice():
    """TIE's own build_package appends the same gap statements to
    known_uncertainty. A handoff that arrives with one must not leave this
    adapter with two."""
    handoff = _handoff(known_uncertainty=(
        "Payment 3 has no stated date in the source.",
        "2 of 10 source segments not inspected",
    ))
    context = TieGovernanceAdapter.handoff_context(handoff, _coverage(8, 2))
    assert context["known_uncertainty"].count("2 of 10 source segments not inspected") == 1
    assert "Payment 3 has no stated date in the source." in context["known_uncertainty"]


def test_missing_segments_are_reported_separately_from_uninspected():
    """Not looked at and not there are different facts about the source."""
    context = TieGovernanceAdapter.handoff_context(_handoff(), _coverage(6, 2, 2))
    joined = " ".join(context["known_uncertainty"])
    assert "not inspected" in joined
    assert "missing from the source" in joined


def test_a_handoff_with_stated_uncertainty_is_not_reported_explicit():
    """TIE's EXPLICIT means the thing was present in the source as written. A
    handoff naming what it does not know has, by its own account, gone beyond
    that. Reporting it EXPLICIT would be this adapter overstating TIE's own
    claim -- the one thing a source-preserving engine's consumer must not
    do."""
    artifact = TieGovernanceAdapter.handoff_to_artifact(_handoff())
    assert artifact.metadata.epistemic_status.value == "INFERRED"


def test_a_handoff_with_no_uncertainty_and_full_coverage_may_be_explicit():
    artifact = TieGovernanceAdapter.handoff_to_artifact(
        _handoff(known_uncertainty=()), _coverage(10, 0)
    )
    assert artifact.metadata.epistemic_status.value == "EXPLICIT"


def test_the_uncertainty_is_visible_in_the_governed_content():
    """A downstream reader of the chain's record should see it without going
    back to TIE."""
    artifact = TieGovernanceAdapter.handoff_to_artifact(_handoff())
    assert "Payment 3 has no stated date" in artifact.content


# ---------------------------------------------------------------------------
# Silence about coverage must not read as full coverage
# ---------------------------------------------------------------------------

def test_a_handoff_with_no_coverage_record_is_not_reported_explicit():
    """Measured before the fix: no uncertainty of its own plus no coverage
    record came out EXPLICIT, which says the source was read in full when the
    adapter was never told how much was read."""
    artifact = TieGovernanceAdapter.handoff_to_artifact(_handoff(known_uncertainty=()))
    assert artifact.metadata.epistemic_status.value == "INFERRED"
    assert "no coverage record" in artifact.content


def test_an_empty_coverage_record_is_treated_like_none():
    empty = _Coverage(source_id="doc-77", segments=())
    context = TieGovernanceAdapter.handoff_context(_handoff(known_uncertainty=()), empty)
    assert any("no coverage record" in u for u in context["known_uncertainty"])
    assert context["coverage_ratio"] is None


def test_the_no_coverage_statement_is_made_once():
    handoff = _handoff(known_uncertainty=(NO_COVERAGE_STATEMENT,))
    context = TieGovernanceAdapter.handoff_context(handoff)
    assert context["known_uncertainty"].count(NO_COVERAGE_STATEMENT) == 1


def test_duplicate_segments_are_explained_not_silently_dropped_from_the_ratio():
    """Measured before the fix: 3 DUPLICATE + 1 INSPECTED gave ratio 0.25, no
    uncertainty line, and EXPLICIT. The ratio showed a gap nothing accounted
    for."""
    segments = (_Segment("s0", _Enum("INSPECTED")),) + tuple(
        _Segment(f"d{i}", _Enum("DUPLICATE")) for i in range(3)
    )
    coverage = _Coverage("doc-77", segments)
    handoff = _handoff(known_uncertainty=())
    context = TieGovernanceAdapter.handoff_context(handoff, coverage)
    assert context["coverage_ratio"] == 0.25
    assert "3 of 4 source segments duplicates of other segments" in context["known_uncertainty"]
    assert TieGovernanceAdapter.handoff_to_artifact(handoff, coverage).metadata.epistemic_status.value == "INFERRED"


def test_an_unrecognised_segment_status_is_still_stated():
    segments = (_Segment("s0", _Enum("INSPECTED")), _Segment("x0", _Enum("QUARANTINED")))
    context = TieGovernanceAdapter.handoff_context(
        _handoff(known_uncertainty=()), _Coverage("doc-77", segments)
    )
    assert "1 of 2 source segments with status quarantined" in context["known_uncertainty"]


# ---------------------------------------------------------------------------
# Coverage is the confidence
# ---------------------------------------------------------------------------

def test_coverage_ratio_is_inspected_over_total():
    assert TieGovernanceAdapter.coverage_ratio(_coverage(8, 2)) == 0.8
    assert TieGovernanceAdapter.coverage_ratio(_coverage(10, 0)) == 1.0


def test_no_coverage_record_is_none_not_zero():
    """None and 0.0 are different answers. 0.0 means TIE looked and inspected
    nothing; None means TIE did not report coverage. Collapsing them turns
    "unknown" into "known to be zero", a stronger claim than the data
    supports."""
    assert TieGovernanceAdapter.coverage_ratio(None) is None
    assert TieGovernanceAdapter.coverage_ratio(_Coverage("doc-77", ())) is None
    assert TieGovernanceAdapter.coverage_ratio(_coverage(0, 5)) == 0.0


def test_coverage_completeness_is_carried_not_inferred_from_the_ratio():
    full = TieGovernanceAdapter.handoff_context(_handoff(), _coverage(10, 0))
    partial = TieGovernanceAdapter.handoff_context(_handoff(), _coverage(8, 2))
    assert full["coverage_complete"] is True
    assert partial["coverage_complete"] is False


# ---------------------------------------------------------------------------
# The origin distinction TIE already models
# ---------------------------------------------------------------------------

def test_human_accepted_ai_is_carried_as_itself():
    """TIE models HUMAN_ACCEPTED_AI separately from HUMAN and AI, which is the
    same distinction the approval seam had to construct by hand. Mapping
    it to HUMAN is how machine output acquires human provenance; mapping it to
    AI discards that a person signed off. Neither is honest."""
    handoff = _handoff(provenance=_Provenance(origin=_Enum("HUMAN_ACCEPTED_AI")))
    artifact = TieGovernanceAdapter.handoff_to_artifact(handoff)

    assert artifact.metadata.authority_status.value == "HUMAN_ACCEPTED_AI"
    assert artifact.metadata.authority_status.value != "HUMAN"
    assert artifact.metadata.authority_status.value != "AI"


@pytest.mark.parametrize("origin,reviewed", [
    ("HUMAN", True),
    ("HUMAN_ACCEPTED_AI", True),
    ("AI", False),
    ("MIXED", False),
    ("UNKNOWN", False),
])
def test_human_review_is_claimed_only_where_a_human_was_involved(origin, reviewed):
    handoff = _handoff(provenance=_Provenance(origin=_Enum(origin)))
    context = TieGovernanceAdapter.handoff_context(handoff)
    assert context["human_reviewed"] is reviewed
    assert context["requires_human_oversight"] is (not reviewed)


def test_lineage_and_extraction_origin_travel_for_tie_back():
    context = TieGovernanceAdapter.handoff_context(_handoff())
    assert context["tie_lineage_id"] == "lin-1"
    assert context["extraction_origin"] == "tie.evidence.extract"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_a_handoff_runs_the_whole_chain(orchestrator):
    result = TieGovernanceAdapter.govern_handoff(
        orchestrator, _handoff(), _coverage(8, 2), context={"reviewer": "w.king"},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["conservation_decision"].verified is True


def test_a_handoff_with_no_objective_is_refused(orchestrator):
    """TIE's objective is its stated reason for the handoff, passed as
    PERCEIVE's justification. A handoff with no stated purpose should not
    clear a gate that exists to require one."""
    result = TieGovernanceAdapter.govern_handoff(
        orchestrator, _handoff(objective=""), _coverage(8, 2),
        context={"reviewer": "w.king"},
    )
    assert result["status"] == "REJECTED"
    assert any("justification" in v.lower()
               for v in result["governance_decision"].violations)
