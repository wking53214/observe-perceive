"""Demo fixture for dit: a real OMEGA gate with real inputs.

Scenario. An AI assistant is asked to release a short status summary of a
regulated record. Preconditions (ALPHA) judge the request; the WORK writes the
summary; output checks (OMEGA) judge the result. This fixture supplies the
output check on the summary text: dit, the Deterministic Integrity Tower's
gate stack, judging the text the work returned.

POSITION is 'OMEGA', for these reasons, all taken from the library itself:

* ``dit.cns_connector`` states the mapping outright: "gate position: OMEGA.
  DIT judges the payload as generated: the result does not leave if a gate
  fails. It has no precondition end, and this connector does not invent one."
  ``CnsGate.position`` returns ``GatePosition.OMEGA`` and
  ``cns_chain(...).complete()`` is False by design (README, "Connecting to
  CNS").
* ``dit.gates``: "Gates run against the payload as generated". The tower's
  pipeline (docs/ARCHITECTURE.md) is prompt, then generator, then the gate
  stack on the raw payload. The gates cannot run first, because what they
  judge does not exist until the generator has produced it.
* ``dit.evaluate``: the callers it serves "already hold the text", it came
  back from a call that is over. Its candidate is a ``str``, and
  ``CnsGate.check`` raises ``TypeError`` for anything else.

The candidate is therefore what the demo's work function RETURNS: the summary
text. The gate then judges it, and a refusal means the summary does not leave.

What the gate checks. DIT's own ``FULL_STACK`` over its own default rule set
(``default_rules()``, the recovered GSA v13.0 patterns), unchanged:

    identity               no first person ("I", "we", "my", "our", ...)   RETRY
    hedging                no "may", "might", "could", "likely", ...       RETRY
    causality              a causal connective OR a measured number        RETRY
    semantic_contamination no "feel", "hope", "believe" (affective claim)  TERMINAL

A ``cns.gate.Gate`` is one object with one ``name``, and DIT is a stack of
four gates, so ``make_gate()`` returns the library's own ``CnsGate`` wrapped
around a small DIT-side adapter (``_DitStack``, below) that runs the whole
stack through DIT's own ``run_stack`` and folds the verdicts into one DIT
``GateResult``: terminal beats retryable (the same precedence as
``cns.gate.resolve``), and the reasons of every failed stratum are kept, in
stack order, each prefixed with its stratum name and separated by a space. The translation to
``cns.gate.GateResult``, the OMEGA position and the subject binding are all
done by the library's ``CnsGate``, not by this file. The verdict's ``gate``
field is always the gate's ``name`` ("dit"); the stratum that refused is
named at the start of ``reason``.

REFUSED_OUTCOME is 'TERMINAL_BREACH'. The refused candidate is a summary that
reports what its author believes. ``SemanticContaminationGate`` is DIT's one
terminal stratum ("a generator that reports what it believes has left the
evidentiary frame, and an instructional delta ... does not restore the
frame"), and the connector maps TERMINAL severity to ``TERMINAL_BREACH``. The
refused text trips only that stratum, so the outcome does not depend on how
the strata are ordered. This was verified by running it (see ``__main__``),
not assumed. RETRY is what DIT gives for hedging, first person or a missing
anchor; ``__main__`` shows those too, as extra evidence, but they are not the
refused candidate.

Subject binding. Every verdict is bound: ``subject`` is the label ``SUBJECT``
and ``subject_digest`` is ``cns.gate.subject_digest({"text": text})``, so a
verdict cannot be moved onto a different text.

Import safety. Nothing here imports ``cns`` or ``dit`` when this module
loads; both are imported inside the functions that need them. Without ``cns``
installed, ``make_gate()`` raises ``dit.cns_connector.CnsNotInstalled`` (an
``ImportError``). The candidate functions need neither library: they return
plain strings.

State. ``make_gate()`` builds a new ``CnsGate``, a new ``_DitStack``, a new
rule set and four new gate instances on every call, and nothing at module
level holds a gate. Each candidate function builds a new ``str`` object on
every call (a ``str`` is immutable, so nothing could be mutated through a
shared one anyway, but the demo never receives a shared object).

Run as a script for the self-check:  python fixture_dit.py
"""

from __future__ import annotations

from typing import Any

POSITION = "OMEGA"

REFUSED_OUTCOME = "TERMINAL_BREACH"

DESCRIPTION = (
    "Checks the released status summary text for first-person wording, "
    "hedging, affective claims (feel, hope, believe) and a missing figure or "
    "cause, and refuses to let a summary that reports a belief leave."
)

#: What the verdict's ``subject`` is set to: the label for the judged text.
SUBJECT = "summary"

#: Where the library's own refusal reason starts, for the demo's log.
REFUSED_REASON_PREFIX = "semantic_contamination: TERMINAL_LOGIC_BREACH: EMOTIONAL_LEAK"


