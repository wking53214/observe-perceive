"""The partial-assessment fault overlay, beyond the seven ported T2b tests.

test_observe_invariants.py carries the fork's seven overlay tests verbatim.
This file pins what the port had to decide for itself: the pediatric
cassette's answer (a neutral vector that is a finding in no engine, a
context-key map that covers every reader), nothing-assessable readings,
a bypass on a valid channel during a fault, fault-page deduplication, the
Kalman tracker, and provenance on the overlay path.
"""
from __future__ import annotations

import math
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

from governance_contracts import compute_state_commitment
from observe_consolidated import (
    ESCALATION_LOCK_SECONDS,
    PARAMETER_SET_VERSION,
    PEDIATRIC_NORMS,
    REGIME_RISK_FLOOR,
    UNASSESSABLE_CONFIDENCE_PENALTY,
    ObserveClinicalEngine,
    OperationalRegime,
    VitalsSnapshot,
    decision_fingerprint,
    regime_distribution,
    validate_vitals,
    verdict_state,
)
from pediatric_cassette import _CHANNEL_CONTEXT_KEYS, _VITALS_NEUTRAL, PediatricCassette

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NAN = float("nan")
_PROVENANCE_KEYS = ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")
_ALL_FAULTED = dict(heart_rate=NAN, oxygen_saturation=float("inf"), respiratory_rate=-1.0, temperature=999.0)
_HARD_RULE_ON_VALID = dict(oxygen_saturation=NAN, temperature=34.0, heart_rate=190.0)  # HYPOTHERMIA + TACHYCARDIA


def make_vitals(**overrides) -> VitalsSnapshot:
    defaults = dict(
        patient_id="P-OVL",
        timestamp=T0,
        heart_rate=100.0,
        oxygen_saturation=97.0,
        respiratory_rate=24.0,
        temperature=37.0,
        context={"age_months": 24},
    )
    defaults.update(overrides)
    return VitalsSnapshot(**defaults)


def _replay(entry) -> str:
    return decision_fingerprint({k: v for k, v in entry["data"].items() if k not in _PROVENANCE_KEYS})


def _drive_to_critical(engine, pid, ts=T0):
    v = engine.evaluate(make_vitals(patient_id=pid, oxygen_saturation=70.0, heart_rate=190.0,
                                    respiratory_rate=65.0, temperature=39.5, timestamp=ts))
    assert v.regime is OperationalRegime.CRITICAL and v.escalation_required
    return v


class _UnattributableFaults(PediatricCassette):
    """Pediatric in every answer except validate, whose fault names no channel."""

    def validate(self, obs):
        return ["lead_off"]


class _BrokenMask(PediatricCassette):
    def mask_faults(self, obs, faults):
        raise RuntimeError("broken domain answer")


