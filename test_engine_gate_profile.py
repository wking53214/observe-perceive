"""The behavioral-engine gate is a named, validated setting.

PR-2a of OBSERVE's RECONCILE_UPSTREAM_PLAN.md. Upstream's widened rule for when
the behavioral (syndrome) engine runs is PROVISIONAL, pending pediatrician
sign-off. A deployment that has not had that sign-off can choose the older,
narrower rule by name instead of inheriting the wider one. The default is the
current upstream rule, byte for byte.

This file proves, in order:
  step 1: the default is unchanged (engine selection over a dense grid against a
          frozen copy of the pre-change function, and the recorded engine lists,
          fingerprints, entry shapes and identity of 14 fixed readings);
          an unknown profile name raises.
  step 2: the conservative_legacy profile selects exactly what OBSERVE's engine
          selects today.
  step 3: a decision made under a non-default profile is distinguishable in the
          stored audit entry and in the fingerprint; a default decision is not
          touched.

The recorded fingerprints below were produced on main BEFORE this change. If a
later change legitimately moves fingerprints (a new parameter-set era), they are
updated deliberately, with that change.
"""

import itertools
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace

from observe_consolidated import (
    PARAMETER_SET_VERSION,
    ObserveClinicalEngine,
    VitalsSnapshot,
    decision_fingerprint,
)
from pediatric_cassette import (
    DEFAULT_ENGINE_GATE,
    ENGINE_GATE_CONSERVATIVE_LEGACY,
    ENGINE_GATE_WIDENED_PROVISIONAL,
    ENGINE_GATES,
    PediatricCassette,
)

T0 = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)


def _has_physiological_telemetry_reference(obs):
    # Only reached when the context carries telemetry; the grid never sets it,
    # so this stays False and the reference matches the real function there.
    return False


def reference_select_engines_main(obs, recent_entropy):
    """A frozen copy of PediatricCassette.select_engines as it was on main
    before this change. Do not edit: it is the oracle the default is held to."""
    engines = ["heuristic"]

    if recent_entropy > 0.6 or obs.context.get("force_heavy"):
        engines += ["bayesian", "trajectory", "drift"]

    if "previous_o2" in obs.context or "previous_hr" in obs.context:
        if "trajectory" not in engines:
            engines.append("trajectory")

    if obs.context.get("recent_o2_readings"):
        engines.append("adversarial")

    if (obs.oxygen_saturation < 92.0 or obs.heart_rate > 140 or obs.heart_rate < 90
            or obs.respiratory_rate > 35 or obs.temperature > 38.5):
        engines.append("behavioral")

    if (recent_entropy > 0.6 or obs.context.get("force_heavy")
            or _has_physiological_telemetry_reference(obs)):
        engines.append("physiological_reserve")

    seen = set()
    ordered = []
    for e in engines:
        if e not in seen:
            seen.add(e)
            ordered.append(e)
    return ordered


def reference_select_engines_observe(obs, recent_entropy):
    """A frozen copy of the engine selection in OBSERVE's own observe_consolidated.py
    (OBSERVE main a4de294, ObserveClinicalEngine.select_engines). The only difference
    from the upstream rule is the behavioral clause, which has no RR or temperature
    term. It is the oracle the conservative_legacy profile is held to."""
    engines = ["heuristic"]

    if recent_entropy > 0.6 or obs.context.get("force_heavy"):
        engines += ["bayesian", "trajectory", "drift"]

    if "previous_o2" in obs.context or "previous_hr" in obs.context:
        if "trajectory" not in engines:
            engines.append("trajectory")

    if obs.context.get("recent_o2_readings"):
        engines.append("adversarial")

    if obs.oxygen_saturation < 92.0 or obs.heart_rate > 140 or obs.heart_rate < 90:
        engines.append("behavioral")

    if (recent_entropy > 0.6 or obs.context.get("force_heavy")
            or _has_physiological_telemetry_reference(obs)):
        engines.append("physiological_reserve")

    seen = set()
    ordered = []
    for e in engines:
        if e not in seen:
            seen.add(e)
            ordered.append(e)
    return ordered


def _obs(hr, o2, rr, temp, **ctx):
    context = {"age_months": 24}
    context.update(ctx)
    return SimpleNamespace(
        patient_id="P-GRID", heart_rate=hr, oxygen_saturation=o2,
        respiratory_rate=rr, temperature=temp, context=context,
    )


