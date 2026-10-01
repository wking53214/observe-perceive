"""Optional CNS combining layer: one fail-closed decision from gates of any library.

observe-perceive is independent of CNS. This module imports nothing from CNS
when it loads, imports no sibling library, and sits next to
``governance_orchestrator`` without touching it: the orchestrator's chain is
hard-wired, this layer is for a caller who wants to combine gates from
several libraries under the ordered, two-ended contract of ``cns.gate``.
CNS is asked for only when a pipeline is built or run. Without it those calls
raise :class:`CnsNotInstalled` with the install command and nothing else in
the repository is affected.

What it does
------------
A :class:`GovernedPipeline` holds two ends of one decision as separate slots.
Each :class:`Slot` pairs a gate (anything that satisfies ``cns.gate.Gate``,
which the libraries' connector classes do) with the callable that builds the
candidate the gate judges. ``run(ctx, work)`` then:

1. refuses the whole run, before any gate or the work runs, if a gate's
   declared position does not match the slot it was placed in (R3);
2. evaluates every ALPHA gate in order, before the work (R1), stopping after
   the first TERMINAL_BREACH because some gates have side effects (R5);
3. calls ``work(ctx)`` only if every alpha verdict is PASS (R1, R6);
4. evaluates the OMEGA gates on the result, only if the work returned (R6);
5. resolves every verdict, synthetic ones included, with ``cns.gate.resolve``
   (terminal over retry over pass, R4) into one :class:`GovernedDecision`
   that carries the verdicts, what was not evaluated and why, and a digest.

Fail closed
-----------
A gate that raises, returns something that is not a well-formed
``cns.gate.GateResult``, or is missing while required, becomes a synthetic
TERMINAL_BREACH verdict (R2), never a pass and never a crash of the pipeline.
Synthetic verdicts are bound like real ones: ``subject`` is
``"synthetic:<kind>"`` and ``subject_digest`` is the CNS digest of
``{"gate": <name>, "reason": <reason>}`` (R7). With ``require_bound`` (the
default) a verdict that does not record what it judged is itself refused.

Choices the rules leave open, stated here so they are not discovered later
--------------------------------------------------------------------------
* Short-circuit applies to both ends. A gate that promotes an accepted
  candidate into a ledger when it is checked must not be asked about a result
  an earlier gate has already refused.
* A required OMEGA gate that is unavailable is refused before the work
  starts, not after. A result that cannot be judged is not produced.
* ``executed`` means the work was called. It stays True if the work raised,
  because the work may have done something before it raised.
* ``result`` is returned only when the outcome is PASS. Under any other
  outcome it is None and ``result_withheld`` is True: an OMEGA failure means
  the result does not leave. ``result_digest`` still records what was
  produced, when the result can be described to CNS at all.
* A verdict stamped with a position other than the slot it ran in is refused.
  The gate's position is never relabelled to fit.

This is tamper-evidence, not tamper-proofing, exactly as in ``cns.gate``: the
digest detects a record edited after the fact, not an adversary who
recomputes it. Install CNS with the extra: ``pip install 'observe-perceive[cns]'``.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Callable, Iterable

__all__ = [
    "CnsNotInstalled",
    "GovernedDecision",
    "GovernedPipeline",
    "INSTALL_HINT",
    "PLACEMENT_GATE",
    "SYNTHETIC_SUBJECT_PREFIX",
    "Slot",
    "WORK_GATE",
    "cns_available",
]

INSTALL_HINT = "pip install 'observe-perceive[cns]'"

#: Every synthetic verdict's ``subject`` starts with this, so a reader of the
#: record can tell a verdict this layer made from one a gate issued.
SYNTHETIC_SUBJECT_PREFIX = "synthetic:"

#: ``gate`` name of the verdict that refuses a run over mis-placed gates.
PLACEMENT_GATE = "cns_governed_decision.placement"

#: ``gate`` name of the verdict recorded when the work itself raises.
WORK_GATE = "cns_governed_decision.work"

_RESERVED_NAMES = (PLACEMENT_GATE, WORK_GATE)

_REQUIRED_NAMES = (
    "GateChain",
    "GateOutcome",
    "GatePosition",
    "GateResult",
    "resolve",
    "subject_digest",
    "unbound",
)


class CnsNotInstalled(ImportError):
    """Raised when ``cns.gate`` cannot be imported, or is too old to use."""


def _cns_gate() -> ModuleType:
    """Import ``cns.gate`` on demand, or say exactly what is missing."""
    try:
        module = importlib.import_module("cns.gate")
    except ImportError as exc:
        raise CnsNotInstalled(
            "observe-perceive's CNS combining layer needs the CNS package "
            f"(cns.gate), which is not installed. Install it with: {INSTALL_HINT}. "
            "observe-perceive itself works without it."
        ) from exc
    missing = [name for name in _REQUIRED_NAMES if not hasattr(module, name)]
    if missing:
        raise CnsNotInstalled(
            "the installed cns.gate lacks " + ", ".join(missing) + ". The combining "
            f"layer needs the pinned CNS release. Install it with: {INSTALL_HINT}."
        )
    return module


def cns_available() -> bool:
    """Whether the CNS gate contract can be imported in this environment."""
    try:
        _cns_gate()
    except CnsNotInstalled:
        return False
    return True


@dataclass(frozen=True)
class Slot:
    """One gate and the callable that builds what it judges.

    ``candidate`` is ``ctx -> object`` in an alpha slot and
    ``(ctx, result) -> object`` in an omega slot. ``gate`` may be None for a
    library that is not installed: a required slot then refuses the run, a
    non-required one is recorded in ``not_evaluated`` and does not block.
    ``name`` labels the slot in synthetic verdicts and in ``not_evaluated``; it
    defaults to ``gate.name`` and is required when ``gate`` is None. A gate's own
    verdicts keep the name the gate gave them.
    """

    gate: Any
    candidate: Callable[..., object]
    required: bool = True
    name: str | None = None

    def __post_init__(self) -> None:
        if not callable(self.candidate):
            raise TypeError(
                "Slot.candidate must be callable: ctx -> object in an alpha slot, "
                "(ctx, result) -> object in an omega slot"
            )
        if self.name is not None and (not isinstance(self.name, str) or not self.name):
            raise ValueError("Slot.name must be a non-empty string when given")

    def label(self) -> str:
        """The name this slot goes by in the record."""
        if self.name is not None:
            return self.name
        if self.gate is None:
            raise ValueError(
                "a slot with no gate needs a name, so the record can say what was unavailable"
            )
        try:
            name = self.gate.name
        except Exception as exc:
            raise ValueError(f"cannot read the gate's name: {_describe(exc)}") from exc
        if not isinstance(name, str) or not name:
            raise ValueError(f"a gate's name must be a non-empty string, got {name!r}")
        return name


def _clean(text: str) -> str:
    """``text`` with anything UTF-8 cannot encode (a lone surrogate) escaped.

    CNS digests are over UTF-8. An exception message with a stray surrogate in
    it would otherwise make the digest of the verdict that reports it raise,
    which turns a refusal into a crash.
    """
    return text.encode("utf-8", "backslashreplace").decode("utf-8")


def _describe(exc: BaseException) -> str:
    """``TypeName: message``, tolerating an exception whose ``__str__`` raises."""
    try:
        message = str(exc)
    except Exception:
        message = "<message unavailable>"
    return _clean(f"{type(exc).__name__}: {message}")


def _text(value: object) -> str:
    """A string for an enum or a stray value: the ``value`` when there is one."""
    return str(getattr(value, "value", value))


def _synthetic(g: ModuleType, gate: str, position: Any, kind: str, reason: str) -> Any:
    """A TERMINAL_BREACH this layer issued itself, bound like a gate's own."""
    gate, reason = _clean(gate), _clean(reason)
    return g.GateResult(
        gate=gate,
        position=position,
        outcome=g.GateOutcome.TERMINAL_BREACH,
        reason=reason,
        subject=SYNTHETIC_SUBJECT_PREFIX + kind,
        subject_digest=g.subject_digest({"gate": gate, "reason": reason}),
    )