class TestThePediatricMask(unittest.TestCase):
    """The cassette's answer: which value is inert, and which history goes with it."""

    AGES = {"neonatal": 1, "infant": 6, "toddler": 24, "child": 72, "generic": None}

    def _neutral(self, age_months, **ctx):
        context = dict(ctx)
        if age_months is not None:
            context["age_months"] = age_months
        return make_vitals(context=context, **_VITALS_NEUTRAL)

    def test_the_age_table_covers_every_norm_group(self):
        self.assertEqual(set(self.AGES), set(PEDIATRIC_NORMS))

    def test_the_neutral_vector_is_a_finding_in_no_engine_for_any_age_group(self):
        cassette = PediatricCassette()
        for group, age in self.AGES.items():
            with self.subTest(group=group):
                obs = self._neutral(age, force_heavy=True)
                self.assertEqual(validate_vitals(obs), [])
                outputs = [fn(obs) for fn in cassette.engines().values()]
                for out in outputs:
                    self.assertEqual(out.risk_score, 0.0, msg=(out.engine_name, out.triggered_rules))
                    if not out.abstained:
                        self.assertLessEqual(set(out.triggered_rules), {"BASE_RISK: 0.00"},
                                             msg=out.engine_name)
                self.assertFalse(cassette.hard_rule_fired(outputs))
                self.assertEqual(cassette.select_engines(self._neutral(age), 0.0), ["heuristic"])
                self.assertEqual(ObserveClinicalEngine().evaluate(obs).regime, OperationalRegime.STABLE)

    def test_the_mask_replaces_only_the_faulted_channel_and_drops_its_history(self):
        ctx = {"previous_o2": 90, "baseline_o2": 95, "history_o2": [95, 96, 95, 94, 96],
               "recent_o2_readings": [97, 96, 97, 96, 97], "previous_hr": 120,
               "age_months": 24, "time_delta_seconds": 60, "alert": "crying"}
        obs = make_vitals(oxygen_saturation=NAN, heart_rate=150.0, context=ctx)
        masked = PediatricCassette().mask_faults(obs, validate_vitals(obs))
        self.assertIsNot(masked, obs)
        self.assertEqual(masked.oxygen_saturation, _VITALS_NEUTRAL["oxygen_saturation"])
        self.assertEqual((masked.heart_rate, masked.respiratory_rate, masked.temperature),
                         (obs.heart_rate, obs.respiratory_rate, obs.temperature))
        self.assertEqual((masked.patient_id, masked.timestamp), (obs.patient_id, obs.timestamp))
        self.assertEqual(set(masked.context), {"previous_hr", "age_months", "time_delta_seconds", "alert"})
        # the reading as it arrived is untouched
        self.assertTrue(math.isnan(obs.oxygen_saturation))
        self.assertIn("previous_o2", obs.context)

    def test_every_key_an_engine_reads_for_a_channel_is_dropped_with_it(self):
        # Each history would fire its channel's rules against the neutral value if
        # trusted (the control run proves it); masking the channel must silence
        # them. previous_hr=100 beside the temperature history only keeps the
        # trajectory adapter from abstaining; HR 100 against it fires nothing.
        cases = {
            "oxygen_saturation": ({"previous_o2": 60.0, "baseline_o2": 80.0,
                                   "history_o2": [95.0, 96.0, 95.0, 94.0, 96.0],
                                   "recent_o2_readings": [97.0] * 5}, {},
                                  ("IMPLAUSIBLE_RATE", "CONSTANT_VALUE_STREAK", "O2_DRIFT", "O2_MOMENTUM")),
            "heart_rate": ({"previous_hr": 200.0, "baseline_hr": 60.0,
                            "history_hr": [100.0, 101.0, 99.0, 100.0, 102.0],
                            "hr_history": [60.0, 150.0, 60.0, 150.0]}, {},
                           ("HR_DECELERATION", "HR_MOMENTUM", "HR_DRIFT", "PHYSIO_INSTABILITY")),
            "respiratory_rate": ({"previous_rr": 5.0}, {}, ("RR_MOMENTUM",)),
            "temperature": ({"previous_temp": 40.0}, {"previous_hr": 100.0}, ("TEMP_DROP",)),
        }
        self.assertEqual(set(cases), set(_CHANNEL_CONTEXT_KEYS))
        for channel, (history, extra, family) in cases.items():
            with self.subTest(channel=channel):
                self.assertEqual(set(history), set(_CHANNEL_CONTEXT_KEYS[channel]))
                context = {"age_months": 24, "force_heavy": True, **extra, **history}
                control = ObserveClinicalEngine().evaluate(make_vitals(
                    context=context, **{channel: _VITALS_NEUTRAL[channel]}))
                self.assertTrue(any(tag in r for r in control.triggered_rules for tag in family),
                                msg=control.triggered_rules)
                faulted = ObserveClinicalEngine().evaluate(make_vitals(context=context, **{channel: NAN}))
                self.assertTrue(faulted.unassessable)
                self.assertFalse(any(tag in r for r in faulted.triggered_rules for tag in family),
                                 msg=faulted.triggered_rules)

    def test_a_fault_that_names_no_known_channel_masks_nothing(self):
        cassette = PediatricCassette()
        obs = make_vitals(oxygen_saturation=NAN)
        self.assertIsNone(cassette.mask_faults(obs, ["vibration_mm_s is not a finite number: nan"]))
        self.assertIsNone(cassette.mask_faults(obs, validate_vitals(obs) + ["lead_off"]))
        self.assertIsNone(cassette.mask_faults(obs, []))

    def test_every_channel_faulted_is_nothing_to_assess(self):
        obs = make_vitals(**_ALL_FAULTED)
        self.assertEqual(len(validate_vitals(obs)), 4)
        self.assertIsNone(PediatricCassette().mask_faults(obs, validate_vitals(obs)))

    def test_the_mask_tolerates_a_context_that_is_not_a_dict(self):
        obs = make_vitals(oxygen_saturation=NAN, context="not a dict")
        masked = PediatricCassette().mask_faults(obs, validate_vitals(obs))
        self.assertEqual(masked.context, {})
        self.assertTrue(ObserveClinicalEngine().evaluate(obs).unassessable)

    def test_the_mask_is_deterministic(self):
        obs = make_vitals(respiratory_rate=-3.0, context={"age_months": 24, "previous_rr": 20})
        cassette = PediatricCassette()
        self.assertEqual(asdict(cassette.mask_faults(obs, validate_vitals(obs))),
                         asdict(cassette.mask_faults(obs, validate_vitals(obs))))


