"""Do the clinical safety properties have tests that can actually fail?

Each mutant below breaks one property the source explicitly documents as
a safety fix. The property is only real if some test notices.

A SURVIVOR IS THE FINDING, NOT AN ERROR IN THIS FILE. It means the
comment in the source is the only thing guarding that behaviour, and the
next refactor that removes the line will pass CI.

The working tree is never modified.
"""
from __future__ import annotations

import pytest

from mutant_harness import assert_killed, run_tests_with_mutation

_O = "observe_consolidated.py"

# The suite each mutant is judged against. Aimed at the existing tests on
# purpose: the question is whether the coverage that already exists is
# capable of failing, not whether tests written alongside a mutant are.
OBSERVE_TESTS = "test_observe_consolidated.py"

MUTANTS = [
    # An engine that abstains says "I have no data", not "this looks
    # stable". The source's own comment: three low-confidence abstentions
    # could outvote a single high-confidence septic-shock detection and
    # drag a critical patient back to stable.
    ("abstaining engines are fused as if they had assessed the patient", _O,
     "        active = [o for o in outputs if not o.abstained]\n",
     "        active = list(outputs)\n"),

    # SpO2 below 88% is a hard rule, not a judgement call.
    ("the CRITICAL_O2 floor drops from 88% to 80%", _O,
     "        if vitals.oxygen_saturation < 88.0:\n",
     "        if vitals.oxygen_saturation < 80.0:\n"),

    # Dwell exists to damp noise on borderline disagreement. A hard rule
    # or a named shock syndrome is not noise; making it wait for a second
    # confirming reading delays escalation on a deteriorating child.
    ("a hard rule no longer bypasses dwell, so escalation waits", _O,
     "        bypass = hard_rule_fired or syndrome_fired\n",
     "        bypass = False\n"),

    # Requiring BOTH is the subtler version of the same defect: a
    # CRITICAL_O2 reading with no named syndrome stops bypassing.
    ("bypass demands a hard rule AND a syndrome instead of either", _O,
     "        bypass = hard_rule_fired or syndrome_fired\n",
     "        bypass = hard_rule_fired and syndrome_fired\n"),

]

# A RECORDED GAP, kept visible rather than deleted.
#
# Raising the hard-rule trigger from 0.5 to 0.9 changes no test outcome,
# and chasing it explains why rather than revealing a missing assertion:
#
#   * The nearest existing test (o2=85, hr=155) escalates because a
#     DANGEROUS_PATTERN syndrome fires, not because of the hard rule. Under
#     the mutation `syndrome_fired` still carries the bypass, so the
#     threshold decides nothing there.
#   * `bypass` is gated behind `candidate_regime in ("warning","critical")`,
#     so it can only skip DWELL on a case fusion already rates that highly.
#     It cannot lift a case fusion rated stable -- and a single CRITICAL_O2
#     at 85% with a normal heart rate fuses to stable (heuristic exactly
#     0.50, no syndrome). That is the engine's design, not a defect: the
#     bypass is about timing, not about overriding fusion.
#
# So the threshold is observable only in a narrow band, through a local
# variable with no public surface. Recorded as a gap in this repository's
# own idiom instead of quietly dropped: a mutant nobody can kill is a
# statement about testability, and deleting it would hide that statement.
HARD_RULE_TRIGGER = (
    "the hard-rule risk trigger rises from 0.5 to 0.9", _O,
    "        hard_rule_fired = heuristic_output is not None and heuristic_output.risk_score >= 0.5\n",
    "        hard_rule_fired = heuristic_output is not None and heuristic_output.risk_score >= 0.9\n",
)


def test_hard_rule_trigger_point_is_not_independently_observable():
    """Recorded gap: no public surface distinguishes 0.5 from 0.9.

    Closing it needs `hard_rule_fired` extracted into a predicate a test
    can call, which is a change to the engine and belongs to its author.
    """
    label, rel, old, new = HARD_RULE_TRIGGER
    result = run_tests_with_mutation(OBSERVE_TESTS, rel, old, new)
    if result.returncode == 0:
        pytest.xfail(
            "the 0.5 hard-rule trigger has no test that can fail on it; "
            "bypass is gated behind an already-warning candidate regime, so "
            "the threshold is unreachable from any public surface"
        )
    assert "failed" in result.stdout.lower() or "error" in result.stdout.lower()


@pytest.mark.parametrize("label,rel,old,new", MUTANTS, ids=[m[0] for m in MUTANTS])
def test_clinical_safety_mutant_is_killed(label, rel, old, new):
    assert_killed(label, OBSERVE_TESTS, run_tests_with_mutation(OBSERVE_TESTS, rel, old, new))


def test_the_suite_passes_unmutated():
    """A mutant is only judged against a suite that passes as written."""
    result = run_tests_with_mutation(
        OBSERVE_TESTS, _O,
        "        active = [o for o in outputs if not o.abstained]\n",
        "        active = [o for o in outputs if not o.abstained]\n",
    )
    assert result.returncode == 0, result.stdout[-2500:]
