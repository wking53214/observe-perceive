"""Optional CNS connector: OBSERVE and PERCEIVE gates usable at either end.

A CNS gate declares the end it belongs to. ALPHA runs before the work and
judges the input. OMEGA runs on the result and judges the output. Each gate
here is built with the end chosen by the caller.

OBSERVE (vitals in, fused verdict out)
  ALPHA refuses vitals that fail sensor-plausibility checks as RETRY, so a new
  reading is requested instead of a score.
  OMEGA refuses a verdict that is only a partial assessment, or that no engine
  backed with evidence, as RETRY. Missing evidence is abstention, not normality.
  A verdict carrying validation_faults is a partial assessment whatever its
  unassessable flag says. A NaN or infinite vital at ALPHA is a sensor fault
  (RETRY); it is described as text for the digest, which refuses such floats,
  so the refusal is still bound to the reading.

PERCEIVE (policy request in, consensus verdict out)
  ALPHA runs one PolicyGates check on the request. A refusal is final.
  OMEGA checks that an approval is sound: it must carry an audit record, must
  have run this gate (the verdict's applied_gates says which gates ran), and
  must carry no violations. A refused verdict is a valid result and passes.

THE HANDOFF (OBSERVE result to PERCEIVE request)
  Each gate above binds only the candidate it is handed, and a CNS chain relates none of them to the others: a chain
  given one patient's vitals and a request about another passes every gate. `HandoffBindingGate` is the gate that
  relates them. It takes an `ObservationHandoff` (the vitals, OBSERVE's verdict, OBSERVE's own audit entry for that
  verdict, and the PolicyRequest) and refuses unless all four provably belong together and the reading is fresh.
  It proves CONSISTENCY, not ORIGIN: the audit entry's hash takes no key, so an assessment fabricated whole (vitals,
  entry and verdict made to agree) passes it. Proving that OBSERVE made the entry needs its ledger head or a signature.

Neither side authorizes anything. These gates decide only whether an input or
result is sound enough to pass along. A fault (an exception, a wrong-type
candidate, or content that cannot be bound) is a TERMINAL_BREACH, never a pass.

CNS is imported only when a connector is built. Without it, building one
raises CnsNotInstalled with the install command and nothing else changes.
"""

from __future__ import annotations

import importlib
import math
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import Enum
from types import ModuleType
from typing import Any, Union

from observe_consolidated import FusedVerdict, VitalsSnapshot, validate_vitals
from perceive_consolidated import PolicyGates, PolicyRequest, PolicyVerdict

__all__ = [
    "CnsNotInstalled",
    "HandoffBindingGate",
    "confirmed_evidence",
    "ObservationHandoff",
    "ObserveCnsGate",
    "PerceiveCnsGate",
    "cns_available",
]

INSTALL_HINT = "pip install 'observe-perceive[cns] @ git+https://github.com/wking53214/observe-perceive.git@main'"

_ALPHA = "alpha"
_OMEGA = "omega"

#: The PolicyGates checks a PerceiveCnsGate may wrap.
PERCEIVE_GATES = frozenset({
    "boundary_gate", "citadel", "fortress", "invariant_validator", "sentinel", "micropatch",
})


class CnsNotInstalled(ImportError):
    """Raised when cns.gate cannot be imported, or is too old to use."""


def _cns_gate() -> ModuleType:
    """Import cns.gate on demand, or say exactly what is missing."""
    try:
        module = importlib.import_module("cns.gate")
    except ImportError as exc:
        raise CnsNotInstalled(
            "the CNS connector needs the CNS package (cns.gate), which is not "
            f"installed. Install it with: {INSTALL_HINT}."
        ) from exc
    needed = ("GateOutcome", "GatePosition", "GateResult", "subject_digest")
    missing = [name for name in needed if not hasattr(module, name)]
    if missing:
        raise CnsNotInstalled(
            "the installed cns.gate lacks " + ", ".join(missing) + ". The connector "
            f"needs the pinned CNS release. Install it with: {INSTALL_HINT}."
        )
    return module


def cns_available() -> bool:
    """Whether the CNS gate contract can be imported in this environment."""
    try:
        _cns_gate()
    except CnsNotInstalled:
        return False
    return True


