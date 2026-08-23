"""Tests for the strictly-observational capacity/accuracy telemetry (capacity_planning.py)."""

import unittest

from capacity_planning import (
    ErlangCapacityForecaster,
    CapacityAlert,
    AccuracyMonitor,
)


class TestErlangForecaster(unittest.TestCase):
    def test_no_demand_abstains_safe(self):
        fc = ErlangCapacityForecaster()
        out = fc.forecast(0, current_clinicians=2)
        self.assertEqual(out.required_clinicians, 0)
        self.assertEqual(out.alert, CapacityAlert.SAFE)

    def test_required_grows_with_demand(self):
        fc = ErlangCapacityForecaster()
        low = fc.forecast(2).required_clinicians
        high = fc.forecast(20).required_clinicians
        self.assertGreater(high, low)

    def test_required_at_least_offered_load(self):
        fc = ErlangCapacityForecaster(minutes_per_response=30, window_minutes=60)
        out = fc.forecast(10)  # offered load = 10*30/60 = 5 Erlangs
        self.assertGreaterEqual(out.required_clinicians, 5)

    def test_understaffed_flags_critical(self):
        fc = ErlangCapacityForecaster()
        out = fc.forecast(20, current_clinicians=1)
        self.assertEqual(out.alert, CapacityAlert.CRITICAL)
        self.assertLess(out.headroom, 0)

    def test_well_staffed_flags_safe(self):
        fc = ErlangCapacityForecaster()
        req = fc.forecast(8).required_clinicians
        out = fc.forecast(8, current_clinicians=req + 3)
        self.assertEqual(out.alert, CapacityAlert.SAFE)
        self.assertGreaterEqual(out.headroom, 0)

    def test_invalid_params_raise(self):
        with self.assertRaises(ValueError):
            ErlangCapacityForecaster(minutes_per_response=0)


class TestAccuracyMonitor(unittest.TestCase):
    def test_insufficient_data_abstains(self):
        am = AccuracyMonitor(min_samples=10)
        am.record(True, True)
        am.record(False, False)
        self.assertEqual(am.report().status, "insufficient_data")

    def test_single_class_abstains(self):
        am = AccuracyMonitor(min_samples=2)
        am.record(True, True)
        am.record(True, True)  # only positives
        self.assertEqual(am.report().status, "insufficient_data")

    def test_metrics_computed_with_both_classes(self):
        am = AccuracyMonitor(min_samples=4)
        for p, a in [(True, True), (True, False), (False, False), (False, False), (True, True)]:
            am.record(p, a)
        rep = am.report()
        self.assertEqual(rep.status, "ok")
        self.assertEqual(rep.tp, 2)
        self.assertEqual(rep.fp, 1)
        self.assertEqual(rep.sensitivity, 1.0)        # caught both deteriorations
        self.assertAlmostEqual(rep.specificity, 2 / 3, places=3)

    def test_drift_detected_on_recent_miss_streak(self):
        am = AccuracyMonitor(min_samples=4, drift_window=10, drift_drop_threshold=0.1)
        # Early window: perfect sensitivity
        for _ in range(10):
            am.record(True, True)
            am.record(False, False)
        # Recent window: misses on positives (predicted False, actual True)
        for _ in range(10):
            am.record(False, True)
        rep = am.report()
        self.assertEqual(rep.status, "ok")
        self.assertTrue(rep.drift_detected)


if __name__ == "__main__":
    unittest.main()
