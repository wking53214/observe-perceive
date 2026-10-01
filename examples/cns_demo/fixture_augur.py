"""Demo fixture for augur: a real ALPHA gate with real inputs.

Scenario. An AI assistant is asked to release a short status summary of a
regulated record. This fixture supplies the screen on the PLANNED ACTION'S
SCENARIO: before the summary is generated, does AUGUR's seeded simulation of
the plan end in a stable regime, or does it predict instability and veto?

POSITION is 'ALPHA', for these reasons, all taken from the library itself:

* The README opens with "Optional pre-execution behavioural simulation
  screen. Veto-only: may refuse; may never approve", and places it in the
  live path as "... PERCEIVE -> [AUGUR veto] -> Conservation -> Execution
  ...": a veto that halts the action before the action runs.
* ``augur.cns_connector`` states the mapping outright: "where it judges:
  ALPHA. AUGUR is a pre-execution screen: a refusal means the action never
  starts. It has no outcome end, and this connector does not invent one."
  ``CnsGate.position`` returns ``GatePosition.ALPHA`` and
  ``cns_chain(...).complete()`` is False by design.
* Its candidate is a ``SimulationConfig``: a scenario that exists before the
  work does. The gate never sees the generated summary, so it could not sit
  at the OMEGA end even if the demo wanted it there.

The candidate this gate judges is therefore the planned action's SCENARIO,
handed to ``gate.check(...)`` before the summary is generated. In AUGUR's own
words the scenario is a single scalar measure (a KPI) steered toward a target
over a number of steps; the demo reads it as "the state the plan will push a
tracked measure through". AUGUR never sees the record or the summary.

* ``ok_candidate()``: a calm plan. The measure starts at 90.0, the target is
  100.0 and does not move, 30 steps, seed 42. AUGUR ends in STABLE and the
  verdict is PASS. On seeds 0 to 199 (measured on the scratch copy of augur
  commit 0ce852d, noise scale 4.0) all 200 end STABLE, with a last-step
  volatility of 0.87 to 7.86 against the UNSTABLE threshold of 10.
* ``refused_candidate()``: a violent plan. The measure starts at -500.0 and
  must reach 1000.0 inside 8 steps, seed 42. AUGUR ends in CRITICAL and the
  verdict is a refusal. On seeds 0 to 199 all 200 end CRITICAL, with a
  last-step volatility of 499.6 to 501.4 against the CRITICAL threshold of
  20. The refusal is driven by the size of the swing, not by a seed or by
  noise, so it does not depend on which random draws come out.

REFUSED_OUTCOME is 'TERMINAL_BREACH'. The connector maps a predicted
UNSTABLE or CRITICAL regime to TERMINAL_BREACH, never RETRY: "AUGUR models no
repair or re-attempt, and re-running a simulation until it looks favourable
would turn a veto into an approval by repetition." This was verified by
running it (see ``_self_check``), not assumed. RETRY is reserved for a
scenario that cannot be screened at all (an unset seed, a NaN, an invalid
step count); this fixture does not exercise it.

Limits of what PASS means, stated by the library and kept here. PASS is "no
objection", never approval: the reason says AUGUR cannot approve. AUGUR also
judges only the regime at the LAST step of the run. In this kernel step 1 of
every run reads UNSTABLE (its distortion estimate is 0.424 there; measured
on all 200 ok-candidate seeds), so no run is STABLE throughout; the ok
candidate was chosen because everything after that step is STABLE with
margin, so its PASS does not rest on a lucky final step.

Side effects of ``gate.check``, which are AUGUR's own and which a host should
expect (the connector docstring lists them):

* It runs the real simulation and reseeds the process-global ``random`` (and
  NumPy, if installed) generators. A host that seeded its own does not
  continue its sequence after a check. Verdicts do not depend on the prior
  global state (the self-check seeds differently before two checks and gets
  equal verdicts).
* It sets ``AUGUR_RUN_ID`` in ``os.environ``.
* It appends one HMAC-signed audit line per simulated step to AUGUR's audit
  log, which defaults to ``augur_audit.log`` in the working directory. This
  fixture keeps that out of the working directory: unless the caller set
  ``AUGUR_AUDIT_LOG``, ``make_gate()`` points the audit log at one temporary
  file for the process (see ``_keep_audit_log_out_of_cwd``). Nothing reads the
  log back, so it cannot change a verdict.

Independence. ``make_gate()`` returns a new ``CnsGate`` each call; the gate
holds only its (immutable) noise scale and subject label, and each check
builds and runs a new simulation from the candidate alone. The candidate
functions build a new ``SimulationConfig`` every call. ``SimulationConfig`` is
a mutable dataclass, so handing one out twice would let a gate or a caller
change what the other sees; it is never done here.

Import safety. Nothing here imports ``cns`` or ``augur`` when this module
loads; both are imported inside the functions that need them. Without ``cns``
installed, ``make_gate()`` raises ``augur.cns_connector.CnsNotInstalled`` (an
``ImportError``) at construction. The candidate functions need only augur,
not cns, because ``SimulationConfig`` works either way.

Run as a script for the self-check:  python fixture_augur.py
"""

