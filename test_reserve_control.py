"""Tests for the PERCEIVE-adjacent reserve control component (reserve_control.py)."""

import unittest

from reserve_control import ReserveModulator, ModulationAction


class TestReserveModulatorSafety(unittest.TestCase):
    def test_critical_always_escalates_even_saturated(self):
        rm = ReserveModulator()
        d = rm.modulate(0.1, base_should_escalate=False, is_critical_or_syndrome=True, utilization=1.0)
        self.assertEqual(d.action, ModulationAction.ESCALATE)
        self.assertTrue(d.exempt)

    def test_baseline_escalation_honored(self):
        rm = ReserveModulator()
        d = rm.modulate(0.6, base_should_escalate=True, is_critical_or_syndrome=False, utilization=0.5)
        self.assertEqual(d.action, ModulationAction.ESCALATE)

    def test_high_reserve_adds_proactive_escalation(self):
        rm = ReserveModulator(base_threshold=0.5, sensitivity=0.3)
        d = rm.modulate(0.35, base_should_escalate=False, is_critical_or_syndrome=False, utilization=0.0)
        self.assertEqual(d.action, ModulationAction.ESCALATE_PROACTIVE)

    def test_low_reserve_never_raises_threshold_above_base_by_default(self):
        rm = ReserveModulator(base_threshold=0.5, sensitivity=0.3)  # load-shedding OFF
        self.assertLessEqual(rm.effective_threshold(0.95), 0.5)

    def test_no_defer_without_load_shedding(self):
        rm = ReserveModulator()  # load-shedding OFF
        d = rm.modulate(0.6, base_should_escalate=True, is_critical_or_syndrome=False, utilization=0.99)
        self.assertEqual(d.action, ModulationAction.ESCALATE)

    def test_defer_only_when_load_shedding_enabled_and_saturated(self):
        rm = ReserveModulator(allow_load_shedding=True, load_shed_utilization=0.9)
        d = rm.modulate(0.6, base_should_escalate=True, is_critical_or_syndrome=False, utilization=0.95)
        self.assertEqual(d.action, ModulationAction.DEFER)

    def test_load_shedding_still_exempts_critical(self):
        rm = ReserveModulator(allow_load_shedding=True, load_shed_utilization=0.9)
        d = rm.modulate(0.9, base_should_escalate=True, is_critical_or_syndrome=True, utilization=0.99)
        self.assertEqual(d.action, ModulationAction.ESCALATE)


if __name__ == "__main__":
    unittest.main()
