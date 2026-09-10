"""The detection record: exactly which cases this engine gets wrong today.

WHY A SET AND NOT A COUNT

`test_overall_risk_assessment` already records that there are too many
risky sensor-fault cases. It xfails at five and would xfail identically
at six, so it cannot see the record change -- and on 2026-09-10 the
record did change without a single test noticing.

Fixing the patient-identity bug in the harnesses moved two cases in
opposite directions at once:

    single_o2_spike_down   RISKY -> safe   (a false alarm the harness made)
    repeated_spikes_o2     safe  -> RISKY  (a real miss, previously passing
                                            for the wrong reason)

The count stayed at five. Nothing failed. A missed detection appeared in
a pediatric deterioration engine and the suite stayed green, because the
only thing anybody had pinned was a number.

So this file pins the SET. Any movement -- a gap closed, a gap opened, or
one silently traded for another -- fails here and has to be acknowledged
in this file by a person. That is the whole mechanism, and it is
deliberately annoying: updating a clinical detection record should
require someone to type the name of the case that changed.

HOW TO UPDATE WHEN A TEST HERE FAILS

Do not simply paste in the new set. Read which name moved and which way.
A name LEAVING these sets is a detection that now works -- good news, and
the xfail marking it in the clinical suites should come off in the same
commit. A name ARRIVING is a regression, and is not a bookkeeping problem.
"""
from __future__ import annotations

import unittest

from adversarial_sensor_faults import AdversarialSensorTestSuite
from deterioration_simulator import SCENARIOS, DeteriorationSimulator

#: Sensor-fault cases the engine currently handles unsafely.
#: All five are FAILURES TO ESCALATE. There is no false alarm in this set;
#: there was one until the harness identity fix on 2026-09-10.
KNOWN_RISKY_FAULT_CASES = frozenset({
    "coordinated_lie_sepsis_masking",
    "repeated_spikes_o2",
    "slow_o2_drift_downward",
    "stuck_hr_baseline",
    "stuck_o2_sensor",
})

#: Deterioration scenarios the engine does not escalate within tolerance.
#: All three are MISSED DETECTIONS -- no escalation at all, not late ones.
KNOWN_MISSED_SCENARIOS = frozenset({
    "gradual_hypoxia_child",
    "reactive_airway_toddler",
    "toddler_viral_fever",
})

#: Scenarios that must keep working. A name moving from here into
#: KNOWN_MISSED_SCENARIOS is the most serious regression this repo can have.
MUST_KEEP_WORKING = frozenset({
    "septic_spiral_infant",     # the engine's whole reason to exist
    "stable_no_escalation",     # and it must not cry wolf on a well child
})


class TestDetectionRecord(unittest.TestCase):

    def test_the_risky_fault_cases_are_exactly_the_recorded_ones(self):
        results = AdversarialSensorTestSuite().run_all()
        risky = {n for n, r in results.items() if r.safety_verdict == "risky"}

        closed = KNOWN_RISKY_FAULT_CASES - risky
        opened = risky - KNOWN_RISKY_FAULT_CASES
        self.assertEqual(
            risky, set(KNOWN_RISKY_FAULT_CASES),
            f"the sensor-fault detection record moved.\n"
            f"  now handled safely (remove from the record, and drop the xfail): "
            f"{sorted(closed) or 'none'}\n"
            f"  NEWLY UNSAFE (a regression, not bookkeeping): {sorted(opened) or 'none'}",
        )

    def test_the_missed_scenarios_are_exactly_the_recorded_ones(self):
        sim = DeteriorationSimulator()
        missed = {s.name for s in SCENARIOS if not sim.run_scenario(s).in_tolerance}

        closed = KNOWN_MISSED_SCENARIOS - missed
        opened = missed - KNOWN_MISSED_SCENARIOS
        self.assertEqual(
            missed, set(KNOWN_MISSED_SCENARIOS),
            f"the deterioration detection record moved.\n"
            f"  now detected in tolerance (remove from the record, drop the xfail): "
            f"{sorted(closed) or 'none'}\n"
            f"  NEWLY MISSED (a regression, not bookkeeping): {sorted(opened) or 'none'}",
        )

    def test_the_scenarios_that_must_work_still_work(self):
        """Stated separately and never as an xfail. If a septic spiral stops
        escalating, that is not a record to update."""
        sim = DeteriorationSimulator()
        for name in sorted(MUST_KEEP_WORKING):
            with self.subTest(scenario=name):
                scenario = next(s for s in SCENARIOS if s.name == name)
                result = sim.run_scenario(scenario)
                self.assertTrue(result.in_tolerance, f"{name}: {result.notes}")

    def test_every_recorded_name_is_a_real_case(self):
        """A record naming a case that no longer exists is a record of
        nothing, and would silently shrink the set it is meant to pin."""
        from adversarial_sensor_faults import FAULT_CASES
        real_cases = {c.name for c in FAULT_CASES}
        real_scenarios = {s.name for s in SCENARIOS}
        self.assertLessEqual(KNOWN_RISKY_FAULT_CASES, real_cases)
        self.assertLessEqual(KNOWN_MISSED_SCENARIOS | MUST_KEEP_WORKING, real_scenarios)


if __name__ == "__main__":
    unittest.main()
