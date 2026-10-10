"""Optional CNS connector: OBSERVE and PERCEIVE gates usable at either end.

A CNS gate declares the end it belongs to. ALPHA runs before the work and
judges the input. OMEGA runs on the result and judges the output. Each gate
here is built with the end chosen by the caller.

OBSERVE (vitals in, fused verdict out)
  ALPHA refuses vitals that fail sensor-plausibility checks as RETRY, so a new
  reading is requested instead of a score.
  OMEGA refuses a verdict that is only a partial assessment, or that no engine
  backed with evidence, as RETRY. Missing evidence is abstention, not normality.

PERCEIVE (policy request in, consensus verdict out)
  ALPHA runs one PolicyGates check on the request. A refusal is final.
  OMEGA checks that an approval is sound: it must carry an audit record, must
  have run this gate (the verdict's applied_gates says which gates ran), and
  must carry no violations. A refused verdict is a valid result and passes.

Neither side authorizes anything. These gates decide only whether an input or
result is sound enough to pass along. A fault (an exception, a wrong-type
candidate, or content that cannot be bound) is a TERMINAL_BREACH, never a pass.

CNS is imported only when a connector is built. Without it, building one
raises CnsNotInstalled with the install command and nothing else changes.
"""

from __future__ import annotations

import importlib
from dataclasses import asdict
from datetime import datetime
from enum import Enum
from types import ModuleType
from typing import Any, Union

from observe_consolidated import FusedVerdict, VitalsSnapshot, validate_vitals
from perceive_consolidated import PolicyGates, PolicyRequest, PolicyVerdict

__all__ = [
    "CnsNotInstalled",
    "ObserveCnsGate",
    "PerceiveCnsGate",
    "cns_available",
]

INSTALL_HINT = "pip install 'observe-perceive[cns]'"

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


def _plain(value: Any) -> Any:
    """Reduce a value to the plain types a digest accepts."""
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(v) for v in value]
    return value


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
            digest = cns.subject_digest(_plain(asdict(candidate)))
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

    def __init__(self, position: str = _ALPHA) -> None:
        if position == _ALPHA:
            name, self._expected = "observe.vitals_ingress", VitalsSnapshot
        elif position == _OMEGA:
            name, self._expected = "observe.verdict_egress", FusedVerdict
        else:
            raise ValueError(f"position must be 'alpha' or 'omega', not {position!r}")
        super().__init__(name, position)

    def _judge(self, candidate: Union[VitalsSnapshot, FusedVerdict]) -> tuple:
        cns = self._cns
        if self._position == _ALPHA:
            faults = validate_vitals(candidate)
            if faults:
                return cns.GateOutcome.RETRY, "sensor fault, re-read required: " + "; ".join(faults)
            return cns.GateOutcome.PASS, ""
        if candidate.unassessable:
            return cns.GateOutcome.RETRY, "partial assessment: " + "; ".join(candidate.validation_faults)
        if not candidate.active_engines:
            return cns.GateOutcome.RETRY, "no engine produced evidence for this verdict"
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
