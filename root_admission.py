"""Root admission: the Conservation Kernel registers a root only after a CNS roster approves that document.

WHY THIS EXISTS

The kernel can be built with a root guard (`ConservationKernel(root_guard=...)`) and, since the guard became
mandatory, it refuses to start without one unless the caller says `allow_unguarded_roots=True`. That flag was
the only thing observe-perceive could offer: roots were asserted, and nothing checked that anyone had ruled on
the document first. This module is the guard that replaces the flag.

A kernel lives for many requests and a CNS `DecisionGuard` serves exactly one decision, so the two do not meet
directly. `RootAdmission` is the callable the kernel holds. It is closed. For one request, `admit` asks a
roster for a decision on one root, opens a fresh `DecisionGuard` from that decision, and parks it where only the
calling thread can reach it. The kernel asks the guard while registering the root, the guard says yes once
and only for that document, and `close` drops it, whatever happened.

THE ROSTER (three seats, and the super gate adds a fourth)

  PERCEIVE:perceive_request          ALPHA. The request names a request id and the document it is about.
  Conservation_Kernel:root_admission ALPHA. What `register_root` would refuse (the kernel's own dry run), and
                                     the kernel must actually hold a guard.
  PERCEIVE:perceive_ruling           OMEGA. PERCEIVE's ruling is about that request and carries an audit record.
                                     The ruling's value (approved or refused) is recorded in what the verdict
                                     binds but does not decide the seat: the orchestrator registers the root for
                                     a refused request too, so the refusal is in the kernel's ledger. This seat
                                     says "a ruling exists for this document", not "PERCEIVE approved it".
  CNS:same_subject                   added by the super gate, first: the kernel's root and PERCEIVE's ruling
                                     judged the same document identity and content digest.

The ruling is the OMEGA end because it is PERCEIVE's product, and the roster needs both ends: the super gate
calls a decision a go only when an OMEGA gate ran, so an ALPHA-only roster could never open the guard.

WHAT IT DOES NOT DO

The ruling is built by the caller in the same process, and the approval proof is a process-local key. This stops
a root being registered without a roster saying yes to that exact document. It does not stop hostile code in
the same process, and an approval means nothing to another process. `reads_*` below are policy, like the
required seats: they are only as honest as this file, so review them with `REQUIRED_SEATS`.

CNS is imported only when `admit` runs. Without it `admit` raises CnsNotInstalled and nothing is registered.
"""

from __future__ import annotations

import importlib
import threading
from dataclasses import dataclass
from typing import Any, Optional

__all__ = [
    "REQUIRED_SEATS",
    "RootAdmission",
    "RootNotAdmitted",
    "Ruling",
    "RequestGate",
    "RulingGate",
    "SEAT_REQUEST",
    "SEAT_ROOT",
    "SEAT_RULING",
    "require_root_admission",
]

SEAT_REQUEST = "PERCEIVE:perceive_request"
SEAT_RULING = "PERCEIVE:perceive_ruling"
SEAT_ROOT = "Conservation_Kernel:root_admission"

#: Hand-written, independent of any candidate. Dropping or renaming a seat must fail at construction, so
#: changing this list is a policy change made in review.
REQUIRED_SEATS = (SEAT_REQUEST, SEAT_ROOT, SEAT_RULING, "CNS:same_subject")


class RootNotAdmitted(Exception):
    """The roster did not approve this root, so the guard stays closed and nothing is registered."""


@dataclass(frozen=True)
class Ruling:
    """What PERCEIVE ruled, and on which request and document. `decision` is a GovernanceDecision."""

    decision: Any
    request_id: str
    artifact_id: str
    artifact_hash: str


def _cns() -> Any:
    from cns_connector import _cns_gate  # raises CnsNotInstalled, naming the install command
    return _cns_gate()


def require_root_admission() -> None:
    """Raise CnsNotInstalled, naming the install command, unless this environment can run a root admission.

    The guard needs CNS with `DecisionGuard` (v2.2.0 or later) and the kernel's CNS connector. `admit` finds that
    out too, but only partway through a request, where it reads as a stage crash. An example calls this first, so
    a missing or too-old CNS is reported before anything runs."""
    from cns_connector import INSTALL_HINT, CnsNotInstalled
    _cns()
    for module in ("cns_composition.decision_guard", "conservation_kernel.cns_connector"):
        try:
            importlib.import_module(module)
        except ImportError as exc:
            raise CnsNotInstalled(
                f"root admission needs {module}, which cannot be imported here. Install it with: {INSTALL_HINT}."
            ) from exc


def _judged(identity: str, digest: str) -> Any:
    from cns_composition.super_gate import Judged
    return Judged(identity, digest)