def _plain(value: Any, non_finite: bool = False) -> Any:
    """Reduce a value to the plain types a digest accepts.

    With non_finite, a NaN or infinite float is described as text, so a
    reading carrying one can still be bound and refused as a sensor fault.
    The digest refuses such floats by design; without the flag they stay
    unbindable, which is a fault.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if non_finite and isinstance(value, float) and not math.isfinite(value):
        return {"non_finite_float": repr(value)}
    if isinstance(value, dict):
        return {str(k): _plain(v, non_finite) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v, non_finite) for v in value]
    return value


def confirmed_evidence(cassette: Any, data: Any) -> tuple:
    """(contributed, silent, problem) for one recorded assessment: re-run each engine the record says was selected on
    the recorded reading, through `cassette`, and sort them into engines that gave evidence and engines that abstained or
    cannot be re-run. `problem` is set when a recorded output is not what its engine gives on the recorded reading.

    Ported from OBSERVE's own cns_connector, where this check already lives. A verdict's `active_engines` lists the
    engines that were SELECTED, including ones that ran and abstained, so it is not evidence: with a cassette whose
    selected engines all abstain, the engine reports STABLE at risk 0 with a non-empty `active_engines`."""
    from observe_consolidated import VitalsSnapshot as _Vitals
    import copy as _copy
    selected = list(data.get("selected_engines") or [])
    recorded = {o.get("engine"): o for o in (data.get("outputs") or [])}
    reading = _Vitals(**_copy.deepcopy(data["vitals"]))
    engines = cassette.engines()
    contributed, silent = [], []
    for name in selected:
        run = engines.get(name)
        if run is None:                                   # not re-runnable, so its evidence cannot be confirmed
            silent.append(name)
            continue
        out = run(reading)
        rec = recorded.get(name)
        if rec is None or (out.risk_score, out.confidence, list(out.triggered_rules)) != (
                rec.get("risk"), rec.get("confidence"), list(rec.get("rules") or [])):
            return contributed, silent, f"the recorded output of {name} is not what it gives on the recorded reading"
        (silent if out.abstained else contributed).append(name)
    return contributed, silent, ""


class _CnsGateBase:
    """Shared plumbing: the end, the result, and fail-closed fault handling."""

    _expected: type
    _subject_prefix: str

    def __init__(self, name: str, position: str) -> None:
        cns = _cns_gate()
        if position not in (_ALPHA, _OMEGA):
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        self._cns = cns
        self._position = position
        self.name = name
        self.position = cns.GatePosition.ALPHA if position == _ALPHA else cns.GatePosition.OMEGA

    def check(self, candidate: Any) -> Any:
        """Judge one candidate and return a CNS GateResult bound to its content."""
        cns = self._cns
        if not isinstance(candidate, self._expected):
            return self._result(
                cns.GateOutcome.TERMINAL_BREACH,
                f"fault: {self._position} end takes {self._expected.__name__}, "
                f"got {type(candidate).__name__}",
            )
        try:
            digest = cns.subject_digest(self._bound_content(candidate))
        except (TypeError, ValueError) as exc:
            return self._result(
                cns.GateOutcome.TERMINAL_BREACH, f"fault: content cannot be bound ({exc})"
            )
        subject = self._subject(candidate)
        try:
            outcome, reason = self._judge(candidate)
        except Exception as exc:  # a gate that raises is a fault, never a pass
            return self._result(
                cns.GateOutcome.TERMINAL_BREACH,
                f"fault: gate raised {type(exc).__name__}: {exc}", subject, digest,
            )
        return self._result(outcome, reason, subject, digest)

    def _bound_content(self, candidate: Any) -> Any:
        """The content the result is bound to."""
        return _plain(asdict(candidate))

    def _judge(self, candidate: Any) -> tuple:
        raise NotImplementedError

    def _subject(self, candidate: Any) -> str:
        raise NotImplementedError

    def _result(self, outcome: Any, reason: str, subject: str = "", digest: str = "") -> Any:
        return self._cns.GateResult(
            gate=self.name,
            position=self.position,
            outcome=outcome,
            reason=reason,
            subject=subject,
            subject_digest=digest,
        )


class ObserveCnsGate(_CnsGateBase):
    """OBSERVE as a CNS gate. ALPHA takes vitals, OMEGA takes a fused verdict."""

    _expected = object  # set per end in __init__

    def __init__(self, position: str = _ALPHA, *, engine: Any = None) -> None:
        #: Optional at OMEGA: the engine whose audit ledger records the verdict. Given, the gate re-runs the recorded
        #: engines and passes only if one of them gave evidence (`confirmed_evidence`). Not given, it reads
        #: `active_engines`, which lists selected engines, abstaining ones included (behaviour before this option).
        self._engine = engine
        if position == _ALPHA:
            name, self._expected = "observe.vitals_ingress", VitalsSnapshot
        elif position == _OMEGA:
            name, self._expected = "observe.verdict_egress", FusedVerdict
        else:
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        super().__init__(name, position)

    def _bound_content(self, candidate: Any) -> Any:
        content = asdict(candidate)
        # A NaN or infinite vital is a sensor fault (RETRY, as documented), not
        # content that cannot be bound, so ALPHA describes it rather than refusing it.
        return _plain(content, non_finite=self._position == _ALPHA)

    def _judge(self, candidate: Union[VitalsSnapshot, FusedVerdict]) -> tuple:
        cns = self._cns
        if self._position == _ALPHA:
            faults = validate_vitals(candidate)
            if faults:
                return cns.GateOutcome.RETRY, "sensor fault, re-read required: " + "; ".join(faults)
            return cns.GateOutcome.PASS, ""
        if candidate.unassessable or candidate.validation_faults:
            # Either one marks a partial assessment. A verdict carrying faults with the
            # flag cleared is inconsistent, and is refused the same way.
            faults = "; ".join(candidate.validation_faults) or "verdict is flagged unassessable"
            return cns.GateOutcome.RETRY, "partial assessment: " + faults
        if not candidate.active_engines:
            return cns.GateOutcome.RETRY, "no engine produced evidence for this verdict"
        if self._engine is not None:
            entry = next((e for e in reversed(self._engine.audit_ledger.entries)
                          if candidate.audit_hash and e.get("immutable_hash") == candidate.audit_hash), None)
            if entry is None:
                return cns.GateOutcome.TERMINAL_BREACH, "the verdict has no entry in the engine's audit ledger"
            contributed, silent, problem = confirmed_evidence(self._engine.cassette, entry.get("data") or {})
            if problem:
                return cns.GateOutcome.TERMINAL_BREACH, problem
            if not contributed:
                return cns.GateOutcome.RETRY, ("no engine produced evidence for this verdict (selected: "
                                               + ", ".join(candidate.active_engines) + "; abstained or unconfirmed: "
                                               + (", ".join(silent) or "none") + ")")
        return cns.GateOutcome.PASS, ""

    def _subject(self, candidate: Any) -> str:
        if self._position == _ALPHA:
            return f"observe:alpha:{candidate.patient_id}"
        return f"observe:omega:{candidate.decision_fingerprint or 'unfingerprinted'}"


class PerceiveCnsGate(_CnsGateBase):
    """One PolicyGates check as a CNS gate. ALPHA takes a request, OMEGA a verdict."""

    _expected = object  # set per end in __init__

    def __init__(self, gate_name: str, position: str = _ALPHA) -> None:
        if gate_name not in PERCEIVE_GATES:
            raise ValueError(f"unknown PolicyGates check {gate_name!r}")
        self._check = getattr(PolicyGates, gate_name)
        self._gate_name = gate_name
        if position == _ALPHA:
            self._expected = PolicyRequest
        elif position == _OMEGA:
            self._expected = PolicyVerdict
        else:
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        super().__init__(f"perceive.{gate_name}", position)

    def _judge(self, candidate: Union[PolicyRequest, PolicyVerdict]) -> tuple:
        cns = self._cns
        if self._position == _ALPHA:
            output = self._check(candidate)
            if output.approved:
                return cns.GateOutcome.PASS, ""
            return cns.GateOutcome.TERMINAL_BREACH, "; ".join(output.violation_details)
        if not candidate.audit_hash:
            return cns.GateOutcome.TERMINAL_BREACH, "verdict has no audit record"
        if not candidate.approved:
            return cns.GateOutcome.PASS, ""
        if self._gate_name not in candidate.applied_gates:
            return cns.GateOutcome.TERMINAL_BREACH, (
                f"approved without {self._gate_name} having run"
            )
        if candidate.violations:
            return cns.GateOutcome.TERMINAL_BREACH, "approved but carries violations"
        return cns.GateOutcome.PASS, ""

    def _subject(self, candidate: Any) -> str:
        if self._position == _ALPHA:
            return f"perceive:alpha:{candidate.request_id}"
        return f"perceive:omega:{candidate.request_id}"


@dataclass(frozen=True)
class ObservationHandoff:
    """What crosses from OBSERVE to PERCEIVE: the reading, OBSERVE's verdict on it, OBSERVE's own audit entry for that
    verdict (from `ObserveClinicalEngine.audit_ledger`), and the PolicyRequest built from them."""

    vitals: Any
    verdict: Any
    audit_entry: Any
    request: Any


#: Keys the engine adds to an audit entry's data after the fingerprint is taken.
_AFTER_FINGERPRINT = ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")


class HandoffBindingGate(_CnsGateBase):
    """ALPHA gate for PERCEIVE's input: the request is about this reading, this reading is the one OBSERVE assessed,
    and the assessment is the one the request carries. `max_age_s` and `clock_skew_s` have no default."""

    _expected = ObservationHandoff

    def __init__(self, *, max_age_s: float, clock_skew_s: float, cassette: Any = None) -> None:
        for name, value in (("max_age_s", max_age_s), ("clock_skew_s", clock_skew_s)):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite number of seconds, zero or more (there is no default)")
        if max_age_s <= 0:
            raise ValueError("max_age_s must be more than zero")
        self.max_age_s, self.clock_skew_s = float(max_age_s), float(clock_skew_s)
        #: Optional: the cassette OBSERVE scored with. Given, the recorded engines are re-run on the recorded reading
        #: and the handoff passes only if one of them gave evidence; without it, an all-abstaining assessment whose
        #: `active_engines` is non-empty is read as assessed (see `confirmed_evidence`).
        self.cassette = cassette
        super().__init__("handoff.observe_to_perceive", _ALPHA)

    def _bound_content(self, candidate: ObservationHandoff) -> Any:
        return _plain({"vitals": asdict(candidate.vitals), "verdict": asdict(candidate.verdict),
                       "audit_entry": candidate.audit_entry, "request": asdict(candidate.request)}, non_finite=True)

    def _subject(self, candidate: ObservationHandoff) -> str:
        return f"handoff:{getattr(candidate.vitals, 'patient_id', '')}"

    def _judge(self, h: ObservationHandoff) -> tuple:
        cns = self._cns
        breach, retry = cns.GateOutcome.TERMINAL_BREACH, cns.GateOutcome.RETRY
        from governance_chain import observation_digest
        from observe_consolidated import ImmutableAuditLedger, decision_fingerprint

        for name, value, kind in (("vitals", h.vitals, VitalsSnapshot), ("verdict", h.verdict, FusedVerdict),
                                  ("request", h.request, PolicyRequest), ("audit_entry", h.audit_entry, dict)):
            if not isinstance(value, kind):
                return breach, f"the handoff's {name} is a {type(value).__name__}, not a {kind.__name__}"
        v, fv, entry, req = h.vitals, h.verdict, h.audit_entry, h.request

        # 1. the evidence is the entry this verdict names, unaltered
        body = {k: val for k, val in entry.items() if k != "immutable_hash"}
        if entry.get("immutable_hash") != ImmutableAuditLedger._digest(body) or entry.get("immutable_hash") != fv.audit_hash:
            return breach, "the audit entry is not the one this verdict names, or it was altered"
        data = entry.get("data") or {}
        payload = {k: val for k, val in data.items() if k not in _AFTER_FINGERPRINT}
        if not fv.decision_fingerprint or decision_fingerprint(payload) != fv.decision_fingerprint \
                or data.get("decision_fingerprint") != fv.decision_fingerprint:
            return breach, "the verdict's fingerprint does not recompute from OBSERVE's recorded evidence"
        if data.get("state_commitment") != fv.state_commitment:
            return breach, "the verdict's state commitment is not the one OBSERVE recorded"
        # 2. the evidence is about this patient, and an abstention is never a stable assessment
        if entry.get("patient_id") != v.patient_id:
            return breach, "OBSERVE's evidence is about a different patient from the vitals handed over"
        if fv.unassessable or fv.validation_faults or not fv.active_engines:
            return retry, "OBSERVE did not fully assess this reading; no assessment is not a stable one"
        if self.cassette is not None:
            contributed, _silent, problem = confirmed_evidence(self.cassette, data)
            if problem:
                return breach, problem
            if not contributed:
                return retry, "every engine OBSERVE selected abstained; no assessment is not a stable one"
        # 3. ... and about this reading
        if _json_round_trip(data.get("vitals")) != _json_round_trip(asdict(v)):
            return breach, "OBSERVE's evidence is about a different reading from the vitals handed over"
        # 4. the request is about this patient, this reading and this assessment
        context = req.context or {}
        if req.subject_id != v.patient_id or context.get("patient_id", v.patient_id) != v.patient_id:
            return breach, "the request is about a different patient from the reading that justifies it"
        if context.get("observation_digest") != observation_digest(v):
            return breach, "the request does not commit to this reading (observation_digest)"
        binding = context.get("observe_assessment") or {}
        if binding.get("status") != "assessed":
            return retry, "the request carries no assessment"
        if binding.get("decision_fingerprint") != fv.decision_fingerprint \
                or binding.get("state_commitment") != fv.state_commitment:
            return breach, "the request carries a different assessment from the one OBSERVE made"
        # 5. fresh, and not from before the reading
        if req.timestamp is None:
            return breach, "the request states no time, so the reading's age cannot be judged"
        age = (req.timestamp - v.timestamp).total_seconds()
        if age < -self.clock_skew_s:
            return breach, f"the request is dated {-age:.0f}s before the reading that justifies it"
        if age > self.max_age_s:
            return retry, f"the reading is {age:.0f}s old (limit {self.max_age_s:g}s); a fresh one is needed"
        return cns.GateOutcome.PASS, ""


def _json_round_trip(value: Any) -> Any:
    """What a value looks like after the engine's ledger stores it (JSON with default=str)."""
    import json
    return json.loads(json.dumps(value, default=str))