class TestNothingAssessable(unittest.TestCase):
    """Nothing measured means nothing scored, said so in the record, and still
    no downgrade of a tracked regime and no write to the policy."""

    def test_every_channel_faulted_is_reported_as_nothing_assessed(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals(patient_id="all", **_ALL_FAULTED))
        self.assertEqual(verdict.regime, OperationalRegime.WARNING)
        self.assertEqual(verdict.risk_score, REGIME_RISK_FLOOR["warning"])
        self.assertEqual(verdict.confidence, 0.0)
        self.assertEqual(verdict.active_engines, [])
        self.assertTrue(verdict.unassessable)
        self.assertEqual(len(verdict.validation_faults), 4)
        self.assertEqual(verdict.triggered_rules,
                         [f"DATA_INTEGRITY_FAULT: {f}" for f in verdict.validation_faults]
                         + ["CLINICAL_SAFETY_BYPASS: data-integrity fault skipped scoring"])
        self.assertTrue(verdict.escalation_required)
        policy = engine._patient_policies["all"]
        self.assertEqual((policy.current_regime, policy.escalation_locked, policy.dwell_count),
                         (OperationalRegime.STABLE, False, 0))
        data = engine.audit_ledger.entries[-1]["data"]
        self.assertIsNone(data["assessed_vitals"])
        self.assertEqual(data["selected_engines"], [])
        self.assertEqual(data["outputs"], [])
        self.assertEqual(data["validation_faults"], verdict.validation_faults)
        self.assertEqual(_replay(engine.audit_ledger.entries[-1]), verdict.decision_fingerprint)

    def test_every_channel_faulted_on_a_tracked_critical_patient_holds_critical(self):
        engine = ObserveClinicalEngine()
        _drive_to_critical(engine, "allc")
        verdict = engine.evaluate(make_vitals(patient_id="allc", timestamp=T0 + timedelta(seconds=10),
                                              **_ALL_FAULTED))
        self.assertEqual(verdict.regime, OperationalRegime.CRITICAL)
        self.assertEqual(verdict.risk_score, REGIME_RISK_FLOOR["critical"])
        self.assertFalse(verdict.escalation_required)
        policy = engine._patient_policies["allc"]
        self.assertEqual(policy.current_regime, OperationalRegime.CRITICAL)
        self.assertTrue(policy.escalation_locked)
        self.assertEqual(policy.last_escalation_time, T0)

    def test_a_fault_the_cassette_cannot_attribute_takes_the_same_path(self):
        engine = ObserveClinicalEngine(cassette=_UnattributableFaults())
        verdict = engine.evaluate(make_vitals(patient_id="lead"))
        self.assertTrue(verdict.unassessable)
        self.assertEqual(verdict.active_engines, [])
        self.assertEqual(verdict.validation_faults, ["lead_off"])
        self.assertIn("DATA_INTEGRITY_FAULT: lead_off", verdict.triggered_rules)
        self.assertEqual(verdict.regime, OperationalRegime.WARNING)
        self.assertEqual(engine._patient_policies["lead"].current_regime, OperationalRegime.STABLE)

    def test_an_exception_from_the_mask_is_not_swallowed(self):
        engine = ObserveClinicalEngine(cassette=_BrokenMask())
        with self.assertRaises(RuntimeError):
            engine.evaluate(make_vitals(oxygen_saturation=NAN))
        self.assertEqual(engine.evaluate(make_vitals()).regime, OperationalRegime.STABLE)


