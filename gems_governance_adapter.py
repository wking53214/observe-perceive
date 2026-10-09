"""
GEMS → governance chain Adapter

Feeds a GEMS Handoff -- artifacts moving between role-oriented gems under a
workflow -- into the orchestrated chain.

The rule GEMS already enforces, and this adapter must not undo
------------------------------------------------------------------
GEMS carries a `HumanAuthorityGuard` with a constitutional rule:

    AI/joint/uncertain material cannot silently become human authorization

It refuses an artifact claiming `Authority.HUMAN_AUTHORIZATION` whose origin
is not `Origin.HUMAN`, and it provides `authorize()` as the *explicit*
crossing -- which records the new origin, the human source id, the parent it
came from, and a note naming the boundary that was crossed.

That rule is enforced here rather than assumed, because this seam is exactly
where it could be lost. An adapter that reads `authority` and passes it along
without checking `origin` would let an artifact arrive downstream carrying
human authority it never legitimately acquired. The guard is run on every
artifact in the handoff; a violation refuses the whole handoff rather than
quietly dropping the offending artifact, because a handoff missing one
artifact is a different handoff and nobody downstream would know.

This is the fourth independent statement of the same principle in this
ecosystem -- the approval seam constructs it by hand, HERALD's August seam
test discovered it against a month-old provenance invariant, TIE models it
natively as `OriginKind.HUMAN_ACCEPTED_AI`, and GEMS makes it constitutional.
Four codebases, arrived at separately.

Duck-typed at the boundary; GEMS's own types are imported only to run its
guard, because running a stand-in's guard would be running nothing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


def _import_gems():
    """Resolve GEMS: sibling checkout first, installed second."""
    gems_src = os.path.join(os.path.dirname(__file__), "..", "GEMS", "src")
    if os.path.isdir(gems_src) and gems_src not in sys.path:
        sys.path.insert(0, gems_src)
    try:
        from gems import Authority, HandoffValidator, HumanAuthorityGuard, Origin
        from gems.governance.constitution import ConstitutionalViolation
    except (ModuleNotFoundError, ImportError):
        return (None,) * 5
    return Authority, HandoffValidator, HumanAuthorityGuard, Origin, ConstitutionalViolation


(Authority, HandoffValidator, HumanAuthorityGuard, Origin,
 ConstitutionalViolation) = _import_gems()


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


@dataclass(frozen=True)
class HandoffRefusal:
    """A handoff GEMS' own rules refuse. Not a policy decision -- the chain
    never saw it."""
    reason: str
    artifact_id: Optional[str] = None


class GemsGovernanceAdapter:
    """Converts GEMS handoffs into governed chain requests."""

    def __init__(self):
        if HumanAuthorityGuard is None:
            raise ImportError(
                "GEMS not found. Clone it beside this repo (../GEMS)."
            )
        self.guard = HumanAuthorityGuard()
        self.handoff_validator = HandoffValidator()

    # ------------------------------------------------------------------
    # GEMS' own rules, run rather than assumed
    # ------------------------------------------------------------------

    def check(self, handoff) -> Optional[HandoffRefusal]:
        """Run GEMS' own validators over the handoff before converting it.

        Returns a refusal, or None if the handoff is admissible. Structural
        validity first (sender, recipient, every artifact has provenance),
        then the constitutional authority rule on each artifact.
        """
        try:
            self.handoff_validator.validate(handoff)
        except ValueError as e:
            return HandoffRefusal(reason=f"GEMS handoff validation failed: {e}")

        for artifact in getattr(handoff, "artifacts", ()) or ():
            try:
                self.guard.assert_not_human_authorization(artifact)
            except ConstitutionalViolation as e:
                # The whole handoff is refused, not just this artifact. A
                # handoff missing one artifact is a different handoff, and
                # nobody downstream would know which one went missing.
                return HandoffRefusal(
                    reason=f"GEMS constitutional violation: {e}",
                    artifact_id=getattr(artifact, "artifact_id", None),
                )
        return None

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    @staticmethod
    def _dominant_provenance(handoff) -> tuple:
        """(origin, epistemic_status, authority) across the handoff's artifacts.

        Where artifacts disagree the weakest claim wins, on each axis
        independently. A handoff containing one AI-origin artifact is not a
        human-origin handoff, and one containing an `unknown` epistemic status
        is not an explicit one -- taking the strongest, or the first, would
        let a single confident artifact speak for the rest.
        """
        origins, statuses, authorities = set(), set(), set()
        for artifact in getattr(handoff, "artifacts", ()) or ():
            provenance = getattr(artifact, "provenance", None)
            if provenance is None:
                continue
            origins.add(getattr(getattr(provenance, "origin", None), "value", None))
            statuses.add(getattr(getattr(provenance, "epistemic_status", None), "value", None))
            authorities.add(getattr(getattr(provenance, "authority", None), "value", None))

        def weakest(values, order, default):
            present = [v for v in order if v in values]
            return present[0] if present else default

        # Weakest-first orderings. "uncertain"/"unknown" beat everything.
        origin = weakest(origins, ["uncertain", "ai", "joint", "human"], "uncertain")
        status = weakest(statuses, ["conflicted", "unknown", "inferred", "explicit"], "unknown")
        authority = weakest(
            authorities, ["observation", "analysis", "proposal", "human_authorization"],
            "observation",
        )
        return origin, status, authority

    @classmethod
    def handoff_to_artifact(cls, handoff) -> HandoffArtifact:
        origin, status, authority = cls._dominant_provenance(handoff)
        artifacts = getattr(handoff, "artifacts", ()) or ()

        content = (
            f"GEMS handoff {getattr(handoff, 'handoff_id', 'unknown')} for task "
            f"{getattr(handoff, 'task_id', 'unknown')}: "
            f"{getattr(handoff, 'sender', '?')} -> {getattr(handoff, 'recipient', '?')}, "
            f"{len(artifacts)} artifact(s), routing "
            f"{getattr(handoff, 'routing_signal', 'none')}"
        )

        metadata = _Metadata(
            origin_status=_Status("GEMS"),
            authority_status=_Status(authority),
            epistemic_status=_Status(status),
            parent_artifact_ids=[
                aid for aid in (
                    getattr(a, "artifact_id", None) for a in artifacts
                ) if aid
            ],
        )
        return HandoffArtifact(
            artifact_id=f"gems-{getattr(handoff, 'handoff_id', 'unknown')}",
            content=content,
            metadata=metadata,
        )

    @classmethod
    def handoff_context(cls, handoff, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        origin, status, authority = cls._dominant_provenance(handoff)
        workflow = getattr(handoff, "workflow_state", None)

        context: Dict[str, Any] = {
            "subject_id": getattr(handoff, "task_id", None),
            "gems_handoff_id": getattr(handoff, "handoff_id", None),
            "sender": getattr(handoff, "sender", None),
            "recipient": getattr(handoff, "recipient", None),
            "routing_signal": getattr(handoff, "routing_signal", None),
            "workflow_state": getattr(workflow, "value", None) or (
                str(workflow) if workflow is not None else None
            ),
            "artifact_count": len(getattr(handoff, "artifacts", ()) or ()),
            "gems_origin": origin,
            "gems_epistemic_status": status,
            "gems_authority": authority,
            # Only a human-origin artifact bearing human authorization counts
            # as reviewed -- which is GEMS' own rule, restated so the chain's
            # gates see it too.
            "human_reviewed": origin == "human" and authority == "human_authorization",
            # The routing signal is GEMS' stated reason for the handoff.
            "justification": (
                f"GEMS workflow handoff to {getattr(handoff, 'recipient', 'unspecified')} "
                f"for routing {getattr(handoff, 'routing_signal', 'unspecified')}"
            ),
        }
        context["requires_human_oversight"] = not context["human_reviewed"]
        if extra:
            context.update(extra)
        return context

    def govern_handoff(
        self,
        orchestrator,
        handoff,
        operation_func=None,
        operation_type: str = "approve",
        context: Optional[Dict[str, Any]] = None,
    ) -> dict:
        """Check the handoff against GEMS' own rules, then run the chain.

        A GEMS refusal returns NOT_ADMITTED, not REJECTED, and carries no
        governance_decision -- the chain never evaluated it, and reporting a
        verdict no gate reached would be inventing one.
        """
        refusal = self.check(handoff)
        if refusal is not None:
            return {
                "status": "NOT_ADMITTED",
                "reason": refusal.reason,
                "refused_artifact_id": refusal.artifact_id,
                "governance_decision": None,
                "audit_chain": None,
            }

        artifact = self.handoff_to_artifact(handoff)
        return orchestrator.orchestrate_request(
            artifact,
            operation_type,
            operation_func or (lambda execution_context: {
                "status": "recorded",
                "handoff": artifact.artifact_id,
            }),
            context=self.handoff_context(handoff, context),
        )