class _DitStack:
    """A DIT-side gate (``name`` and ``check(text)`` returning a DIT result)
    that runs DIT's whole ``FULL_STACK`` and reports one verdict.

    It satisfies ``dit.Gate``, the library's own gate protocol, so the
    library's ``CnsGate`` can wrap it. It holds no state between calls: the
    stack is built once per instance from a fresh rule set and only read.
    """

    name = "dit"

    def __init__(self) -> None:
        from dit import FULL_STACK, build_stack, default_rules

        self._stack = build_stack(default_rules(), FULL_STACK)

    def check(self, text: str) -> Any:
        from dit import GateResult, Severity, run_stack

        results = run_stack(text, self._stack)  # short-circuits on a terminal
        failed = [r for r in results if not r.passed]
        if not failed:
            return GateResult(self.name, True)
        severity = (
            Severity.TERMINAL
            if any(r.severity is Severity.TERMINAL for r in failed)
            else Severity.RETRYABLE
        )
        reason = " ".join(f"{r.gate}: {r.reason}" for r in failed)
        return GateResult(self.name, False, severity, reason)


def make_gate() -> Any:
    """A new, independent ``cns.gate.Gate``: the library's own ``CnsGate``.

    Each call builds a fresh ``CnsGate`` around a fresh ``_DitStack``; no
    instance is cached or shared. Raises ``CnsNotInstalled`` when CNS is
    absent (the connector checks at construction, not on first use).
    """
    from dit.cns_connector import CnsGate

    return CnsGate(_DitStack(), subject=SUBJECT)


def ok_candidate() -> str:
    """A summary the work returns that DIT passes. A new ``str`` every call.

    No first person, no hedging, no affective marker, and it carries both a
    causal connective ("because") and measured quantities.
    """
    return " ".join(
        [
            "Lot L-2291 of Compound X 10 mg tablets was released on 2026-09-14",
            "after QA-117 review, because assay measured 99.2%,",
            "dissolution 96.0% and endotoxin 0.12 EU/mL.",
        ]
    )


def refused_candidate() -> str:
    """A summary the work returns that DIT refuses. A new ``str`` every call.

    The same facts, plus a claim about what the reviewers believe. It has no
    first person and no hedging word, so the only stratum it trips is
    ``semantic_contamination`` ("believe"), which is terminal.
    """
    return " ".join(
        [
            "Lot L-2291 of Compound X 10 mg tablets was released on 2026-09-14",
            "after QA-117 review, and the reviewers believe",
            "the results are within specification.",
        ]
    )


