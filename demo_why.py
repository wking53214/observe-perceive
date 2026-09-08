#!/usr/bin/env python3
"""
Why did the system do that?

One reproducible scenario through the real governed action gate, then one
deliberate corruption of the record, which the chain must catch.

The ten steps the mission asks for, and where each lives in this run:

  1. source entered          a Gateway-sealed artifact (or, without the
                             Gateway checkout beside this repo, a caller-
                             declared artifact -- the demo says which)
  2. evidence preserved      the request carries the content and its hash;
                             Conservation refuses if they disagree
  3. interpretation          PERCEIVE's policy gates evaluate the request
  4. epistemic status        carried from the artifact into the request,
                             the handoff, and the commitment; never collapsed
  5. authority               same: recorded and committed, refused if absent
  6. prediction              AUGUR's simulation screen, veto-only, when present
  7. permission              PERCEIVE's decision, in its audit ledger
  8. execution               the caller's operation, with the approval and
                             context that authorised it
  9. outcome                 OBSERVE's verdict on the vitals
 10. historical record       the result: every record, every commitment,
                             and `chain_verification` re-deriving all of it

Run from a clean clone (see README, "Why did the system do that"):

    python3 demo_why.py                # corrupt the recorded outcome
    python3 demo_why.py --corrupt authority | source | approval | timestamp | outcome

Exit status 0 when the corruption was detected, 1 when it was not.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from conservation_kernel import ConservationKernel
from governance_chain import verify_result
from governance_contracts import GovernanceApproval
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest

HERE = os.path.dirname(os.path.abspath(__file__))


# --- the artifact: the chain's de facto input contract ----------------------------

class _Status:
    def __init__(self, value):
        self.value = value


class _Metadata:
    def __init__(self):
        self.origin_status = _Status("SENTINEL")
        self.authority_status = _Status("CHARGE_NURSE")
        self.epistemic_status = _Status("INFERRED")
        self.parent_artifact_ids = ["vitals-feed-2026-09-08T12:00"]


class Artifact:
    def __init__(self, artifact_id: str, content: str):
        self.artifact_id = artifact_id
        self.content = content
        self.metadata = _Metadata()


def _perceive():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="demo-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return kernel


def _vitals():
    return VitalsSnapshot(
        patient_id="P001", timestamp=datetime(2026, 9, 8, 12, 0, tzinfo=timezone.utc),
        heart_rate=142, oxygen_saturation=91, respiratory_rate=34, temperature=39.1,
    )


_QUIET = False


def _say(step, title, detail=""):
    if not _QUIET:
        print(f"  {step:>2}. {title:<22} {detail}")


def run_scenario(quiet: bool = False):
    """Run the canonical scenario. Returns (result, orchestrator, sealed_at_gateway)."""
    global _QUIET
    _QUIET = quiet
    log = (lambda *a, **k: None) if quiet else print
    content = "escalate P001: HR 142, SpO2 91, RR 34, T 39.1"
    artifact = Artifact("demo-escalation-001", content)
    context = {"patient_id": "P001", "severity": "high", "execution_id": "exec-demo-001"}

    try:
        from augur_screen_adapter import AugurScreenAdapter  # noqa: F401
        screen = True
    except ImportError:
        screen = False

    orchestrator = GovernanceOrchestrator(
        _perceive(), ConservationKernel(), ObserveClinicalEngine(),
        simulation_screen=screen,
    )

    def operation(execution_context):
        return {"status": "done", "action": "page on-call", "for": execution_context.artifact_id}

    log("\nWHY DID THE SYSTEM DO THAT?  one governed escalation, end to end\n")

    sealed = False
    try:
        from gateway_admission_adapter import GatewayAdmissionAdapter, GatewayEpistemicStatus
        gateway = GatewayAdmissionAdapter()
        sealed_artifact = gateway.seal(
            artifact_id=artifact.artifact_id, payload={"content": content},
            provenance={"source": "SENTINEL", "feed": artifact.metadata.parent_artifact_ids[0]},
            epistemic_status=GatewayEpistemicStatus.INFERENCE,
            authority_actor="CHARGE_NURSE", authority_grant="unit-escalation", execute=True,
        )
        _say(1, "source entered", "sealed at the Gateway with EXECUTE scope; admission re-checks the seal")
        result = gateway.govern_admitted(
            orchestrator, sealed_artifact, artifact, operation_type="escalate",
            operation_func=operation, context=context, vitals_snapshot=_vitals(),
        )
        sealed = True
    except ImportError:
        _say(1, "source entered", "Governance_Gateway not checked out beside this repo: scope declared by the caller (stated, not sealed)")
        result = orchestrator.orchestrate_request(
            artifact, "escalate", operation, vitals_snapshot=_vitals(),
            context={**context, "gateway_scope": "EXECUTE"},
        )

    request = result["governance_request"]
    _say(2, "evidence preserved", f"content hash {request.artifact_hash[:12]}... committed; Conservation refuses on mismatch")
    _say(3, "interpretation", f"PERCEIVE gates: {', '.join(result['governance_decision'].applied_gates) or 'none applied'}")
    _say(4, "epistemic status", f"{request.epistemic_status} (carried in request, handoff and commitment)")
    _say(5, "authority", f"{request.authority}, origin {request.origin}")
    screen_result = result.get("simulation_screen")
    if screen_result is None:
        _say(6, "prediction", "AUGUR not checked out beside this repo: no screen (stated)")
    else:
        _say(6, "prediction", f"screen {'evaluated' if screen_result.screened else 'abstained'}: {screen_result.reason}")
    decision = result["governance_decision"]
    _say(7, "permission", f"{decision.approval.value} by PERCEIVE, decision {decision.decision_id}")
    _say(8, "execution", f"{result['status']} / {result.get('execution_status')}: {result.get('gsa815_result')}")
    verdict = result.get("observe_verdict")
    if verdict is None:
        _say(9, "outcome", f"OBSERVE did not run ({result.get('observe_error') or 'no vitals on this path'})")
    else:
        _say(9, "outcome", f"regime {verdict.regime.value}, escalation_required={verdict.escalation_required}")
    cv = result["chain_verification"]
    _say(10, "historical record", f"{len(cv['checks'])} checks, valid={cv['valid']}, complete={cv['complete']}, audit_chain_valid={result['audit_chain_valid']}")
    log(f"\n  handoff: {result['handoff']['producer']} contract {result['handoff']['contract_version']}, strict {result['handoff']['strict']}")
    return result, orchestrator, sealed


CORRUPTIONS = {
    "outcome": lambda r: r["outcome_context"].outcome.__setitem__("observe_regime", "STABLE"),
    "source": lambda r: setattr(r["governance_request"], "artifact_content", "escalate P001: HR 80, SpO2 99, RR 18, T 36.8"),
    "authority": lambda r: setattr(r["governance_request"], "authority", "ATTENDING"),
    "approval": lambda r: setattr(r["governance_decision"], "approval", GovernanceApproval.REJECTED),
    "timestamp": lambda r: setattr(r["outcome_context"], "timestamp", r["governance_decision"].timestamp - timedelta(hours=2)),
}


def corrupt_and_verify(result, orchestrator, which: str, quiet: bool = False):
    log = (lambda *a, **k: None) if quiet else print
    if which in ("outcome", "timestamp") and result.get("outcome_context") is None:
        # Without OBSERVE there is no outcome to corrupt; fall back to the
        # execution record, which every executed result carries.
        which = "execution"
        CORRUPTIONS[which] = lambda r: r.__setitem__("gsa815_result", {"status": "done", "action": "nothing"})
    before = verify_result(result, kernel=orchestrator.conservation_kernel, perceive=orchestrator.perceive).as_dict()
    log(f"\nCORRUPTING one element of the record after the fact: {which}")
    CORRUPTIONS[which](result)
    after = verify_result(result, kernel=orchestrator.conservation_kernel, perceive=orchestrator.perceive).as_dict()
    caught = [c for c in after["checks"] if not c["ok"]]
    log(f"  before: valid={before['valid']}   after: valid={after['valid']}")
    for c in caught:
        log(f"  DETECTED  {c['name']:<40} {c['detail']}")
    if not caught:
        log("  NOT DETECTED -- this is a finding; the enforcement boundary needs repair")
    return bool(caught) and not after["valid"]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--corrupt", choices=sorted(CORRUPTIONS), default="outcome")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)
    # The components log at INFO; the demo's own lines are the narrative.
    import logging
    logging.disable(logging.INFO)
    result, orchestrator, _ = run_scenario(quiet=args.quiet)
    if result["status"] != "APPROVED_AND_EXECUTED":
        print(f"\nscenario did not execute: {result['status']}: {result.get('reason')}")
        return 1
    if not result["chain_verification"]["valid"]:
        print(f"\nclean record failed verification: {result['chain_verification']['failed']}")
        return 1
    detected = corrupt_and_verify(result, orchestrator, args.corrupt, quiet=args.quiet)
    if not args.quiet:
        print("\nresult:", "corruption detected" if detected else "corruption ACCEPTED")
    return 0 if detected else 1


if __name__ == "__main__":
    sys.exit(main())