class TestValidChannelEmergencyDuringAFault(unittest.TestCase):
    """A hard rule or syndrome on a channel that still works is a real emergency."""

    def test_a_hard_rule_on_a_valid_channel_pages_and_is_labelled(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals(patient_id="hyp", **_HARD_RULE_ON_VALID))
        self.assertTrue(verdict.unassessable)
        self.assertTrue(verdict.escalation_required)
        self.assertIn("CLINICAL_SAFETY_BYPASS: hard-rule trigger on a valid channel during a data-integrity fault",
                      verdict.triggered_rules)
        self.assertTrue(any(r.startswith("HYPOTHERMIA") for r in verdict.triggered_rules))
        self.assertTrue(any(r.startswith("TACHYCARDIA") for r in verdict.triggered_rules))
        self.assertIn(verdict.regime, (OperationalRegime.WARNING, OperationalRegime.CRITICAL))
        policy = engine._patient_policies["hyp"]
        self.assertEqual((policy.current_regime, policy.escalation_locked, policy.dwell_count,
                          policy.pending_regime), (OperationalRegime.STABLE, False, 0, None))

    def test_a_syndrome_on_valid_channels_reports_critical(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals(patient_id="syn", temperature=NAN,
                                              oxygen_saturation=89.0, respiratory_rate=50.0))
        self.assertEqual(verdict.regime, OperationalRegime.CRITICAL)
        self.assertGreaterEqual(verdict.risk_score, REGIME_RISK_FLOOR["critical"])
        self.assertIn("CLINICAL_SAFETY_BYPASS: dangerous-syndrome trigger on a valid channel "
                      "during a data-integrity fault", verdict.triggered_rules)
        self.assertEqual(engine._patient_policies["syn"].current_regime, OperationalRegime.STABLE)

    def test_a_bypass_that_fires_during_a_fault_leaves_the_next_real_crisis_pageable(self):
        engine = ObserveClinicalEngine()
        engine.evaluate(make_vitals(patient_id="next", **_HARD_RULE_ON_VALID))
        crisis = engine.evaluate(make_vitals(patient_id="next", oxygen_saturation=75.0, heart_rate=150.0,
                                             respiratory_rate=45.0, timestamp=T0 + timedelta(seconds=600)))
        self.assertEqual(crisis.regime, OperationalRegime.CRITICAL)
        self.assertTrue(crisis.escalation_required)

    def test_a_sensor_page_does_not_silence_a_valid_channel_emergency(self):
        engine = ObserveClinicalEngine()
        sequence = [(0, {}, True), (60, _HARD_RULE_ON_VALID, True), (70, _HARD_RULE_ON_VALID, False),
                    (80, {}, False), (360, _HARD_RULE_ON_VALID, True)]
        for seconds, extra, pages in sequence:
            fields = {"oxygen_saturation": NAN, **extra}
            verdict = engine.evaluate(make_vitals(patient_id="g2", timestamp=T0 + timedelta(seconds=seconds),
                                                  **fields))
            self.assertEqual(verdict.escalation_required, pages, msg=seconds)
            self.assertGreaterEqual(verdict.risk_score, REGIME_RISK_FLOOR["warning"])
        stamp = T0 + timedelta(seconds=360)
        self.assertEqual(engine._patient_fault_paged["g2"], {"sensor": stamp, "bypass": stamp})

    def test_a_valid_channel_emergency_pages_whatever_the_tracked_regime(self):
        # Fork semantics: a bypass during a fault pages through the fault dedup
        # even on a tracked CRITICAL patient; the policy's own clock is untouched.
        engine = ObserveClinicalEngine()
        _drive_to_critical(engine, "h2")
        policy = engine._patient_policies["h2"]
        emergency = engine.evaluate(make_vitals(patient_id="h2", timestamp=T0 + timedelta(seconds=10),
                                                **_HARD_RULE_ON_VALID))
        self.assertTrue(emergency.escalation_required)
        self.assertEqual(emergency.regime, OperationalRegime.CRITICAL)
        plain = engine.evaluate(make_vitals(patient_id="h2", oxygen_saturation=NAN,
                                            timestamp=T0 + timedelta(seconds=20)))
        self.assertFalse(plain.escalation_required)
        self.assertEqual(plain.regime, OperationalRegime.CRITICAL)
        self.assertEqual((policy.current_regime, policy.escalation_locked, policy.last_escalation_time),
                         (OperationalRegime.CRITICAL, True, T0))