def reads_ruling(ruling: Ruling) -> Any:
    """Which document PERCEIVE's ruling was about, as the request named it."""
    return _judged(ruling.artifact_id, ruling.artifact_hash)


def reads_root(artifact: Any) -> Any:
    """Which document a root artifact is: its id, and the kernel's digest of its content."""
    return _judged(artifact.artifact_id, artifact.content_digest)


class _RulingGateBase:
    """Shared plumbing for PERCEIVE's two seats: the end, the bound verdict, and fail-closed on a wrong candidate."""

    name = ""
    _end = ""

    @property
    def position(self) -> Any:
        return getattr(_cns().GatePosition, self._end)

    def _faults(self, candidate: Ruling) -> list:
        raise NotImplementedError

    def check(self, candidate: Any) -> Any:
        cns = _cns()

        def verdict(outcome: Any, reason: str, bound: Optional[dict] = None) -> Any:
            return cns.GateResult(
                gate=self.name, position=self.position, outcome=outcome, reason=reason,
                subject=f"perceive:{self._end.lower()}:{candidate.request_id}" if bound is not None else "",
                subject_digest=cns.subject_digest(bound) if bound is not None else "")

        if not isinstance(candidate, Ruling):
            return verdict(cns.GateOutcome.TERMINAL_BREACH,
                           f"fault: {self.name} takes a Ruling, got {type(candidate).__name__}")
        why = self._faults(candidate)
        if why:
            return verdict(cns.GateOutcome.TERMINAL_BREACH, "; ".join(why))
        decision = candidate.decision
        approval = getattr(getattr(decision, "approval", None), "value", str(getattr(decision, "approval", "")))
        return verdict(cns.GateOutcome.PASS, "", {
            "decision_id": str(getattr(decision, "decision_id", "")), "request_id": candidate.request_id,
            "approval": approval, "audit_hash": getattr(decision, "perceive_audit_hash", ""),
            "artifact_id": candidate.artifact_id, "artifact_hash": candidate.artifact_hash})


class RequestGate(_RulingGateBase):
    """ALPHA gate: the request names a request id and the document it is about."""

    name = "perceive_request"
    _end = "ALPHA"

    def _faults(self, candidate: Ruling) -> list:
        why = []
        if not candidate.request_id:
            why.append("no request to bind the ruling to")
        if not candidate.artifact_id or not candidate.artifact_hash:
            why.append("the request does not name the document it is about")
        return why


class RulingGate(_RulingGateBase):
    """OMEGA gate: PERCEIVE's ruling is about that request and carries an audit record."""

    name = "perceive_ruling"
    _end = "OMEGA"

    def _faults(self, candidate: Ruling) -> list:
        decision, why = candidate.decision, []
        if getattr(decision, "request_id", None) != candidate.request_id:
            why.append("the ruling is about a different request")
        if not getattr(decision, "perceive_audit_hash", ""):
            why.append("the ruling carries no audit record")
        return why


class RootAdmission:
    """The kernel's `root_guard`, for a kernel that outlives any one request. Closed until `admit` succeeds."""

    def __init__(self) -> None:
        self._mine = threading.local()

    def __call__(self, artifact: Any) -> bool:
        guard = getattr(self._mine, "guard", None)
        return guard is not None and guard(artifact) is True

    def admit(self, kernel: Any, ruling: Ruling, root: Any) -> Any:
        """Ask the roster about `root`. On a go, open the guard for it (this thread, one use) and return the
        decision. Otherwise raise RootNotAdmitted and leave the guard closed."""
        from conservation_kernel.cns_connector import CnsRootAdmissionGate
        from cns_composition.decision_guard import DecisionGuard
        from cns_composition.super_gate import SuperGate

        self.close()
        seats = [("PERCEIVE", RequestGate()), ("Conservation_Kernel", CnsRootAdmissionGate(kernel, require_guard=True)),
                 ("PERCEIVE", RulingGate())]
        decision = SuperGate(seats, required=REQUIRED_SEATS,
                             same_subject={SEAT_REQUEST: reads_ruling, SEAT_ROOT: reads_root,
                                           SEAT_RULING: reads_ruling}
                             ).decide({SEAT_REQUEST: ruling, SEAT_ROOT: root, SEAT_RULING: ruling})
        guard = DecisionGuard(reads_root)
        try:
            guard.authorize(decision)
        except ValueError as exc:
            raise RootNotAdmitted(str(exc)) from exc
        self._mine.guard = guard
        return decision

    def close(self) -> None:
        """Drop this thread's guard. Called after every attempt, so an approval cannot outlive its request."""
        self._mine.guard = None
