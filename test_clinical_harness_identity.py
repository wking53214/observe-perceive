"""The clinical harnesses must hand the engine one patient, not a crowd.

WHAT THIS GUARDS AGAINST
------------------------
Both simulation harnesses used to build the patient id as
`f"SIM_{scenario.name}_{hour}"` -- a new identity for every hourly
reading. The engine keys all of its stateful safety machinery on
patient id: the Kalman trajectory tracker, per-patient dwell and
hysteresis, per-patient entropy. Handed a fresh patient each hour, every
one of those was untestable by construction. Measured before the fix,
with the trajectory engine explicitly enabled: it sat in warm-up on
10/10, 13/13 and 8/8 readings across the three deterioration scenarios
and contributed to zero verdicts.

The damage ran in both directions, which is why the count of failing
cases did not move when it was fixed:

  * `single_o2_spike_down` was recorded RISKY for escalating on a lone
    benign spike. It does not escalate for a real patient; the harness
    was manufacturing a false alarm.
  * `repeated_spikes_o2` was recorded SAFE. For a real patient it never
    escalates at all -- a missed detection that had been passing for the
    wrong reason.
  * `gradual_hypoxia_child` was recorded as detected late, at hour 11
    against an expected 8. For a real patient it is not detected at all.

A harness that gives every reading a new identity cannot measure any of
this, so these tests pin the identity itself rather than any particular
clinical verdict, which is free to change as the engine improves.
"""
from __future__ import annotations

import unittest

from adversarial_sensor_faults import AdversarialSensorTestSuite, FAULT_CASES
from deterioration_simulator import SCENARIOS, DeteriorationSimulator
from observe_consolidated import ObserveClinicalEngine


class _RecordingEngine:
    """Passes everything through and remembers who it was asked about."""

    def __init__(self, inner):
        self._inner = inner
        self.patient_ids = []

    def evaluate(self, vitals):
        self.patient_ids.append(vitals.patient_id)
        return self._inner.evaluate(vitals)


class TestOnePatientOneIdentity(unittest.TestCase):

    def test_a_deterioration_scenario_is_one_patient(self):
        scenario = next(s for s in SCENARIOS if s.name == "gradual_hypoxia_child")
        sim = DeteriorationSimulator()
        spy = _RecordingEngine(sim.engine)
        sim.engine = spy
        sim.run_scenario(scenario)

        self.assertGreater(len(spy.patient_ids), 1, "the scenario must feed several readings")
        self.assertEqual(
            len(set(spy.patient_ids)), 1,
            "every reading in one scenario must belong to the same patient; got "
            f"{len(set(spy.patient_ids))} distinct ids",
        )

    def test_a_fault_case_is_one_patient(self):
        case = next(c for c in FAULT_CASES if c.name == "stuck_o2_sensor")
        suite = AdversarialSensorTestSuite()
        spy = _RecordingEngine(suite.engine)
        suite.engine = spy
        suite.evaluate_fault_case(case)

        self.assertGreater(len(spy.patient_ids), 1)
        self.assertEqual(len(set(spy.patient_ids)), 1)


class TestStatefulPathIsReachable(unittest.TestCase):
    """The engine's stateful machinery was untestable through the harnesses
    for as long as the identity bug stood. These are characterization
    tests: they record that the path can now be exercised at all, not that
    any particular clinical answer is correct."""

    def _feed(self, engine, scenario):
        from datetime import timedelta

        from observe_consolidated import VitalsSnapshot
        base = DeteriorationSimulator().base_time
        verdicts = []
        for hour, (hr, o2, rr, temp) in enumerate(scenario.vitals_sequence):
            verdicts.append(engine.evaluate(VitalsSnapshot(
                patient_id=f"SIM_{scenario.name}",
                timestamp=base + timedelta(hours=hour),
                heart_rate=hr, oxygen_saturation=o2,
                respiratory_rate=rr, temperature=temp,
                context={"age_months": scenario.age_months},
            )))
        return verdicts

    def test_the_trajectory_engine_leaves_warm_up_when_enabled(self):
        """Before the identity fix this was 0 of 13, every time."""
        scenario = next(s for s in SCENARIOS if s.name == "gradual_hypoxia_child")
        verdicts = self._feed(ObserveClinicalEngine(enable_kalman=True), scenario)

        contributed = [
            v for v in verdicts
            if "trajectory_kalman" in v.active_engines
            and not any("warming up" in r for r in v.triggered_rules)
        ]
        self.assertGreater(
            len(contributed), 0,
            "the trajectory engine never left warm-up, which means the harness "
            "is not giving it a trajectory to track",
        )

    def test_the_trajectory_engine_stays_out_when_disabled(self):
        """It is off by default. That is a clinical decision, recorded here
        so that changing it is a visible change and not a silent one."""
        scenario = next(s for s in SCENARIOS if s.name == "gradual_hypoxia_child")
        verdicts = self._feed(ObserveClinicalEngine(), scenario)
        self.assertTrue(
            all("trajectory_kalman" not in v.active_engines for v in verdicts)
        )


if __name__ == "__main__":
    unittest.main()