class TestFaultPageDedup(unittest.TestCase):

    def test_the_dedup_state_is_evicted_with_the_policy(self):
        engine = ObserveClinicalEngine(max_tracked_patients=2)
        self.assertTrue(engine.evaluate(make_vitals(patient_id="A", oxygen_saturation=NAN)).escalation_required)
        engine.evaluate(make_vitals(patient_id="B"))
        engine.evaluate(make_vitals(patient_id="C"))
        self.assertNotIn("A", engine._patient_policies)
        self.assertNotIn("A", engine._patient_fault_paged)
        again = engine.evaluate(make_vitals(patient_id="A", oxygen_saturation=NAN,
                                            timestamp=T0 + timedelta(seconds=10)))
        self.assertTrue(again.escalation_required)

    def test_a_clean_reading_does_not_reset_the_dedup(self):
        engine = ObserveClinicalEngine()
        pages = []
        for seconds, o2 in ((0, NAN), (60, 97.0), (120, NAN), (ESCALATION_LOCK_SECONDS, NAN)):
            v = engine.evaluate(make_vitals(patient_id="flap", oxygen_saturation=o2,
                                            timestamp=T0 + timedelta(seconds=seconds)))
            pages.append(v.escalation_required)
        self.assertEqual(pages, [True, False, False, True])  # exactly the window re-pages

    def test_the_window_is_measured_in_reading_time(self):
        engine = ObserveClinicalEngine()
        self.assertTrue(engine.evaluate(make_vitals(patient_id="rt", oxygen_saturation=NAN)).escalation_required)
        self.assertFalse(engine.evaluate(make_vitals(patient_id="rt", oxygen_saturation=NAN)).escalation_required)
        late = T0 + timedelta(seconds=ESCALATION_LOCK_SECONDS - 1)
        self.assertFalse(engine.evaluate(make_vitals(patient_id="rt", oxygen_saturation=NAN,
                                                     timestamp=late)).escalation_required)

    def test_the_dedup_is_per_subject(self):
        engine = ObserveClinicalEngine()
        self.assertTrue(engine.evaluate(make_vitals(patient_id="X", oxygen_saturation=NAN)).escalation_required)
        self.assertTrue(engine.evaluate(make_vitals(patient_id="Y", oxygen_saturation=NAN)).escalation_required)

    def test_a_tracked_warning_subject_is_not_repaged_for_a_sensor(self):
        engine = ObserveClinicalEngine()
        first = engine.evaluate(make_vitals(patient_id="tw", oxygen_saturation=85.0))
        self.assertEqual((first.regime, first.escalation_required), (OperationalRegime.WARNING, True))
        fault = engine.evaluate(make_vitals(patient_id="tw", oxygen_saturation=NAN,
                                            timestamp=T0 + timedelta(seconds=600)))
        self.assertEqual(fault.regime, OperationalRegime.WARNING)
        self.assertFalse(fault.escalation_required)
        self.assertNotIn("tw", engine._patient_fault_paged)

    # Reading times the dedup cannot measure (red-team finding on the overlay):
    # no timestamp, naive against aware, or not a datetime at all. The base
    # fault path returned a verdict for all of these; the overlay must too.
    _NAIVE = datetime(2026, 1, 1, 0, 0, 10)

    def test_a_missing_reading_time_does_not_turn_a_sensor_fault_into_a_page_storm(self):
        engine = ObserveClinicalEngine()
        pages = [engine.evaluate(make_vitals(patient_id="nt", oxygen_saturation=NAN,
                                             timestamp=ts)).escalation_required
                 for ts in (None, None, T0, T0 + timedelta(seconds=ESCALATION_LOCK_SECONDS))]
        # held while the window cannot be measured; once the feed's times are
        # comparable again, the next fault a full window later pages
        self.assertEqual(pages, [True, False, False, True])

    def test_an_incomparable_reading_time_never_raises_on_the_fault_path(self):
        engine = ObserveClinicalEngine()
        pages = []
        for ts in (T0, self._NAIVE, "2026-01-01T00:00:20", 1767225630.0):
            verdict = engine.evaluate(make_vitals(patient_id="tz", oxygen_saturation=NAN, timestamp=ts))
            self.assertGreaterEqual(verdict.risk_score, REGIME_RISK_FLOOR["warning"], msg=repr(ts))
            pages.append(verdict.escalation_required)
        self.assertEqual(pages, [True, False, False, False])  # the window cannot be measured: held

    def test_a_valid_channel_emergency_still_pages_when_the_window_cannot_be_measured(self):
        engine = ObserveClinicalEngine()
        self.assertTrue(engine.evaluate(make_vitals(patient_id="tb", timestamp=T0,
                                                    **_HARD_RULE_ON_VALID)).escalation_required)
        again = engine.evaluate(make_vitals(patient_id="tb", timestamp=self._NAIVE, **_HARD_RULE_ON_VALID))
        self.assertTrue(again.escalation_required)  # never swallow an emergency on a working channel
        sensor = engine.evaluate(make_vitals(patient_id="tb", oxygen_saturation=NAN, timestamp=self._NAIVE))
        self.assertFalse(sensor.escalation_required)


