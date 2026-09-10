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

# THE GAP THAT WAS RECORDED HERE IS NOW CLOSED.
#
# It read: "the hard-rule risk trigger rises from 0.5 to 0.9 changes no test
# outcome". The cause was not a missing assertion. The trigger was a literal
# compared against a local variable inside `evaluate`, with no public surface
# a test could reach -- and the nearest existing test escalated via a
# DANGEROUS_PATTERN syndrome, so the threshold decided nothing there.
#
# Extracting the cassette gave it a surface. HARD_RULE_RISK is now a named
# constant behind a callable the domain owns, and the mutation below dies.
# The gap was a symptom of the missing seam, which is why no amount of extra
# test-writing against the old shape would have closed it.
CASSETTE_MUTANTS = [
    ("the hard-rule risk trigger rises from 0.5 to 0.9", "pediatric_cassette.py",
     "HARD_RULE_RISK = 0.5\n", "HARD_RULE_RISK = 0.9\n"),
    ("any engine's score can fire the hard rule, not just the domain's", "pediatric_cassette.py",
     '            if out.engine_name == HARD_RULE_ENGINE:\n',
     '            if True:\n'),
    ("the domain stops reporting its subject identity", "pediatric_cassette.py",
     "        return obs.patient_id\n", '        return "ALL_SUBJECTS"\n'),
]

SEAM_TESTS = "test_cassette_seam.py"


@pytest.mark.parametrize("label,rel,old,new", CASSETTE_MUTANTS,
                         ids=[m[0] for m in CASSETTE_MUTANTS])
def test_cassette_mutant_is_killed(label, rel, old, new):
    assert_killed(label, SEAM_TESTS, run_tests_with_mutation(SEAM_TESTS, rel, old, new))


# The policy itself, mutated. A registry rule that has never been shown to
# reject anything is a comment; these prove it rejects.
REGISTRY_MUTANTS = [
    ("one domain is enough to claim agnosticism", "installed_cassettes.py",
     "MINIMUM_DOMAINS = 2\n", "MINIMUM_DOMAINS = 1\n"),
    ("domains may share an observation type", "installed_cassettes.py",
     "    types = {type(e.nominal()) for e in entries}\n",
     "    types = set(range(len(entries)))\n"),
    ("the industrial domain silently stops being installed", "installed_cassettes.py",
     "        cassette=IndustrialCassette(),\n",
     "        cassette=PediatricCassette(),\n"),
]

REGISTRY_TESTS = "test_all_cassettes.py"


@pytest.mark.parametrize("label,rel,old,new", REGISTRY_MUTANTS,
                         ids=[m[0] for m in REGISTRY_MUTANTS])
def test_registry_mutant_is_killed(label, rel, old, new):
    assert_killed(label, REGISTRY_TESTS, run_tests_with_mutation(REGISTRY_TESTS, rel, old, new))
