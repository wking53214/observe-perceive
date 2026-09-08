"""
PERCEIVE → Conservation Kernel Adapter

Submits PERCEIVE decisions to Conservation Kernel for verification.
Ensures all governance decisions pass through conservation boundary.
"""

from governance_contracts import GovernanceDecision, ConservationDecision
from datetime import datetime, timezone
import hashlib

# Optional conservation_kernel dependency, resolved once at import time.
#
# This resolver used to be defined and never called, so `Actor`, `ActorKind`
# and `Artifact` were never bound to anything. Every call to
# verify_perceive_decision raised `NameError: name 'Actor' is not defined`,
# the orchestrator caught it as a generic Exception, and the run came back
# "REJECTED -- Conservation Kernel rejected decision". The Conservation
# Kernel stage had never actually executed; it only ever looked like a
# principled refusal. Binding the names at module scope is what makes the
# stage real, and the tests below assert it now runs rather than raises.
def _import_conservation():
    try:
        from conservation_kernel import (
            ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
            DeclaredChange, Dimension, TransitionKind,
        )
        from conservation_kernel.errors import InvalidArtifact, LedgerError
        return (ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
                DeclaredChange, Dimension, TransitionKind, LedgerError, InvalidArtifact)
    except ModuleNotFoundError:
        # Stand-ins that no exception can ever be an instance of, so the
        # `except` clauses below are inert rather than a NameError when
        # the kernel is absent.
        class _NoLedgerError(Exception):
            pass

        class _NoInvalidArtifact(Exception):
            pass
        return (None,) * 8 + (_NoLedgerError, _NoInvalidArtifact)


(ConservationKernel, Artifact, TransformationRecord, Actor, ActorKind,
 DeclaredChange, Dimension, TransitionKind, LedgerError, InvalidArtifact) = _import_conservation()


class ConservationRefusal(Exception):
    """The Conservation Kernel evaluated the transformation and refused it.

    A subclass of Exception so every existing `except Exception` still
    catches it. It exists so the orchestrator can distinguish a refusal the
    kernel actually made from an AttributeError raised because there was no
    kernel -- two outcomes that used to produce the same REJECTED record.
    """