class TestTheKalmanTrackerIsNotFedAPlaceholder(unittest.TestCase):

    CLEAN = dict(heart_rate=100.0, oxygen_saturation=97.0, respiratory_rate=24.0, temperature=37.0)

    def _run(self, engine, hours_and_o2):
        verdicts = []
        for hour, o2 in hours_and_o2:
            fields = dict(self.CLEAN, oxygen_saturation=o2)
            verdicts.append(engine.evaluate(make_vitals(patient_id="K", timestamp=T0 + timedelta(hours=hour),
                                                        **fields)))
        return verdicts

    def test_a_fault_reading_does_not_update_the_tracker(self):
        engine = ObserveClinicalEngine(enable_kalman=True)
        self._run(engine, [(0, 97.0), (1, 97.0), (2, 97.0)])
        tracker = engine._patient_kalman["K"]
        before = (tracker._n_updates, tracker._last_ts, list(tracker._surprise_window))
        fault = self._run(engine, [(3, NAN)])[0]
        self.assertTrue(fault.unassessable)
        self.assertNotIn("trajectory_kalman", fault.active_engines)
        self.assertNotIn("trajectory_kalman", engine.audit_ledger.entries[-1]["data"]["selected_engines"])
        self.assertEqual((tracker._n_updates, tracker._last_ts, list(tracker._surprise_window)), before)
        after = self._run(engine, [(4, 97.0)])[0]
        self.assertIn("trajectory_kalman", after.active_engines)
        self.assertEqual(tracker._n_updates, before[0] + 1)

    def test_a_subject_whose_first_reading_is_a_fault_gets_no_tracker(self):
        engine = ObserveClinicalEngine(enable_kalman=True)
        self._run(engine, [(0, NAN)])
        self.assertNotIn("K", engine._patient_kalman)

    def test_the_filter_is_identical_with_and_without_the_fault_reading(self):
        with_fault = ObserveClinicalEngine(enable_kalman=True)
        without = ObserveClinicalEngine(enable_kalman=True)
        self._run(with_fault, [(0, 97.0), (1, 96.0), (2, 95.0), (3, NAN), (4, 94.0)])
        self._run(without, [(0, 97.0), (1, 96.0), (2, 95.0), (4, 94.0)])
        a, b = with_fault._patient_kalman["K"], without._patient_kalman["K"]
        self.assertEqual((a._n_updates, a._last_ts, a._surprise_window),
                         (b._n_updates, b._last_ts, b._surprise_window))
        for name in a._channels:
            self.assertEqual((a._channels[name].p, a._channels[name].v),
                             (b._channels[name].p, b._channels[name].v), msg=name)

    def test_kalman_mode_stays_deterministic_across_a_fault(self):
        runs = []
        for _ in range(2):
            verdicts = self._run(ObserveClinicalEngine(enable_kalman=True),
                                 [(0, 97.0), (1, 96.0), (2, NAN), (3, 94.0)])
            runs.append([(v.risk_score, v.regime, v.decision_fingerprint) for v in verdicts])
        self.assertEqual(runs[0], runs[1])


