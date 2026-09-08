"""vertical_slice.py -- one governed action, end to end, through the real code,
then explained backwards from its effect after a restart.

    SOURCE      a clinical feed artifact with its own event time
    PRESERVE    sealed at the Governance Gateway (EXECUTE scope); registered as a
                root in the Conservation Kernel, with its epistemic claims as
                propositions
    INTERPRET   PERCEIVE policy gates
    GATE        the kernel verifies PERCEIVE's decision as a derived artifact
    AUTHORIZE   an ExecutionApproval and ExecutionContext are minted, chained,
                and ISSUED into an on-disk execution ledger
    EXECUTE     a guarded executor that refuses anything not issued, altered or
                already used, then appends one line to an effects log
    OBSERVE     OBSERVE evaluates the vitals after the action
    AUDIT       a hash-chained receipt on disk; the kernel snapshot on disk; CCC
                records the orchestration when the optional pack is present

Then the process "restarts": every object is discarded, the files are
reopened, and `explain(effect)` reconstructs, from the effect line alone,
why that action happened -- verifying every commitment against the record
and the restored kernel. Finally the same action is replayed three ways and
refused three ways.

Nothing here bypasses the real system: the executor is the only synthetic
part, and it is synthetic only in what it does (write a line), not in how
it is authorized.

Run it:  python vertical_slice.py [workdir]
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from conservation_kernel import ConservationKernel
from execution_guard import ExecutionLedger, ExecutionRefusal, authorize_execution, guarded
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog, from_record, verify_record
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest


class _Status:
    def __init__(self, value):
        self.value = value


class _Metadata:
    def __init__(self, origin, authority, epistemic, parents, occurred_at):
        self.origin_status = _Status(origin)
        self.authority_status = _Status(authority)
        self.epistemic_status = _Status(epistemic)
        self.parent_artifact_ids = parents
        self.occurred_at = occurred_at


class FeedArtifact:
    """What a source hands the system: the duck-typed contract the spine reads."""

    def __init__(self, artifact_id: str, content: str, occurred_at: datetime):
        self.artifact_id = artifact_id
        self.content = content
        self.metadata = _Metadata("SENTINEL", "SYSTEM", "INFERRED", ["feed-icu-3"], occurred_at)


def _perceive() -> PerceiveGovernanceKernel:
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(manifest_id="slice-manifest", version="1.0.0",
                                            created_at=datetime.now(timezone.utc), policies={}))
    return kernel


def _vitals() -> VitalsSnapshot:
    return VitalsSnapshot(patient_id="P001", timestamp=datetime(2026, 9, 8, 12, 5, tzinfo=timezone.utc),
                          heart_rate=142, oxygen_saturation=91, respiratory_rate=28, temperature=38.9)


class Paths:
    def __init__(self, workdir: Path):
        self.workdir = Path(workdir)
        self.executions = self.workdir / "executions.jsonl"
        self.receipts = self.workdir / "receipts.jsonl"
        self.kernel = self.workdir / "kernel.json"
        self.effects = self.workdir / "effects.log"
        self.ccc = self.workdir / "ccc.json"


# ---------------------------------------------------------------- the action

def run_action(workdir: Path, *, artifact_id: str = "feed-icu-3-0042", execution_id: str = "exec-0042") -> Dict[str, Any]:
    """The governed action, in the process that performs it."""
    paths = Paths(workdir)
    paths.workdir.mkdir(parents=True, exist_ok=True)
    kernel = ConservationKernel()
    ledger = ExecutionLedger(paths.executions)
    receipts = ReceiptLog(paths.receipts)
    orchestrator = GovernanceOrchestrator(
        _perceive(), kernel, ObserveClinicalEngine(),
        require_declared_scope=True,          # a scope must be sealed, not claimed
        execution_ledger=ledger, receipts=receipts,
    )

    # EXECUTE: the actuator. It will not act on anything the ledger did not issue.
    def actuator(execution_context):
        line = {"execution_id": execution_context.execution_id, "artifact_id": execution_context.artifact_id,
                "action": "page on-call", "at": datetime.now(timezone.utc).isoformat()}
        with paths.effects.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(line, sort_keys=True) + "\n")
        return {"status": "done", "action": "page on-call"}

    executor = guarded(actuator, ledger, kernel=kernel, consumer="on-call-pager")

    # SOURCE + PRESERVE
    occurred = datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc)
    artifact = FeedArtifact(artifact_id, "escalate P001: sustained tachycardia, SpO2 91", occurred)
    from gateway_admission_adapter import GatewayAdmissionAdapter, GatewayEpistemicStatus
    gateway = GatewayAdmissionAdapter()
    sealed = gateway.seal(
        artifact_id=artifact_id, payload={"content": artifact.content},
        provenance={"source": "SENTINEL", "feed": "feed-icu-3", "occurred_at": occurred.isoformat()},
        epistemic_status=GatewayEpistemicStatus.INFERENCE,
        authority_actor="CHARGE_NURSE", authority_grant="unit-escalation", execute=True,
    )

    # INTERPRET → GATE → AUTHORIZE → EXECUTE → OBSERVE → AUDIT, in the orchestrator
    result = gateway.govern_admitted(
        orchestrator, sealed, artifact, operation_type="escalate", operation_func=executor,
        context={"patient_id": "P001", "execution_id": execution_id}, vitals_snapshot=_vitals(),
    )

    # AUDIT: the kernel's ledger leaves the process too; CCC when present.
    kernel.save(paths.kernel)
    ccc_recorded = None
    try:
        # The adapter resolves CCC (sibling checkout or installed package)
        # and must be imported first.
        from orchestrator_ccc_adapter import OrchestratorCCCAdapter
        from ccc import CCCSystem
        ccc = CCCSystem(persistence_path=paths.ccc)
        record = OrchestratorCCCAdapter(ccc_system=ccc).record(result)
        ccc.save()
        ccc_recorded = getattr(record, "discovery_id", None)
    except ImportError:
        pass
    return {"result": result, "sealed_integrity": sealed.integrity, "ccc_recorded": ccc_recorded, "paths": paths}


# ---------------------------------------------------------------- after the restart

def explain(workdir: Path, effect_line: Dict[str, Any]) -> Dict[str, Any]:
    """Why did this action happen? Reconstructed from the files alone."""
    paths = Paths(workdir)
    receipts = ReceiptLog(paths.receipts)                     # refuses a broken chain
    ledger = ExecutionLedger(paths.executions)                # refuses a broken chain
    kernel = ConservationKernel.load(paths.kernel)            # re-admits roots, re-verifies transformations

    execution_id = effect_line["execution_id"]
    found = receipts.find(execution_id=execution_id)
    steps = []

    def step(name, ok, detail):
        steps.append({"step": name, "ok": bool(ok), "detail": detail})

    step("ACTION", bool(found), f"effect {effect_line['action']!r} for {execution_id} has {len(found)} receipt(s)")
    if not found:
        return {"execution_id": execution_id, "explained": False, "steps": steps}
    record = found[0]["record"]
    verification = verify_record(record, kernel=kernel)
    rebuilt = from_record(record)

    consumed = ledger.consumed(execution_id)
    issued = ledger.issued(execution_id)
    step("EXECUTION", consumed is not None and issued is not None and consumed["consumer"] == "on-call-pager",
         f"issued at {issued and issued['recorded_at']}, consumed by {consumed and consumed['consumer']} at {consumed and consumed['recorded_at']}; "
         f"guard checks recorded: {len((record.get('execution_authorization') or {}).get('guard', {}).get('checks', []))}")
    approval = rebuilt["execution_approval"]
    step("AUTHORIZATION", approval is not None and approval.approval.value == "approved"
         and "approval.state_commitment" not in verification.as_dict()["failed"],
         f"approval {approval.request_id} chained to conservation audit {approval.conservation_audit_hash[:12]}...; commitment recomputes")
    conservation = rebuilt["conservation_decision"]
    decision = rebuilt["governance_decision"]
    in_kernel = "conservation.in_kernel_ledger" not in verification.as_dict()["failed"]
    reconstruction = kernel.reconstruct(f"decision-{decision.decision_id}")
    props = [p for a in reconstruction.artifact_ids_in_order for p in kernel.ledger.artifact(a).propositions]
    step("GOVERNANCE DECISION", conservation.verified and in_kernel,
         f"kernel decision {conservation.conservation_receipt_id} derives from root {reconstruction.root_artifact_ids} "
         f"by {len(reconstruction.transformation_ids_in_order)} transformation(s); {len(props)} proposition(s) constrained")
    step("INTERPRETATION", decision.approval.value == "approved",
         f"PERCEIVE {decision.approval.value}; gates {decision.applied_gates or 'none'}; advisory violations {decision.advisory_violations}")
    request = rebuilt["governance_request"]
    step("EVIDENCE", "request.artifact_hash_matches_content" not in verification.as_dict()["failed"],
         f"content digest {request.artifact_hash[:12]}... matches the content carried in the record")
    step("SOURCE", request.event_time is not None and record.get("scope_sealed") is True,
         f"origin {request.origin}, epistemic {request.epistemic_status}, occurred {request.event_time}, "
         f"ingested {request.ingested_at}; Gateway admission {record.get('gateway_admission')}")
    step("RECORD", verification.valid and verification.complete and receipts.entries[0]["receipt"]["record_hash"] == found[0]["receipt"]["record_hash"],
         f"{len(verification.checks)} checks, failed {verification.as_dict()['failed']}; receipt {found[0]['receipt']['hash'][:12]}... in a chain of {len(receipts.entries)}")
    outcome = rebuilt["outcome_context"]
    step("OBSERVATION", outcome is not None and outcome.outcome.get("observe_ran") is True,
         f"OBSERVE regime {outcome.outcome.get('observe_regime')}, escalation_required {outcome.outcome.get('escalation_required')}")
    return {"execution_id": execution_id, "explained": all(s["ok"] for s in steps), "steps": steps,
            "verification": verification.as_dict(), "rebuilt": rebuilt, "kernel": kernel, "ledger": ledger}


def attack_after_restart(workdir: Path, explained: Dict[str, Any]) -> Dict[str, Any]:
    """The same action, three ways, after the restart. Each must be refused."""
    paths = Paths(workdir)
    ledger = explained["ledger"]
    kernel = explained["kernel"]
    context = explained["rebuilt"]["execution_context"]
    outcomes = {}
    effects_before = paths.effects.read_text().count("\n")

    def actuator(execution_context):
        with paths.effects.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"execution_id": execution_context.execution_id, "action": "REPLAYED"}) + "\n")
        return {"status": "done"}

    executor = guarded(actuator, ledger, kernel=kernel, consumer="on-call-pager")
    # 1. replay the recorded context
    try:
        executor(context)
        outcomes["replay_issued_context"] = "EXECUTED"
    except ExecutionRefusal as e:
        outcomes["replay_issued_context"] = f"refused: {e}"
    # 2. a hand-built context with the same ids
    import copy
    forged = copy.deepcopy(context)
    forged.execution_id = "exec-0042-forged"
    forged.approval.request_id = "exec-0042-forged"
    try:
        authorize_execution(forged, ledger, kernel=kernel)
        outcomes["forged_context"] = "AUTHORIZED"
    except ExecutionRefusal as e:
        outcomes["forged_context"] = f"refused: {e}"
    # 3. the whole request again, same execution id, new process
    orchestrator = GovernanceOrchestrator(_perceive(), ConservationKernel(), ObserveClinicalEngine(),
                                          require_declared_scope=True, execution_ledger=ledger,
                                          receipts=ReceiptLog(paths.receipts))
    from gateway_admission_adapter import GatewayAdmissionAdapter, GatewayEpistemicStatus
    artifact = FeedArtifact("feed-icu-3-0042", "escalate P001: sustained tachycardia, SpO2 91", datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc))
    sealed = GatewayAdmissionAdapter.seal(artifact_id=artifact.artifact_id, payload={"content": artifact.content},
                                          provenance={"source": "SENTINEL"}, epistemic_status=GatewayEpistemicStatus.INFERENCE,
                                          authority_actor="CHARGE_NURSE", authority_grant="unit-escalation", execute=True)
    again = GatewayAdmissionAdapter().govern_admitted(orchestrator, sealed, artifact, operation_type="escalate",
                                                       operation_func=executor,
                                                       context={"patient_id": "P001", "execution_id": "exec-0042"},
                                                       vitals_snapshot=_vitals())
    outcomes["replay_whole_request"] = f"{again['status']}: {again.get('reason')}"
    outcomes["effects_written_by_attacks"] = paths.effects.read_text().count("\n") - effects_before
    outcomes["attack_receipted"] = again["status"] == "REJECTED" and again.get("receipt") is not None
    return outcomes


def run_slice(workdir: Path) -> Dict[str, Any]:
    action = run_action(workdir)
    result = action["result"]
    paths = action["paths"]
    effect = json.loads(paths.effects.read_text().splitlines()[0])
    explained = explain(workdir, effect)
    attacks = attack_after_restart(workdir, explained)
    return {"status": result["status"], "ccc_recorded": action["ccc_recorded"], "effect": effect,
            "explained": explained["explained"], "steps": explained["steps"], "attacks": attacks}


def main(argv=None) -> int:
    workdir = Path((argv or sys.argv[1:])[0]) if (argv or sys.argv[1:]) else Path("slice_run")
    import shutil
    if workdir.exists():
        shutil.rmtree(workdir)
    out = run_slice(workdir)
    print(f"\nONE GOVERNED ACTION, EXPLAINED AFTER A RESTART   ({workdir})\n")
    print(f"  action status: {out['status']}   effect: {out['effect']['action']!r} for {out['effect']['execution_id']}")
    for s in out["steps"]:
        print(f"  [{'ok' if s['ok'] else 'FAIL':4s}] {s['step']:20s} {s['detail']}")
    print("\n  after restart, the same action three ways:")
    for k, v in out["attacks"].items():
        print(f"    {k}: {v}")
    print(f"\n  explained: {out['explained']}   CCC discovery: {out['ccc_recorded']}")
    return 0 if out["explained"] and out["attacks"]["effects_written_by_attacks"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
