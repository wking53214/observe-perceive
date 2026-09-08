"""
Sentinel OS → PERCEIVE Adapter

Converts Sentinel artifacts to PERCEIVE governance requests, drives the
PERCEIVE kernel, and converts its verdict back into the cross-system
GovernanceDecision contract.

Two vocabularies meet at this seam and neither one gets to win:

- `governance_contracts` is the *cross-system* vocabulary. Every adapter in
  this chain speaks it, so a decision can travel Sentinel → PERCEIVE →
  Conservation → GSA-815 → OBSERVE without any one system's internal types
  leaking into the next.
- `perceive_consolidated` is PERCEIVE's *internal* vocabulary
  (`PolicyRequest` / `PolicyVerdict`). PERCEIVE is a standalone governance
  kernel; it does not import the orchestration contracts and must not have to.

Translating between them is this adapter's whole job. An earlier version of
the orchestrator skipped the translation and called a `perceive.evaluate()`
that never existed, handing PERCEIVE a `GovernanceRequest` it could not read
and passing its `PolicyVerdict` to a downstream adapter expecting a
`GovernanceDecision`. The seam is explicit now so that drift is a failing
test rather than an AttributeError at runtime.
"""

from governance_contracts import (
    GovernanceApproval,
    GovernanceDecision,
    GovernanceRequest,
    GovernanceRequestType,
    compute_state_commitment,
)
from datetime import datetime, timezone
import hashlib

# There used to be a lazy importer here for `sentinel_os.conservation.types`.
# That module does not exist in sentinel_os and the function was never called
# (measured 2026-09-08). The artifact this adapter consumes is a duck-typed
# contract -- see `sentinel_artifact_to_governance_request` -- and no
# repository implements a `SentinelArtifact` type. Removed rather than kept
# as a guard that would raise ModuleNotFoundError the first time it ran.