# Dense grid, boundaries included: every cutoff the gate reads (92.0, 90, 140,
# 35, 38.5) is hit exactly, one step below and one step above.
_O2 = (70.0, 91.9, 92.0, 92.1, 98.0, 100.0)
_HR = (40, 89, 90, 91, 110, 139, 140, 141, 200)
_RR = (10, 24, 34.9, 35, 35.1, 45, 60)
_TEMP = (35.0, 37.0, 38.4, 38.5, 38.6, 40.0, 42.0)
_CONTEXTS = ({}, {"force_heavy": True}, {"previous_o2": 97.0}, {"recent_o2_readings": [97, 96]})
_ENTROPY = (0.0, 0.7)

# How many of the grid's points the two profiles disagree on. The grid is
# synthetic and dense at the cutoffs, so this is the size of the choice, not a
# clinical rate.
_EXPECTED_GRID_DISAGREEMENT = (5280, 21168)


def _grid():
    for o2, hr, rr, temp in itertools.product(_O2, _HR, _RR, _TEMP):
        for ctx in _CONTEXTS:
            for entropy in _ENTROPY:
                yield _obs(hr, o2, rr, temp, **ctx), entropy


def _golden_vitals(index, hr, o2, rr, temp, ctx):
    return VitalsSnapshot("P-GOLD-%d" % index, T0, hr, o2, rr, temp, context=dict(ctx))


_GOLDEN_CASES = [
    ("calm",            110, 98.0, 24, 37.0, {"age_months": 24}),
    ("low_o2",          110, 91.9, 24, 37.0, {"age_months": 24}),
    ("o2_boundary",     110, 92.0, 24, 37.0, {"age_months": 24}),
    ("hr_high",         141, 98.0, 24, 37.0, {"age_months": 24}),
    ("hr_boundary_hi",  140, 98.0, 24, 37.0, {"age_months": 24}),
    ("hr_low",           89, 98.0, 24, 37.0, {"age_months": 24}),
    ("hr_boundary_lo",   90, 98.0, 24, 37.0, {"age_months": 24}),
    ("rr_only",         110, 98.0, 36, 37.0, {"age_months": 24}),
    ("rr_boundary",     110, 98.0, 35, 37.0, {"age_months": 24}),
    ("temp_only",       110, 98.0, 24, 38.6, {"age_months": 24}),
    ("temp_boundary",   110, 98.0, 24, 38.5, {"age_months": 24}),
    ("rr_and_temp",     110, 98.0, 40, 39.0, {"age_months": 24}),
    ("force_heavy",     110, 98.0, 24, 37.0, {"age_months": 24, "force_heavy": True}),
    ("critical",        168, 83.0, 46, 39.5, {"age_months": 12, "force_heavy": True}),
]

# (engines, decision_fingerprint) recorded on main before this change.
_GOLDEN_MAIN = {
    "calm": (['heuristic'],
        "b9d0dbab29a07d6b9a710aee3d4ccf5287362f2c394affdae5806f7babffc505"),
    "low_o2": (['heuristic', 'behavioral'],
        "d3293d7cc2fc75cf6e327b33254ef54563ef28108e4b5e4b8a0dca11a5cab28e"),
    "o2_boundary": (['heuristic'],
        "03eb43f4d0a78f8fae0a5a3bdbed65f6da359dc3c23a9751926e12309d37209f"),
    "hr_high": (['heuristic', 'behavioral'],
        "ece39c6dcf94bc48e618a922757e9a7aa32fef0b6a5b331ff705f1a28013aba8"),
    "hr_boundary_hi": (['heuristic'],
        "9074ac31dc3b5918d85943256fb5be34ef2b602307e183e3859fdb524628e915"),
    "hr_low": (['heuristic', 'behavioral'],
        "cbdfa29d2485809f93089f94db5a73135c26ce7f6f004761566fea4df0cf65ba"),
    "hr_boundary_lo": (['heuristic'],
        "8df76de03389d608c524f41d5917c629b02442e9853fe3987eb36f6b6b9f8975"),
    "rr_only": (['heuristic', 'behavioral'],
        "b7bdcafb5fe8a1c6d3777a0d210d10415d272a84ba06fc97628652c9403b0bd8"),
    "rr_boundary": (['heuristic'],
        "4f30cb292797565d59ee60537e815f2988c12b1c416d63e2f595f5b2eee7d30f"),
    "temp_only": (['heuristic', 'behavioral'],
        "426a897c6d1fa166f6d59b3e73d7e9c080b06668c5d80903b5f7f8be99b683a7"),
    "temp_boundary": (['heuristic'],
        "1759d535ee405f33ba2a9de32a1f4ee07c84f71b67e215e4fb37a7b719e4c583"),
    "rr_and_temp": (['heuristic', 'behavioral'],
        "468be431ff35ff32edfcd5d07cf79a4b4be543ed723db1835b7b055d799998f3"),
    "force_heavy": (['heuristic', 'bayesian', 'trajectory', 'drift', 'physiological_reserve'],
        "31ae875dafe743518b0c2ecabd72e62c40e5a329050dcfa2c956db2a1a9d5010"),
    "critical": (['heuristic', 'bayesian', 'trajectory', 'drift', 'behavioral', 'physiological_reserve'],
        "58792cb0dac7fa0f2e25abe2fa87a7946b694778df70a63264f2e29d65023013"),
}


