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

from cassette import REQUIRED, Cassette, conformance_failures, require_cassette
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
        rejected at load, so every problem is reported together.

        Derived from REQUIRED rather than a hardcoded count: adding a
        question to the contract should not need this test edited, but a
        checker that silently stopped reporting one still fails here.
        """
        class Empty:
            pass
        problems = conformance_failures(Empty())
        named = {member for member in REQUIRED
                 if any(repr(member) in p for p in problems)}
        self.assertEqual(named, set(REQUIRED))
        self.assertEqual(len(problems), len(REQUIRED))

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
        from industrial_cassette import IndustrialCassette
        return ObserveClinicalEngine(cassette=IndustrialCassette())

    def _reading(self, **o):
        from industrial_cassette import AssetReading
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


class TestTheTrustBoundaryIsDomainDriven(unittest.TestCase):
    """What is physically impossible is a domain fact, so the sanitizer
    must use the cassette's tables, not a clinical one."""

    def test_pediatric_bounds_still_apply_by_default(self):
        from observe_consolidated import sanitize_context
        clean, notes = sanitize_context({"age_months": 12, "previous_o2": 200.0})
        self.assertEqual(clean, {"age_months": 12})
        self.assertTrue(any("previous_o2" in n for n in notes))

    def test_an_industrial_cassette_gets_industrial_bounds(self):
        """rpm 45000 is impossible for this machine class and must be
        dropped; previous_o2 is meaningless here and must simply pass
        through as an unrecognised key rather than being range-checked
        against a child's oxygen saturation."""
        from observe_consolidated import sanitize_context
        from industrial_cassette import IndustrialCassette
        cas = IndustrialCassette()
        clean, notes = sanitize_context(
            {"rpm": 45000.0, "ambient_temp_c": 22.0, "previous_o2": 200.0},
            cas.context_bounds(), cas.context_list_bounds())
        self.assertNotIn("rpm", clean)
        self.assertEqual(clean["ambient_temp_c"], 22.0)
        self.assertEqual(clean["previous_o2"], 200.0)
        self.assertTrue(any("rpm" in n for n in notes))

    def test_list_bounds_filter_elements_rather_than_dropping_the_key(self):
        from observe_consolidated import sanitize_context
        from industrial_cassette import IndustrialCassette
        cas = IndustrialCassette()
        clean, notes = sanitize_context(
            {"recent_vibration": [2.0, -5.0, 3.0, float("inf")]},
            cas.context_bounds(), cas.context_list_bounds())
        self.assertEqual(clean["recent_vibration"], [2.0, 3.0])
        self.assertTrue(any("filtered 2" in n for n in notes))

    def test_the_engine_passes_its_own_cassettes_tables(self):
        """End to end: an out-of-range rpm reaching the real engine is
        dropped by the industrial bounds, which the core never knew."""
        from industrial_cassette import AssetReading, IndustrialCassette
        engine = ObserveClinicalEngine(cassette=IndustrialCassette())
        obs = AssetReading("PUMP-Z", datetime.now(timezone.utc), 2.0, 50.0, 3.0,
                           context={"rpm": 45000.0})
        engine.evaluate(obs)  # must not raise, and must not trust the value


