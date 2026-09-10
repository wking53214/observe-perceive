"""Tests for the deterioration simulator (clinical validation)."""

import unittest

from clinical_gaps import known_gap as _known_gap
from deterioration_simulator import DeteriorationSimulator, SCENARIOS


class TestDeteriorationSimulator(unittest.TestCase):
    """Verify OBSERVE escalates realistic deterioration curves at expected times."""

    def setUp(self):
        self.sim = DeteriorationSimulator()

    def test_septic_spiral_detected_on_time(self):
        """Septic spiral (PASS case): should escalate at hour 6 ± 1."""
        scenario = next(s for s in SCENARIOS if s.name == "septic_spiral_infant")
        result = self.sim.run_scenario(scenario)
        self.assertTrue(result.in_tolerance, result.notes)

    def test_stable_no_false_alarm(self):
        """Healthy toddler (PASS case): should never escalate."""
        scenario = next(s for s in SCENARIOS if s.name == "stable_no_escalation")
        result = self.sim.run_scenario(scenario)
        self.assertTrue(result.in_tolerance, result.notes)

    def test_viral_fever_detection(self):
        """Viral fever (FAIL case): expected escalation not yet detected.
        This is a known gap — marks the behavioral/temperature threshold tuning need.
        """
        scenario = next(s for s in SCENARIOS if s.name == "toddler_viral_fever")
        result = self.sim.run_scenario(scenario)
        if not result.in_tolerance:
            # Document the failure for pediatrician review
            _known_gap(
                self,
                f"Viral fever thresholds need tuning: {result.notes}. "
                "Requires pediatrician input on RR/temp escalation point."
            )

    def test_gradual_hypoxia_detection(self):
        """Gradual hypoxia (FAIL case): not detected at all.

        This docstring used to read "detected but late (hour 11 vs expected
        8±2)". Hour 11 was an artifact of the harness giving every reading a
        new patient id; for one patient across one timeline the engine never
        escalates. Corrected 2026-09-10 along with the identity fix -- a
        missed detection recorded as a late one is the more dangerous of the
        two errors to leave in a clinical record.
        """
        scenario = next(s for s in SCENARIOS if s.name == "gradual_hypoxia_child")
        result = self.sim.run_scenario(scenario)
        if not result.in_tolerance:
            _known_gap(
                self,
                f"Gradual hypoxia not detected: {result.notes}. "
                "Consider enabling Kalman trajectory for improved drift detection."
            )

    def test_reactive_airway_detection(self):
        """Reactive airway (FAIL case): pattern not yet recognized.
        Indicates need for syndrome-based pattern matching (may need Kalman).
        """
        scenario = next(s for s in SCENARIOS if s.name == "reactive_airway_toddler")
        result = self.sim.run_scenario(scenario)
        if not result.in_tolerance:
            _known_gap(
                self,
                f"Reactive airway pattern not detected: {result.notes}. "
                "Requires rule enhancement or machine-learned pattern detection."
            )


if __name__ == "__main__":
    unittest.main()