_BASE_ENTRY_KEYS = {
    "cassette", "decision_fingerprint", "outputs", "parameter_set_version",
    "predecessor_state_commitment", "selected_engines", "state_commitment", "verdict", "vitals",
}
_RECORDED_PARAMETER_SET_VERSION = "7761a1db8d54b54925f7fbb682aca7e780167de53b9008acc196573eb3ed2cef"


class DefaultGateIsUnchanged(unittest.TestCase):
    def test_the_default_profile_is_the_widened_provisional_rule(self):
        self.assertEqual(DEFAULT_ENGINE_GATE, ENGINE_GATE_WIDENED_PROVISIONAL)
        self.assertEqual(PediatricCassette().engine_gate, ENGINE_GATE_WIDENED_PROVISIONAL)
        self.assertEqual(PediatricCassette(ENGINE_GATE_WIDENED_PROVISIONAL).engine_gate, DEFAULT_ENGINE_GATE)

    def test_default_selection_equals_the_frozen_pre_change_function_over_the_whole_grid(self):
        cassette = PediatricCassette()
        checked = behavioral = 0
        for obs, entropy in _grid():
            expected = reference_select_engines_main(obs, entropy)
            self.assertEqual(cassette.select_engines(obs, entropy), expected,
                             msg=f"{obs.heart_rate=} {obs.oxygen_saturation=} {obs.respiratory_rate=} "
                                 f"{obs.temperature=} {obs.context=} {entropy=}")
            checked += 1
            behavioral += "behavioral" in expected
        self.assertGreater(checked, 10000)
        self.assertTrue(0 < behavioral < checked, "the grid must exercise both outcomes")

    def test_the_explicit_default_selects_exactly_what_the_bare_constructor_does(self):
        bare, named = PediatricCassette(), PediatricCassette(engine_gate=DEFAULT_ENGINE_GATE)
        for obs, entropy in _grid():
            self.assertEqual(bare.select_engines(obs, entropy), named.select_engines(obs, entropy))

    def test_recorded_engines_fingerprints_and_entry_shapes_of_14_readings_are_identical(self):
        self.assertEqual(PARAMETER_SET_VERSION, _RECORDED_PARAMETER_SET_VERSION,
                         "the parameter-set era moved; the recorded fingerprints below belong to the old era")
        for index, (label, hr, o2, rr, temp, ctx) in enumerate(_GOLDEN_CASES):
            engine = ObserveClinicalEngine()  # a fresh engine per case: no carried state
            verdict = engine.evaluate(_golden_vitals(index, hr, o2, rr, temp, ctx))
            data = engine.audit_ledger.entries[-1]["data"]
            engines, fingerprint = _GOLDEN_MAIN[label]
            self.assertEqual(list(verdict.active_engines), engines, msg=label)
            self.assertEqual(data["decision_fingerprint"], fingerprint, msg=label)
            self.assertEqual(data["cassette"], {"name": "pediatric_deterioration", "version": "1.1.0"}, msg=label)
            self.assertIn(set(data), (_BASE_ENTRY_KEYS, _BASE_ENTRY_KEYS | {"safety_bypass"}), msg=label)