from __future__ import annotations

import os
import random
import sys
import tempfile
from typing import Any

POSITION = "ALPHA"

REFUSED_OUTCOME = "TERMINAL_BREACH"

DESCRIPTION = (
    "Before the summary is written, simulates the planned action's scenario "
    "and vetoes it if the simulation predicts an unstable or critical regime "
    "at its last step."
)

#: The label every verdict carries as ``subject``: what was judged.
SUBJECT = "planned-action-scenario"

#: Bound into every verdict's digest. Equal to ``run_simulation``'s own default.
NOISE_SCALE = 4.0

#: What the verdict reason begins with, for each kind of verdict.
OK_REASON_PREFIX = "no objection: the simulation ended in regime STABLE"
REFUSED_REASON_PREFIX = "refused: the simulation predicts regime CRITICAL"

_DEFAULT_AUDIT_LOG = "augur_audit.log"

#: One temp file per process, made on first use. Not a verdict input.
_audit_log_path: str | None = None


def _keep_audit_log_out_of_cwd() -> None:
    """Send AUGUR's audit lines to a temp file unless the caller chose a path.

    ``augur.kernel`` reads ``AUGUR_AUDIT_LOG`` once, when it is first imported,
    and otherwise appends to ``augur_audit.log`` in the working directory. If
    the demo runs from inside a repository that would create a log there. This
    sets the module's path, which ``audit_append`` reads on every call (the
    library's own tests do the same), so it works whether or not augur was
    imported before this fixture. A path the caller chose, by the environment
    variable or by setting it before this runs, is left alone.
    """
    global _audit_log_path
    if os.environ.get("AUGUR_AUDIT_LOG"):
        return
    import augur.kernel as kernel

    if getattr(kernel, "_AUDIT_LOG_PATH", None) != _DEFAULT_AUDIT_LOG:
        return
    if _audit_log_path is None:
        handle, _audit_log_path = tempfile.mkstemp(
            prefix="augur_demo_audit_", suffix=".log"
        )
        os.close(handle)
    kernel._AUDIT_LOG_PATH = _audit_log_path


def make_gate() -> Any:
    """A new, independent ``cns.gate.Gate``: the library's own ``CnsGate``.

    Each call builds a new ``CnsGate``; no instance is cached or shared.
    Raises ``CnsNotInstalled`` when CNS is absent (the connector checks at
    construction, not on first use), before anything else is touched.
    """
    from augur.cns_connector import CnsGate

    gate = CnsGate(noise_scale=NOISE_SCALE, subject=SUBJECT)
    _keep_audit_log_out_of_cwd()
    return gate


