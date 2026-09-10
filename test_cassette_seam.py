"""The seam between the core and a domain, checked in both directions.

Written BEFORE the engine was rewired to use the cassette, so the move
could be proved behaviour-preserving rather than hoped to be. The
differential test below is the one that matters: it sweeps the input
space and asserts the cassette and the engine's original method agree on
every point.
"""
from __future__ import annotations

import itertools
import unittest
from datetime import datetime, timezone

from cassette import Cassette, conformance_failures, require_cassette
from observe_consolidated import ObserveClinicalEngine, RiskOutput, VitalsSnapshot, regime_distribution
from pediatric_cassette import HARD_RULE_ENGINE, HARD_RULE_RISK, PediatricCassette


def _v(**o) -> VitalsSnapshot:
    d = dict(patient_id="P1", timestamp=datetime.now(timezone.utc), heart_rate=100,
             oxygen_saturation=97.0, respiratory_rate=24, temperature=37.0,
             context={"age_months": 24})
    d.update(o)
    return VitalsSnapshot(**d)


class TestConformance(unittest.TestCase):

    def test_the_pediatric_cassette_conforms(self):
        self.assertEqual(conformance_failures(PediatricCassette()), [])
        require_cassette(PediatricCassette())

    def test_conformance_names_every_missing_piece_at_once(self):
        """A cassette that fails on its fourth evaluation is worse than one
        rejected at load, so every problem is reported together."""
        class Empty:
            pass
        problems = conformance_failures(Empty())
        self.assertEqual(len(problems), 9)
        self.assertTrue(any("subject_id" in p for p in problems))
        self.assertTrue(any("hard_rule_fired" in p for p in problems))

    def test_a_non_callable_member_is_rejected(self):
        c = PediatricCassette()
        c.validate = "not a function"
        self.assertIn("'validate' is not callable", conformance_failures(c))

    def test_a_non_string_name_is_rejected(self):
        c = PediatricCassette()
        c.name = 3
        self.assertTrue(any("must be a str" in p for p in conformance_failures(c)))

    def test_runtime_protocol_accepts_the_cassette(self):
        self.assertIsInstance(PediatricCassette(), Cassette)


class TestSelectEnginesIsUnchanged(unittest.TestCase):
    """The differential check: the extracted rule must equal the original
    on every point of a swept input space, not merely on a happy path."""

    def test_cassette_and_engine_agree_across_the_input_space(self):
        cas = PediatricCassette()
        grid = itertools.product(
            [85.0, 91.0, 92.0, 97.0],          # oxygen_saturation, around 92
            [85, 90, 120, 140, 141],           # heart_rate, around 90 and 140
            [20, 35, 36, 46],                  # respiratory_rate, around 35
            [37.0, 38.5, 38.6],                # temperature, around 38.5
            [0.0, 0.61],                       # recent entropy, around 0.6
            [{}, {"force_heavy": True}, {"previous_o2": 95.0},
             {"recent_o2_readings": [95.0, 94.0]}],
        )
        checked = 0
        for o2, hr, rr, temp, entropy, extra in grid:
            ctx = {"age_months": 24}
            ctx.update(extra)
            v = _v(oxygen_saturation=o2, heart_rate=hr, respiratory_rate=rr,
                   temperature=temp, context=ctx)
            engine = ObserveClinicalEngine()
            engine._patient_entropy[v.patient_id] = entropy
            self.assertEqual(
                engine.select_engines(v), cas.select_engines(v, entropy),
                f"disagreement at o2={o2} hr={hr} rr={rr} temp={temp} "
                f"entropy={entropy} ctx={extra}",
            )
            checked += 1
        self.assertGreater(checked, 500, "the sweep must actually cover ground")


