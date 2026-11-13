"""production_profile.py -- what a governed PRODUCTION orchestrator must prove about itself before it starts.

The strict profile makes every advisory check a refusal, but it only asks that the Conservation Kernel HAS a
root guard. Any callable satisfies that, so a strict orchestrator could run with no CNS roster anywhere
(measured: strict plus `lambda artifact: True` executed an action). The production profile closes that by
checking IDENTITY and BEHAVIOUR, not shape:

  * CNS is importable at the required version, or the orchestrator does not start.
  * The root guard installed on the kernel IS the `RootAdmission` object handed to the orchestrator. A similar
    callable, or a second `RootAdmission` the kernel does not hold, is refused.
  * The roster really refuses: a canary request that must be refused is put to it at startup. A roster that
    approves it (or a guard that opens for it) is refused. The canary registers nothing.
  * The audit is durable and signed: receipts and the execution ledger have a path and a signer.
  * OBSERVE is configured. Event-time source keys are registered.
  * The executor is guarded.
  * Someone can be told AND followed up: an EscalationDesk with a valid policy, a key for every responder, and a
    durable signed log (human_escalation.py). It cannot prove that a scheduler calls `sweep()`; that stays an
    operator duty, stated in docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md.

Every problem is reported together, so an operator fixes the wiring once. Nothing here is a clinical rule, a
privacy rule or an authorization: it is a check that the controls the deployment claims to have are the
controls that are wired.
"""
from __future__ import annotations

from typing import Any, List, Optional, Tuple

__all__ = ["PRODUCTION_PROFILE", "ProductionProfileError", "REQUIRED_CNS", "production_problems"]

PRODUCTION_PROFILE = "production"

#: The lowest CNS release the production path accepts (the tag v4.0.0, which is what this repository pins).
#: DecisionGuard, which root admission needs, arrived in v2.2.0. Raising it is a policy change made in review.
REQUIRED_CNS: Tuple[int, int, int] = (4, 0, 0)


class ProductionProfileError(ValueError):
    """The orchestrator was asked to run as `production` and could not prove it is wired as one."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("production profile refused: " + "; ".join(self.problems))


def _parse_version(text: Any) -> Optional[Tuple[int, ...]]:
    try:
        return tuple(int(part) for part in str(text).split(".")[:3])
    except (TypeError, ValueError):
        return None


def _cns_problem() -> Optional[str]:
    """Why CNS cannot back the production path, or None."""
    try:
        from root_admission import require_root_admission
        require_root_admission()
    except Exception as exc:  # noqa: BLE001 - any failure to load the roster's dependencies is a refusal
        return f"CNS cannot back root admission here: {type(exc).__name__}: {exc}"
    import cns
    have = _parse_version(getattr(cns, "__version__", None))
    need = ".".join(map(str, REQUIRED_CNS))
    if have is None:
        return f"the installed CNS does not state a readable version (need {need} or later)"
    if have < REQUIRED_CNS:
        return f"the installed CNS is {'.'.join(map(str, have))}; the production profile needs {need} or later"
    return None


def _canary_problem(kernel: Any, root_admission: Any) -> Optional[str]:
    """Put a request the roster must refuse to the roster itself. Registers nothing, whatever happens."""
    from conservation_kernel import Actor, ActorKind, Artifact
    from root_admission import RootNotAdmitted, Ruling
    canary = Artifact(artifact_id="production-profile-canary", content="canary document that must never be admitted",
                      propositions=(), producer=Actor(actor_id="production-profile", kind=ActorKind.SYSTEM))
    refused_ruling = Ruling(decision=None, request_id="", artifact_id="", artifact_hash="")
    try:
        root_admission.admit(kernel, refused_ruling, canary)
    except RootNotAdmitted:
        refusal = None
    except Exception as exc:  # noqa: BLE001 - a roster that crashes on a canary is not one to run on
        refusal = f"the roster crashed on the startup canary: {type(exc).__name__}: {exc}"
    else:
        refusal = "the roster ADMITTED the startup canary it must refuse"
    finally:
        try:
            root_admission.close()
        except Exception:  # noqa: BLE001
            pass
    if refusal is not None:
        return refusal
    try:
        if root_admission(canary) is not False:
            return "the root guard stayed open after the startup canary was refused"
    except Exception as exc:  # noqa: BLE001
        return f"the root guard raised when asked about the canary: {type(exc).__name__}: {exc}"
    return None


def production_problems(*, conservation_kernel: Any, root_admission: Any, observe_engine: Any, receipts: Any,
                        execution_ledger: Any, alert_human: Any, source_signers: Any,
                        guard_executor: bool, escalations: Any = None) -> List[str]:
    """Every reason this wiring is not a production wiring; empty if it is."""
    problems: List[str] = []

    cns_problem = _cns_problem()
    if cns_problem:
        problems.append(cns_problem)

    from root_admission import RootAdmission
    wired = True
    if root_admission is None:
        problems.append("no RootAdmission was given: the kernel's root guard would be anything, or nothing")
        wired = False
    elif not isinstance(root_admission, RootAdmission):
        problems.append(f"root_admission is a {type(root_admission).__name__}, not a RootAdmission: "
                        "a similar callable is not the CNS roster")
        wired = False
    if conservation_kernel is None:
        problems.append("no Conservation Kernel was given")
        wired = False
    elif wired:
        held = getattr(getattr(conservation_kernel, "ledger", None), "_root_guard", None)
        if held is None:
            problems.append("the Conservation Kernel has no root guard")
            wired = False
        elif held is not root_admission:
            problems.append("the root guard on the Conservation Kernel is not the RootAdmission given to the "
                            "orchestrator: the roster would not be the one that gates the kernel")
            wired = False
    if wired and not cns_problem:
        canary = _canary_problem(conservation_kernel, root_admission)
        if canary:
            problems.append(canary)

    if not guard_executor:
        problems.append("guard_executor is off: the executor guard is the pre-action authorization")
    if receipts is None:
        problems.append("no receipt log: a result would not be recorded outside this process")
    elif getattr(receipts, "signer", None) is None:
        problems.append("the receipt log is unsigned: it shows integrity, not who wrote it")
    if execution_ledger is None or getattr(execution_ledger, "path", None) is None:
        problems.append("the execution ledger is in memory: a replay after a restart would not be a replay")
    elif getattr(execution_ledger, "signer", None) is None:
        problems.append("the execution ledger is unsigned")
    if escalations is None:
        problems.append("no escalation desk: a bare alert_human callable proves only that a function returned, not "
                        "that a person was told, took responsibility, or was followed up")
    else:
        from human_escalation import EscalationDesk
        if not isinstance(escalations, EscalationDesk):
            problems.append(f"escalations is a {type(escalations).__name__}, not an EscalationDesk")
        else:
            problems.extend(f"escalation desk: {p}" for p in escalations.problems())
    if alert_human is not None:
        problems.append("alert_human is given beside the production escalation desk: use the desk alone")
    if observe_engine is None:
        problems.append("no OBSERVE engine is configured")
    if not source_signers:
        problems.append("no event-time source keys are registered, so no request's event time can be attested")
    return problems
