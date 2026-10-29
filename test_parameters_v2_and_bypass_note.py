"""The parameter-set label is v2, and the safety-bypass note is fingerprinted.

Step 1 (label): observe.parameters/v1 named two different parameter sets (this
core's and OBSERVE's own engine). This core is v2 so one label never names
different content. No persisted v1-era evidence is assumed to exist.

Step 2 (bypass note): OBSERVE's fingerprint included the safety-bypass note;
this core's did not. The note is now part of the fingerprinted decision data,
present only when a bypass note exists so a clean entry keeps its shape.
"""

import copy
import hashlib
import unittest
from datetime import datetime, timezone

import observe_consolidated as oc
from observe_consolidated import (
    PARAMETER_SET,
    PARAMETER_SET_VERSION,
    ObserveClinicalEngine,
    VitalsSnapshot,
    decision_fingerprint,
)


def _version_of(parameter_set) -> str:
    return hashlib.sha256(oc._canonical_json(parameter_set).encode("utf-8")).hexdigest()


class ParameterSetLabelIsV2(unittest.TestCase):
    def test_label_is_v2(self):
        self.assertEqual(PARAMETER_SET["schema"], "observe.parameters/v2")

    def test_version_is_the_hash_of_the_v2_manifest(self):
        self.assertEqual(PARAMETER_SET_VERSION, _version_of(PARAMETER_SET))

    def test_the_v1_era_value_is_not_produced(self):
        v1 = copy.deepcopy(PARAMETER_SET)
        v1["schema"] = "observe.parameters/v1"
        self.assertNotEqual(PARAMETER_SET_VERSION, _version_of(v1))

    def test_every_verdict_carries_the_v2_version(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-LABEL", hr=110, o2=98.0))
        self.assertEqual(verdict.parameter_set_version, PARAMETER_SET_VERSION)
        entry = engine.audit_ledger.entries[-1]
        self.assertEqual(entry["data"]["parameter_set_version"], PARAMETER_SET_VERSION)


def _vitals(pid, hr=110, o2=98.0, rr=24, temp=37.0, ts=None, **ctx):
    context = {"age_months": 24}
    context.update(ctx)
    return VitalsSnapshot(pid, ts or datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc), hr, o2, rr, temp, context=context)


_PROVENANCE_KEYS = ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")
_NAN = float("nan")


def _entry_data(engine):
    return engine.audit_ledger.entries[-1]["data"]


def _replay(data, **overrides):
    """Recompute the fingerprint from a stored audit entry, as a verifier would."""
    payload = {k: v for k, v in data.items() if k not in _PROVENANCE_KEYS}
    payload.update(overrides)
    return decision_fingerprint(payload)


def _bypass_notes(verdict):
    return [r for r in verdict.triggered_rules if r.startswith("CLINICAL_SAFETY_BYPASS")]


class SafetyBypassNoteIsFingerprinted(unittest.TestCase):
    def test_a_hard_rule_bypass_is_recorded_and_fingerprinted(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-HARD", o2=70.0))
        notes = _bypass_notes(verdict)
        self.assertEqual(len(notes), 1)
        data = _entry_data(engine)
        self.assertEqual(data["safety_bypass"], notes)
        self.assertEqual(_replay(data), verdict.decision_fingerprint)

    def test_removing_the_note_changes_the_fingerprint(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-HARD", o2=70.0))
        data = _entry_data(engine)
        without = {k: v for k, v in data.items() if k != "safety_bypass"}
        self.assertNotEqual(_replay(without), verdict.decision_fingerprint)
        self.assertNotEqual(_replay(data, safety_bypass=["something else"]), verdict.decision_fingerprint)

    def test_a_clean_entry_keeps_its_shape(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-CLEAN"))
        self.assertEqual(_bypass_notes(verdict), [])
        data = _entry_data(engine)
        self.assertNotIn("safety_bypass", data)
        self.assertEqual(_replay(data), verdict.decision_fingerprint)

    def test_a_fault_that_skipped_scoring_is_fingerprinted(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-FAULT", hr=_NAN, o2=_NAN, rr=_NAN, temp=_NAN))
        notes = _bypass_notes(verdict)
        self.assertEqual(len(notes), 1)
        self.assertIn("data-integrity fault skipped scoring", notes[0])
        data = _entry_data(engine)
        self.assertEqual(data["safety_bypass"], notes)
        self.assertEqual(_replay(data), verdict.decision_fingerprint)

    def test_a_partly_faulted_reading_with_no_bypass_has_no_note(self):
        engine = ObserveClinicalEngine()
        verdict = engine.evaluate(_vitals("P-PART", hr=_NAN))
        self.assertEqual(_bypass_notes(verdict), [])
        self.assertNotIn("safety_bypass", _entry_data(engine))

    def test_the_fault_verdict_path_without_an_overlay_is_fingerprinted(self):
        from industrial_cassette import AssetReading, IndustrialCassette
        engine = ObserveClinicalEngine(cassette=IndustrialCassette())
        ts = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
        verdict = engine.evaluate(AssetReading("PUMP-F", ts, _NAN, 55.0, 3.2))
        data = _entry_data(engine)
        self.assertEqual(len(data["safety_bypass"]), 1)
        self.assertEqual(data["safety_bypass"], [verdict.triggered_rules[0]])
        self.assertEqual(_replay(data), verdict.decision_fingerprint)
        without = {k: v for k, v in data.items() if k != "safety_bypass"}
        self.assertNotEqual(_replay(without), verdict.decision_fingerprint)

    def test_identical_inputs_give_identical_fingerprints(self):
        fingerprints = set()
        for _ in range(3):
            fingerprints.add(ObserveClinicalEngine().evaluate(_vitals("P-SAME", o2=70.0)).decision_fingerprint)
        self.assertEqual(len(fingerprints), 1)


if __name__ == "__main__":
    unittest.main()
