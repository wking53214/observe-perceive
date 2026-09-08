"""
Governance Gateway → chain admission Adapter

Puts Governance Gateway in front of the orchestrated chain as the door: an
artifact is checked for structural validity, provenance, declared authority,
epistemic state, scope and integrity *before* any governance stage spends
work on it.

Why in front rather than as another gate
----------------------------------------
PERCEIVE asks whether a request is permitted. The Gateway asks a prior and
narrower question -- is this a well-formed governed artifact at all, and has
it been altered since it was sealed. Those are different failures and
deserve different answers: a tampered artifact is not a policy refusal, it is
an artifact that should never have reached policy evaluation.

Keeping it at the door also means the expensive stages never run on input
that could not have been governed anyway.

The scope rule the chain did not have
-------------------------------------
Gateway artifacts carry `Scope.READ_ONLY` or `Scope.EXECUTE`. The chain's
orchestrator calls `gsa815_operation_func` on approval without ever asking
whether the artifact was scoped to be executed at all -- an artifact admitted
for reading could be run. `admit()` reports the scope and
`execution_permitted()` answers that question explicitly, so the caller
cannot execute a READ_ONLY artifact without stepping over a named check.

Integrity theatre, avoided
--------------------------
The temptation at a seam like this is to build the Gateway artifact here and
then validate it, which always passes: the digest is computed over fields
this adapter just supplied. That proves nothing. So the two operations are
deliberately separate -- `seal()` for a producer that has no artifact yet,
`admit()` for one that arrives already sealed, and only the second is
tamper-evident. A test covers the case that matters: a field mutated after
sealing is refused.

Duck-typed at the outer edge like the other seams here, but the Gateway's own
types are imported when present -- it is a validator, and validating a
structural stand-in would be validating nothing.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import Any, Dict, Optional


def _import_gateway():
    """Resolve Governance_Gateway: sibling checkout first, installed second."""
    gw_src = os.path.join(os.path.dirname(__file__), "..", "Governance_Gateway", "src")
    if os.path.isdir(gw_src) and gw_src not in sys.path:
        sys.path.insert(0, gw_src)
    try:
        from governance_gateway.gateway import GovernanceGateway
        from governance_gateway.models import (
            Artifact, Authority, EpistemicStatus, GateReason, Scope,
        )
    except ModuleNotFoundError:
        return (None,) * 6
    return GovernanceGateway, Artifact, Authority, EpistemicStatus, GateReason, Scope


(GovernanceGateway, GatewayArtifact, Authority, GatewayEpistemicStatus,
 GateReason, Scope) = _import_gateway()


@dataclass(frozen=True)
class AdmissionResult:
    """The door's answer, in the chain's own terms."""
    admitted: bool
    artifact: Any = None
    reason: Optional[str] = None
    scope: Optional[str] = None

    @property
    def execution_permitted(self) -> bool:
        """Whether this artifact may be executed, not merely read.

        False for a refused artifact and for one admitted READ_ONLY. The
        orchestrator executes on approval without asking this; a caller that
        wants the guarantee has to consult it.
        """
        return self.admitted and self.scope == "EXECUTE"


class GatewayAdmissionAdapter:
    """Governance Gateway as the chain's front door."""

    def __init__(self, gateway=None):
        if GovernanceGateway is None:
            raise ImportError(
                "Governance_Gateway not found. Clone it beside this repo "
                "(../Governance_Gateway) or install it."
            )
        self.gateway = gateway or GovernanceGateway()

    # ------------------------------------------------------------------
    # Admission
    # ------------------------------------------------------------------

    def admit(self, artifact) -> AdmissionResult:
        """Evaluate an already-sealed Gateway artifact.

        This is the tamper-evident path: the digest was computed by whoever
        sealed the artifact, so a field altered since then fails here.
        """
        result = self.gateway.evaluate(artifact)
        if not result.accepted:
            reason = getattr(result.reason, "value", None) or str(result.reason)
            return AdmissionResult(admitted=False, reason=reason)
        scope = getattr(getattr(result.artifact, "scope", None), "value", None)
        return AdmissionResult(admitted=True, artifact=result.artifact, scope=scope)

    @staticmethod
    def seal(
        artifact_id: str,
        payload: Any,
        provenance: Dict[str, Any],
        epistemic_status,
        authority_actor: str,
        authority_grant: str,
        execute: bool = False,
    ):
        """Create a sealed Gateway artifact for a producer that has none.

        Separate from `admit` on purpose: sealing and then immediately
        admitting proves only that this adapter can compute a digest. Real
        tamper evidence requires the seal and the check to be different
        events, usually in different systems.

        `execute` defaults to False. A producer that wants an artifact
        runnable has to say so, rather than acquiring execution scope by
        omission.
        """
        return GatewayArtifact.create(
            artifact_id=artifact_id,
            payload=payload,
            provenance=provenance,
            epistemic_status=epistemic_status,
            authority=Authority(actor=authority_actor, grant=authority_grant),
            scope=Scope.EXECUTE if execute else Scope.READ_ONLY,
        )

    # ------------------------------------------------------------------
    # Running the chain behind the door
    # ------------------------------------------------------------------

    def govern_admitted(
        self,
        orchestrator,
        artifact,
        chain_artifact,
        operation_type: str = "approve",
        operation_func=None,
        context: Optional[Dict[str, Any]] = None,
        require_execute_scope: bool = True,
        vitals_snapshot=None,
    ) -> dict:
        """Admit an artifact at the Gateway, then run the chain on it.

        A refusal at the door returns without touching the chain, and says so
        in terms that cannot be mistaken for a policy decision: the status is
        NOT_ADMITTED rather than REJECTED, because nothing was evaluated.

        `require_execute_scope` defaults True. A READ_ONLY artifact is refused
        before execution rather than quietly executed, which is the gap this
        seam closes.
        """
        admission = self.admit(artifact)
        if not admission.admitted:
            return {
                "status": "NOT_ADMITTED",
                "reason": f"Governance Gateway refused admission: {admission.reason}",
                "admission": admission,
                "governance_decision": None,
                "audit_chain": None,
            }

        if require_execute_scope and not admission.execution_permitted:
            return {
                "status": "NOT_ADMITTED",
                "reason": (
                    f"artifact admitted with scope {admission.scope}; "
                    "execution requires EXECUTE scope"
                ),
                "admission": admission,
                "governance_decision": None,
                "audit_chain": None,
            }

        merged = dict(context or {})
        merged.setdefault("gateway_scope", admission.scope)
        merged.setdefault("gateway_admitted", True)

        # `vitals_snapshot` is threaded through so the admitted path can reach
        # OBSERVE. It could not before: every production caller entered the
        # chain here and none could supply vitals, so OBSERVE never ran on an
        # admitted artifact and the record said so with observe_enforced=False.
        result = orchestrator.orchestrate_request(
            chain_artifact,
            operation_type,
            operation_func or (lambda execution_context: {
                "status": "recorded",
                "artifact_id": getattr(chain_artifact, "artifact_id", None),
            }),
            vitals_snapshot=vitals_snapshot,
            context=merged,
        )
        result["admission"] = admission
        return result
