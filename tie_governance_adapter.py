"""
TIE → governance chain Adapter

Feeds a TIE TypedHandoff -- structured intelligence extracted from a source
document -- into the orchestrated chain, carrying forward what TIE knows it
does *not* know.

Why this seam is unusually easy, and unusually easy to ruin
------------------------------------------------------------
TIE's `TypedHandoff` is already designed to be handed to a downstream system.
It names an objective, the source it came from, the evidence and artifacts
supporting it, a routing signal, provenance -- and, first-class,
`known_uncertainty`.

That last field is the point of the whole seam. TIE is a source-preserving
engine: it holds the line between what was originally present, what was
extracted, what was inferred, what was reconstructed, and what remains
unknown. Everything downstream of here can only degrade that. The failure
mode is not a crash, it is a partial reading arriving as though it were a
complete one, because the uncertainty was dropped somewhere in transit.

So `known_uncertainty` travels verbatim, coverage becomes the confidence
signal, and neither is inferred, rounded away, or summarised.

Coverage is the confidence
--------------------------
TIE records, per source segment, whether it was INSPECTED, NOT_INSPECTED,
MISSING or DUPLICATE. A handoff drawn from 20% coverage is a far weaker basis
for action than one drawn from 95%, and the chain has no way to know that
unless it is told.

`coverage_ratio` is therefore reported as inspected-over-total, and
un-inspected segments are surfaced as an explicit uncertainty rather than as
silence. Silence reads as "nothing to report", which is exactly wrong here.

The origin distinction TIE already models
-----------------------------------------
`OriginKind` has a value most systems lack: HUMAN_ACCEPTED_AI, separate from
both HUMAN and AI. That is the same distinction the innovation_os seam had to
construct by hand -- a human accepting machine output does not make the
output human-authored -- and TIE models it natively. It is mapped through
intact rather than collapsed into HUMAN, because collapsing it is precisely
how machine-generated content acquires human provenance.

Duck-typed at the boundary as usual; TIE's own types are not imported.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


# TIE's OriginKind values, mirrored. HUMAN_ACCEPTED_AI is deliberately its own
# thing here as it is there.
ORIGIN_HUMAN = "HUMAN"
ORIGIN_AI = "AI"
ORIGIN_HUMAN_ACCEPTED_AI = "HUMAN_ACCEPTED_AI"
ORIGIN_MIXED = "MIXED"
ORIGIN_UNKNOWN = "UNKNOWN"


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
class HandoffArtifact:
    artifact_id: str
    content: str
    metadata: _Metadata


class TieGovernanceAdapter:
    """Converts TIE handoffs into governed chain requests."""

    @staticmethod
    def coverage_ratio(coverage) -> Optional[float]:
        """Inspected segments over total, or None when there is no coverage
        record at all.

        None and 0.0 are different answers and are kept different: 0.0 means
        TIE looked and inspected nothing, None means TIE did not report
        coverage. Collapsing them would turn "unknown" into "known to be
        zero", which is a stronger claim than the data supports.
        """
        if coverage is None:
            return None
        segments = getattr(coverage, "segments", None) or ()
        if not segments:
            return None
        inspected = sum(
            1 for s in segments
            if getattr(getattr(s, "status", None), "value", None) == "INSPECTED"
        )
        return round(inspected / len(segments), 4)

    @staticmethod
    def uncertainties(handoff, coverage=None) -> List[str]:
        """Everything TIE says it does not know, plus what the coverage record
        implies it did not look at.

        The un-inspected count is stated rather than left implicit. A reader
        who is not told that 8 of 10 segments went uninspected will read the
        handoff as covering the source.
        """
        stated = list(getattr(handoff, "known_uncertainty", None) or ())

        segments = getattr(coverage, "segments", None) or () if coverage else ()
        if segments:
            by_status: Dict[str, int] = {}
            for segment in segments:
                status = getattr(getattr(segment, "status", None), "value", "UNKNOWN")
                by_status[status] = by_status.get(status, 0) + 1
            for status, label in (
                ("NOT_INSPECTED", "not inspected"),
                ("MISSING", "missing from the source"),
            ):
                if by_status.get(status):
                    stated.append(
                        f"{by_status[status]} of {len(segments)} source segments "
                        f"{label}"
                    )
        return stated

    @classmethod
    def handoff_to_artifact(cls, handoff, coverage=None) -> HandoffArtifact:
        """Convert a TIE TypedHandoff into the artifact shape the chain
        consumes."""
        provenance = getattr(handoff, "provenance", None)
        origin = getattr(getattr(provenance, "origin", None), "value", None) or ORIGIN_UNKNOWN
        uncertainties = cls.uncertainties(handoff, coverage)

        content = (
            f"TIE handoff for objective {getattr(handoff, 'objective', 'unspecified')!r} "
            f"from source {getattr(handoff, 'source_id', 'unknown')}: "
            f"{len(getattr(handoff, 'evidence_ids', ()) or ())} evidence record(s), "
            f"{len(getattr(handoff, 'artifact_ids', ()) or ())} artifact(s). "
            f"Known uncertainty: "
            f"{'; '.join(uncertainties) if uncertainties else 'none stated'}"
        )

        metadata = _Metadata(
            origin_status=_Status("TIE"),
            # HUMAN_ACCEPTED_AI is carried as itself. Mapping it to HUMAN is
            # how machine output acquires human provenance; mapping it to AI
            # discards the fact that a person signed off. Neither is honest.
            authority_status=_Status(origin),
            # TIE's own epistemic vocabulary is preserved rather than
            # translated. A handoff whose evidence is INFERRED must not
            # arrive downstream looking EXPLICIT.
            epistemic_status=_Status(
                cls._epistemic_status_for(handoff, uncertainties)
            ),
            parent_artifact_ids=[
                pid for pid in (getattr(handoff, "source_id", None),) if pid
            ] + list(getattr(handoff, "artifact_ids", None) or ()),
        )
        return HandoffArtifact(
            artifact_id=f"tie-handoff-{getattr(handoff, 'source_id', 'unknown')}",
            content=content,
            metadata=metadata,
        )

    @staticmethod
    def _epistemic_status_for(handoff, uncertainties: List[str]) -> str:
        """A handoff carrying stated uncertainty is not EXPLICIT.

        TIE's EXPLICIT means the thing was present in the source as written.
        A handoff that names what it does not know has, by its own account,
        gone beyond what was explicitly there -- so it is reported INFERRED.
        Reporting it EXPLICIT would be the adapter overstating TIE's own
        claim, which is the one thing a source-preserving engine's consumer
        must not do.
        """
        return "INFERRED" if uncertainties else "EXPLICIT"

    @classmethod
    def handoff_context(cls, handoff, coverage=None,
                        extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Context for the chain's gates."""
        uncertainties = cls.uncertainties(handoff, coverage)
        ratio = cls.coverage_ratio(coverage)
        provenance = getattr(handoff, "provenance", None)

        context: Dict[str, Any] = {
            "subject_id": getattr(handoff, "source_id", None),
            "objective": getattr(handoff, "objective", None),
            "source_id": getattr(handoff, "source_id", None),
            "routing_signal": getattr(handoff, "routing_signal", None),
            "evidence_count": len(getattr(handoff, "evidence_ids", ()) or ()),
            "artifact_count": len(getattr(handoff, "artifact_ids", ()) or ()),
            # Verbatim. Not summarised, not counted -- the strings themselves.
            "known_uncertainty": uncertainties,
            "coverage_ratio": ratio,
            "coverage_complete": bool(getattr(coverage, "complete", False)) if coverage else False,
            "tie_origin": getattr(getattr(provenance, "origin", None), "value", None),
            "tie_lineage_id": getattr(provenance, "lineage_id", None),
            "extraction_origin": getattr(provenance, "extraction_origin", None),
            # TIE extracted this; no person reviewed it unless the origin says
            # a human accepted it.
            "human_reviewed": (
                getattr(getattr(provenance, "origin", None), "value", None)
                in (ORIGIN_HUMAN, ORIGIN_HUMAN_ACCEPTED_AI)
            ),
            # The objective is TIE's stated reason for the handoff, which is
            # the justification PERCEIVE's citadel gate looks for.
            "justification": getattr(handoff, "objective", "") or "",
        }
        context["requires_human_oversight"] = not context["human_reviewed"]
        if extra:
            context.update(extra)
        return context

    @classmethod
    def govern_handoff(
        cls,
        orchestrator,
        handoff,
        coverage=None,
        operation_func=None,
        operation_type: str = "approve",
        context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Run one TIE handoff through the governance chain."""
        artifact = cls.handoff_to_artifact(handoff, coverage)
        return orchestrator.orchestrate_request(
            artifact,
            operation_type,
            operation_func or (lambda execution_context: {
                "status": "recorded",
                "handoff": artifact.artifact_id,
            }),
            context=cls.handoff_context(handoff, coverage, context),
        )
