"""
Governance Orchestrator

Central orchestrator that routes requests through:
1. Sentinel artifact creation
2. PERCEIVE governance evaluation (6 gates, unanimous consensus)
3. Conservation Kernel verification
4. GSA-815 execution (with approval)
5. OBSERVE monitoring
6. Complete audit chain linking
"""

import logging
from datetime import datetime, timezone

from governance_contracts import CONTRACT_VERSION
from governance_chain import verify_result
from execution_guard import ExecutionLedger, ExecutionRefusal as LedgerRefusal

logger = logging.getLogger("GovernanceOrchestrator")


class GovernanceOrchestrator:
    """Central orchestrator for unified governance flow."""

    def __init__(self, perceive=None, conservation_kernel=None, observe_engine=None,
                 fortress_controller: str = None,
                 simulation_screen: bool = False, simulation_seed: int = 42,
                 require_declared_scope: bool = False,
                 require_vitals: bool = False,
                 raise_on_stage_error: bool = False,
                 raise_on_execution_error: bool = False,
                 execution_ledger: "ExecutionLedger | None" = None,
                 receipts=None):
        """
        Initialize orchestrator with all governance systems.

        Args:
            perceive: PERCEIVE governance kernel (optional for testing)
            conservation_kernel: Conservation Kernel for verification (optional)
            observe_engine: OBSERVE clinical AI system (optional)
            fortress_controller: "energy", "lyapunov" or "sage" to place
                fortress-kernel in the chain between PERCEIVE and the
                Conservation Kernel. None (the default) leaves it out
                entirely, so the chain behaves as it did before and a missing
                fortress-kernel checkout is never a hard failure.
            simulation_screen: place AUGUR's behavioural-simulation screen
                ahead of PERCEIVE. A different system from fortress-kernel
                above; the two were both called FORTRESS until AUGUR was
                renamed. Off by default.
            simulation_seed: seed for that screen, so its runs are
                reproducible by a reviewer.
            require_declared_scope: refuse to execute a request that declares
                no gateway scope at all. Defaults False, matching the advisory
                pattern PolicyEnforcementConfig uses in PERCEIVE: the check
                runs and records what it WOULD decide, but does not flip an
                existing verdict.

                The default is permissive because callers predating the
                Gateway seam pass no scope. It is not permissive *silently*:
                an undeclared scope is logged as a warning and reported in the
                result as `scope_enforced: False`, so the difference between
                "checked and allowed" and "never checked" is visible to
                anyone reading the record. Measured when this was added: 15 of
                16 call sites declared no scope, so the check was reaching
                almost nothing while looking like a control.
            require_vitals: refuse to execute a request that supplies no
                vitals snapshot. Defaults False: OBSERVE (Phase 6) has always
                been skipped when there are no vitals. The skip is no longer
                silent -- it is logged as a warning and the result carries
                `observe_enforced`, so an APPROVED_AND_EXECUTED record with
                no OBSERVE verdict can be told apart from one where OBSERVE
                ran. Same shape as require_declared_scope.
            raise_on_stage_error: raise instead of recording REJECTED when a
                stage raises something other than its own refusal. Defaults
                False: the Conservation and execution-approval stages have
                always converted any exception into a REJECTED record, which
                made "the kernel refused" and "there was no kernel" the same
                record. Every such record now carries `stage_refused`,
                `refused_by` and `stage_error`; this flag turns the crash
                case into an exception for callers that would rather not
                have a stage failure filed as a verdict.
        """
        self.require_declared_scope = require_declared_scope
        self.require_vitals = require_vitals
        self.raise_on_stage_error = raise_on_stage_error
        # The execution stage (Phase 5) used to call the caller's function
        # unguarded: an exception there left NO record at all, after
        # Conservation had verified and execution had been approved. Now a
        # failure is a recorded state, EXECUTION_FAILED, with the approval and
        # context that preceded it. Opt in to raising instead.
        self.raise_on_execution_error = raise_on_execution_error
        # Every execution context this orchestrator issues is recorded here
        # before the executor sees it, and marked consumed after. An executor
        # wrapped with `execution_guard.guarded(func, ledger)` refuses a
        # context that is not in this ledger, was altered since issuance, or
        # was consumed before -- measured 2026-09-08, all three executed
        # without it. In memory by default; pass ExecutionLedger(path) for a
        # ledger that survives the process, so a replay after restart is
        # still a replay.
        self.execution_ledger = execution_ledger if execution_ledger is not None else ExecutionLedger()
        # A governance_record.ReceiptLog. When set, every result -- refusal,
        # failure or execution -- is written as a hash-chained receipt before
        # it is returned, so the explanation exists outside this process.
        self.receipts = receipts
        self.perceive = perceive
        self.conservation_kernel = conservation_kernel
        self.observe_engine = observe_engine

        # Lazy imports for adapters
        from sentinel_perceive_adapter import SentinelPerceiveAdapter
        from perceive_conservation_adapter import PerceiveConservationAdapter
        from conservation_gsa815_adapter import ConservationGSA815Adapter
        from gsa815_observe_adapter import GSA815ObserveAdapter

        self.sentinel_adapter = SentinelPerceiveAdapter()
        self.perceive_adapter = PerceiveConservationAdapter(conservation_kernel) if conservation_kernel else None
        self.conservation_adapter = ConservationGSA815Adapter()
        self.gsa815_adapter = GSA815ObserveAdapter()

        # fortress-kernel is opt-in. Imported only when asked for, so the rest of the
        # chain does not acquire a hard dependency on a sibling checkout.
        self.fortress_adapter = None
        if fortress_controller:
            from fortress_perceive_adapter import FortressPerceiveAdapter
            self.fortress_adapter = FortressPerceiveAdapter(controller_mode=fortress_controller)

        # AUGUR's behavioural-simulation screen, also opt-in. A different
        # system from the fortress-kernel containment adapter above -- both
        # were called FORTRESS until AUGUR took its own name. Runs first
        # among the judging stages: it can only
        # refuse, never approve, which is the right shape for a screen and
        # means it cannot smuggle an endorsement into the chain.
        self.simulation_screen = None
        if simulation_screen:
            from augur_screen_adapter import AugurScreenAdapter
            self.simulation_screen = AugurScreenAdapter(seed=simulation_seed)

    def _verify_on_the_way_out(self, result: dict, forensic_proof) -> dict:
        """Recompute the whole chain before handing the record over.

        `audit_chain_valid` used to mean "six fields are non-empty". It now
        means every commitment link recomputes, the artifact hash matches the
        content, every id agrees across records, time runs forward, and the
        decision is in both kernels' ledgers -- AND the chain reached an
        outcome. The per-check detail travels in `chain_verification`.
        """
        verification = verify_result(result, kernel=self.conservation_kernel, perceive=self.perceive)
        result["chain_verification"] = verification.as_dict()
        result["audit_chain_valid"] = verification.valid and verification.complete
        if forensic_proof is not None:
            forensic_proof["chain_valid"] = result["audit_chain_valid"]
            forensic_proof["chain_verification"] = result["chain_verification"]
        if not verification.valid:
            logger.error(
                "[Orchestrator] Chain verification FAILED: "
                + ", ".join(c.name for c in verification.failed)
            )
        return result

    @staticmethod
    def _admission_problems(admission, declared_scope: str, artifact_id: str) -> list:
        """Why a Gateway admission does not back a declared scope; empty if it does."""
        problems = []
        expected = getattr(admission, "expected_integrity", None)
        integrity = getattr(admission, "integrity", None)
        if not callable(expected) or not integrity:
            return ["admission is not a sealed Gateway artifact"]
        if expected() != integrity:
            problems.append("admission integrity does not recompute (altered since sealing)")
        sealed_scope = getattr(getattr(admission, "scope", None), "value", getattr(admission, "scope", None))
        if sealed_scope != declared_scope:
            problems.append(f"sealed scope is {sealed_scope}, declared scope is {declared_scope}")
        admitted_id = getattr(admission, "artifact_id", None)
        if admitted_id is not None and admitted_id != artifact_id:
            problems.append(f"admission is for artifact {admitted_id}, this request is for {artifact_id}")
        return problems

    def _consume(self, execution_context, gsa815_result, execution_status: str) -> dict:
        """Mark the issued context consumed, once.

        A guarded executor consumes it at the moment of authorization and
        attaches the authorization to its result; the orchestrator then only
        records what happened. An unguarded executor leaves consumption to
        the orchestrator, after the fact, so the ledger still shows one use
        per issuance, and the record says which of the two it was.
        """
        attached = gsa815_result.get("_authorization") if isinstance(gsa815_result, dict) else None
        already = self.execution_ledger.consumed(execution_context.execution_id)
        if already is not None:
            return {
                "consumed_by": already.get("consumer"),
                "ledger_entry_hash": already.get("hash"),
                "authorized_at": already.get("recorded_at"),
                "guard": attached,
            }
        entry = self.execution_ledger.consume(execution_context, consumer="orchestrator", outcome=execution_status)
        return {
            "consumed_by": "orchestrator",
            "ledger_entry_hash": entry["hash"],
            "authorized_at": entry["recorded_at"],
            "guard": None,
        }

    @staticmethod
    def _temporal_anomalies(request) -> list:
        from datetime import timedelta
        anomalies = []
        try:
            event = datetime.fromisoformat(request.event_time) if request.event_time else None
            ingested = datetime.fromisoformat(request.ingested_at) if request.ingested_at else None
        except (TypeError, ValueError):
            return ["event_time or ingested_at is not ISO-8601"]
        if event is not None and ingested is not None:
            if event.tzinfo is None or ingested.tzinfo is None:
                anomalies.append("event_time or ingested_at has no timezone")
            elif event > ingested:
                anomalies.append(f"event_time {request.event_time} is after ingestion {request.ingested_at}")
            elif ingested - event > timedelta(days=30):
                anomalies.append(f"event_time {request.event_time} is more than 30 days before ingestion")
        return anomalies

    def _stamp(self, result: dict, governance_request=None) -> dict:
        """Attach the handoff record and the request to every result.

        A downstream reader must be able to tell what it received, from whom,
        under what contract version and authority, and with which strict
        flags in force -- without reaching back into this process. And the
        request must travel with the record: a decision without the request
        it decided cannot be reconstructed from the record alone.
        """
        result.setdefault("governance_request", governance_request)
        result.setdefault("temporal_anomalies",
                          self._temporal_anomalies(governance_request) if governance_request is not None else [])
        result.setdefault("handoff", {
            "producer": "observe-perceive.GovernanceOrchestrator",
            "contract_version": CONTRACT_VERSION,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "authority": getattr(governance_request, "authority", None),
            "epistemic_status": getattr(governance_request, "epistemic_status", None),
            "origin": getattr(governance_request, "origin", None),
            "request_state_commitment": getattr(governance_request, "state_commitment", None),
            "strict": {
                "require_declared_scope": self.require_declared_scope,
                "require_vitals": self.require_vitals,
                "raise_on_stage_error": self.raise_on_stage_error,
                "raise_on_execution_error": self.raise_on_execution_error,
            },
            "execution_ledger": str(self.execution_ledger.path) if self.execution_ledger.path else "in-memory",
        })
        return result

    def orchestrate_request(
        self,
        sentinel_artifact,
        operation_type: str,
        gsa815_operation_func,
        vitals_snapshot=None,  # VitalsSnapshot (type annotation removed to avoid import)
        context: dict = None
    ) -> dict:
        """Orchestrate the flow (see `_orchestrate`) and receipt the result."""
        result = self._orchestrate(sentinel_artifact, operation_type, gsa815_operation_func, vitals_snapshot, context)
        if self.receipts is not None:
            try:
                result["receipt"] = self.receipts.append(result)
            except Exception as e:  # noqa: BLE001
                # The action (if any) has already happened; raising here
                # would lose even the in-memory record. Degrade explicitly:
                # the result says it was not receipted, and says why.
                result["receipt"] = None
                result["receipt_error"] = f"{type(e).__name__}: {e}"
                logger.critical(f"[Orchestrator] Result NOT receipted: {result['receipt_error']}")
        return result

    def _orchestrate(
        self,
        sentinel_artifact,
        operation_type: str,
        gsa815_operation_func,
        vitals_snapshot=None,
        context: dict = None
    ) -> dict:
        """
        Orchestrate complete governance flow.

        Flow:
        1. Sentinel artifact → GovernanceRequest
        2. PERCEIVE evaluation (6 gates, unanimous)
        3. Conservation Kernel verification
        4. GSA-815 execution approval
        5. Execute GSA-815 operation
        6. OBSERVE monitoring
        7. Link all audit chains

        Args:
            sentinel_artifact: The artifact to govern
            operation_type: "escalate", "modify", "export", "override"
            gsa815_operation_func: Function to execute if approved
            vitals_snapshot: Optional vitals for OBSERVE monitoring
            context: Additional context

        Returns:
            Complete governance decision with audit chain
        """
        # A missing or malformed artifact used to raise AttributeError from
        # deep inside Phase 1. It is a request the chain could not read, and
        # the record should say so rather than the stack trace.
        missing = [
            name for name in ("artifact_id", "content", "metadata")
            if not hasattr(sentinel_artifact, name)
        ]
        if sentinel_artifact is None or missing:
            reason = (
                "no artifact supplied" if sentinel_artifact is None
                else f"artifact lacks required attribute(s): {', '.join(missing)}"
            )
            logger.error(f"[Orchestrator] Invalid request: {reason}")
            return self._stamp({
                "status": "INVALID_REQUEST",
                "reason": reason,
                "governance_decision": None,
                "audit_chain": None,
            })

        logger.info(f"[Orchestrator] Starting governance flow for {sentinel_artifact.artifact_id}")

        # PHASE 1: Convert Sentinel artifact to PERCEIVE request
        logger.info("[Orchestrator] Phase 1: Converting Sentinel artifact to governance request")
        if context is None:
            context = {}
        # The sealed Gateway admission is checked here, not committed into
        # the request: the request's context is part of its commitment and
        # must stay plain data. What is recorded about the admission is its
        # id, scope and integrity digest.
        gateway_admission = context.pop("gateway_admission", None) if "gateway_admission" in context else None
        admission_summary = None
        if gateway_admission is not None:
            admission_summary = {
                "artifact_id": getattr(gateway_admission, "artifact_id", None),
                "scope": getattr(getattr(gateway_admission, "scope", None), "value", None),
                "integrity": getattr(gateway_admission, "integrity", None),
            }
            context["gateway_admission_integrity"] = admission_summary["integrity"]
        governance_request = self.sentinel_adapter.sentinel_artifact_to_governance_request(
            sentinel_artifact,
            operation_type,
            context
        )
        logger.info(f"[Orchestrator] Request ID: {governance_request.request_id}")
        # Temporal sanity is recorded, never assumed. An event time after
        # ingestion, or an ingestion before the event by more than a day,
        # is an anomaly the record names; refusing on it would be a policy
        # this library does not own.
        temporal_anomalies = self._temporal_anomalies(governance_request)
        if temporal_anomalies:
            logger.warning(f"[Orchestrator] Temporal anomalies on {governance_request.request_id}: {temporal_anomalies}")

        # PHASE 1b: behavioural-simulation screen
        #
        # First of the judging stages, and veto-only by construction: it can
        # refuse but can never approve, so it cannot smuggle an endorsement
        # into the chain. Removing doomed work here costs one model run
        # instead of the whole chain.
        #
        # It abstains on any request with no trajectory to simulate, which is
        # most of them. `screened` distinguishes "looked at it and had no
        # objection" from "could not evaluate it" -- both proceed, only one is
        # coverage.
        screen_result = None
        if self.simulation_screen:
            try:
                screen_result = self.simulation_screen.screen_request(context)
            except Exception as e:
                # The screen can only refuse, never approve. A screen that
                # crashed did neither; letting the request through would treat
                # "could not evaluate" as "no objection", so it refuses, and
                # the record says it was a crash, not a verdict.
                logger.warning(
                    "[Orchestrator] Simulation screen did not run to a verdict -- "
                    f"it raised {type(e).__name__}: {e}. Recording REJECTED with "
                    "stage_refused=False."
                )
                if self.raise_on_stage_error:
                    raise
                return self._stamp({
                    "status": "REJECTED",
                    "reason": f"Simulation screen did not run to a verdict: {type(e).__name__}: {e}",
                    "refused_by": None,
                    "stage_refused": False,
                    "stage_error": f"{type(e).__name__}: {e}",
                    "simulation_screen": None,
                    "governance_decision": None,
                    "audit_chain": None,
                }, governance_request)
            if screen_result.screened:
                logger.info(f"[Orchestrator] Phase 1b: simulation screen -- {screen_result.reason}")
            else:
                logger.info(f"[Orchestrator] Phase 1b: simulation screen abstained -- {screen_result.reason}")
            if not screen_result.proceed:
                logger.error("[Orchestrator] Simulation screen refused: halting before PERCEIVE")
                return self._stamp({
                    "status": "REJECTED",
                    "reason": f"Simulation screen refused: {screen_result.reason}",
                    "simulation_screen": screen_result,
                    "governance_decision": None,
                    "audit_chain": None,
                }, governance_request)

        # PHASE 2: PERCEIVE evaluation
        #
        # Goes through the adapter, not straight at the kernel: PERCEIVE
        # speaks PolicyRequest/PolicyVerdict, everything downstream of here
        # speaks GovernanceDecision. The adapter owns that translation (see
        # sentinel_perceive_adapter for why the two vocabularies stay separate).
        logger.info("[Orchestrator] Phase 2: PERCEIVE evaluation (unanimous consensus across applied gates)")
        try:
            perceive_decision = self.sentinel_adapter.evaluate_through_perceive(
                self.perceive,
                governance_request
            )
        except Exception as e:
            # Measured 2026-09-08: with no PERCEIVE configured this phase
            # raised AttributeError out of the orchestrator and left no
            # record at all -- the one stage whose failure was a crash rather
            # than a refusal. Same shape as the other stages now.
            logger.warning(
                "[Orchestrator] PERCEIVE stage did not run to a verdict -- "
                f"it raised {type(e).__name__}: {e}. Recording REJECTED with stage_refused=False."
            )
            if self.raise_on_stage_error:
                raise
            return self._stamp({
                "status": "REJECTED",
                "reason": f"PERCEIVE stage did not run to a verdict: {type(e).__name__}: {e}",
                "refused_by": None,
                "stage_refused": False,
                "stage_error": f"{type(e).__name__}: {e}",
                "governance_decision": None,
                "audit_chain": None,
            }, governance_request)
        logger.info(f"[Orchestrator] PERCEIVE approval: {perceive_decision.approval.value}")
        logger.info(f"[Orchestrator] Applied gates: {perceive_decision.applied_gates}")
        logger.info(f"[Orchestrator] Unanimous: {perceive_decision.unanimous_consensus}")
        if perceive_decision.violations:
            logger.info(f"[Orchestrator] Violations: {perceive_decision.violations}")

        # PHASE 2b: fortress-kernel safety containment (opt-in)
        #
        # Sits between PERCEIVE and the Conservation Kernel: PERCEIVE decides
        # whether the request is *permitted*, FORTRESS decides whether acting
        # on it stays inside safe operating bounds. A request can be
        # legitimately approved and still be refused here, which is the whole
        # reason the layer is separate.
        fortress_result = None
        if self.fortress_adapter:
            logger.info(f"[Orchestrator] Phase 2b: FORTRESS containment ({self.fortress_adapter.controller_mode})")
            fortress_result = self.fortress_adapter.process_governance_decision(
                perceive_decision,
                governance_request,
            )
            logger.info(
                f"[Orchestrator] FORTRESS: {fortress_result.decision} "
                f"(distortion={fortress_result.distortion:.3f}, regime={fortress_result.regime})"
            )
            if fortress_result.decision != "APPROVED":
                # Fail closed. A containment refusal stops the chain here --
                # nothing downstream gets to re-approve what FORTRESS refused.
                logger.error("[Orchestrator] FORTRESS refused: halting before Conservation Kernel")
                return self._stamp({
                    "status": "REJECTED",
                    "reason": (
                        f"FORTRESS containment refused (distortion "
                        f"{fortress_result.distortion:.3f}, regime {fortress_result.regime})"
                    ),
                    "governance_decision": perceive_decision,
                    "fortress_result": fortress_result,
                    "audit_chain": None,
                }, governance_request)

        # PHASE 3: Conservation Kernel verification
        logger.info("[Orchestrator] Phase 3: Conservation Kernel verification")
        try:
            conservation_decision = self.perceive_adapter.verify_perceive_decision(
                perceive_decision,
                governance_request.artifact_id,
                governance_request.artifact_content,
                governance_request.artifact_hash,
                request=governance_request,
            )
            logger.info(f"[Orchestrator] Conservation verified: {conservation_decision.verified}")
        except Exception as e:
            from perceive_conservation_adapter import ConservationRefusal
            refused = isinstance(e, ConservationRefusal)
            if not refused:
                # The stage did not refuse -- it was absent or it crashed. The
                # record below still says REJECTED (unchanged), but now says
                # which of the two happened: from the outside, "the kernel
                # refused" and "there was no kernel" used to be identical.
                logger.warning(
                    "[Orchestrator] Conservation stage did not run to a verdict -- "
                    f"it raised {type(e).__name__}: {e}. Recording REJECTED with "
                    "stage_refused=False. Pass a working conservation_kernel, or "
                    "construct the orchestrator with raise_on_stage_error=True to "
                    "raise instead of filing a stage failure as a rejection."
                )
                if self.raise_on_stage_error:
                    raise
            logger.error(f"[Orchestrator] Conservation Kernel rejected: {e}")
            return self._stamp({
                "status": "REJECTED",
                "reason": f"Conservation Kernel rejected decision: {e}",
                "governance_decision": perceive_decision,
                "refused_by": "conservation" if refused else None,
                "stage_refused": refused,
                "stage_error": None if refused else f"{type(e).__name__}: {e}",
                "conservation_enforced": refused,
                "audit_chain": None,
            }, governance_request)

        # PHASE 4: GSA-815 execution approval
        logger.info("[Orchestrator] Phase 4: GSA-815 execution approval")
        try:
            execution_approval = self.conservation_adapter.approve_execution(
                conservation_decision,
                governance_request.artifact_id,
                governance_request.artifact_hash,
                sentinel_artifact.metadata.origin_status.value if hasattr(sentinel_artifact.metadata.origin_status, 'value') else "Sentinel",
                governance_request.lineage,
                # A caller replaying a canonical scenario fixes the execution
                # id so every commitment downstream is reproducible. Absent,
                # the adapter mints one.
                execution_id=context.get("execution_id"),
            )
            execution_context = self.conservation_adapter.create_execution_context(
                execution_approval,
                governance_request.artifact_id,
                governance_request.artifact_hash,
                "GSA-815",
                governance_request.lineage
            )
            # Issue it. A context that is not in the ledger is one nobody
            # may execute; an execution id already issued is a replay and is
            # refused here, before anything downstream can act on it.
            self.execution_ledger.issue(execution_context)
            logger.info("[Orchestrator] GSA-815 execution approved and issued")
        except Exception as e:
            from conservation_gsa815_adapter import ExecutionRefusal
            from governance_contracts import GovernanceApproval
            refused = isinstance(e, (ExecutionRefusal, LedgerRefusal))
            if isinstance(e, LedgerRefusal):
                refused_by = "execution_ledger"
            elif refused:
                # The approval gate refuses when the decision it was handed is
                # not an approval -- and that decision is PERCEIVE's. A
                # PERCEIVE refusal reaches this point with the Conservation
                # Kernel having verified a *rejected* decision, and used to
                # be recorded here in the approval gate's words. Name the
                # stage that actually said no.
                refused_by = (
                    "perceive"
                    if perceive_decision.approval is not GovernanceApproval.APPROVED
                    else "execution_approval"
                )
            else:
                refused_by = None
                logger.warning(
                    "[Orchestrator] Execution-approval stage did not run to a verdict -- "
                    f"it raised {type(e).__name__}: {e}. Recording REJECTED with "
                    "stage_refused=False. Construct the orchestrator with "
                    "raise_on_stage_error=True to raise instead."
                )
                if self.raise_on_stage_error:
                    raise
            logger.error(f"[Orchestrator] Execution rejected: {e}")
            return self._stamp({
                "status": "REJECTED",
                "reason": str(e),
                "governance_decision": perceive_decision,
                "conservation_decision": conservation_decision,
                "refused_by": refused_by,
                "stage_refused": refused,
                "stage_error": None if refused else f"{type(e).__name__}: {e}",
                "conservation_enforced": True,
                "audit_chain": None,
            }, governance_request)

        # PHASE 5: Execute GSA-815 operation
        #
        # Scope check first. An artifact can be legitimately admitted, permitted
        # and verified and still not be a thing anyone is allowed to *run* --
        # Governance Gateway distinguishes READ_ONLY from EXECUTE scope, and
        # until now the orchestrator executed on approval without ever asking.
        # An artifact admitted for reading could be executed.
        #
        # Enforced here rather than only at the door so a caller that bypasses
        # the admission adapter still cannot execute a read-only artifact. The
        # check is skipped entirely when no scope was declared: callers that
        # predate the Gateway seam pass no scope and must keep working.
        declared_scope = (context or {}).get("gateway_scope")

        # A declared scope is only as good as the seal behind it. The Gateway
        # seals scope into the artifact's integrity digest; the admission
        # adapter passes that artifact along. A scope with no admission is a
        # claim; a scope whose admission does not recompute, or names another
        # scope or another artifact, is tamper evidence and is refused in
        # every mode. A bare claim is refused in strict mode and recorded
        # (`scope_sealed: False`) otherwise.
        scope_sealed = None
        if declared_scope is not None:
            admission = gateway_admission
            if admission is None:
                scope_sealed = False
                if self.require_declared_scope:
                    logger.error("[Orchestrator] Refusing execution: scope declared without a sealed Gateway admission")
                    return self._stamp({
                        "status": "REJECTED",
                        "reason": f"scope {declared_scope} declared without a sealed Gateway admission",
                        "governance_decision": perceive_decision,
                        "conservation_decision": conservation_decision,
                        "execution_approval": execution_approval,
                        "scope_enforced": True, "scope_sealed": False,
                        "audit_chain": None,
                    }, governance_request)
                logger.warning("[Orchestrator] Scope declared without a sealed Gateway admission; honouring it as a CLAIM (scope_sealed=False)")
            else:
                problems = self._admission_problems(admission, declared_scope, governance_request.artifact_id)
                scope_sealed = not problems
                if problems:
                    logger.error(f"[Orchestrator] Refusing execution: Gateway admission does not back the declared scope: {problems}")
                    return self._stamp({
                        "status": "REJECTED",
                        "reason": "Gateway admission does not back the declared scope: " + "; ".join(problems),
                        "governance_decision": perceive_decision,
                        "conservation_decision": conservation_decision,
                        "execution_approval": execution_approval,
                        "scope_enforced": True, "scope_sealed": False,
                        "audit_chain": None,
                    }, governance_request)

        # An undeclared scope used to fall straight through here, silently.
        # That is the worse half of the two ways this can be wrong: a request
        # with a READ_ONLY scope is refused loudly, while a request with no
        # scope at all is executed with no record that the check did nothing.
        # From the outside those two approvals are indistinguishable.
        if declared_scope is None:
            if self.require_declared_scope:
                logger.error(
                    "[Orchestrator] Refusing execution: no gateway scope declared "
                    "and require_declared_scope is on"
                )
                return self._stamp({
                    "status": "REJECTED",
                    "reason": "no gateway scope declared; execution requires one",
                    "governance_decision": perceive_decision,
                    "conservation_decision": conservation_decision,
                    "execution_approval": execution_approval,
                    "scope_enforced": False,
                    "audit_chain": None,
                }, governance_request)
            logger.warning(
                "[Orchestrator] No gateway scope declared -- executing WITHOUT a "
                "scope check. Pass context['gateway_scope'], or construct the "
                "orchestrator with require_declared_scope=True to refuse instead."
            )

        if declared_scope is not None and declared_scope != "EXECUTE":
            logger.error(
                f"[Orchestrator] Refusing execution: artifact scope is {declared_scope}, not EXECUTE"
            )
            return self._stamp({
                "status": "REJECTED",
                "reason": (
                    f"artifact scope {declared_scope} does not permit execution"
                ),
                "governance_decision": perceive_decision,
                "conservation_decision": conservation_decision,
                "execution_approval": execution_approval,
                "scope_enforced": True,
                "audit_chain": None,
            }, governance_request)

        # OBSERVE (Phase 6) runs only when vitals are supplied. Like the
        # scope check above, the skip used to be a log line and nothing else:
        # an APPROVED_AND_EXECUTED record with observe_verdict=None looked the
        # same whether OBSERVE was skipped or never existed. Decided here,
        # before execution, so strict mode refuses before anything runs.
        if vitals_snapshot is None:
            if self.require_vitals:
                logger.error(
                    "[Orchestrator] Refusing execution: no vitals supplied and require_vitals is on"
                )
                return self._stamp({
                    "status": "REJECTED",
                    "reason": "no vitals supplied; OBSERVE monitoring requires them",
                    "governance_decision": perceive_decision,
                    "conservation_decision": conservation_decision,
                    "execution_approval": execution_approval,
                    "scope_enforced": declared_scope is not None,
                    "observe_enforced": False,
                    "audit_chain": None,
                }, governance_request)
            logger.warning(
                "[Orchestrator] No vitals supplied -- OBSERVE will NOT run and no "
                "forensic proof will be produced. Pass vitals_snapshot, or construct "
                "the orchestrator with require_vitals=True to refuse instead."
            )

        logger.info("[Orchestrator] Phase 5: GSA-815 execution")
        executed_at = {"started": datetime.now(timezone.utc).isoformat(), "finished": None}
        try:
            gsa815_result = gsa815_operation_func(execution_context)
        except Exception as e:
            executed_at["finished"] = datetime.now(timezone.utc).isoformat()
            logger.error(f"[Orchestrator] Execution FAILED: {type(e).__name__}: {e}")
            if self.raise_on_execution_error:
                raise
            # Everything that led here is kept: the action was approved and
            # attempted, and the record must show both, or a failed action
            # is indistinguishable from one that was never approved. The
            # context is consumed either way: a failed attempt does not
            # leave an approval that can be tried again.
            execution_authorization = self._consume(execution_context, None, "failed")
            outcome_context = self.gsa815_adapter.create_outcome_context(
                execution_context, None, None, execution_status="failed")
            failed = self._stamp({
                "status": "EXECUTION_FAILED",
                "reason": f"execution raised {type(e).__name__}: {e}",
                "execution_status": "failed",
                "execution_error": f"{type(e).__name__}: {e}",
                "executed_at": executed_at,
                "scope_enforced": declared_scope is not None,
                "scope_sealed": scope_sealed,
                "declared_scope": declared_scope,
                "governance_decision": perceive_decision,
                "simulation_screen": screen_result,
                "fortress_result": fortress_result,
                "conservation_decision": conservation_decision,
                "execution_approval": execution_approval,
                "execution_context": execution_context,
                "execution_authorization": execution_authorization,
                "gsa815_result": None,
                "observe_verdict": None,
                "observe_enforced": False,
                "outcome_context": outcome_context,
                "audit_chain": None,
                "audit_chain_valid": False,
            }, governance_request)
            return self._verify_on_the_way_out(failed, None)
        executed_at["finished"] = datetime.now(timezone.utc).isoformat()
        # The callable may report a partial execution explicitly by returning
        # a mapping with status "partial". Anything else that returned is
        # "completed" from the chain's point of view; the chain does not guess.
        execution_status = (
            "partial"
            if isinstance(gsa815_result, dict) and str(gsa815_result.get("status", "")).lower() == "partial"
            else "completed"
        )
        logger.info(f"[Orchestrator] GSA-815 result ({execution_status}): {gsa815_result}")
        execution_authorization = self._consume(execution_context, gsa815_result, execution_status)

        # PHASE 6: OBSERVE monitoring
        logger.info("[Orchestrator] Phase 6: OBSERVE clinical monitoring")
        observe_verdict = None
        observe_error = None
        if vitals_snapshot:
            # The action has already run. Whatever happens here must not lose
            # the record of it, so an OBSERVE failure is recorded beside the
            # execution rather than raised over it.
            try:
                if self.observe_engine is None:
                    raise RuntimeError("no OBSERVE engine configured")
                observe_verdict = self.observe_engine.evaluate(vitals_snapshot)
                logger.info(f"[Orchestrator] OBSERVE verdict: regime={observe_verdict.regime.value}")
            except Exception as e:
                observe_error = f"{type(e).__name__}: {e}"
                logger.error(f"[Orchestrator] OBSERVE did not produce a verdict: {observe_error}")
        else:
            logger.info("[Orchestrator] No vitals provided, OBSERVE evaluation skipped (warned above)")

        # PHASE 7: Link audit chains
        #
        # The outcome is created whether or not OBSERVE ran: the execution's
        # result is committed either way. The forensic proof, which asserts
        # an observed outcome, exists only when there was one.
        logger.info("[Orchestrator] Phase 7: Linking audit chains")
        outcome_context = self.gsa815_adapter.create_outcome_context(
            execution_context,
            gsa815_result,
            observe_verdict,
            execution_status=execution_status,
        )
        forensic_proof = self.gsa815_adapter.create_forensic_proof(outcome_context) if observe_verdict else None

        # Return complete result
        logger.info("[Orchestrator] Governance flow complete")
        result = self._stamp({
            "status": "APPROVED_AND_EXECUTED",
            "scope_enforced": declared_scope is not None,
            "scope_sealed": scope_sealed,
            "declared_scope": declared_scope,
            "gateway_admission": admission_summary,
            "observe_enforced": observe_verdict is not None,
            "observe_error": observe_error,
            "execution_status": execution_status,
            "executed_at": executed_at,
            "advisory_violations": list(getattr(perceive_decision, "advisory_violations", None) or []),
            "governance_decision": perceive_decision,
            "simulation_screen": screen_result,
            "fortress_result": fortress_result,
            "conservation_decision": conservation_decision,
            "execution_approval": execution_approval,
            "execution_context": execution_context,
            "execution_authorization": execution_authorization,
            "gsa815_result": gsa815_result,
            "observe_verdict": observe_verdict,
            "outcome_context": outcome_context,
            "forensic_proof": forensic_proof,
            # Every refusal path carries `audit_chain`; the success path used
            # not to, so the one key a reader could count on was missing from
            # the one record that mattered. It is the forensic proof, or None
            # when OBSERVE did not run and there is no outcome to prove.
            "audit_chain": forensic_proof,
            "audit_chain_valid": False,
        }, governance_request)
        return self._verify_on_the_way_out(result, forensic_proof)
