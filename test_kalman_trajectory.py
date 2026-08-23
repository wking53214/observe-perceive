"""Tests for the Kalman trajectory engine and its opt-in OBSERVE integration."""

import unittest
from datetime import datetime, timezone, timedelta

from kalman_trajectory import PatientKalmanTracker
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot


BASE = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)


def _spiral():
    # Stable baseline then a rising-HR / falling-O2 deterioration.
    return [(85, 98), (86, 98), (88, 97), (95, 96), (108, 94), (120, 92), (132, 90)]


class TestKalmanTracker(unittest.TestCase):
    def test_warmup_abstains(self):
        tr = PatientKalmanTracker()
        r0 = tr.update(85, 98, 30, 37.5, BASE)
        self.assertTrue(r0["abstained"])
        self.assertEqual(r0["score"], 0.0)

    def test_deterministic_across_identical_sequences(self):
        def run():
            t = PatientKalmanTracker()
            return [round(t.update(hr, o2, 30, 37.5, BASE + timedelta(hours=i))["score"], 6)
                    for i, (hr, o2) in enumerate(_spiral())]
        self.assertEqual(run(), run())

    def test_risk_rises_on_sustained_deterioration(self):
        tr = PatientKalmanTracker()
        scores = [tr.update(hr, o2, 30, 37.5, BASE + timedelta(hours=i))["score"]
                  for i, (hr, o2) in enumerate(_spiral())]
        self.assertGreater(scores[-1], scores[2])  # later spiral > early settled state

    def test_velocity_tracks_direction(self):
        tr = PatientKalmanTracker()
        last = None
        for i, (hr, o2) in enumerate(_spiral()):
            last = tr.update(hr, o2, 30, 37.5, BASE + timedelta(hours=i))
        # HR rising -> positive velocity; O2 falling -> negative velocity
        self.assertGreater(last["details"]["heart_rate_velocity"], 0)
        self.assertLess(last["details"]["oxygen_saturation_velocity"], 0)

    def test_stable_stream_stays_low(self):
        tr = PatientKalmanTracker()
        last = None
        for i in range(8):
            last = tr.update(100, 98, 24, 37.0, BASE + timedelta(hours=i))
        self.assertLess(last["score"], 0.2)
        self.assertFalse(last["abstained"])


class TestObserveKalmanIntegration(unittest.TestCase):
    def test_disabled_by_default(self):
        eng = ObserveClinicalEngine()  # default
        d = eng.evaluate(VitalsSnapshot("P", BASE, 110, 98.0, 24, 37.0, {"age_months": 24}))
        self.assertNotIn("trajectory_kalman", d.active_engines)

    def test_enabled_adds_adapter_to_fusion(self):
        eng = ObserveClinicalEngine(enable_kalman=True)
        last = None
        for i, (hr, o2) in enumerate(_spiral()):
            last = eng.evaluate(VitalsSnapshot("PK", BASE + timedelta(hours=i), hr, o2, 30, 37.5, {"age_months": 24}))
        self.assertIn("trajectory_kalman", last.active_engines)
        self.assertTrue(eng.audit_ledger.verify_integrity())

    def test_kalman_mode_preserves_decision_determinism(self):
        def run():
            eng = ObserveClinicalEngine(enable_kalman=True)
            out = []
            for i, (hr, o2) in enumerate(_spiral()):
                d = eng.evaluate(VitalsSnapshot("PK", BASE + timedelta(hours=i), hr, o2, 30, 37.5, {"age_months": 24}))
                out.append((round(d.risk_score, 6), d.regime.value, d.decision_fingerprint))
            return out
        self.assertEqual(run(), run())

    def test_per_patient_isolation(self):
        # Two patients tracked independently; one's trajectory must not bleed into the other.
        eng = ObserveClinicalEngine(enable_kalman=True)
        for i, (hr, o2) in enumerate(_spiral()):
            eng.evaluate(VitalsSnapshot("SPIRAL", BASE + timedelta(hours=i), hr, o2, 30, 37.5, {"age_months": 24}))
        # A fresh patient's first reading is warm-up regardless of the other patient's history.
        d = eng.evaluate(VitalsSnapshot("FRESH", BASE, 100, 98.0, 24, 37.0, {"age_months": 24}))
        self.assertIn("trajectory_kalman", d.active_engines)
        self.assertTrue(eng.audit_ledger.verify_integrity())


if __name__ == "__main__":
    unittest.main()
