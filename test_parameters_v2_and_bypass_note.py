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


if __name__ == "__main__":
    unittest.main()