class UnknownGateRaises(unittest.TestCase):
    def test_an_unknown_name_raises_and_never_falls_back_to_the_default(self):
        for bad in ("", "WIDENED_PROVISIONAL", "widened", "conservative", "legacy", None, 0, ["widened_provisional"]):
            with self.assertRaises(ValueError, msg=repr(bad)):
                PediatricCassette(engine_gate=bad)

    def test_the_message_names_the_valid_choices(self):
        with self.assertRaises(ValueError) as caught:
            PediatricCassette(engine_gate="nope")
        for name in ENGINE_GATES:
            self.assertIn(name, str(caught.exception))


class ConservativeLegacyGate(unittest.TestCase):
    def test_it_is_a_named_choice(self):
        self.assertIn(ENGINE_GATE_CONSERVATIVE_LEGACY, ENGINE_GATES)
        self.assertEqual(PediatricCassette(ENGINE_GATE_CONSERVATIVE_LEGACY).engine_gate, "conservative_legacy")
        self.assertNotEqual(ENGINE_GATE_CONSERVATIVE_LEGACY, DEFAULT_ENGINE_GATE)

    def test_legacy_selection_equals_OBSERVEs_own_rule_over_the_whole_grid(self):
        cassette = PediatricCassette(engine_gate=ENGINE_GATE_CONSERVATIVE_LEGACY)
        checked = 0
        for obs, entropy in _grid():
            self.assertEqual(
                cassette.select_engines(obs, entropy), reference_select_engines_observe(obs, entropy),
                msg=f"{obs.heart_rate=} {obs.oxygen_saturation=} {obs.respiratory_rate=} "
                    f"{obs.temperature=} {obs.context=} {entropy=}")
            checked += 1
        self.assertGreater(checked, 10000)

    def test_the_two_profiles_differ_only_where_rr_or_temperature_alone_would_pull_the_engine_in(self):
        widened = PediatricCassette(ENGINE_GATE_WIDENED_PROVISIONAL)
        legacy = PediatricCassette(ENGINE_GATE_CONSERVATIVE_LEGACY)
        differing = total = 0
        for obs, entropy in _grid():
            total += 1
            w, g = widened.select_engines(obs, entropy), legacy.select_engines(obs, entropy)
            if w == g:
                continue
            differing += 1
            # The wider rule only ever ADDS the behavioral engine, and only for a
            # reading with normal O2 and heart rate but a high RR or temperature.
            self.assertEqual([e for e in w if e != "behavioral"], g)
            self.assertIn("behavioral", w)
            self.assertNotIn("behavioral", g)
            self.assertTrue(obs.oxygen_saturation >= 92.0 and 90 <= obs.heart_rate <= 140)
            self.assertTrue(obs.respiratory_rate > 35 or obs.temperature > 38.5)
        self.assertGreater(differing, 0)
        self.assertLess(differing, total)
        # Recorded so a reader can see the size of the choice: how many grid points move.
        self.assertEqual((differing, total), _EXPECTED_GRID_DISAGREEMENT)

    def test_unknown_names_still_raise_with_two_profiles_defined(self):
        with self.assertRaises(ValueError):
            PediatricCassette(engine_gate="widened_provisional ")


_PROVENANCE_KEYS = ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")
_NAN = float("nan")
_IDENTITY = {"name": "pediatric_deterioration", "version": "1.1.0"}


def _decide(gate, pid, hr, o2, rr, temp, **ctx):
    """One reading on a fresh engine built with the named gate; returns (verdict, stored entry data)."""
    cassette = PediatricCassette() if gate is None else PediatricCassette(engine_gate=gate)
    engine = ObserveClinicalEngine(cassette=cassette)
    context = {"age_months": 24}
    context.update(ctx)
    verdict = engine.evaluate(VitalsSnapshot(pid, T0, hr, o2, rr, temp, context=context))
    return verdict, engine.audit_ledger.entries[-1]["data"]


def _replay(data, **overrides):
    """Recompute the fingerprint from a stored entry, the way a verifier would."""
    payload = {k: v for k, v in data.items() if k not in _PROVENANCE_KEYS}
    payload.update(overrides)
    return decision_fingerprint(payload)


