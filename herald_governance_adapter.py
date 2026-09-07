"""
HERALD → governance chain Adapter

Feeds a HERALD claim into the orchestrated governance chain, so an
interpretation drawn out of a source document is governed like any other
request before anything downstream acts on it.

Direction of the seam
---------------------
HERALD is the natural-language interpretation layer: it reads a source
document and produces claims, each carrying where it came from, how it was
derived, what standing it has, and how confident the extraction was. It
deliberately has no authority over what any of it means or what happens next.
The chain is what supplies that authority.

Duck-typed like every other seam here: a claim is read structurally
(`.claim_id`, `.kind`, `.value`, `.standing`, `.derivation_method`,
`.extractor`, `.source_id`, `.source_hash`, `.confidence`, `.authority`), so
observe-perceive takes no dependency on HERALD.

Built against three findings from a prior hand-run of this exact seam
--------------------------------------------------------------------
A throwaway 25-line adapter was written against the real contract in August
and run once. It produced three results that reading the code had not, and
this adapter exists to not repeat them.

**1. Attestation is not derivation.** The obvious mapping sends a
human-confirmed HERALD claim to Sentinel's "attested" status, because it
reads as the natural fit. A provenance invariant written a month earlier, for
call ingestion, refused it: a fact stamped as observed or claimed may not also
carry a derivation method, because something observed was not derived. And a
HERALD claim still carries `extractor` and `derivation_method` even after a
human confirms its value -- the human confirmed *what it says*, not that it
was never extracted. So `_epistemic_status_for` refuses to report
ATTESTED for any claim still carrying derivation evidence, and says
HUMAN_CONFIRMED_DERIVED instead. Losing that distinction is how a
machine-extracted figure acquires the standing of a first-hand record.

**2. Valid and wrong at the same time.** The original adapter stamped each
event as having occurred at the moment the document was read, rather than the
date the document stated. Every field type was right, the provenance was
consistent, the schema passed -- and the event lied about when the thing
happened. `occurred_at` here therefore comes from the claim's own asserted
value and never from ingestion time, and when the claim does not carry a date
the answer is None rather than now(). The same error class was independently
found and fixed one layer up (CCC's event-time vs ingest-time provenance);
finding it twice, in two systems, from two directions, is the argument for
the rule.

**3. A bank statement and an unverified letter extracted identically.** They
arrived downstream looking the same, because the adapter carried the claim
but not what kind of source it came from. Standing, source id and source hash
are all preserved into the governed context here so a later reader can still
tell a record from an assertion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

# HERALD's own vocabulary, mirrored rather than imported (see module docstring).
STANDING_RECORD = "record"
STANDING_ATTESTATION = "attestation"
STANDING_DERIVED = "derived"
STANDING_UNKNOWN = "unknown"

# An ISO-ish date appearing in a claim's asserted value. Deliberately narrow:
# a wrong date is worse than no date, so anything not unambiguously a date is
# left as None (see finding 2).
_ISO_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


@dataclass
class _Status:
    """Duck-typed stand-in for a Sentinel status enum member; the chain reads
    `.value` and never checks the type."""
    value: str


@dataclass
class _Metadata:
    origin_status: _Status
    authority_status: _Status
    epistemic_status: _Status
    parent_artifact_ids: List[str]


@dataclass
class ClaimArtifact:
    artifact_id: str
    content: str
    metadata: _Metadata


class HeraldGovernanceAdapter:
    """Converts HERALD claims into governed chain requests."""

    @staticmethod
    def _has_derivation_evidence(claim) -> bool:
        """Whether the claim still carries evidence that it was derived.

        This is the check finding 1 turns on. A human confirming a claim's
        value does not un-derive it -- the extractor that first read it is
        still named on the record.
        """
        return bool(
            getattr(claim, "derivation_method", None)
            or getattr(claim, "extractor", None)
        )

    @classmethod
    def epistemic_status_for(cls, claim, human_confirmed: bool = False) -> str:
        """Map a HERALD claim's standing onto an epistemic status the chain
        can carry, without overstating it.

        The refusal in here is the point: nothing that still carries a
        derivation method is reported as ATTESTED, however it was confirmed.
        """
        standing = (getattr(claim, "standing", None) or STANDING_UNKNOWN).lower()
        derived = cls._has_derivation_evidence(claim)

        if human_confirmed:
            # A person vouched for the value. If the claim was extracted, that
            # remains true and must stay visible -- this is the mapping the
            # August seam test got wrong and a month-old invariant caught.
            return "HUMAN_CONFIRMED_DERIVED" if derived else "HUMAN_CONFIRMED"

        if standing == STANDING_RECORD and not derived:
            # A first-hand record, taken as read rather than reconstructed.
            return "RECORD"
        if standing == STANDING_ATTESTATION:
            # A party asserts it. HERALD is explicit that this does not
            # establish truth, and neither does passing it along.
            return "ASSERTED"
        if standing == STANDING_DERIVED or derived:
            return "DERIVED"
        return "UNVERIFIED"

    @staticmethod
    def occurred_at(claim) -> Optional[str]:
        """The date the claim itself asserts, or None.

        Never ingestion time. Finding 2 was an adapter that stamped every
        event with the moment the document was read: schema-valid, internally
        consistent, and wrong about when the thing happened. A missing date is
        reported as missing.
        """
        for field in ("value", "raw", "reading"):
            text = getattr(claim, field, None)
            if not isinstance(text, str):
                continue
            found = _ISO_DATE.search(text)
            if found:
                return found.group(1)
        return None

    @classmethod
    def claim_to_artifact(cls, claim, human_confirmed: bool = False) -> ClaimArtifact:
        """Convert a HERALD ClaimExport into the artifact shape the chain
        consumes."""
        standing = getattr(claim, "standing", None) or STANDING_UNKNOWN
        content = (
            f"HERALD claim {claim.claim_id} ({getattr(claim, 'kind', 'unspecified')}): "
            f"{getattr(claim, 'value', '')!r} "
            f"[standing {standing}, source {getattr(claim, 'source_id', 'unknown')}]"
        )

        metadata = _Metadata(
            origin_status=_Status("HERALD"),
            # HERALD is explicit that it carries a message and has no
            # authority over what happens next. Anything stronger here would
            # be this adapter inventing authority the producer disclaims.
            authority_status=_Status(
                getattr(claim, "authority", None) or "ADVISORY"
            ),
            epistemic_status=_Status(cls.epistemic_status_for(claim, human_confirmed)),
            parent_artifact_ids=[
                pid for pid in (
                    getattr(claim, "source_id", None),
                    getattr(claim, "bundle_id", None),
                ) if pid
            ],
        )
        return ClaimArtifact(
            artifact_id=claim.claim_id,
            content=content,
            metadata=metadata,
        )

    @classmethod
    def claim_context(cls, claim, human_confirmed: bool = False,
                      extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Context for the chain's gates.

        Standing, source id and source hash all travel. Finding 3 was a bank
        statement and an unverified letter arriving downstream
        indistinguishable, because the adapter carried the claim but not what
        kind of source produced it.
        """
        context: Dict[str, Any] = {
            "subject_id": getattr(claim, "source_id", None),
            "claim_kind": getattr(claim, "kind", None),
            "standing": getattr(claim, "standing", None) or STANDING_UNKNOWN,
            "source_id": getattr(claim, "source_id", None),
            "source_hash": getattr(claim, "source_hash", None),
            "content_hash": getattr(claim, "content_hash", None),
            "extractor": getattr(claim, "extractor", None),
            "derivation_method": getattr(claim, "derivation_method", None),
            "extraction_confidence": getattr(claim, "confidence", None),
            "human_confirmed": human_confirmed,
            # The date the claim asserts, not when it was read.
            "occurred_at": cls.occurred_at(claim),
            # HERALD produced this; a person has not reviewed it unless one
            # explicitly confirmed the claim.
            "requires_human_oversight": not human_confirmed,
            "human_reviewed": human_confirmed,
            # The claim's own reasons are the justification PERCEIVE's citadel
            # gate looks for. An extraction with no stated reasoning should
            # not clear a gate that exists to require one.
            "justification": " ".join(getattr(claim, "reasons", None) or []),
        }
        if extra:
            context.update(extra)
        return context

    @classmethod
    def govern_claim(
        cls,
        orchestrator,
        claim,
        human_confirmed: bool = False,
        operation_func=None,
        operation_type: str = "approve",
        context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Run one HERALD claim through the full governance chain."""
        artifact = cls.claim_to_artifact(claim, human_confirmed)
        return orchestrator.orchestrate_request(
            artifact,
            operation_type,
            operation_func or (lambda execution_context: {
                "status": "recorded",
                "claim_id": artifact.artifact_id,
            }),
            context=cls.claim_context(claim, human_confirmed, context),
        )
