"""
Governance chain verification.

The orchestrator's result used to carry `audit_chain_valid`, computed by
`GSA815ObserveAdapter.verify_governance_chain`, which checked that six fields
were non-empty. It recomputed nothing. A record with every hash altered, or a
record whose approval was built by hand and never saw the Conservation Kernel,
was "valid" as long as the strings were not blank.

This module is the real check. Given a result dict from
`GovernanceOrchestrator.orchestrate_request`, it re-derives every state
commitment link from the fields recorded beside it and compares, checks that
the artifact hash matches the content it travelled with, that every identifier
agrees across records, that timestamps run forward, and, when the kernels are
supplied, that the decision is in PERCEIVE's audit ledger and the derived
decision artifact is in the Conservation Kernel's ledger. A forged approval, a
substituted artifact, an altered outcome, a removed record, or a timestamp
moved backwards each fails a named check.

Each check is one row: (name, ok, detail). `valid` is the conjunction of the
checks that could be run; `complete` says whether the chain reached an
outcome. `audit_chain_valid` on the result means both.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from governance_contracts import CONTRACT_VERSION, compute_state_commitment


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


@dataclass
class ChainVerification:
    valid: bool
    complete: bool
    checks: List[Check] = field(default_factory=list)
    contract_version: str = CONTRACT_VERSION

    @property
    def failed(self) -> List[Check]:
        return [c for c in self.checks if not c.ok]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "valid": self.valid,
            "complete": self.complete,
            "contract_version": self.contract_version,
            "checks": [{"name": c.name, "ok": c.ok, "detail": c.detail} for c in self.checks],
            "failed": [c.name for c in self.failed],
        }


def request_state(request) -> Dict[str, Any]:
    """The exact state the request commitment is computed over.

    Shared with `SentinelPerceiveAdapter` so the producer and the verifier
    cannot drift apart: a commitment is only checkable if both sides agree on
    what it covers.
    """
    request_type = request.request_type
    return {
        "request_id": request.request_id,
        "request_type": request_type.value if hasattr(request_type, "value") else str(request_type),
        "artifact_id": request.artifact_id,
        "artifact_hash": request.artifact_hash,
        "producer": request.producer,
        "origin": request.origin,
        "authority": request.authority,
        "epistemic_status": request.epistemic_status,
        "lineage": request.lineage,
        "context": request.context,
        # 1.1.0: event time is part of what the request is. Absent on
        # requests made before 1.1.0 and on sources that state no time.
        "event_time": getattr(request, "event_time", None),
    }


def approval_state(approval, artifact_id: str, artifact_hash: str, producer: str, lineage: list) -> Dict[str, Any]:
    """The state an ExecutionApproval commitment covers (see approve_execution)."""
    return {
        "request_id": approval.request_id,
        "artifact_id": artifact_id,
        "artifact_hash": artifact_hash,
        "producer": producer,
        "lineage": lineage,
        "approval": approval.approval.value,
    }


def execution_context_state(execution_context) -> Dict[str, Any]:
    return {
        "request_id": execution_context.request_id,
        "artifact_id": execution_context.artifact_id,
        "artifact_hash": execution_context.artifact_hash,
        "lineage": execution_context.lineage,
        "producer": execution_context.producer,
        # 1.1.0: the execution's identity is committed. Before this a context
        # could be re-labelled with another execution id after issuance and
        # still verify whenever OBSERVE had not run.
        "execution_id": execution_context.execution_id,
    }


def outcome_state(outcome_context, execution_context) -> Dict[str, Any]:
    return {
        "execution_id": outcome_context.execution_id,
        "artifact_id": execution_context.artifact_id,
        "artifact_hash": execution_context.artifact_hash,
        "lineage": outcome_context.lineage,
        "outcome": outcome_context.outcome,
    }


def _ts(value) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def verify_result(result: Dict[str, Any], kernel=None, perceive=None) -> ChainVerification:
    """Verify a result from `orchestrate_request`.

    `kernel` (a ConservationKernel) and `perceive` (a PerceiveGovernanceKernel)
    are optional. Without them the structural and cryptographic links are
    checked; with them, ledger membership is checked too, which is what
    catches an approval that never went through the kernels.
    """
    checks: List[Check] = []

    def check(name: str, ok: bool, detail: str = "") -> bool:
        checks.append(Check(name, bool(ok), detail))
        return bool(ok)

    request = result.get("governance_request")
    decision = result.get("governance_decision")
    conservation = result.get("conservation_decision")
    approval = result.get("execution_approval")
    execution_context = result.get("execution_context")
    outcome = result.get("outcome_context")
    status = result.get("status")

    check("request.present", request is not None, "result carries the governance request")
    if request is None:
        return ChainVerification(valid=False, complete=False, checks=checks)

    # --- request: content hash and commitment ---------------------------------
    expected_hash = hashlib.sha256(str(request.artifact_content).encode()).hexdigest()
    check(
        "request.artifact_hash_matches_content",
        request.artifact_hash == expected_hash,
        f"carried {str(request.artifact_hash)[:12]} vs digest {expected_hash[:12]}",
    )
    check(
        "request.state_commitment",
        request.state_commitment == compute_state_commitment(None, request_state(request)),
        "request commitment recomputes from the request fields",
    )

    # --- decision --------------------------------------------------------------
    executed = status in ("APPROVED_AND_EXECUTED", "EXECUTION_FAILED")
    check("decision.present", decision is not None or not executed, "an executed result must carry a decision")
    if decision is not None:
        check("decision.request_id", decision.request_id == request.request_id, "decision names the request")
        if perceive is not None and hasattr(perceive, "audit_ledger"):
            entries = getattr(perceive.audit_ledger, "entries", [])
            found = any(
                isinstance(e.final_verdict, dict)
                and e.final_verdict.get("state_commitment") == decision.state_commitment
                for e in entries
            )
            check("decision.in_perceive_ledger", found, "decision commitment appears in PERCEIVE's audit ledger")
            if found:
                entry = next(
                    e for e in entries
                    if isinstance(e.final_verdict, dict)
                    and e.final_verdict.get("state_commitment") == decision.state_commitment
                )
                ledger_approved = bool(entry.final_verdict.get("approved"))
                recorded_approved = getattr(decision.approval, "value", decision.approval) == "approved"
                check(
                    "decision.matches_perceive_ledger",
                    ledger_approved == recorded_approved
                    and list(entry.final_verdict.get("violations") or []) == list(decision.violations or []),
                    "the recorded approval and violations are what PERCEIVE's ledger holds for this decision",
                )
            verify = getattr(perceive.audit_ledger, "verify_chain_integrity", None)
            if callable(verify):
                check("perceive_ledger.integrity", verify(), "PERCEIVE ledger hash chain intact")

    # --- conservation ----------------------------------------------------------
    if executed:
        check("conservation.present", conservation is not None, "an executed result must carry a conservation decision")
    if conservation is not None and decision is not None:
        check("conservation.decision_id", conservation.governance_decision_id == decision.decision_id, "conservation names the decision")
        check("conservation.artifact_hash", conservation.artifact_hash == request.artifact_hash, "conservation verified the request's artifact hash")
        check("conservation.verified", bool(conservation.verified), "conservation reports verified")
        if kernel is not None:
            decision_artifact_id = f"decision-{decision.decision_id}"
            try:
                reconstruction = kernel.reconstruct(decision_artifact_id)
                # Reconstructing is not enough: a ROOT registered under the
                # decision's id reconstructs too, and that is exactly what a
                # forged membership looks like (measured 2026-09-08). A decision
                # the kernel actually made is DERIVED, by a transformation,
                # from the request's artifact.
                derived = bool(reconstruction.transformation_ids_in_order)
                from_request = request.artifact_id in reconstruction.root_artifact_ids
                in_ledger = derived and from_request
                detail = (
                    f"{decision_artifact_id} derives by transformation from root {request.artifact_id}"
                    if in_ledger else
                    f"{decision_artifact_id} is in the kernel ledger but not as a decision derived from "
                    f"{request.artifact_id} (transformations={len(reconstruction.transformation_ids_in_order)}, "
                    f"roots={list(reconstruction.root_artifact_ids)})"
                )
            except Exception as e:  # the kernel raises its own error family for unknown ids
                in_ledger = False
                detail = f"{decision_artifact_id} not in kernel ledger: {type(e).__name__}"
            check("conservation.in_kernel_ledger", in_ledger, detail)

    # --- approval ---------------------------------------------------------------
    if executed:
        check("approval.present", approval is not None, "an executed result must carry an execution approval")
    if approval is not None and conservation is not None:
        check("approval.conservation_decision_id", approval.conservation_decision_id == conservation.governance_decision_id, "approval names the conservation decision")
        check("approval.receipt", approval.conservation_receipt_id == conservation.conservation_receipt_id, "approval carries the conservation receipt")
        check("approval.parent_hash", approval.conservation_audit_hash == conservation.conservation_audit_hash, "approval is chained to the conservation audit hash")
        if getattr(approval, "artifact_id", ""):
            check(
                "approval.names_request_artifact",
                approval.artifact_id == request.artifact_id and approval.artifact_hash == request.artifact_hash
                and approval.producer == request.producer and list(approval.lineage) == list(request.lineage),
                "the approval names the request's artifact, producer and lineage",
            )
        recomputed = compute_state_commitment(
            conservation.conservation_audit_hash,
            approval_state(approval, request.artifact_id, request.artifact_hash, request.producer, request.lineage),
        )
        check("approval.state_commitment", approval.state_commitment == recomputed, "approval commitment recomputes")

    # --- execution context -------------------------------------------------------
    if executed:
        check("execution_context.present", execution_context is not None, "an executed result must carry the execution context")
    if execution_context is not None and approval is not None:
        check("execution_context.approval", execution_context.approval.state_commitment == approval.state_commitment, "execution context carries the approval")
        check("execution_context.artifact", execution_context.artifact_id == request.artifact_id and execution_context.artifact_hash == request.artifact_hash, "execution context names the request's artifact")
        recomputed = compute_state_commitment(approval.state_commitment, execution_context_state(execution_context))
        check("execution_context.state_commitment", execution_context.state_commitment == recomputed, "execution context commitment recomputes")

    # --- outcome -------------------------------------------------------------------
    # 1.1.0: every executed result carries an outcome, so the execution's
    # result is committed whether or not OBSERVE ran. `complete` keeps its
    # meaning -- the chain reached an OBSERVED outcome -- so an executed
    # action nobody observed is verifiable but not complete.
    if executed:
        check("outcome.present", outcome is not None, "an executed result must carry an outcome")
    observed = outcome is not None and bool((outcome.outcome or {}).get("observe_ran", "observe_regime" in (outcome.outcome or {})))
    complete = observed
    if outcome is not None and execution_context is not None and approval is not None:
        check("outcome.execution_id", outcome.execution_id == execution_context.execution_id, "outcome names the execution")
        check("outcome.decision_id", outcome.governance_decision_id == approval.conservation_decision_id, "outcome names the decision, not the execution")
        check("outcome.audit_hashes", outcome.governance_audit_hash == approval.governance_audit_hash and outcome.conservation_audit_hash == approval.conservation_audit_hash, "outcome carries the approval's audit hashes")
        recomputed = compute_state_commitment(
            execution_context.state_commitment or approval.state_commitment,
            outcome_state(outcome, execution_context),
        )
        check("outcome.state_commitment", outcome.state_commitment == recomputed, "outcome commitment recomputes")
        gsa = result.get("gsa815_result")
        verdict = result.get("observe_verdict")
        regime = verdict.regime.value if verdict is not None else "no-observe"
        expected_result_hash = hashlib.sha256(f"{gsa}:{regime}".encode()).hexdigest()
        check("outcome.result_artifact_hash", outcome.result_artifact_hash == expected_result_hash, "result artifact hash matches the recorded execution result and verdict")
        if verdict is not None:
            check("outcome.matches_verdict", outcome.outcome.get("observe_audit_hash") == verdict.audit_hash and outcome.outcome.get("observe_regime") == verdict.regime.value, "outcome records the verdict that was observed")
        recorded_status = result.get("execution_status")
        if recorded_status is not None and "execution_status" in (outcome.outcome or {}):
            check("outcome.execution_status", outcome.outcome.get("execution_status") == recorded_status, "outcome records the execution status the result reports")

    # --- time runs forward --------------------------------------------------------
    executed_at = result.get("executed_at") or {}
    sequence = [
        ("request", _ts(getattr(request, "timestamp", None))),
        ("decision", _ts(getattr(decision, "timestamp", None))),
        ("conservation", _ts(getattr(conservation, "timestamp", None))),
        ("approval", _ts(getattr(approval, "timestamp", None))),
        ("execution_context", _ts(getattr(execution_context, "timestamp", None))),
        ("execution.started", _ts(executed_at.get("started"))),
        ("execution.finished", _ts(executed_at.get("finished"))),
        ("outcome", _ts(getattr(outcome, "timestamp", None))),
    ]
    present = [(n, t) for n, t in sequence if t is not None]
    out_of_order = [
        f"{a}({ta.isoformat()}) after {b}({tb.isoformat()})"
        for (a, ta), (b, tb) in zip(present, present[1:])
        if ta > tb
    ]
    check("time.monotonic", not out_of_order, "; ".join(out_of_order) or "every recorded time is at or after the one before it")

    valid = all(c.ok for c in checks)
    return ChainVerification(valid=valid, complete=complete, checks=checks)