def ok_candidate() -> Any:
    """A calm planned scenario, which AUGUR lets through. Fresh each call.

    The measure starts 10 below a fixed target of 100.0 and has 30 steps to
    settle. Needs no CNS.
    """
    from augur import SimulationConfig

    return SimulationConfig(
        initial_state=90.0,
        initial_target=100.0,
        shifted_target=100.0,
        target_shift_step=None,
        total_steps=30,
        seed=42,
    )


def refused_candidate() -> Any:
    """A violent planned scenario, which AUGUR vetoes. Fresh each call.

    The measure must travel from -500.0 to 1000.0 inside 8 steps. Same seed
    as ``ok_candidate()``; the swing, not the seed, decides the verdict.
    Needs no CNS.
    """
    from augur import SimulationConfig

    return SimulationConfig(
        initial_state=-500.0,
        initial_target=1000.0,
        shifted_target=1000.0,
        target_shift_step=None,
        total_steps=8,
        seed=42,
    )


def _check(condition: bool, label: str, failures: list) -> None:
    print(f"  [{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def _audit_lines() -> int:
    """Lines in the audit log AUGUR is currently writing to (0 if absent)."""
    import augur.kernel as kernel

    try:
        with open(kernel._AUDIT_LOG_PATH) as log:
            return sum(1 for _ in log)
    except OSError:
        return 0


def _self_check() -> int:
    try:
        gate = make_gate()
    except ImportError as exc:  # CnsNotInstalled is an ImportError
        print(f"make_gate() could not run: {type(exc).__name__}: {exc}")
        print("The module imported fine; the gate itself needs cns installed.")
        try:
            ok_in, bad_in = ok_candidate(), refused_candidate()
            print(
                "The candidates need only augur and were built without cns: "
                f"ok seed={ok_in.seed} steps={ok_in.total_steps}, "
                f"refused seed={bad_in.seed} steps={bad_in.total_steps}"
            )
        except ImportError as inner:
            print(f"the candidates could not be built either: {inner}")
        return 2

    from augur.cns_connector import scenario_digest
    from cns.gate import (
        Gate,
        GateChain,
        GateOutcome,
        GatePosition,
        resolve,
        unbound,
    )

    failures: list = []
    print(f"gate name      : {gate.name}")
    print(f"POSITION       : {POSITION} (gate declares {gate.position.name})")
    print(f"REFUSED_OUTCOME: {REFUSED_OUTCOME}")
    print(f"DESCRIPTION    : {DESCRIPTION}")

    cwd_log = os.path.join(os.getcwd(), _DEFAULT_AUDIT_LOG)
    cwd_log_size = os.path.getsize(cwd_log) if os.path.exists(cwd_log) else None
    lines_before = _audit_lines()

    ok_in, bad_in = ok_candidate(), refused_candidate()
    ok_fields, bad_fields = ok_in.as_dict(), bad_in.as_dict()
    ok_res, bad_res = gate.check(ok_in), gate.check(bad_in)
    lines_after = _audit_lines()
    for label, res in (("ok     ", ok_res), ("refused", bad_res)):
        print(
            f"{label}: position={res.position.name} outcome={res.outcome.name} "
            f"reason={res.reason!r} subject={res.subject!r} "
            f"digest={res.subject_digest[:16]}... bound={res.bound()}"
        )

    ok_digest = scenario_digest(ok_in, NOISE_SCALE)
    bad_digest = scenario_digest(bad_in, NOISE_SCALE)

    print("checks")
    _check(isinstance(gate, Gate), "gate satisfies cns.gate.Gate", failures)
    _check(
        gate.position is GatePosition.ALPHA and gate.position.name == POSITION,
        "POSITION matches the gate's declared position",
        failures,
    )
    _check(
        GateChain(alpha=(gate,)).misplaced() == (),
        "gate sits in the alpha slot",
        failures,
    )
    _check(
        ok_res.outcome is GateOutcome.PASS
        and ok_res.reason.startswith(OK_REASON_PREFIX),
        "ok candidate PASSES",
        failures,
    )
    _check(
        bad_res.outcome is GateOutcome[REFUSED_OUTCOME]
        and bad_res.reason.startswith(REFUSED_REASON_PREFIX),
        f"refused candidate gives {REFUSED_OUTCOME} with a CRITICAL regime reason",
        failures,
    )
    _check(
        bad_res.blocking() and not ok_res.blocking(),
        "the refusal blocks and the pass does not",
        failures,
    )
    _check(
        ok_res.position is GatePosition.ALPHA and bad_res.position is GatePosition.ALPHA,
        "both verdicts carry position ALPHA",
        failures,
    )
    _check(unbound([ok_res, bad_res]) == (), "both verdicts are bound", failures)
    _check(
        ok_res.binds(SUBJECT, ok_digest) and bad_res.binds(SUBJECT, bad_digest),
        "each verdict binds to the scenario it judged",
        failures,
    )
    _check(
        not ok_res.binds(SUBJECT, bad_digest) and not bad_res.binds(SUBJECT, ok_digest),
        "neither verdict binds to the other scenario",
        failures,
    )
    _check(ok_digest != bad_digest, "the two scenarios digest differently", failures)
    _check(resolve([ok_res]) is GateOutcome.PASS, "resolve([ok]) is PASS", failures)
    _check(
        resolve([ok_res, bad_res]) is GateOutcome[REFUSED_OUTCOME],
        "resolve([ok, refused]) is the refusal outcome",
        failures,
    )
    _check(
        ok_in.as_dict() == ok_fields and bad_in.as_dict() == bad_fields,
        "checking left both candidates unchanged",
        failures,
    )

    # Where the audit lines went: one per simulated step, and not into the cwd.
    expected = ok_in.total_steps + bad_in.total_steps
    _check(
        lines_after - lines_before == expected,
        f"checking appended {expected} audit lines (one per step) "
        f"to {os.environ.get('AUGUR_AUDIT_LOG') or _audit_log_path}",
        failures,
    )
    cwd_log_size_now = os.path.getsize(cwd_log) if os.path.exists(cwd_log) else None
    _check(
        cwd_log_size_now == cwd_log_size,
        f"no {_DEFAULT_AUDIT_LOG} was created or grown in the working directory",
        failures,
    )

    # Determinism, and independence from the host's global random state.
    random.seed(1)
    again_ok = gate.check(ok_in)
    random.seed(999)
    again_bad = gate.check(bad_in)
    _check(
        again_ok == ok_res and again_bad == bad_res,
        "re-checking, after seeding the global random differently, gives equal verdicts",
        failures,
    )

    # Independent gates.
    other = make_gate()
    _check(
        other is not gate and other.name == gate.name,
        "make_gate() twice gives distinct gate objects",
        failures,
    )
    other_bad = other.check(refused_candidate())
    other_ok = other.check(ok_candidate())
    _check(
        other_ok == ok_res and other_bad == bad_res,
        "a second gate, checking in the other order, gives the same verdicts",
        failures,
    )
    _check(
        gate.check(ok_candidate()) == ok_res and gate.check(refused_candidate()) == bad_res,
        "the first gate is unchanged by the second gate's checks",
        failures,
    )

    # Fresh candidates.
    a, b = ok_candidate(), ok_candidate()
    _check(
        a is not b and a.as_dict() == b.as_dict() == ok_fields,
        "ok_candidate() returns a fresh object each call, with equal content",
        failures,
    )
    c, d = refused_candidate(), refused_candidate()
    _check(
        c is not d and c.as_dict() == d.as_dict() == bad_fields,
        "refused_candidate() returns a fresh object each call, with equal content",
        failures,
    )
    a.seed = 7
    a.total_steps = 5
    _check(
        ok_candidate().as_dict() == ok_fields and b.as_dict() == ok_fields,
        "changing one ok candidate does not change the next one or another",
        failures,
    )
    _check(
        not ok_res.binds(SUBJECT, scenario_digest(a, NOISE_SCALE)),
        "the verdict does not bind to a candidate edited after it was issued",
        failures,
    )

    print("PASS" if not failures else f"FAILED: {len(failures)} check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_self_check())
