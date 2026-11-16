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

from collections.abc import Mapping
import logging
import math
import threading
from datetime import datetime, timezone

from governance_contracts import CONTRACT_VERSION
from governance_chain import verify_result
from human_escalation import AlertOutcome
from log_redaction import patient_tag, safe_error
from execution_guard import ExecutionLedger, ExecutionRefusal as LedgerRefusal, guard_ledger_of

logger = logging.getLogger("GovernanceOrchestrator")


class IdentityMismatch(ValueError):
    """A request's own statements disagree about who it is about (or which reading justified it)."""


class GovernanceOrchestrator:
    """Central orchestrator for unified governance flow."""

    def __init__(self, perceive=None, conservation_kernel=None, observe_engine=None,
                 fortress_controller: str = None,
                 require_declared_scope: bool = False,
                 require_vitals: bool = False,
                 raise_on_stage_error: bool = False,
                 raise_on_execution_error: bool = False,
                 execution_ledger: "ExecutionLedger | None" = None,
                 receipts=None,
                 profile: str = "advisory",
                 guard_executor: bool = True,
                 source_signers=None,
                 require_attested_event_time: bool = False,
                 enforce_advisory_violations: bool = False,
                 root_admission=None,
                 alert_human=None,
                 escalations=None,
                 clinical_notification=None,
                 sweep_runner=None,
                 attempt_id_factory=None,
                 clinical_settings=None):
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
            root_admission: the `root_admission.RootAdmission` the Conservation
                Kernel was built with as its `root_guard`. With it, a request's
                root is registered only after a CNS roster approves that
                document. Needs the `cns` extra. Without it a kernel that has
                a guard refuses every root.
            alert_human: optional callable, called with a dict (stage,
                stage_error, request_id, artifact_id) when a stage RAISES
                instead of reaching a verdict, or when the caller's executor
                raises after approval (stage "execution"), or when OBSERVE
                raises in the strict profile (stage "observe"), so a person hears
                about a crash and not only a log reader. It must reach a
                human (page, dashboard, phone). Ordinary refusals and
                successes never call it. The record of such a failure carries
                `human_alert` ("delivered", "failed" or "no_channel"); a
                failing or missing channel is logged at CRITICAL and never
                suppresses or alters the outcome. With raise_on_execution_error
                or raise_on_stage_error the person is alerted before the
                re-raise. It is also called when the receipt of a result
                cannot be appended after the action ran (stage
                "receipt_append"); the result then carries `human_alert`, or
                `receipt_human_alert` when a stage-error alert is already on
                it, and its outcome is unchanged. Not required.
            escalations: optional `human_escalation.EscalationDesk`. Replaces
                `alert_human` (give one or the other, never both): every event
                that would call `alert_human` opens a ticket, dispatched tier
                by tier, which stays open until a named responder signs an
                acknowledgement. The record gains `escalation_ticket`.
                `human_alert` becomes "delivered" only when the channel returned
                its own delivery receipt, "dispatched" when a callable merely
                returned, "failed" when nobody could be reached; acknowledgement
                lives on the ticket, never in this field. Required by profile
                "production". See docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md.
            attempt_id_factory: optional callable returning a fresh id per `orchestrate_request` call (default: a random
                UUID hex). The id is on the result (`attempt_id`), the receipt envelope, and every alert and ticket.
            sweep_runner: optional `escalation_runner.SweepRunner` bound to the same `escalations` desk. It is what calls
                `sweep()` and alarms when that stops; the orchestrator only holds it so profile "production" can refuse
                to start without a running one. Start it before building the orchestrator.
            clinical_notification: optional `clinical_notification.ClinicalNotificationSettings`,
                used only with `escalations`. When OBSERVE reports an escalation EVENT on a
                request and the settings say a person must be told (default: a new
                escalation at CRITICAL, a severity increase, an unassessable reading), a
                ticket is opened and the record gains `clinical_notification` (reason,
                status, ticket). Giving settings without `escalations` is refused. A
                request refused before OBSERVE ran has no verdict and no notification.
        """
        # 1.3.0 profile. "advisory" is the historical default: every check
        # runs and records, only refusals refuse. "strict" turns every
        # advisory check into a refusal: a scope must be sealed, vitals must
        # be supplied, the event time must be attested by a registered source
        # key, and violations PERCEIVE only advised on are enforced here.
        # Whichever profile produced a record is named on the record.
        if profile not in ("advisory", "strict", "production"):
            raise ValueError(f"unknown profile {profile!r}; use 'advisory', 'strict' or 'production'")
        self.profile = profile
        # "production" is strict plus a startup proof that the controls it claims are the controls wired
        # (production_profile.py). It enforces everything strict does.
        strict = profile in ("strict", "production")
        # Strict is the protected profile. A kernel with no root guard registers any root it admits, so the
        # roster that decides what may become ancestry would be advisory. Refuse it here, not at the first root.
        if strict and conservation_kernel is not None and not getattr(conservation_kernel, "has_root_guard", False):
            raise ValueError("the strict profile needs a Conservation Kernel built with a root guard; this one "
                             "would register any root it admits (use profile='advisory' for an unguarded kernel)")
        # The executor guard is strict's pre-action authorization. Without it the orchestrator can only consume
        # the context after the action has run. Advisory may still turn it off, for callers that guard elsewhere.
        if strict and not guard_executor:
            raise ValueError("the strict profile needs guard_executor=True: the executor guard is what checks the "
                             "execution context before the action runs (use profile='advisory' if you guard elsewhere)")
        self.require_declared_scope = require_declared_scope or strict
        self.require_vitals = require_vitals or strict
        self.require_attested_event_time = require_attested_event_time or strict
        self.enforce_advisory_violations = enforce_advisory_violations or strict
        # The executor is wrapped with execution_guard.guarded by the
        # orchestrator itself unless the caller already did, so an executor
        # that ignores the context still runs only an issued, unaltered,
        # unused one. Off only for callers that guard elsewhere.
        self.guard_executor = guard_executor
        # Keys of the sources whose event-time attestations count:
        # {key_id: Signer} or an iterable of Signers.
        self.source_signers = source_signers
        self.raise_on_stage_error = raise_on_stage_error
        if alert_human is not None and escalations is not None:
            raise ValueError("give alert_human or escalations, not both: there must be no doubt which channel tells a person")
        self.alert_human = alert_human
        if escalations is not None and not callable(getattr(escalations, "raise_and_dispatch", None)):
            raise ValueError(f"escalations must be an EscalationDesk, not {type(escalations).__name__}")
        self.escalations = escalations
        self.sweep_runner = sweep_runner
        # One id per call to `orchestrate_request`, whatever the artifact: `request_id` is the artifact's id, so a refused
        # attempt and its retry share it. The attempt id tells them apart on the result, the receipt and every alert and
        # ticket. A callable so a test can make it deterministic.
        import uuid
        self._new_attempt_id = attempt_id_factory or (lambda: uuid.uuid4().hex)
        # The latest OBSERVE verdict made on this thread during `_orchestrate`: if the pipeline raises (the strict
        # raise_on_* options), the escalation EVENT in that verdict is already spent and must still reach a person.
        self._seen = threading.local()
        if clinical_notification is not None and escalations is None:
            raise ValueError("clinical_notification settings need an escalation desk to notify through")
        from clinical_notification import ClinicalNotificationSettings
        self.clinical_notification = clinical_notification or ClinicalNotificationSettings()
        settings_problems = self.clinical_notification.problems()
        if settings_problems:
            raise ValueError("clinical_notification settings refused: " + "; ".join(settings_problems))
        if escalations is not None and self.clinical_notification.silent:
            logger.critical("[Orchestrator] every clinical notification is switched off: no escalation event will "
                            "reach a person")
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
        # The ClinicalSettings the deployment attests it runs (the production profile requires it and checks the engine against
        # it); its digest is recorded in every handoff.
        self.clinical_settings = clinical_settings
        if profile == "production":
            # Fail closed at construction: an orchestrator that cannot prove its wiring never starts.
            from production_profile import ProductionProfileError, production_problems
            problems = production_problems(
                conservation_kernel=conservation_kernel, root_admission=root_admission,
                observe_engine=observe_engine, receipts=receipts, execution_ledger=self.execution_ledger,
                alert_human=alert_human, escalations=escalations, source_signers=source_signers,
                guard_executor=guard_executor, sweep_runner=sweep_runner, clinical_settings=clinical_settings)
            if problems:
                raise ProductionProfileError(problems)

        # Lazy imports for adapters
        from sentinel_perceive_adapter import SentinelPerceiveAdapter
        from perceive_conservation_adapter import PerceiveConservationAdapter
        from conservation_gsa815_adapter import ConservationGSA815Adapter
        from gsa815_observe_adapter import GSA815ObserveAdapter

        self.sentinel_adapter = SentinelPerceiveAdapter()
        self.perceive_adapter = PerceiveConservationAdapter(conservation_kernel, root_admission) if conservation_kernel else None
        self.conservation_adapter = ConservationGSA815Adapter()
        self.gsa815_adapter = GSA815ObserveAdapter()

        # fortress-kernel is opt-in. Imported only when asked for, so the rest of the
        # chain does not acquire a hard dependency on a sibling checkout.
        self.fortress_adapter = None
        if fortress_controller:
            from fortress_perceive_adapter import FortressPerceiveAdapter
            self.fortress_adapter = FortressPerceiveAdapter(controller_mode=fortress_controller)

    def _verify_on_the_way_out(self, result: dict, forensic_proof) -> dict:
        """Recompute the whole chain before handing the record over.

        `audit_chain_valid` used to mean "six fields are non-empty". It now
        means every commitment link recomputes, the artifact hash matches the
        content, every id agrees across records, time runs forward, and the
        decision is in both kernels' ledgers -- AND the chain reached an
        outcome. The per-check detail travels in `chain_verification`.
        """
        verification = verify_result(result, kernel=self.conservation_kernel, perceive=self.perceive,
                                     observe=self.observe_engine)
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

    def _consume(self, execution_context, attached, execution_status: str) -> dict:
        """Mark the issued context consumed, once.

        A guarded executor consumes it at the moment of authorization and
        attaches the authorization to its result; the orchestrator then only
        records what happened. An unguarded executor leaves consumption to
        the orchestrator, after the fact, so the ledger still shows one use
        per issuance, and the record says which of the two it was.
        """
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
    def _observe_problems(verdict) -> list:
        """Why an OBSERVE verdict cannot back an action in the strict profile; empty if it can."""
        problems = []
        if getattr(verdict, "unassessable", True) is not False:
            faults = list(getattr(verdict, "validation_faults", None) or [])
            problems.append("the observation is unassessable" + (f" ({'; '.join(map(str, faults))})" if faults else ""))
        # OP-3: a verdict nothing assessed is not evidence, whatever regime it names. No engine ran, or the engines
        # that ran contributed no confidence (every one abstained), so a "stable" here is a default, not a finding.
        engines = list(getattr(verdict, "active_engines", None) or [])
        confidence = getattr(verdict, "confidence", None)
        if not engines:
            problems.append("the verdict rests on no evidence: no OBSERVE engine assessed the reading")
        elif not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or confidence <= 0:
            problems.append(f"the verdict rests on no evidence: its confidence is {confidence!r}, so no engine "
                            "that ran assessed the reading")
        return problems

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
        problem = getattr(request, "event_time_attestation_problem", None)
        if problem:
            anomalies.append(f"event_time attestation rejected: {problem}")
        return anomalies

    @staticmethod
    def _bind_identity(context, vitals_snapshot):
        """Return (context_copy, identity, mismatch).

        The patient is the one the context names in `patient_id`, written back as text. PERCEIVE already gives `patient_id`
        precedence over `subject_id`, and five adapters put a non-patient entity (an approval target, a task, a source) in
        `subject_id`, so `subject_id` is never compared with the vitals. With NO patient named, and no `subject_id` either, the
        vitals' patient is written into the (copied) context so the request commitment covers it and PERCEIVE's per-subject
        limits accumulate per patient. The context's `patient_id` must equal the vitals' patient, and a caller-supplied
        `observation_digest` must be the digest of the vitals actually supplied (compared without case or surrounding space).
        Any disagreement, or a context that is not a mapping, is a mismatch. The caller's own dict is left as it was given.
        Comparison is exact text: "p001" and "P001" are different patients, and 5 and "5" are the same."""
        from governance_chain import observation_digest
        try:
            context = dict(context)
        except Exception as exc:  # noqa: BLE001 - fail closed, and say why
            return {}, {"patient_id": None, "subject_source": "artifact", "observation_digest": None,
                        "observation_patient": None}, {
                "problems": [f"the context is not a mapping ({type(exc).__name__}: {exc})"], "context": {},
                "vitals_patient": None}
        problems = []
        try:
            if vitals_snapshot is None:
                vitals_patient = None
            elif isinstance(vitals_snapshot, Mapping):
                vitals_patient = vitals_snapshot.get("patient_id")
            else:
                vitals_patient = getattr(vitals_snapshot, "patient_id", None)
            vitals_patient = None if vitals_patient in (None, "") else str(vitals_patient)
        except Exception as exc:  # noqa: BLE001 - a patient that cannot be read cannot be shown to match
            vitals_patient = None
            problems.append(f"the patient of the vitals could not be read ({type(exc).__name__})")
        try:
            named = context.get("patient_id")
            claimed = {} if named in (None, "") else {"patient_id": str(named)}
        except Exception as exc:  # noqa: BLE001
            claimed = {}
            problems.append(f"the context's patient_id could not be read ({type(exc).__name__})")
        if vitals_patient is not None and claimed and claimed["patient_id"] != vitals_patient:
            problems.append(f"context patient_id {claimed['patient_id']!r} is not the patient the vitals are for "
                            f"({vitals_patient!r})")
        digest = observation_digest(vitals_snapshot)
        if digest is not None:
            given = context.get("observation_digest")
            if given is not None and str(given).strip().lower() != digest.lower():
                problems.append("the context's observation_digest is not the digest of the vitals supplied")
            context["observation_digest"] = digest
        has_entity = context.get("subject_id") not in (None, "")
        if claimed:
            context["patient_id"] = claimed["patient_id"]          # text, so PERCEIVE's subject and the limits agree
            source, patient = "context", claimed["patient_id"]
        elif has_entity:
            source, patient = "context", None                      # a named entity; PERCEIVE reasons about it, not a patient
        elif vitals_patient is not None:
            context["patient_id"] = vitals_patient
            source, patient = "vitals", vitals_patient
        else:
            source, patient = "artifact", None
        identity = {"patient_id": patient, "subject_source": source, "observation_digest": digest,
                    "observation_patient": vitals_patient}
        mismatch = {"problems": problems, "context": claimed, "vitals_patient": vitals_patient} if problems else None
        return context, identity, mismatch

    def _observe_first(self, vitals_snapshot):
        """Assess the reading BEFORE anything is asked of PERCEIVE, in every profile. Returns (verdict, error).

        The patient's physiology does not depend on the action's fate, so the assessment is made once, first, and every
        later stage reads it: a request refused at any stage still has its assessment, and a person is still told about
        an escalation event it reports. A crash is not raised here: the policy for it differs by profile (strict
        refuses, advisory proceeds and tells a person) and needs the request to exist, so it is returned."""
        if vitals_snapshot is None:
            return None, None
        try:
            if self.observe_engine is None:
                raise RuntimeError("no OBSERVE engine configured")
            verdict = self.observe_engine.evaluate(vitals_snapshot)
        except Exception as exc:  # noqa: BLE001 - the profile decides what a crash means
            self._seen.observe_error = f"{type(exc).__name__}: {exc}"
            return None, exc
        self._seen.verdict = verdict
        return verdict, None

    @staticmethod
    def _assessment_binding(verdict, error) -> dict:
        """What the request commits to about the assessment: which one (its reproducible fingerprint and its chained state
        commitment), what it said, or that there is none. Plain data, so the request commitment, the PERCEIVE ruling that
        carries it and the approvals chained to that ruling all cover the assessment and not only the reading."""
        if verdict is None:
            return {"status": "unavailable", "error_type": type(error).__name__ if error is not None else None}
        return {"status": "assessed", "decision_fingerprint": getattr(verdict, "decision_fingerprint", ""),
                "state_commitment": getattr(verdict, "state_commitment", ""),
                "regime": getattr(getattr(verdict, "regime", None), "value", None),
                "escalation_required": bool(getattr(verdict, "escalation_required", False)),
                "unassessable": bool(getattr(verdict, "unassessable", False))}

    def _identity_refusal(self, mismatch, governance_request, vitals_snapshot, observe_exc=None) -> dict:
        """Refuse a request whose own statements disagree, before PERCEIVE is asked, in every profile.

        It is not an ordinary refusal: it means an integration is sending one patient's data with another's request, so a
        person is told. And the reading is still a reading of a real patient: it is assessed and, if it is an escalation, paged
        for the patient the VITALS are for, so a deteriorating child is not lost with the refused request. The request is built
        first, so the record names the artifact and can be audited."""
        problems = "; ".join(mismatch["problems"])
        logger.error("[Orchestrator] Refusing: identity mismatch (context patient %s, vitals patient %s, %d problem(s))",
                     patient_tag((mismatch.get("context") or {}).get("patient_id")), patient_tag(mismatch.get("vitals_patient")),
                     len(mismatch.get("problems") or []))
        vitals_patient = mismatch.get("vitals_patient")
        alert = self._alert_stage_error("identity", IdentityMismatch(problems), governance_request, patient_id=vitals_patient)
        result = {
            "status": "REJECTED",
            "reason": "identity mismatch: " + problems,
            "refused_by": "identity",
            "stage_refused": True,
            "identity_mismatch": mismatch,
            **self._alert_field(alert),
            "governance_decision": None,
            "audit_chain": None,
        }
        # The reading was assessed first (`_observe_first`); `_stamp` puts the verdict, or the error, on the record. A crash
        # there is told like any other OBSERVE crash.
        if observe_exc is not None:
            self._seen.observe_alert = self._alert_stage_error("observe", observe_exc, governance_request,
                                                               patient_id=vitals_patient)
        return self._stamp(result, governance_request)

    @staticmethod
    def _patient_context(stage: str, patient_id) -> dict:
        """The patient an alert is about, when the stage knows one. An OBSERVE crash is named as what it is for the
        patient (nobody assessed them), in the same words the clinical notification uses."""
        if patient_id is None:
            return {}
        out = {"patient_id": str(patient_id)}
        reason = {"observe": "assessment_unavailable", "identity": "identity_mismatch"}.get(stage)
        if reason is not None:
            out["reason"] = reason
        return out

    @staticmethod
    def _log_error(error: BaseException, governance_request=None) -> str:
        """An exception as a log line may say it: the request's patient is a tag, not an identifier."""
        context = getattr(governance_request, "context", None)
        return safe_error(error, context.get("patient_id") if isinstance(context, dict) else None)

    def _alert_stage_error(self, stage: str, error: Exception, governance_request, patient_id=None) -> str:
        """Tell a person that a stage raised instead of reaching a verdict.

        Returns "delivered", "failed" or "no_channel". Never raises: the
        rejection (or the re-raise under raise_on_stage_error) happens
        whatever this does.
        """
        request_id = getattr(governance_request, "request_id", None)
        # What the LOG may say about the error: the patients' identifiers (the one in hand and the request's own) are tags. The ticket and the alert, which go to a person, still carry the full text.
        context_patient = (getattr(governance_request, "context", None) or {}).get("patient_id") \
            if isinstance(getattr(governance_request, "context", None), dict) else None
        ids = (patient_id, context_patient)
        shown = safe_error(error, *ids)
        if self.escalations is not None:
            try:
                ticket_id, state = self.escalations.raise_and_dispatch(
                    kind="stage_error", request_id=request_id,
                    artifact_id=getattr(governance_request, "artifact_id", None),
                    context={"stage": stage, "stage_error": f"{type(error).__name__}: {error}",
                             "attempt_id": getattr(self._seen, "attempt_id", None),
                             **self._patient_context(stage, patient_id)})
            except Exception as desk_error:  # noqa: BLE001 - never alter the rejection
                logger.critical(
                    f"[Orchestrator] HUMAN ALERT FAILED: {stage} stage raised {shown} "
                    f"on request {request_id}; the escalation desk raised {safe_error(desk_error, *ids)}."
                )
                return AlertOutcome("failed")
            # "delivered" only when the channel itself returned a delivery receipt; "dispatched" when a callable
            # merely returned; "failed" when nobody could be reached. Acknowledgement is on the ticket, not here.
            return AlertOutcome({"exhausted": "failed", "delivered": "delivered"}.get(state, "dispatched"), ticket_id)
        if self.alert_human is None:
            logger.critical(
                f"[Orchestrator] HUMAN ALERT NOT SENT: {stage} stage raised {shown} "
                f"on request {request_id}; no alert_human channel is configured."
            )
            return "no_channel"
        try:
            self.alert_human({
                "stage": stage,
                "stage_error": f"{type(error).__name__}: {error}",
                "request_id": request_id,
                "artifact_id": getattr(governance_request, "artifact_id", None),
                "attempt_id": getattr(self._seen, "attempt_id", None),
                **self._patient_context(stage, patient_id),
            })
        except Exception as alert_error:  # noqa: BLE001 - never alter the rejection
            logger.critical(
                f"[Orchestrator] HUMAN ALERT FAILED: {stage} stage raised {shown} "
                f"on request {request_id}; the alert channel raised {safe_error(alert_error, *ids)}."
            )
            return "failed"
        return "delivered"

    @staticmethod
    def _alert_field(human_alert):
        """The record field for a stage error. Other records never get it."""
        if human_alert is None:
            return {}
        ticket = getattr(human_alert, "ticket_id", None)
        return {"human_alert": human_alert, **({"escalation_ticket": ticket} if ticket else {})}

    def _stamp(self, result: dict, governance_request=None) -> dict:
        """Attach the handoff record and the request to every result.

        A downstream reader must be able to tell what it received, from whom,
        under what contract version and authority, and with which strict
        flags in force -- without reaching back into this process. And the
        request must travel with the record: a decision without the request
        it decided cannot be reconstructed from the record alone.
        """
        result.setdefault("governance_request", governance_request)
        result.setdefault("attempt_id", getattr(self._seen, "attempt_id", None))
        if getattr(self._seen, "identity", None) is not None:
            result.setdefault("identity", self._seen.identity)
        # OBSERVE runs first, so a request refused at any later stage still carries the assessment that was made (and
        # the failure to make one), instead of looking as though nobody had looked at the patient.
        if getattr(self._seen, "verdict", None) is not None:
            result.setdefault("observe_verdict", self._seen.verdict)
        if getattr(self._seen, "observe_error", None) is not None:
            result.setdefault("observe_error", self._seen.observe_error)
        # Whether a person was told about that failure is on every record too, under its own key: a record's own stage
        # alert (an execution that raised, say) must not displace it.
        told = getattr(self._seen, "observe_alert", None)
        if told is not None:
            result.setdefault("observe_human_alert", told)
            if getattr(told, "ticket_id", None):
                result.setdefault("observe_escalation_ticket", told.ticket_id)
        result.setdefault("temporal_anomalies",
                          self._temporal_anomalies(governance_request) if governance_request is not None else [])
        handoff = {
            "producer": "observe-perceive.GovernanceOrchestrator",
            "contract_version": CONTRACT_VERSION,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "authority": getattr(governance_request, "authority", None),
            "epistemic_status": getattr(governance_request, "epistemic_status", None),
            "origin": getattr(governance_request, "origin", None),
            "request_state_commitment": getattr(governance_request, "state_commitment", None),
            "profile": self.profile,
            "strict": {
                "require_declared_scope": self.require_declared_scope,
                "require_vitals": self.require_vitals,
                "require_attested_event_time": self.require_attested_event_time,
                "enforce_advisory_violations": self.enforce_advisory_violations,
                "guard_executor": self.guard_executor,
                "raise_on_stage_error": self.raise_on_stage_error,
                "raise_on_execution_error": self.raise_on_execution_error,
            },
            "execution_ledger": str(self.execution_ledger.path) if self.execution_ledger.path else "in-memory",
            "execution_ledger_signed": self.execution_ledger.signer is not None,
            "receipts": str(getattr(self.receipts, "path", None)) if self.receipts is not None else None,
            "receipts_signed": getattr(self.receipts, "signer", None) is not None,
        }
        if self.clinical_settings is not None:      # present only then, so a record without it keeps its shape
            handoff["clinical_settings_digest"] = self.clinical_settings.digest()
        result.setdefault("handoff", handoff)
        return result

    def _clinical_notice(self, verdict, vitals_snapshot, request, record_status):
        """Open a ticket when OBSERVE reported an escalation event the settings say a person must be told about.
        Returns {reason, status, ticket} or None. Never raises and never changes the outcome: telling a person is
        added to the record, not a gate on it."""
        try:
            if verdict is None or not getattr(verdict, "escalation_required", False):
                return None
            from clinical_notification import notification_reason, notify, ticket_context
            unassessable = bool(getattr(verdict, "unassessable", False))
            reason = notification_reason(
                self.clinical_notification, regime=verdict.regime.value, escalation_required=True,
                escalation_kind=getattr(verdict, "escalation_kind", ""), unassessable=unassessable)
            if reason is None:
                return None
            context = ticket_context(
                reason=reason, patient_id=getattr(vitals_snapshot, "patient_id", None),
                regime=verdict.regime.value, escalation_kind=getattr(verdict, "escalation_kind", ""),
                previous_regime=getattr(verdict, "previous_regime", ""), risk_score=verdict.risk_score,
                triggered_rules=verdict.triggered_rules, unassessable=unassessable,
                observe_audit_hash=verdict.audit_hash, record_status=record_status,
                attempt_id=getattr(self._seen, "attempt_id", None))
            ticket, status = notify(self.escalations, context=context,
                                    request_id=getattr(request, "request_id", None),
                                    artifact_id=getattr(request, "artifact_id", None))
            return {"reason": reason, "status": status, "ticket": ticket}
        except Exception as exc:  # noqa: BLE001 - never alter the outcome
            logger.critical(f"[Orchestrator] CLINICAL NOTIFICATION FAILED: {safe_error(exc, getattr(vitals_snapshot, 'patient_id', None))}")
            return {"reason": None, "status": "failed", "ticket": None}

    def _notify_clinical(self, result: dict, vitals_snapshot) -> None:
        verdict = result.get("observe_verdict") or getattr(self._seen, "verdict", None)
        note = self._clinical_notice(verdict, vitals_snapshot, result.get("governance_request"), result.get("status"))
        if note is not None:
            result["clinical_notification"] = note

    def orchestrate_request(
        self,
        sentinel_artifact,
        operation_type: str,
        gsa815_operation_func,
        vitals_snapshot=None,  # VitalsSnapshot (type annotation removed to avoid import)
        context: dict = None
    ) -> dict:
        """Orchestrate the flow (see `_orchestrate`) and receipt the result.

        The per-call state (the attempt id, the bound identity, the last OBSERVE verdict) is saved and restored around the call:
        an executor that calls this orchestrator again on the same thread must not change what the outer call records."""
        seen = self._seen
        saved = (getattr(seen, "verdict", None), getattr(seen, "attempt_id", None), getattr(seen, "identity", None),
                 getattr(seen, "observe_error", None), getattr(seen, "observe_alert", None))
        try:
            return self._orchestrate_one(sentinel_artifact, operation_type, gsa815_operation_func, vitals_snapshot, context)
        finally:
            seen.verdict, seen.attempt_id, seen.identity, seen.observe_error, seen.observe_alert = saved

    def _orchestrate_one(self, sentinel_artifact, operation_type, gsa815_operation_func, vitals_snapshot, context) -> dict:
        self._seen.verdict = None
        self._seen.attempt_id = self._new_attempt_id()
        self._seen.identity = None
        self._seen.observe_error = None
        self._seen.observe_alert = None
        try:
            result = self._orchestrate(sentinel_artifact, operation_type, gsa815_operation_func, vitals_snapshot,
                                       context)
        except BaseException:
            # raise_on_stage_error / raise_on_execution_error re-raise out of the pipeline. An escalation event OBSERVE
            # already reported is spent (the next reading of this patient is not an event), so tell a person first.
            if self.escalations is not None:
                self._clinical_notice(getattr(self._seen, "verdict", None), vitals_snapshot, None, "raised")
            raise
        if self.escalations is not None:
            self._notify_clinical(result, vitals_snapshot)      # before the receipt, so the receipt records it
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
                # A person hears that the record of what happened was lost.
                # The outcome is untouched: nothing is undone, retried or
                # altered. A record that already carries a stage-error alert
                # keeps it, and this one is recorded beside it.
                alert = self._alert_stage_error("receipt_append", e, result.get("governance_request"))
                result["receipt_human_alert" if "human_alert" in result else "human_alert"] = alert
                if getattr(alert, "ticket_id", None):
                    result["receipt_escalation_ticket" if "escalation_ticket" in result else "escalation_ticket"] = alert.ticket_id
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
        # Who the request is about, and which reading justified it, are decided here and bound into the request (so its
        # commitment covers them). A request whose own statements disagree is refused in every profile once the request
        # exists (so the refusal can be audited): it would be a governance decision about one patient made with
        # another's data. See docs/race/IDENTITY_BINDING_DESIGN.md.
        context, identity, mismatch = self._bind_identity(context, vitals_snapshot)
        self._seen.identity = identity
        # The reading is assessed FIRST, in every profile, and the assessment is bound into the request beside the reading
        # (see `_observe_first`). A caller who supplies an `observe_assessment` that is not the one OBSERVE made is
        # claiming an assessment nobody made: that is an identity mismatch like any other.
        observe_verdict, observe_exc = self._observe_first(vitals_snapshot)
        if isinstance(context, dict):
            # The assessment in the request is the orchestrator's alone. A caller's own `observe_assessment` (a replayed
            # context, or a claim) is dropped, never committed: an assessment is not replayable (the engine's state has moved
            # on), and one the caller wrote is not one OBSERVE made.
            if context.pop("observe_assessment", None) is not None:
                logger.warning("[Orchestrator] A caller-supplied observe_assessment was dropped; the orchestrator's own "
                               "assessment is the only one committed")
            if vitals_snapshot is not None:
                context["observe_assessment"] = self._assessment_binding(observe_verdict, observe_exc)
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
        try:
            governance_request = self.sentinel_adapter.sentinel_artifact_to_governance_request(
                sentinel_artifact,
                operation_type,
                context,
                source_signers=self.source_signers,
            )
        except Exception:
            if mismatch is None:
                if observe_exc is not None:
                    # OBSERVE crashed and now the request cannot be built either: the crash would otherwise reach nobody.
                    self._seen.observe_alert = self._alert_stage_error(
                        "observe", observe_exc, None, patient_id=getattr(vitals_snapshot, "patient_id", None))
                raise
            # The request cannot be built, and it was already known to disagree with the vitals: refuse with the mismatch,
            # tell a person, and assess the reading, rather than losing all three to the construction error.
            return self._identity_refusal(mismatch, None, vitals_snapshot, observe_exc)
        logger.info(f"[Orchestrator] Request ID: {governance_request.request_id}")
        if mismatch is not None:
            return self._identity_refusal(mismatch, governance_request, vitals_snapshot, observe_exc)
        # What the assessment means for this profile, now that the request exists (an alert names it). A crash: strict
        # refuses (OBSERVE is mandatory there) and a person is told, as for any stage that raises instead of reaching a
        # verdict; advisory proceeds without an assessment, tells a person, and records the failure. A reading OBSERVE
        # itself cannot stand behind (unassessable, or no engine contributed evidence) is refused in strict.
        observe_error = None
        observe_alert = None
        if observe_exc is not None:
            observe_error = f"{type(observe_exc).__name__}: {observe_exc}"
            logger.error(f"[Orchestrator] OBSERVE did not produce a verdict: {observe_error}")
            observe_alert = self._alert_stage_error("observe", observe_exc, governance_request,
                                                    patient_id=getattr(vitals_snapshot, "patient_id", None))
            self._seen.observe_alert = observe_alert
            if self.profile in ("strict", "production"):
                if self.raise_on_stage_error:
                    raise observe_exc
                return self._stamp({
                    "status": "REJECTED",
                    "reason": f"OBSERVE stage did not run to a verdict: {observe_error}",
                    "refused_by": "observe",
                    "stage_refused": False,
                    "stage_error": observe_error,
                    **self._alert_field(observe_alert),
                    "governance_decision": None,
                    "observe_enforced": False,
                    "observe_error": observe_error,
                    "audit_chain": None,
                }, governance_request)
        elif observe_verdict is not None and self.profile in ("strict", "production"):
            problems = self._observe_problems(observe_verdict)
            if problems:
                logger.error(f"[Orchestrator] Refusing: OBSERVE cannot back the action: {problems}")
                return self._stamp({
                    "status": "REJECTED",
                    "reason": "OBSERVE cannot back the action: " + "; ".join(problems),
                    "refused_by": "observe",
                    "stage_refused": True,
                    "governance_decision": None,
                    "observe_enforced": True,
                    "audit_chain": None,
                }, governance_request)
        if self.require_attested_event_time and not governance_request.event_time_attested:
            why = governance_request.event_time_attestation_problem or (
                "no attestation offered" if governance_request.event_time else "no event time stated")
            logger.error(f"[Orchestrator] Refusing: event time not attested ({why})")
            return self._stamp({
                "status": "REJECTED",
                "reason": f"event time not attested by a registered source key: {why}",
                "refused_by": "source_attestation",
                "stage_refused": True,
                "governance_decision": None,
                "audit_chain": None,
            }, governance_request)
        # Temporal sanity is recorded, never assumed. An event time after
        # ingestion, or an ingestion before the event by more than a day,
        # is an anomaly the record names; refusing on it would be a policy
        # this library does not own.
        temporal_anomalies = self._temporal_anomalies(governance_request)
        if temporal_anomalies:
            logger.warning(f"[Orchestrator] Temporal anomalies on {governance_request.request_id}: {temporal_anomalies}")

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
                f"it raised {self._log_error(e, governance_request)}. Recording REJECTED with stage_refused=False."
            )
            human_alert = self._alert_stage_error("perceive", e, governance_request)
            if self.raise_on_stage_error:
                raise
            return self._stamp({
                "status": "REJECTED",
                "reason": f"PERCEIVE stage did not run to a verdict: {type(e).__name__}: {e}",
                "refused_by": None,
                "stage_refused": False,
                "stage_error": f"{type(e).__name__}: {e}",
                **self._alert_field(human_alert),
                "governance_decision": None,
                "audit_chain": None,
            }, governance_request)
        logger.info(f"[Orchestrator] PERCEIVE approval: {perceive_decision.approval.value}")
        logger.info(f"[Orchestrator] Applied gates: {perceive_decision.applied_gates}")
        logger.info(f"[Orchestrator] Unanimous: {perceive_decision.unanimous_consensus}")
        if perceive_decision.violations:
            logger.info(f"[Orchestrator] Violations: {perceive_decision.violations}")
        advisory = list(getattr(perceive_decision, "advisory_violations", None) or [])
        if advisory and self.enforce_advisory_violations and perceive_decision.approval.value == "approved":
            # PERCEIVE's gates advised and approved; this profile does not
            # let an advised violation through. Refused here, in PERCEIVE's
            # name, with PERCEIVE's own findings as the reason.
            logger.error(f"[Orchestrator] Refusing: PERCEIVE advised violations {advisory} and this profile enforces them")
            return self._stamp({
                "status": "REJECTED",
                "reason": "PERCEIVE advised violations and this profile enforces them: " + "; ".join(map(str, advisory)),
                "refused_by": "perceive",
                "stage_refused": True,
                "advisory_violations": advisory,
                "governance_decision": perceive_decision,
                "audit_chain": None,
            }, governance_request)

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
            human_alert = None
            if not refused:
                # The stage did not refuse -- it was absent or it crashed. The
                # record below still says REJECTED (unchanged), but now says
                # which of the two happened: from the outside, "the kernel
                # refused" and "there was no kernel" used to be identical.
                logger.warning(
                    "[Orchestrator] Conservation stage did not run to a verdict -- "
                    f"it raised {self._log_error(e, governance_request)}. Recording REJECTED with "
                    "stage_refused=False. Pass a working conservation_kernel, or "
                    "construct the orchestrator with raise_on_stage_error=True to "
                    "raise instead of filing a stage failure as a rejection."
                )
                human_alert = self._alert_stage_error("conservation", e, governance_request)
                if self.raise_on_stage_error:
                    raise
            logger.error(f"[Orchestrator] Conservation Kernel rejected: {self._log_error(e, governance_request)}")
            return self._stamp({
                "status": "REJECTED",
                "reason": f"Conservation Kernel rejected decision: {e}",
                "governance_decision": perceive_decision,
                "refused_by": "conservation" if refused else None,
                "stage_refused": refused,
                "stage_error": None if refused else f"{type(e).__name__}: {e}",
                **self._alert_field(human_alert),
                "conservation_enforced": refused,
                "audit_chain": None,
            }, governance_request)

        # PHASE 4: GSA-815 execution approval
        # Before anything is issued: can the result be receipted? A store
        # that cannot take an append now would leave an executed action with
        # only an in-memory record (measured as F10). Refuse first.
        if self.receipts is not None and hasattr(self.receipts, "probe"):
            try:
                self.receipts.probe()
            except Exception as e:  # noqa: BLE001
                logger.error(f"[Orchestrator] Refusing: receipt store unavailable ({self._log_error(e, governance_request)})")
                human_alert = self._alert_stage_error("receipt_store", e, governance_request)
                return self._stamp({
                    "status": "REJECTED",
                    "reason": f"receipt store unavailable before execution: {type(e).__name__}: {e}",
                    "refused_by": "receipt_store",
                    "stage_refused": True,
                    "stage_error": f"{type(e).__name__}: {e}",
                    **self._alert_field(human_alert),
                    "governance_decision": perceive_decision,
                    "conservation_decision": conservation_decision,
                    "audit_chain": None,
                }, governance_request)

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
            human_alert = None
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
                    f"it raised {self._log_error(e, governance_request)}. Recording REJECTED with "
                    "stage_refused=False. Construct the orchestrator with "
                    "raise_on_stage_error=True to raise instead."
                )
                human_alert = self._alert_stage_error("execution_approval", e, governance_request)
                if self.raise_on_stage_error:
                    raise
            logger.error(f"[Orchestrator] Execution rejected: {self._log_error(e, governance_request)}")
            return self._stamp({
                "status": "REJECTED",
                "reason": str(e),
                "governance_decision": perceive_decision,
                "conservation_decision": conservation_decision,
                "refused_by": refused_by,
                "stage_refused": refused,
                "stage_error": None if refused else f"{type(e).__name__}: {e}",
                **self._alert_field(human_alert),
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
        executor = gsa815_operation_func
        # Only a wrapper `guarded` made, for THIS orchestrator's ledger, counts as already guarded. The
        # `execution_guard` attribute is a label any function can carry, so it is not trusted.
        if self.guard_executor and guard_ledger_of(gsa815_operation_func) is not self.execution_ledger:
            from execution_guard import guarded
            executor = guarded(gsa815_operation_func, self.execution_ledger,
                               kernel=self.conservation_kernel, consumer="orchestrator")
        executed_at = {"started": datetime.now(timezone.utc).isoformat(), "finished": None}
        try:
            gsa815_result = executor(execution_context)
        except Exception as e:
            executed_at["finished"] = datetime.now(timezone.utc).isoformat()
            logger.error(f"[Orchestrator] Execution FAILED: {self._log_error(e, governance_request)}")
            # A person hears about a crashed executor before anything else
            # happens, including the re-raise below.
            human_alert = self._alert_stage_error("execution", e, governance_request)
            if self.raise_on_execution_error:
                raise
            # Everything that led here is kept: the action was approved and
            # attempted, and the record must show both, or a failed action
            # is indistinguishable from one that was never approved. The
            # context is consumed either way: a failed attempt does not
            # leave an approval that can be tried again.
            execution_authorization = self._consume(execution_context, None, "failed")
            outcome_context = self.gsa815_adapter.create_outcome_context(
                execution_context, None, observe_verdict, execution_status="failed")
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
                "fortress_result": fortress_result,
                "conservation_decision": conservation_decision,
                "execution_approval": execution_approval,
                "execution_context": execution_context,
                "execution_authorization": execution_authorization,
                "gsa815_result": None,
                "observe_verdict": observe_verdict,
                "observe_enforced": observe_verdict is not None,
                "outcome_context": outcome_context,
                "audit_chain": None,
                "audit_chain_valid": False,
                **self._alert_field(human_alert),
            }, governance_request)
            return self._verify_on_the_way_out(failed, None)
        executed_at["finished"] = datetime.now(timezone.utc).isoformat()
        # The guard attaches its authorization to a mapping result. It is
        # recorded under execution_authorization, not inside the executor's
        # result: the outcome commits the result, and the authorization
        # carries a timestamp that would make a canonical replay's outcome
        # commitment differ from the original's.
        attached = None
        if isinstance(gsa815_result, dict) and "_authorization" in gsa815_result:
            gsa815_result = dict(gsa815_result)
            attached = gsa815_result.pop("_authorization")
        # The callable may report a partial execution explicitly by returning
        # a mapping with status "partial". Anything else that returned is
        # "completed" from the chain's point of view; the chain does not guess.
        execution_status = (
            "partial"
            if isinstance(gsa815_result, dict) and str(gsa815_result.get("status", "")).lower() == "partial"
            else "completed"
        )
        logger.info(f"[Orchestrator] GSA-815 result ({execution_status}): {gsa815_result}")
        execution_authorization = self._consume(execution_context, attached, execution_status)

        # PHASE 6: OBSERVE monitoring already happened, first (see `_observe_first`): every later stage read that one
        # assessment, so there is nothing left to run here and nothing that can fail after the action.
        if vitals_snapshot is None:
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
            **self._alert_field(observe_alert),
            "execution_status": execution_status,
            "executed_at": executed_at,
            "advisory_violations": list(getattr(perceive_decision, "advisory_violations", None) or []),
            "governance_decision": perceive_decision,
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