@dataclass(frozen=True)
class GovernedDecision:
    """One decision, and everything needed to say why.

    ``outcome`` is the ``cns.gate.GateOutcome`` value string: ``"pass"``,
    ``"retry"`` or ``"terminal_breach"``. ``verdicts`` holds every verdict in
    evaluation order, synthetic ones included. Every configured slot is
    accounted for, either by a verdict or by an entry in ``not_evaluated``.
    ``result`` is None unless the outcome is ``"pass"``.
    """

    outcome: str
    executed: bool
    result: Any
    verdicts: tuple[Any, ...]
    not_evaluated: tuple[tuple[str, str], ...]
    work_error: str | None
    chain_complete: bool
    result_digest: str | None = None
    result_withheld: bool = False

    def _content(self) -> dict[str, Any]:
        """The stable, JSON-safe mapping the digest is computed over."""
        return {
            "outcome": _clean(self.outcome),
            "executed": self.executed,
            "work_error": None if self.work_error is None else _clean(self.work_error),
            "chain_complete": self.chain_complete,
            "result_digest": self.result_digest,
            "result_withheld": self.result_withheld,
            "verdicts": [
                {
                    "gate": _clean(v.gate),
                    "position": _clean(_text(v.position)),
                    "outcome": _clean(_text(v.outcome)),
                    "reason": _clean(v.reason),
                    "subject": _clean(v.subject),
                    "subject_digest": _clean(v.subject_digest),
                }
                for v in self.verdicts
            ],
            "not_evaluated": [[_clean(name), _clean(reason)] for name, reason in self.not_evaluated],
        }

    @property
    def digest(self) -> str:
        """``cns.gate.subject_digest`` of the decision's content."""
        return _cns_gate().subject_digest(self._content())

    def as_dict(self) -> dict[str, Any]:
        """The decision as JSON-safe data, with its digest.

        Recompute the digest from every key except ``"digest"`` to check the
        record was not edited. The result itself is not included: it is
        arbitrary, and the verdicts' ``subject_digest`` and ``result_digest``
        bind the record to it.
        """
        data = self._content()
        data["digest"] = self.digest
        return data