class TestTrajectoryIsGenericOverChannels(unittest.TestCase):
    """The Kalman tracker took four named vitals. It now takes whatever
    channels a cassette declares -- three here, with different names,
    different units and a different bad direction."""

    def _engine(self):
        from industrial_cassette import IndustrialCassette
        return ObserveClinicalEngine(cassette=IndustrialCassette(), enable_kalman=True)

    def test_a_degrading_bearing_is_caught_before_it_crosses_the_hard_rule(self):
        """Vibration climbing 0.9 mm/s per hour is caught at 4.7 mm/s --
        below the ISO 10816 unacceptable line of 7.1. That is the whole
        point of a trajectory: the trend arrives before the threshold."""
        from industrial_cassette import AssetReading
        from datetime import timedelta
        base = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        engine = self._engine()
        velocity_seen = []
        for i in range(6):
            v = engine.evaluate(AssetReading(
                "PUMP-K", base + timedelta(hours=i),
                vibration_mm_s=2.0 + i * 0.9,
                bearing_temp_c=55.0 + i * 3.0,
                oil_pressure_bar=3.0 - i * 0.35))
            velocity_seen += [r for r in v.triggered_rules
                              if "KALMAN_VELOCITY: vibration_mm_s" in r]
            if i == 3:
                self.assertLess(2.0 + i * 0.9, 7.1, "must still be under the hard rule")
                self.assertTrue(velocity_seen, "trend should be reported before the threshold")

    def test_the_tracker_abstains_during_warmup_in_any_domain(self):
        from industrial_cassette import AssetReading
        base = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)
        engine = self._engine()
        v = engine.evaluate(AssetReading("PUMP-W", base, 2.0, 55.0, 3.0))
        self.assertTrue(any("warming up" in r for r in v.triggered_rules))

    def test_a_reading_missing_a_declared_channel_is_refused_loudly(self):
        """channels() and channel_model() must agree. Silently tracking
        three of four channels would degrade the filter invisibly."""
        from kalman_trajectory import PatientKalmanTracker
        tr = PatientKalmanTracker()
        with self.assertRaises(KeyError) as ctx:
            tr.update(values={"heart_rate": 100.0}, timestamp=datetime.now(timezone.utc))
        self.assertIn("must agree", str(ctx.exception))

    def test_the_pediatric_model_is_derived_not_restated(self):
        """The neutral model is built FROM the original tables, so the
        clinical numbers cannot drift away from the ones in use."""
        from kalman_trajectory import _CHANNEL_TUNING, _VELOCITY_RULES, _PEDIATRIC_CHANNEL_MODEL
        self.assertEqual(set(_PEDIATRIC_CHANNEL_MODEL), set(_CHANNEL_TUNING))
        m = _PEDIATRIC_CHANNEL_MODEL["oxygen_saturation"]
        self.assertEqual(m.measurement_noise, _CHANNEL_TUNING["oxygen_saturation"]["r"])
        self.assertEqual(m.adverse_direction, _VELOCITY_RULES["oxygen_saturation"][0])


class TestTheDomainOwnsItsVocabulary(unittest.TestCase):
    """Audit labels go into an append-only, hash-chained ledger. A refactor
    does not get to rename a governed vocabulary, and a second domain does
    not get to inherit the first one's words."""

    def test_the_clinical_ledger_vocabulary_is_unchanged(self):
        """Byte-identical to what the ledger already contains. If this
        fails, historical entries have become unmatchable."""
        from pediatric_cassette import PediatricCassette
        labels = PediatricCassette().labels()
        self.assertEqual(labels["record_kind"], "clinical_assessment")
        self.assertEqual(labels["safety_bypass"], "CLINICAL_SAFETY_BYPASS")

    def test_the_clinical_engine_still_emits_its_own_words(self):
        engine = ObserveClinicalEngine()
        v = engine.evaluate(_v(oxygen_saturation=85.0, heart_rate=155,
                               context={"age_months": 24, "force_heavy": True}))
        self.assertTrue(any("CLINICAL_SAFETY_BYPASS" in r for r in v.triggered_rules))

    def test_another_domain_gets_its_own_words(self):
        from industrial_cassette import AssetReading, IndustrialCassette
        engine = ObserveClinicalEngine(cassette=IndustrialCassette())
        v = engine.evaluate(AssetReading("PUMP-1", datetime.now(timezone.utc), 9.4, 98.0, 0.6))
        self.assertTrue(any("EQUIPMENT_SAFETY_BYPASS" in r for r in v.triggered_rules))
        self.assertFalse(any("CLINICAL" in r for r in v.triggered_rules))

    def test_a_domain_that_says_nothing_gets_neutral_words(self):
        """A cassette with no opinion must not inherit pediatrics."""
        from cassette import DEFAULT_LABELS, label
        class Quiet:
            def labels(self):
                return {}
        self.assertEqual(label(Quiet(), "safety_bypass"), "SAFETY_BYPASS")
        self.assertEqual(label(Quiet(), "record_kind"), "assessment")
        self.assertNotIn("CLINICAL", " ".join(DEFAULT_LABELS.values()))