class SentinelPerceiveAdapter:
    """Converts Sentinel artifacts to PERCEIVE requests."""

    @staticmethod
    def sentinel_artifact_to_governance_request(
        artifact,  # SentinelArtifact (typing removed to avoid import)
        operation_type: str,
        context: dict = None,
        source_signers=None,
    ) -> GovernanceRequest:
        """
        Convert SentinelArtifact to GovernanceRequest.

        Args:
            artifact: The Sentinel artifact
            operation_type: "escalate", "modify", "export", "override"
            context: Additional context (age_months, risk_level, etc.)

        Returns:
            GovernanceRequest ready for PERCEIVE evaluation
        """
        if context is None:
            context = {}

        # Map Sentinel operation to PERCEIVE request type
        request_type_map = {
            "escalate": GovernanceRequestType.ESCALATE_PATIENT,
            "modify": GovernanceRequestType.MODIFY_RULE,
            "export": GovernanceRequestType.EXPORT_DATA,
            "override": GovernanceRequestType.EMERGENCY_OVERRIDE,
        }
        request_type = request_type_map.get(operation_type, GovernanceRequestType.APPROVE_DECISION)

        event_time = SentinelPerceiveAdapter._event_time_of(artifact)
        attested, key_id, problem = SentinelPerceiveAdapter.check_event_time_attestation(
            artifact, event_time, source_signers)
        request = GovernanceRequest(
            request_id=artifact.artifact_id,
            request_type=request_type,
            artifact_id=artifact.artifact_id,
            artifact_content=artifact.content,
            artifact_hash=SentinelPerceiveAdapter._compute_hash(artifact.content),
            producer=artifact.metadata.origin_status.value if hasattr(artifact.metadata.origin_status, 'value') else str(artifact.metadata.origin_status),
            origin=artifact.metadata.origin_status.value if hasattr(artifact.metadata.origin_status, 'value') else "SENTINEL",
            authority=artifact.metadata.authority_status.value if hasattr(artifact.metadata.authority_status, 'value') else "SYSTEM",
            epistemic_status=artifact.metadata.epistemic_status.value if hasattr(artifact.metadata.epistemic_status, 'value') else "INFERRED",
            lineage=artifact.metadata.parent_artifact_ids or [],
            context=context,
            timestamp=datetime.now(timezone.utc),
            event_time=event_time,
            ingested_at=datetime.now(timezone.utc).isoformat(),
            event_time_attested=attested,
            event_time_key_id=key_id,
            event_time_attestation_problem=problem,
        )
        # The state this commitment covers is defined once, in
        # governance_chain.request_state, and shared with the verifier: a
        # commitment only means something if the checker recomputes it over
        # exactly the fields the producer used.
        from governance_chain import request_state
        request.state_commitment = compute_state_commitment(
            parent_commitment=None,
            state=request_state(request),
        )
        return request

    @staticmethod
    def governance_request_to_policy_request(request: GovernanceRequest):
        """Convert a cross-system GovernanceRequest into PERCEIVE's own
        PolicyRequest.

        `request_type` carries the string value, not the enum: PERCEIVE's
        `_select_gates` matches on literals ("escalate_patient",
        "modify_rule", ...), and GovernanceRequestType's values were chosen
        to line up with them exactly.

        `timestamp` is passed through rather than left to default. PERCEIVE
        uses it as the reference time for its rate-limit windows, so passing
        the request's own timestamp keeps evaluation a pure function of
        recorded inputs; letting it fall back to wall-clock would make the
        same request evaluate differently on a re-run.
        """
        from perceive_consolidated import PolicyRequest

        request_type = (
            request.request_type.value
            if hasattr(request.request_type, "value")
            else str(request.request_type)
        )
        # PERCEIVE reasons about a subject (the patient/entity the request is
        # about); the orchestration layer reasons about an artifact. Prefer an
        # explicit subject from context, fall back to the artifact itself.
        subject_id = (
            request.context.get("patient_id")
            or request.context.get("subject_id")
            or request.artifact_id
        )
        return PolicyRequest(
            request_id=request.request_id,
            request_type=request_type,
            subject_id=subject_id,
            actor_id=request.producer,
            context=dict(request.context),
            timestamp=request.timestamp,
            state_commitment=request.state_commitment,
        )

    @staticmethod
    def policy_verdict_to_governance_decision(verdict, request: GovernanceRequest) -> GovernanceDecision:
        """Convert PERCEIVE's PolicyVerdict into the cross-system
        GovernanceDecision the rest of the chain consumes.

        `unanimous_consensus` maps to `verdict.approved`, not to
        `verdict.consensus_result`. These are two different things and the
        distinction is load-bearing: PERCEIVE's ConsensusEngine approves only
        when *every* applied gate approved, so `approved is True` IS the
        unanimity claim. `consensus_result` is the separate, optional DGK
        multi-node consensus that runs only for critical request types, and is
        None on the ordinary path -- reading it as the unanimity signal would
        report every normal unanimous approval as non-unanimous.

        `decision_id` is derived from the audit hash rather than generated
        randomly, so the same verdict always yields the same decision id and
        the audit chain stays reproducible.
        """
        approval = (
            GovernanceApproval.APPROVED if verdict.approved else GovernanceApproval.REJECTED
        )
        decision_id = f"perceive-{verdict.audit_hash[:16]}" if verdict.audit_hash else f"perceive-{verdict.request_id}"
        return GovernanceDecision(
            request_id=verdict.request_id,
            decision_id=decision_id,
            approval=approval,
            applied_gates=list(verdict.applied_gates),
            unanimous_consensus=bool(verdict.approved),
            violations=list(verdict.violations),
            policy_version=verdict.policy_version,
            perceive_audit_hash=verdict.audit_hash,
            # PERCEIVE already chained its commitment onto the request's, so
            # carry its value through rather than recomputing a parallel one.
            state_commitment=verdict.state_commitment,
            timestamp=datetime.now(timezone.utc),
            advisory_violations=list(getattr(verdict, "advisory_violations", None) or []),
        )

    @classmethod
    def evaluate_through_perceive(cls, perceive_kernel, request: GovernanceRequest) -> GovernanceDecision:
        """Drive a full PERCEIVE evaluation for a GovernanceRequest.

        The one call the orchestrator needs: cross-system request in,
        cross-system decision out, with PERCEIVE's internal vocabulary
        confined to this method.
        """
        policy_request = cls.governance_request_to_policy_request(request)
        verdict = perceive_kernel.evaluate_request(policy_request)
        return cls.policy_verdict_to_governance_decision(verdict, request)

    # -- event time attestation (1.2.0) ---------------------------------------
    #
    # Until 1.2.0 the event time was whatever the source said. A source that
    # holds a key registered with the deployment can now sign it, and the
    # request records (and commits) that the time was attested and by which
    # key. The payload is the artifact id and the time, so an attestation
    # cannot be moved to another artifact or another time.

    @staticmethod
    def attestation_payload(artifact_id: str, event_time: str) -> bytes:
        return f"{artifact_id}|{event_time}".encode("utf-8")

    @staticmethod
    def attest_event_time(signer, artifact_id: str, event_time: str) -> dict:
        """What a source attaches as `metadata.event_time_attestation`."""
        return {
            "key_id": signer.key_id,
            "algorithm": getattr(signer, "algorithm", "unknown"),
            "value": signer.sign(SentinelPerceiveAdapter.attestation_payload(artifact_id, event_time)),
        }

    @staticmethod
    def _attestation_of(artifact):
        for holder in (getattr(artifact, "metadata", None), artifact):
            value = getattr(holder, "event_time_attestation", None) if holder is not None else None
            if isinstance(value, dict):
                return value
        return None

    @staticmethod
    def check_event_time_attestation(artifact, event_time, source_signers):
        """(attested, key_id, problem). `source_signers` is a mapping of
        key_id to Signer, or an iterable of Signers, or None."""
        attestation = SentinelPerceiveAdapter._attestation_of(artifact)
        if attestation is None:
            return False, None, None
        if event_time is None:
            return False, attestation.get("key_id"), "attestation present but the artifact states no event time"
        signers = {}
        if source_signers:
            values = source_signers.values() if hasattr(source_signers, "values") else source_signers
            signers = {getattr(sg, "key_id", None): sg for sg in values}
        key_id = attestation.get("key_id")
        signer = signers.get(key_id)
        if signer is None:
            return False, key_id, f"attestation by unregistered key {key_id!r}"
        payload = SentinelPerceiveAdapter.attestation_payload(artifact.artifact_id, event_time)
        if not signer.verify(payload, attestation.get("value")):
            return False, key_id, f"attestation by key {key_id!r} does not verify"
        return True, key_id, None

    @staticmethod
    def _event_time_of(artifact) -> "str | None":
        """The source's own statement of when the described thing happened.

        Read from the first of `event_time`, `occurred_at`, `created_at`,
        `timestamp` present on the artifact's metadata or on the artifact
        itself, as an ISO-8601 string. None when the source states nothing,
        which is recorded as None rather than substituted with the clock:
        a request that says "no event time" is a different request from one
        that says "now".
        """
        for holder in (getattr(artifact, "metadata", None), artifact):
            if holder is None:
                continue
            for name in ("event_time", "occurred_at", "created_at", "timestamp"):
                value = getattr(holder, name, None)
                if value is None:
                    continue
                if isinstance(value, datetime):
                    return value.isoformat()
                return str(value)
        return None

    @staticmethod
    def _compute_hash(content: str) -> str:
        """Compute canonical SHA256 hash of content."""
        return hashlib.sha256(content.encode()).hexdigest()