class GovernedPipeline:
    """Preconditions, then the work, then output checks, as one decision.

    Raises :class:`CnsNotInstalled` at construction when CNS is absent, and
    ``ValueError`` when two slots share a name (across both ends, so the
    record names every gate unambiguously).
    """

    def __init__(
        self,
        alpha: Iterable[Slot] = (),
        omega: Iterable[Slot] = (),
        *,
        require_bound: bool = True,
    ) -> None:
        _cns_gate()  # fail here, at construction, not on first use
        seen: dict[str, str] = {}
        labelled: dict[str, tuple[tuple[str, Slot], ...]] = {}
        for end, slots in (("alpha", tuple(alpha)), ("omega", tuple(omega))):
            entries = []
            for slot in slots:
                if not isinstance(slot, Slot):
                    raise TypeError(f"the {end} end takes Slot objects, got {type(slot).__name__}")
                label = slot.label()
                if label in _RESERVED_NAMES:
                    raise ValueError(f"{label!r} is reserved for this layer's own verdicts")
                if label in seen:
                    raise ValueError(
                        f"duplicate gate name {label!r}: already in the {seen[label]} end"
                    )
                seen[label] = end
                entries.append((label, slot))
            labelled[end] = tuple(entries)
        self._alpha = labelled["alpha"]
        self._omega = labelled["omega"]
        self._require_bound = bool(require_bound)

    @property
    def require_bound(self) -> bool:
        return self._require_bound

    @property
    def names(self) -> tuple[str, ...]:
        """Every slot's name, alpha end first, in the order they are evaluated."""
        return tuple(name for name, _ in self._alpha + self._omega)

    def run(self, ctx: Any, work: Callable[[Any], Any]) -> GovernedDecision:
        """Judge, maybe run ``work(ctx)``, judge its result, and decide."""
        if not callable(work):
            raise TypeError("work must be callable: ctx -> result")
        g = _cns_gate()
        alpha_pos = g.GatePosition.ALPHA
        omega_pos = g.GatePosition.OMEGA
        verdicts: list[Any] = []
        skipped: list[tuple[str, str]] = []

        # R3: nothing runs, not even a gate, while the wiring is wrong.
        wrong = self._misplaced(g)
        if wrong:
            verdicts.append(
                _synthetic(
                    g,
                    PLACEMENT_GATE,
                    alpha_pos,
                    "placement",
                    "gate placement mismatch, run refused: "
                    + ", ".join(wrong)
                    + " declared a position that does not match the slot it was placed in",
                )
            )
            for name, _ in self._alpha + self._omega:
                skipped.append((name, "not evaluated: gate placement refused the run"))
            return self._decide(g, verdicts, skipped, executed=False)

        # R1: every alpha gate is judged before the work is called.
        self._evaluate_end(g, self._alpha, alpha_pos, (ctx,), verdicts, skipped)
        if not all(v.outcome is g.GateOutcome.PASS for v in verdicts):
            for name, _ in self._omega:
                skipped.append((name, "not evaluated: the work did not run, an alpha gate did not pass"))
            return self._decide(g, verdicts, skipped, executed=False)

        # A result that cannot be judged is not produced: refuse before the work.
        unavailable = [n for n, s in self._omega if s.gate is None and s.required]
        if unavailable:
            for name in unavailable:
                verdicts.append(
                    _synthetic(
                        g,
                        name,
                        omega_pos,
                        "gate_unavailable",
                        f"gate unavailable: required output gate {name!r} is not installed or "
                        "not supplied, so the work was not started",
                    )
                )
            for name, _ in self._omega:
                if name not in unavailable:
                    skipped.append((name, "not evaluated: the work did not run, a required output gate is unavailable"))
            return self._decide(g, verdicts, skipped, executed=False)

        # R6: the work, then the omega end only if it returned.
        try:
            result = work(ctx)
        except Exception as exc:
            described = _describe(exc)
            verdicts.append(
                _synthetic(g, WORK_GATE, omega_pos, "work_error", f"work raised {described}")
            )
            for name, _ in self._omega:
                skipped.append((name, "not evaluated: the work raised"))
            return self._decide(g, verdicts, skipped, executed=True, work_error=described)

        self._evaluate_end(g, self._omega, omega_pos, (ctx, result), verdicts, skipped)
        return self._decide(g, verdicts, skipped, executed=True, result=result, has_result=True)

    def _misplaced(self, g: ModuleType) -> list[str]:
        """Names of gates whose declared position does not match their slot."""
        wrong: list[str] = []
        for end, entries in (("alpha", self._alpha), ("omega", self._omega)):
            for name, slot in entries:
                if slot.gate is None:
                    continue
                one = (slot.gate,)
                chain = g.GateChain(alpha=one) if end == "alpha" else g.GateChain(omega=one)
                try:
                    bad = chain.misplaced()
                except Exception as exc:
                    wrong.append(f"{name} (position unreadable: {_describe(exc)})")
                    continue
                if bad:
                    wrong.append(f"{name} (placed in the {end} slot)")
        return wrong

    def _evaluate_end(
        self,
        g: ModuleType,
        entries: tuple[tuple[str, Slot], ...],
        position: Any,
        args: tuple[Any, ...],
        verdicts: list[Any],
        skipped: list[tuple[str, str]],
    ) -> None:
        """Judge one end in order, stopping after the first TERMINAL_BREACH (R5)."""
        stopper: str | None = None
        for name, slot in entries:
            if stopper is not None:
                skipped.append((name, f"short-circuit: {stopper} returned TERMINAL_BREACH"))
                continue
            if slot.gate is None:
                if not slot.required:
                    skipped.append((name, "gate unavailable (slot not required)"))
                    continue
                produced = [
                    _synthetic(
                        g,
                        name,
                        position,
                        "gate_unavailable",
                        f"gate unavailable: required gate {name!r} is not installed or not supplied",
                    )
                ]
            else:
                produced = self._judge(g, name, slot, position, args)
            verdicts.extend(produced)
            if any(v.outcome is g.GateOutcome.TERMINAL_BREACH for v in produced):
                stopper = name

    def _judge(
        self, g: ModuleType, name: str, slot: Slot, position: Any, args: tuple[Any, ...]
    ) -> list[Any]:
        """One gate's verdict on one candidate, or the synthetic one that replaces it."""
        try:
            candidate = slot.candidate(*args)
        except Exception as exc:
            return [
                _synthetic(
                    g,
                    name,
                    position,
                    "gate_error",
                    f"candidate for gate {name!r} could not be built: {_describe(exc)}",
                )
            ]
        try:
            verdict = slot.gate.check(candidate)
        except Exception as exc:
            return [
                _synthetic(g, name, position, "gate_error", f"gate {name!r} raised {_describe(exc)}")
            ]
        if not _well_formed(g, verdict):
            return [
                _synthetic(
                    g,
                    name,
                    position,
                    "malformed",
                    f"gate {name!r} returned {type(verdict).__name__}, not a well-formed "
                    "cns.gate.GateResult",
                )
            ]
        produced = [verdict]
        if verdict.position is not position:
            produced.append(
                _synthetic(
                    g,
                    name,
                    position,
                    "misplaced_verdict",
                    f"gate {name!r} issued a verdict stamped {_text(verdict.position)} "
                    f"in the {_text(position)} slot",
                )
            )
        if self._require_bound and g.unbound((verdict,)):
            produced.append(
                _synthetic(
                    g,
                    name,
                    position,
                    "unbound",
                    f"unbound verdict from gate {name!r}: it records no subject or digest "
                    "of what it judged",
                )
            )
        return produced

    def _decide(
        self,
        g: ModuleType,
        verdicts: list[Any],
        skipped: list[tuple[str, str]],
        *,
        executed: bool,
        result: Any = None,
        has_result: bool = False,
        work_error: str | None = None,
    ) -> GovernedDecision:
        outcome = g.resolve(verdicts)  # R4: terminal over retry over pass
        result_digest = None
        if has_result:
            try:
                result_digest = g.subject_digest({"result": result})
            except Exception:
                result_digest = None  # not content CNS can describe; the verdicts still bind
        passed = outcome is g.GateOutcome.PASS
        # "Populated" counts the gates actually supplied, whether or not they ran.
        alpha_gates = tuple(s.gate for _, s in self._alpha if s.gate is not None)
        omega_gates = tuple(s.gate for _, s in self._omega if s.gate is not None)
        return GovernedDecision(
            outcome=_text(outcome),
            executed=executed,
            result=result if passed else None,
            verdicts=tuple(verdicts),
            not_evaluated=tuple(skipped),
            work_error=work_error,
            chain_complete=g.GateChain(alpha=alpha_gates, omega=omega_gates).complete(),
            result_digest=result_digest,
            result_withheld=has_result and not passed,
        )


def _well_formed(g: ModuleType, verdict: object) -> bool:
    """Whether ``verdict`` is a GateResult the record and ``resolve`` can trust.

    ``GateResult`` is a plain dataclass: nothing stops a gate building one
    whose ``outcome`` is the string ``"terminal_breach"``, which ``resolve``
    would read as PASS because it compares by identity.
    """
    if not isinstance(verdict, g.GateResult):
        return False
    if not isinstance(verdict.outcome, g.GateOutcome):
        return False
    if not isinstance(verdict.position, g.GatePosition):
        return False
    return all(
        isinstance(getattr(verdict, field), str)
        for field in ("gate", "reason", "subject", "subject_digest")
    )