class TestProvenanceOnTheOverlayPath(unittest.TestCase):

    def test_the_entry_replays_and_records_the_reading_as_it_arrived(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals(patient_id="prov", oxygen_saturation=NAN, heart_rate=210.0))
        entry = engine.audit_ledger.entries[-1]
        data = entry["data"]
        self.assertEqual(_replay(entry), verdict.decision_fingerprint)
        self.assertEqual(data["decision_fingerprint"], verdict.decision_fingerprint)
        self.assertEqual(data["validation_faults"], verdict.validation_faults)
        self.assertTrue(data["verdict"]["unassessable"])
        self.assertTrue(math.isnan(data["vitals"]["oxygen_saturation"]))
        self.assertEqual(data["assessed_vitals"]["oxygen_saturation"], _VITALS_NEUTRAL["oxygen_saturation"])
        self.assertEqual(data["cassette"], {"name": PediatricCassette.name, "version": PediatricCassette.version})
        self.assertEqual(data["parameter_set_version"], PARAMETER_SET_VERSION)
        self.assertEqual(verdict.parameter_set_version, PARAMETER_SET_VERSION)
        self.assertEqual(entry["action"], "clinical_assessment")
        self.assertEqual(
            compute_state_commitment(data["predecessor_state_commitment"],
                                     verdict_state(entry["patient_id"], data["decision_fingerprint"])),
            verdict.state_commitment)
        self.assertTrue(engine.audit_ledger.verify_integrity())

    def test_a_masked_decision_does_not_collide_with_a_clean_one(self):
        masked = ObserveClinicalEngine().evaluate(make_vitals(oxygen_saturation=NAN))
        clean = ObserveClinicalEngine().evaluate(make_vitals(oxygen_saturation=_VITALS_NEUTRAL["oxygen_saturation"]))
        self.assertNotEqual(masked.decision_fingerprint, clean.decision_fingerprint)

    def test_a_clean_entry_keeps_its_shape(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals())
        data = engine.audit_ledger.entries[-1]["data"]
        self.assertEqual(set(data), {"vitals", "selected_engines", "outputs", "verdict", "parameter_set_version",
                                     "cassette", *_PROVENANCE_KEYS})
        self.assertEqual(set(data["verdict"]), {"risk_score", "regime", "escalation_required", "entropy"})
        self.assertEqual((verdict.validation_faults, verdict.unassessable), ([], False))


class TestVerdictShape(unittest.TestCase):

    def test_confidence_is_penalised_on_a_faulted_reading(self):
        faulted = ObserveClinicalEngine().evaluate(make_vitals(oxygen_saturation=NAN))
        clean = ObserveClinicalEngine().evaluate(make_vitals())
        self.assertAlmostEqual(faulted.confidence, clean.confidence * UNASSESSABLE_CONFIDENCE_PENALTY)

    def test_masking_and_sanitization_compose(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(make_vitals(oxygen_saturation=NAN,
                                              context={"previous_o2": 90, "previous_hr": "?", "age_months": 24}))
        self.assertEqual(verdict.regime, OperationalRegime.WARNING)
        self.assertFalse(any("MOMENTUM" in r for r in verdict.triggered_rules))
        self.assertEqual(engine.audit_ledger.entries[-1]["data"]["assessed_vitals"]["context"], {"age_months": 24})

    def test_fault_rules_lead_with_the_data_integrity_prefix(self):
        verdict = ObserveClinicalEngine().evaluate(make_vitals(oxygen_saturation=NAN, heart_rate=210.0))
        self.assertEqual(verdict.triggered_rules[0], "DATA_INTEGRITY_FAULT: oxygen_saturation=nan is not a finite number")
        self.assertEqual(verdict.validation_faults, ["oxygen_saturation=nan is not a finite number"])

    def test_each_risk_floor_sits_in_the_band_its_regime_wins(self):
        for regime in OperationalRegime:
            d = regime_distribution(REGIME_RISK_FLOOR[regime.value])
            self.assertEqual(max(d, key=d.get), regime.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