def _check(condition: bool, label: str, failures: list) -> None:
    print(f"  [{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def _show(label: str, res: Any) -> None:
    print(
        f"{label}: gate={res.gate!r} position={res.position.name} "
        f"outcome={res.outcome.name} reason={res.reason!r} "
        f"subject={res.subject!r} digest={res.subject_digest[:16]}... "
        f"bound={res.bound()}"
    )


def _self_check() -> int:
    try:
        gate = make_gate()
    except ImportError as exc:  # CnsNotInstalled is an ImportError
        print(f"make_gate() could not run: {type(exc).__name__}: {exc}")
        print("The module imported fine; the gate itself needs cns installed.")
        return 2

    from cns.gate import (
        Gate,
        GateChain,
        GateOutcome,
        GatePosition,
        resolve,
        subject_digest,
        unbound,
    )
    from dit import FULL_STACK, build_stack, default_rules, evaluate
    from dit.cns_connector import payload_digest

    failures: list = []
    print(f"gate name      : {gate.name}")
    print(f"POSITION       : {POSITION} (gate declares {gate.position.name})")
    print(f"REFUSED_OUTCOME: {REFUSED_OUTCOME}")
    print(f"DESCRIPTION    : {DESCRIPTION}")

    ok_in, bad_in = ok_candidate(), refused_candidate()
    print(f"ok candidate     : {ok_in!r}")
    print(f"refused candidate: {bad_in!r}")
    ok_res, bad_res = gate.check(ok_in), gate.check(bad_in)
    _show("ok     ", ok_res)
    _show("refused", bad_res)

    print("checks: contract")
    _check(isinstance(gate, Gate), "gate satisfies cns.gate.Gate", failures)
    _check(
        gate.position is GatePosition.OMEGA and gate.position.name == POSITION,
        "POSITION matches the gate's declared position",
        failures,
    )
    _check(
        GateChain(omega=(gate,)).misplaced() == (),
        "gate sits correctly in the omega slot",
        failures,
    )
    _check(
        GateChain(alpha=(gate,)).misplaced() == (gate.name,),
        "a chain would flag it if it were put in the alpha slot",
        failures,
    )
    _check(
        ok_res.position is GatePosition.OMEGA
        and bad_res.position is GatePosition.OMEGA,
        "both verdicts carry position OMEGA",
        failures,
    )
    _check(
        ok_res.gate == gate.name and bad_res.gate == gate.name,
        "both verdicts name the gate that issued them",
        failures,
    )

    print("checks: verdicts")
    _check(
        ok_res.outcome is GateOutcome.PASS and not ok_res.blocking(),
        "ok candidate PASSES",
        failures,
    )
    _check(
        bad_res.outcome is GateOutcome[REFUSED_OUTCOME] and bad_res.blocking(),
        f"refused candidate gives {REFUSED_OUTCOME}",
        failures,
    )
    _check(
        bad_res.reason.startswith(REFUSED_REASON_PREFIX),
        "refusal reason names the stratum and the library's own breach code",
        failures,
    )
    _check(
        resolve([ok_res]) is GateOutcome.PASS
        and resolve([bad_res]) is GateOutcome[REFUSED_OUTCOME],
        "cns.gate.resolve agrees with each verdict",
        failures,
    )

    print("checks: binding")
    _check(unbound([ok_res, bad_res]) == (), "both verdicts are bound", failures)
    _check(
        ok_res.subject == SUBJECT and bad_res.subject == SUBJECT,
        f"subject label is {SUBJECT!r}",
        failures,
    )
    _check(
        ok_res.subject_digest == subject_digest({"text": ok_in})
        and ok_res.subject_digest == payload_digest(ok_in),
        "ok digest is the canonical digest of the ok text",
        failures,
    )
    _check(
        ok_res.binds(SUBJECT, payload_digest(ok_in))
        and bad_res.binds(SUBJECT, payload_digest(bad_in)),
        "each verdict binds to the text it judged",
        failures,
    )
    _check(
        not ok_res.binds(SUBJECT, payload_digest(bad_in))
        and not bad_res.binds(SUBJECT, payload_digest(ok_in)),
        "neither verdict binds to the other's text",
        failures,
    )

    print("checks: the connector agrees with DIT's own evaluation")
    stack = build_stack(default_rules(), FULL_STACK)
    native_ok, native_bad = evaluate(ok_in, stack), evaluate(bad_in, stack)
    _check(
        native_ok.passed and not native_ok.terminal,
        "dit.evaluate passes the ok candidate",
        failures,
    )
    _check(
        (not native_bad.passed)
        and native_bad.terminal_gate == "semantic_contamination"
        and native_bad.failures == (
            "TERMINAL_LOGIC_BREACH: EMOTIONAL_LEAK ('believe').",
        ),
        "dit.evaluate refuses the refused candidate, terminal, one stratum only",
        failures,
    )

    print("checks: what else the stack does (extra evidence, not the demo's inputs)")
    extras = {
        "hedged": ("Lot L-2291 may have been released on 2026-09-14.", "RETRY"),
        "first person": (
            "I released lot L-2291 on 2026-09-14 because assay measured 99.2%.",
            "RETRY",
        ),
        "no figure or cause": ("The lot was released after review.", "RETRY"),
        "hedged and affective": (
            "Lot L-2291 may be fine and the reviewers hope so, 99.2%.",
            "TERMINAL_BREACH",
        ),
    }
    for label, (text, expected) in extras.items():
        res = gate.check(text)
        print(f"  {label:<22}: {res.outcome.name:<16} {res.reason}")
        _check(
            res.outcome is GateOutcome[expected],
            f"{label} gives {expected}",
            failures,
        )

    print("checks: independence and freshness")
    gate2 = make_gate()
    _check(gate2 is not gate, "make_gate() returns a new object each call", failures)
    _check(
        gate2._inner is not gate._inner
        and gate2._inner._stack[0]._rules is not gate._inner._stack[0]._rules,
        "the two gates share no inner stack and no rule set",
        failures,
    )
    first = gate.check(bad_in)
    other = gate2.check(ok_in)
    again = gate.check(ok_in)
    _check(
        first == bad_res and other == ok_res and again == ok_res,
        "interleaved use of two gates gives the same verdicts as a first run",
        failures,
    )
    fresh = make_gate()
    _check(
        fresh.check(bad_in) == bad_res and fresh.check(ok_in) == ok_res,
        "a gate built after use is not changed by that use",
        failures,
    )
    _check(
        ok_candidate() is not ok_candidate() and ok_candidate() == ok_candidate(),
        "ok_candidate() returns a new, equal object each call",
        failures,
    )
    _check(
        refused_candidate() is not refused_candidate()
        and refused_candidate() == refused_candidate(),
        "refused_candidate() returns a new, equal object each call",
        failures,
    )

    print()
    if failures:
        print(f"SELF-CHECK FAILED: {len(failures)} check(s) failed")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("SELF-CHECK OK")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_self_check())