class PerceiveConservationAdapter:
    """Submits PERCEIVE verdicts to Conservation Kernel."""

    def __init__(self, kernel=None):  # ConservationKernel (optional for testing)
        """Initialize with Conservation Kernel instance."""
        self.kernel = kernel

    # --- the spine's vocabulary, mapped onto the kernel's ------------------
    #
    # Measured 2026-09-08: both artifacts handed to the kernel carried
    # `propositions=()`, so every proposition-level dimension of the
    # constitution (origin, authority, epistemic status, evidence,
    # canonicality, lineage) was a loop over nothing. The kernel reported
    # eleven dimensions checked; on this path it had adjudicated content
    # digests and a declared derivation. The request's epistemic claims never
    # reached it.
    #
    # The mappings below are deliberately conservative. An unknown epistemic
    # word becomes UNKNOWN, an unknown origin becomes MACHINE_ORIGINATED, and
    # a request's `authority` string -- a role label such as CLINICIAN, not
    # an authorization event -- never becomes kernel authority by itself. The
    # raw words are kept in the proposition's metadata as `declared_*`, so a
    # reader sees both what was claimed and what the kernel was told. Only an
    # explicit `authorization_refs` list in the request context makes a
    # proposition HUMAN_AUTHORIZED, and then the kernel's root admission
    # demands that those references exist and are about that proposition.
    EPISTEMIC = {
        "FACT": "FACT", "OBSERVATION": "OBSERVATION", "EXPLICIT": "OBSERVATION",
        "INFERRED": "INFERENCE", "INFERENCE": "INFERENCE", "ESTIMATED": "ESTIMATED",
        "ASSUMPTION": "ASSUMPTION", "RECOMMENDATION": "RECOMMENDATION", "DECISION": "DECISION",
        "UNKNOWN": "UNKNOWN", "CONFLICTED": "CONFLICTED", "SIMULATED": "SIMULATED",
    }
    HUMAN_ORIGINS = {"HUMAN", "CLINICIAN", "PHYSICIAN", "NURSE", "OPERATOR", "USER", "REVIEWER", "HUMAN_ORIGINATED"}
    EXTERNAL_ORIGINS = {"EXTERNAL", "EXTERNAL_ORIGINATED", "THIRD_PARTY"}

    @classmethod
    def input_proposition(cls, artifact_id: str, content: str, request=None):
        """The kernel's view of the artifact PERCEIVE ruled on."""
        from conservation_kernel import Proposition
        declared_epistemic = str(getattr(request, "epistemic_status", "UNKNOWN") or "UNKNOWN").upper()
        declared_origin = str(getattr(request, "origin", "UNKNOWN") or "UNKNOWN").upper()
        declared_authority = str(getattr(request, "authority", "NONE") or "NONE").upper()
        context = dict(getattr(request, "context", None) or {})
        authorization_refs = tuple(str(ref) for ref in (context.get("authorization_refs") or ()))

        epistemic = cls.EPISTEMIC.get(declared_epistemic, "UNKNOWN")
        if declared_origin in cls.HUMAN_ORIGINS:
            origin = "HUMAN_ORIGINATED"
        elif declared_origin in cls.EXTERNAL_ORIGINS:
            origin = "EXTERNAL_ORIGINATED"
        else:
            origin = "MACHINE_ORIGINATED"
        authority = "HUMAN_AUTHORIZED" if authorization_refs else "NONE"
        return Proposition(
            proposition_id=f"p-{artifact_id}",
            text=content,
            epistemic_status=epistemic,
            origin=origin,
            authority=authority,
            authorization_refs=authorization_refs,
            source_refs=(artifact_id,),
            derivation_method="source-declared" if epistemic in ("ESTIMATED", "SIMULATED") else None,
            metadata={
                "declared_epistemic_status": declared_epistemic,
                "declared_origin": declared_origin,
                "declared_authority": declared_authority,
                "mapped_by": "observe-perceive.PerceiveConservationAdapter",
            },
        )

    @staticmethod
    def decision_proposition(governance_decision: GovernanceDecision, input_prop, input_artifact_id: str):
        """PERCEIVE's decision as a proposition the kernel can constrain.

        Born DECISION, machine-originated, with NO authority: PERCEIVE
        recommends and permits; it does not authorize execution. Any later
        transformation that tries to raise this proposition's authority must
        carry an authorization the kernel can check.
        """
        from conservation_kernel import Proposition
        return Proposition(
            proposition_id=f"p-decision-{governance_decision.decision_id}",
            text=(
                f"PERCEIVE {governance_decision.approval.value} for request "
                f"{governance_decision.request_id} across gates "
                f"{', '.join(governance_decision.applied_gates) or 'none'}"
            ),
            epistemic_status="DECISION",
            origin="MACHINE_ORIGINATED",
            authority="NONE",
            parent_proposition_ids=(input_prop.proposition_id,),
            source_refs=(input_artifact_id,),
            derivation_method="perceive-policy-gates",
            metadata={
                "approval": governance_decision.approval.value,
                "violations": list(governance_decision.violations or []),
                "advisory_violations": list(getattr(governance_decision, "advisory_violations", None) or []),
                "policy_version": governance_decision.policy_version,
                "unanimous_consensus": bool(governance_decision.unanimous_consensus),
            },
        )

    def verify_perceive_decision(
        self,
        governance_decision: GovernanceDecision,
        input_artifact_id: str,
        input_artifact_content: str,
        input_artifact_hash: str,
        request=None,
    ) -> ConservationDecision:
        """
        Verify PERCEIVE decision through Conservation Kernel.

        Args:
            governance_decision: The PERCEIVE decision
            input_artifact_id: Original artifact ID
            input_artifact_content: Original artifact content
            input_artifact_hash: Original artifact hash

        Returns:
            ConservationDecision with conservation receipt

        Raises:
            Exception if Kernel rejects the decision
        """
        # The request carries a hash of the content it was built from. The
        # kernel re-derives digests from the content it is handed, but nothing
        # compared the two, so content altered after hashing sailed through
        # with the stale hash still attached to every downstream record. This
        # is the constitutional boundary; the comparison belongs here.
        expected_hash = hashlib.sha256(input_artifact_content.encode()).hexdigest()
        if input_artifact_hash != expected_hash:
            raise ConservationRefusal(
                "artifact hash mismatch: the request carries "
                f"{input_artifact_hash[:12]}... but the content digests to "
                f"{expected_hash[:12]}...; the content changed after it was hashed"
            )

        # Create Conservation Kernel Artifact for the decision
        decision_content = f"""
PERCEIVE Governance Decision:
- Request ID: {governance_decision.request_id}
- Decision ID: {governance_decision.decision_id}
- Approval: {governance_decision.approval.value}
- Applied Gates: {', '.join(governance_decision.applied_gates)}
- Unanimous Consensus: {governance_decision.unanimous_consensus}
- Violations: {', '.join(governance_decision.violations) if governance_decision.violations else 'None'}
- Policy Version: {governance_decision.policy_version}
- Timestamp: {governance_decision.timestamp.isoformat()}
"""

        # Create producer actor for PERCEIVE
        perceive_actor = Actor(
            actor_id="PERCEIVE",
            kind=ActorKind.SYSTEM
        )

        # The input artifact, as the Conservation Kernel understands it. This
        # is the thing Sentinel produced and PERCEIVE ruled on.
        input_prop = self.input_proposition(input_artifact_id, input_artifact_content, request)
        input_artifact = Artifact(
            artifact_id=input_artifact_id,
            content=input_artifact_content,
            propositions=(input_prop,),
            producer=Actor(actor_id="Sentinel", kind=ActorKind.SYSTEM),
        )

        # The decision artifact is a *derivation* of the input, not a free
        # standing object: parent_artifact_ids and the version bump are what
        # let the kernel reconstruct the lineage later.
        decision_prop = self.decision_proposition(governance_decision, input_prop, input_artifact_id)
        decision_artifact = Artifact(
            artifact_id=f"decision-{governance_decision.decision_id}",
            content=decision_content,
            # The input proposition travels unchanged -- any change to its
            # origin, authority or epistemic status here would be an
            # undeclared transition the kernel refuses -- and the decision is
            # a new proposition derived from it.
            propositions=(input_prop, decision_prop),
            producer=perceive_actor,
            parent_artifact_ids=(input_artifact_id,),
            version=input_artifact.version + 1,
        )

        # The record the kernel actually verifies against.
        #
        # `declared_changes` is a tuple of DeclaredChange, not a prose string.
        # An earlier version passed a sentence here and omitted `transformer`,
        # `transformation_type`, `input_hashes` and `output_hash` entirely, so
        # construction raised TypeError before the kernel was ever reached --
        # the orchestrator caught it and reported "Conservation Kernel
        # rejected", which read as a governance refusal but was a broken call.
        #
        # The CONTENT change has to be declared explicitly. Writing a decision
        # about an artifact produces different content from the artifact, and
        # the kernel refuses an output that moved a protected dimension the
        # record did not account for (UNDECLARED_CHANGE). Declaring it is not
        # a formality that satisfies the checker -- it is the claim the kernel
        # then independently re-derives from the digests and can refuse.
        #
        # TransitionKind.DERIVATION is the honest kind here: the decision is
        # derived from the artifact. It is not an adoption, a promotion, or an
        # escalation of authority, and claiming one of those would assert
        # something about human involvement that did not happen.
        content_change = DeclaredChange(
            subject_id=decision_artifact.artifact_id,
            dimension=Dimension.CONTENT,
            from_value=input_artifact.content_digest,
            to_value=decision_artifact.content_digest,
            reason=(
                f"PERCEIVE governance evaluation over "
                f"{len(governance_decision.applied_gates)} gate(s); "
                f"decision recorded as a derived artifact"
            ),
            transition_kind=TransitionKind.DERIVATION,
        )

        # The hashes come from the artifacts' own digests. That is the point of
        # the record: the kernel re-derives them and refuses if what was
        # declared does not match what was actually submitted.
        # The new decision proposition is a declared LINEAGE change: the
        # kernel observes a proposition that was absent and is now present,
        # and refuses it as UNDECLARED_CHANGE unless the record claims it.
        lineage_change = DeclaredChange(
            subject_id=decision_prop.proposition_id,
            dimension=Dimension.LINEAGE,
            from_value="absent",
            to_value="present",
            reason="PERCEIVE's decision, derived from the artifact's proposition",
            transition_kind=TransitionKind.DERIVATION,
        )

        transformation_record = TransformationRecord(
            transformation_id=f"perceive-verify-{governance_decision.decision_id}",
            input_artifact_ids=(input_artifact_id,),
            output_artifact_id=decision_artifact.artifact_id,
            transformer=perceive_actor,
            transformation_type="GOVERNANCE_EVALUATION",
            declared_changes=(content_change, lineage_change),
            input_hashes=(input_artifact.artifact_digest,),
            output_hash=decision_artifact.artifact_digest,
            reason=(
                f"PERCEIVE governance evaluation "
                f"(gates: {', '.join(governance_decision.applied_gates)})"
            ),
        )

        # Submit to Conservation Kernel. The input has to be registered as a
        # root first -- the kernel will not verify a transformation whose
        # input it has never seen, which is exactly the provenance guarantee
        # it exists to make.
        #
        # The ledger's own refusals -- a replay (duplicate artifact id) or an
        # input it has never seen -- are raised as LedgerError rather than
        # returned as a verdict. They are refusals all the same: the kernel
        # looked and said no. Re-raised as ConservationRefusal so the
        # orchestrator records them as such, not as a stage crash.
        try:
            self.kernel.register_root(input_artifact)
            verification_result = self.kernel.submit(
                input_artifact,
                decision_artifact,
                transformation_record,
            )
        except LedgerError as e:
            raise ConservationRefusal(
                f"Conservation Kernel ledger refused the transformation: {e}"
            ) from e
        except InvalidArtifact as e:
            # The kernel's own vocabulary refused the claim before any ledger
            # was reached (for example, human-adopted output with no
            # authorization). A refusal, not a crash.
            raise ConservationRefusal(f"Conservation Kernel refused the artifact: {e}") from e

        # If Kernel rejects, fail closed
        if not verification_result.accepted:
            raise ConservationRefusal(
                f"Conservation Kernel rejected PERCEIVE decision: {verification_result.violations}"
            )

        # Return conservation decision
        return ConservationDecision(
            governance_decision_id=governance_decision.decision_id,
            approval=governance_decision.approval,
            conservation_receipt_id=verification_result.transformation_id,
            artifact_hash=input_artifact_hash,
            conservation_audit_hash=self._compute_audit_hash(verification_result),
            verified=True,
            timestamp=datetime.now(timezone.utc)
        )

    @staticmethod
    def _compute_audit_hash(verification_result) -> str:
        """Compute audit hash from verification result."""
        content = f"{verification_result.transformation_id}:{verification_result.status.value}"
        return hashlib.sha256(content.encode()).hexdigest()