class TestHardRulePredicate(unittest.TestCase):
    """The escalation trigger, now reachable. It was a literal inside
    `evaluate` where no test could put a value into it -- recorded as a
    known gap by the mutation suite on 2026-09-10."""

    def _out(self, name, risk):
        return RiskOutput(name, risk, 0.9, regime_distribution(risk), [],
                          datetime.now(timezone.utc))

    # THE LITERALS BELOW ARE DELIBERATE. Writing these against
    # HARD_RULE_RISK made them move with the constant: mutation showed
    # 0.5 -> 0.9 with both still green, because a test that restates the
    # implementation asserts nothing about the domain. 0.5 is the clinical
    # fact -- a CRITICAL_O2 reading scores exactly 0.50 -- so 0.5 is what
    # the test pins. If the domain's answer changes, this test SHOULD fail
    # and a clinician should be the one to change it.

    def test_the_trigger_sits_at_exactly_one_half(self):
        self.assertEqual(HARD_RULE_RISK, 0.5)

    def test_at_the_threshold_the_rule_fires(self):
        cas = PediatricCassette()
        self.assertTrue(cas.hard_rule_fired([self._out(HARD_RULE_ENGINE, 0.5)]))

    def test_just_below_the_threshold_it_does_not(self):
        cas = PediatricCassette()
        self.assertFalse(cas.hard_rule_fired([self._out(HARD_RULE_ENGINE, 0.49)]))

    def test_a_single_critical_o2_reading_reaches_the_trigger(self):
        """The threshold is only meaningful if the domain actually produces
        it. A lone CRITICAL_O2 scores exactly 0.50, which is why the
        boundary is inclusive."""
        from observe_consolidated import RiskAdapters
        out = RiskAdapters.heuristic(_v(oxygen_saturation=85.0))
        self.assertEqual(out.risk_score, 0.5)
        self.assertTrue(PediatricCassette().hard_rule_fired([out]))

    def test_only_the_domains_hard_rule_engine_counts(self):
        """A high score from another engine is a vote, not a hard rule."""
        cas = PediatricCassette()
        self.assertFalse(cas.hard_rule_fired([self._out("bayesian", 0.99)]))

    def test_no_hard_rule_engine_present_is_not_a_firing(self):
        self.assertFalse(PediatricCassette().hard_rule_fired([]))


class TestChannelsAreDeclaredByTheDomain(unittest.TestCase):

    def test_the_cassette_names_its_own_numeric_series(self):
        ch = PediatricCassette().channels(_v())
        self.assertEqual(set(ch), {"heart_rate", "oxygen_saturation",
                                   "respiratory_rate", "temperature"})
        self.assertEqual(ch["oxygen_saturation"], 97.0)


if __name__ == "__main__":
    unittest.main()


class TestASecondIndustryRunsOnTheSameCore(unittest.TestCase):
    """The claim under test: the engine is domain-agnostic.

    A claim like that is worth nothing asserted and everything demonstrated,
    so this drives the real engine with a cassette that shares no vocabulary
    with medicine -- assets rather than patients, ISO 10816 vibration
    severity rather than oxygen saturation -- and checks it reaches the
    right verdicts. Every wiring gap found during the extraction was found
    by running this, not by reading the code.
    """

    def _engine(self):
        from example_industrial_cassette import IndustrialCassette
        return ObserveClinicalEngine(cassette=IndustrialCassette())

    def _reading(self, **o):
        from example_industrial_cassette import AssetReading
        d = dict(asset_id="PUMP-7", timestamp=datetime.now(timezone.utc),
                 vibration_mm_s=2.1, bearing_temp_c=55.0, oil_pressure_bar=3.2)
        d.update(o)
        return AssetReading(**d)

    def test_a_healthy_machine_is_stable(self):
        v = self._engine().evaluate(self._reading())
        self.assertEqual(v.regime.value, "stable")

    def test_a_failing_machine_escalates_without_waiting(self):
        """Vibration in the ISO 10816 unacceptable zone is this domain's
        answer to 'never noise', so dwell is skipped exactly as a
        CRITICAL_O2 reading skips it for a child."""
        v = self._engine().evaluate(
            self._reading(vibration_mm_s=9.4, bearing_temp_c=98.0, oil_pressure_bar=0.6))
        self.assertIn(v.regime.value, ("warning", "critical"))
        self.assertTrue(any("ISO10816_UNACCEPTABLE" in r for r in v.triggered_rules))
        self.assertTrue(any("hard-rule" in r for r in v.triggered_rules))

    def test_a_bad_sensor_is_a_fault_not_a_healthy_reading(self):
        v = self._engine().evaluate(self._reading(vibration_mm_s=float("nan")))
        self.assertEqual(v.regime.value, "warning")
        self.assertTrue(any("DATA_INTEGRITY_FAULT" in r for r in v.triggered_rules))

    def test_the_core_never_reads_a_field_this_domain_does_not_have(self):
        """AssetReading has no patient_id, no oxygen_saturation. If the core
        still reached for one, this raises AttributeError rather than
        failing an assertion -- which is the more useful failure."""
        engine = self._engine()
        obs = self._reading(vibration_mm_s=9.4)
        self.assertFalse(hasattr(obs, "patient_id"))
        self.assertFalse(hasattr(obs, "oxygen_saturation"))
        engine.evaluate(obs)

    def test_subjects_are_tracked_separately_across_domains(self):
        """Identity comes from the cassette, so per-subject state keys on
        asset_id here and patient_id there, with no core change."""
        engine = self._engine()
        engine.evaluate(self._reading(asset_id="PUMP-A", vibration_mm_s=9.4))
        engine.evaluate(self._reading(asset_id="PUMP-B"))
        self.assertIn("PUMP-A", engine._patient_entropy)
        self.assertIn("PUMP-B", engine._patient_entropy)
