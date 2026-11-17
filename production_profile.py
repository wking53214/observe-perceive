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
  * The audit is durable and signed: receipts and the execution ledger have a path and a signer. When FORTRESS is in
    the chain, its audit ledger signs with a configured key (FORTRESS_AUDIT_KEY), not a random per-process one, of at
    least 32 bytes and not the published 'fortress-key'.
  * OBSERVE is configured, runs the clinical settings the deployment attests (their digest is recorded in every handoff), and
    keeps a bounded ledger with an archive. Patient identifiers are not written to logs. Event-time source keys are registered.
  * The executor is guarded.
  * Someone can be told AND followed up: an EscalationDesk with a valid policy, a key for every responder, and a
    durable signed log (human_escalation.py), swept by a running SweepRunner bound to that same desk whose watchdog
    alarms through a channel of its own (escalation_runner.py). It cannot prove the alarm channel reaches a person, or that
    the process stays up; see docs/race/ESCALATION_SWEEP_RUNNER.md.

Every problem is reported together, so an operator fixes the wiring once. Nothing here is a clinical rule, a
privacy rule or an authorization: it is a check that the controls the deployment claims to have are the
controls that are wired.
"""
from __future__ import annotations

import hmac
from typing import Any, List, Optional, Tuple

__all__ = ["PRODUCTION_PROFILE", "ProductionProfileError", "REQUIRED_CNS", "production_problems"]

PRODUCTION_PROFILE = "production"

#: The key fortress-kernel before c361569 signed every audit record with. It is published, so it is refused, never used.
_PUBLISHED_FORTRESS_KEY = b"fortress-key"
#: The shortest FORTRESS audit key production accepts: the HMAC-SHA256 output length. RFC 2104 discourages shorter keys.
_FORTRESS_KEY_MIN_BYTES = 32

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


def _data_protection_problems(observe_engine: Any, clinical_settings: Any) -> List[str]:
    """What production needs of the clinical settings, the OBSERVE ledger and the logs (decision D11)."""
    problems: List[str] = []
    from clinical_settings import ClinicalSettings
    if clinical_settings is None:
        problems.append("no clinical settings were attested: pass the ClinicalSettings the clinical owner approved "
                        "(clinical_settings=...), so the engine's behaviour is the approved one and its digest is recorded in "
                        "every handoff, not silent defaults")
    elif type(clinical_settings) is not ClinicalSettings:       # exactly the class: a subclass could say any digest it liked
        problems.append(f"clinical_settings is a {type(clinical_settings).__name__}, not a ClinicalSettings")
    elif observe_engine is not None:
        from pediatric_cassette import PediatricCassette
        cassette = getattr(observe_engine, "cassette", None)
        held = getattr(cassette, "settings", None)
        if not isinstance(cassette, PediatricCassette) or type(held) is not ClinicalSettings:
            problems.append("the OBSERVE engine's cassette is not a PediatricCassette holding ClinicalSettings, so the attested "
                            "settings cannot be shown to be the ones its scoring uses")
        elif ClinicalSettings.digest(held) != ClinicalSettings.digest(clinical_settings):
            problems.append("the OBSERVE engine runs different clinical settings from the ones attested "
                            f"(engine {ClinicalSettings.digest(held)[:12]}, attested {ClinicalSettings.digest(clinical_settings)[:12]})")
    if observe_engine is not None:
        ledger = getattr(observe_engine, "audit_ledger", None)
        if getattr(ledger, "max_entries", None) is None:        # a bounded ledger cannot exist without its archive
            problems.append("the OBSERVE ledger is in memory and unbounded: give the engine ledger_max_entries and "
                            "ledger_archive_path, so old entries go to an owner-only archive instead of growing forever")
    from log_redaction import raw_ids_enabled
    if raw_ids_enabled():
        problems.append("OBSERVE_LOG_RAW_PATIENT_IDS is set: patient identifiers would be written to the logs")
    return problems


def production_problems(*, conservation_kernel: Any, root_admission: Any, observe_engine: Any, receipts: Any,
                        execution_ledger: Any, alert_human: Any, source_signers: Any,
                        guard_executor: bool, escalations: Any = None, sweep_runner: Any = None,
                        clinical_settings: Any = None, fortress_adapter: Any = None) -> List[str]:
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
    if fortress_adapter is not None:
        fortress_audit = getattr(getattr(fortress_adapter, "fortress", None), "audit", None)
        if getattr(fortress_audit, "ephemeral_key", None) is not False:
            problems.append("FORTRESS is in the chain but its audit ledger has no configured key (set "
                            "FORTRESS_AUDIT_KEY): its records are signed with a random per-process key (or, on a "
                            "fortress-kernel older than c361569, the published key 'fortress-key') and cannot be "
                            "verified outside this process")
        else:
            # The key is read only to compare it; no message carries it.
            key = getattr(fortress_audit, "audit_key", None)
            key = (key.encode() if isinstance(key, str) else key if isinstance(key, bytes) else b"").strip()
            if hmac.compare_digest(key, _PUBLISHED_FORTRESS_KEY):
                problems.append("FORTRESS is in the chain but its audit ledger signs with the published key "
                                "'fortress-key' (set FORTRESS_AUDIT_KEY to a secret): anyone can forge records "
                                "that verify")
            elif len(key) < _FORTRESS_KEY_MIN_BYTES:
                problems.append(f"FORTRESS is in the chain but its audit key (FORTRESS_AUDIT_KEY) is blank or shorter "
                                f"than {_FORTRESS_KEY_MIN_BYTES} bytes: a short key can be guessed, and then records "
                                "can be forged")
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
    if sweep_runner is None:
        problems.append("no sweep runner: nothing calls EscalationDesk.sweep(), so a page nobody answers would never "
                        "move to the next tier and nobody would be told that it had stopped")
    else:
        from escalation_runner import SweepRunner
        if not isinstance(sweep_runner, SweepRunner):
            problems.append(f"sweep_runner is a {type(sweep_runner).__name__}, not a SweepRunner")
        else:
            problems.extend(f"sweep runner: {p}" for p in sweep_runner.problems())
            if escalations is not None and sweep_runner.desk is not escalations:
                problems.append("the sweep runner is bound to a different desk from the orchestrator's: the desk that "
                                "pages would never be swept")
            if not sweep_runner.running:
                problems.append("the sweep runner is not running: start it before building the orchestrator")
    if observe_engine is None:
        problems.append("no OBSERVE engine is configured")
    problems.extend(_data_protection_problems(observe_engine, clinical_settings))
    if not source_signers:
        problems.append("no event-time source keys are registered, so no request's event time can be attested")
    return problems