_READINGS = (
    ("calm", dict(hr=110, o2=98.0, rr=24, temp=37.0)),
    ("rr_only", dict(hr=110, o2=98.0, rr=40, temp=37.0)),
    ("temp_only", dict(hr=110, o2=98.0, rr=24, temp=39.0)),
    ("critical", dict(hr=168, o2=83.0, rr=46, temp=39.5, force_heavy=True)),
    ("nothing_assessable", dict(hr=_NAN, o2=_NAN, rr=_NAN, temp=_NAN)),
)


class NonDefaultGateIsBoundIntoProvenance(unittest.TestCase):
    def test_a_default_entry_has_exactly_the_old_identity(self):
        for label, kw in _READINGS:
            _, data = _decide(None, "P-PROV-" + label, **kw)
            self.assertEqual(data["cassette"], _IDENTITY, msg=label)
            self.assertNotIn("engine_gate", data["cassette"], msg=label)

    def test_a_legacy_entry_names_its_gate_in_the_stored_identity(self):
        for label, kw in _READINGS:
            _, data = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-PROV-" + label, **kw)
            self.assertEqual(data["cassette"], dict(_IDENTITY, engine_gate="conservative_legacy"), msg=label)

    def test_the_fingerprint_differs_even_when_both_profiles_select_the_same_engines(self):
        v_default, _ = _decide(None, "P-SAME-ENGINES", hr=110, o2=98.0, rr=24, temp=37.0)
        v_legacy, _ = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-SAME-ENGINES", hr=110, o2=98.0, rr=24, temp=37.0)
        self.assertEqual(v_default.active_engines, v_legacy.active_engines)
        self.assertNotEqual(v_default.decision_fingerprint, v_legacy.decision_fingerprint)

    def test_the_fingerprint_differs_for_every_reading_and_the_outcome_differs_where_the_rule_does(self):
        for label, kw in _READINGS:
            v_default, _ = _decide(None, "P-DIFF-" + label, **kw)
            v_legacy, _ = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-DIFF-" + label, **kw)
            self.assertNotEqual(v_default.decision_fingerprint, v_legacy.decision_fingerprint, msg=label)
        d, _ = _decide(None, "P-RR", hr=110, o2=98.0, rr=40, temp=37.0)
        g, _ = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-RR", hr=110, o2=98.0, rr=40, temp=37.0)
        self.assertIn("behavioral", d.active_engines)
        self.assertNotIn("behavioral", g.active_engines)

    def test_a_legacy_entry_replays_to_its_stored_fingerprint(self):
        for label, kw in _READINGS:
            verdict, data = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-REPLAY-" + label, **kw)
            self.assertEqual(_replay(data), verdict.decision_fingerprint, msg=label)

    def test_a_legacy_entry_cannot_be_relabelled_as_default(self):
        for label, kw in _READINGS:
            verdict, data = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-RELABEL-" + label, **kw)
            relabelled = _replay(data, cassette=dict(_IDENTITY))
            self.assertNotEqual(relabelled, verdict.decision_fingerprint, msg=label)

    def test_the_fault_verdict_path_carries_the_gate_too(self):
        # All four channels faulted: nothing is assessable, so the core builds the
        # verdict itself (the second place provenance is built).
        verdict, data = _decide(ENGINE_GATE_CONSERVATIVE_LEGACY, "P-FAULT", hr=_NAN, o2=_NAN, rr=_NAN, temp=_NAN)
        self.assertIn("safety_bypass", data)
        self.assertEqual(data["cassette"]["engine_gate"], "conservative_legacy")
        self.assertEqual(_replay(data), verdict.decision_fingerprint)

    def test_other_cassettes_and_odd_cassette_objects_are_untouched(self):
        from industrial_cassette import AssetReading, IndustrialCassette
        engine = ObserveClinicalEngine(cassette=IndustrialCassette())
        engine.evaluate(AssetReading("PUMP-1", T0, 60.0, 55.0, 3.2))
        data = engine.audit_ledger.entries[-1]["data"]
        self.assertEqual(set(data["cassette"]), {"name", "version"})

        class NotADict(PediatricCassette):
            identity_extras = ["engine_gate", "x"]  # not a dict: the core must ignore it

        odd = ObserveClinicalEngine(cassette=NotADict())
        odd.evaluate(VitalsSnapshot("P-ODD", T0, 110, 98.0, 24, 37.0, context={"age_months": 24}))
        self.assertEqual(odd.audit_ledger.entries[-1]["data"]["cassette"], _IDENTITY)


if __name__ == "__main__":
    unittest.main()
