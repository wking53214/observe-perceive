"""test_logic_stamp.py -- a digest of what scoring code DOES (decision D9).

Found on `main` (EXECUTED, docs/race/CHANGE_CONTROL_AND_VALIDATION.md): `PARAMETER_SET_VERSION` lists eleven constants and the
SpO2 line (88.0) and the hypothermia line (35.0) are not among them, so a build with a different point value, syndrome threshold
or gate cutoff wrote the same version. Every decision now records `logic_version`, which moves when the code's behaviour does.
"""
import hashlib
import inspect
import textwrap

import observe_consolidated
from observe_consolidated import ENGINE_LOGIC_DIGEST, ObserveClinicalEngine, RiskAdapters, VitalsSnapshot
import kalman_trajectory
import clinical_settings
import pediatric_cassette
from logic_stamp import logic_digest, module_logic_digest, source_digest
from pediatric_cassette import PediatricCassette
from test_clinical_settings import T0

BASE = '''
def score(x):
    """Docstring."""
    total = 0.0
    if x < 88.0:          # a comment
        total += 0.5
    logger.warning("low value %s", x)
    return total
'''


def digest(source):
    return source_digest(textwrap.dedent(source))


# ---- what does not change it --------------------------------------------------------------------------------------------

def test_comments_whitespace_docstrings_and_logging_do_not_change_it():
    edited = '''
def score(x):
    """A completely different docstring."""

    total = 0.0
    if   x<88.0:
        total+=0.5      # another comment
    logger.warning("a different message")
    logger.info("and one more")
    log.error("a module-level alias")
    logging.debug("the logging module itself")
    return total
'''
    assert digest(edited) == digest(BASE)


def test_a_known_digest_is_pinned_so_the_stamp_cannot_drift_across_python_versions():
    """The same source gives the same digest on 3.11, 3.12 and 3.13 (checked on all three when this was written). If this value
    moves on one interpreter, installations that upgrade Python would see every recorded fingerprint change."""
    assert digest(BASE) == "e7f2ee0a6340b7e8702fec15c2b9515d7c5ce122fdc76cba6631a5dedeb565f6"


# ---- what does ---------------------------------------------------------------------------------------------------------

def test_a_changed_constant_comparison_operator_name_or_structure_changes_it():
    base = digest(BASE)
    variants = {
        "the line moved": BASE.replace("88.0", "89.0"),
        "the point value moved": BASE.replace("0.5", "0.6"),
        "the comparison flipped": BASE.replace("x < 88.0", "x > 88.0"),
        "the boundary became inclusive": BASE.replace("x < 88.0", "x <= 88.0"),
        "the operator changed": BASE.replace("total += 0.5", "total -= 0.5"),
        "a name changed": BASE.replace("total", "result"),
        "the function was renamed": BASE.replace("def score", "def rating"),
        "a branch was removed": BASE.replace("if x < 88.0:          # a comment\n        total += 0.5", "pass"),
        "a statement was added": BASE.replace("return total", "total *= 2\n    return total"),
        "an argument changed": BASE.replace("def score(x)", "def score(y)").replace("x < 88.0", "y < 88.0"),
    }
    for what, source in variants.items():
        assert digest(source) != base, what


def test_a_changed_f_string_rule_text_changes_it_because_rule_text_is_recorded():
    a = 'def r(v):\n    return f"CRITICAL_O2: {v}% (<88%)"\n'
    b = 'def r(v):\n    return f"CRITICAL_O2: {v}% (<90%)"\n'
    assert digest(a) != digest(b)


def test_object_order_and_constants_matter():
    def one(x):
        return x + 1

    def two(x):
        return x + 2
    assert logic_digest(one, two) != logic_digest(two, one)
    assert logic_digest(one, constants={"a": 1}) != logic_digest(one, constants={"a": 2})
    assert logic_digest(one, constants={"a": 1, "b": 2}) == logic_digest(one, constants={"b": 2, "a": 1})


# ---- the engine ----------------------------------------------------------------------------------------------------------

def test_the_real_scoring_source_changes_the_digest_when_a_clinical_value_inside_a_function_changes():
    """The finding itself: the heuristic's hard line and hypothermia line are literals inside a function."""
    source = inspect.getsource(RiskAdapters)
    assert "critical_o2_below: float = 88.0" in source and "score += 0.5" in source
    assert source_digest(source.replace("critical_o2_below: float = 88.0", "critical_o2_below: float = 90.0")) != source_digest(source)
    assert source_digest(source.replace("score += 0.5", "score += 0.4")) != source_digest(source)
    assert source_digest(source.replace("o2_mean, o2_std = (thresh[\"o2_low\"] + 100) / 2.0, 3.0",
                                        "o2_mean, o2_std = (thresh[\"o2_low\"] + 100) / 2.0, 4.0")) != source_digest(source)
    assert "parameter_set_version" not in source                                 # (the old stamp never saw any of these)


def test_every_decision_records_the_logic_version_and_it_is_the_core_the_cassette_module_and_the_cassette_together():
    eng = ObserveClinicalEngine()
    eng.evaluate(VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    recorded = eng.audit_ledger.entries[-1]["data"]["logic_version"]
    expected = hashlib.sha256("|".join([ENGINE_LOGIC_DIGEST, module_logic_digest("pediatric_cassette"),
                                        PediatricCassette().logic_digest()]).encode()).hexdigest()
    assert recorded == expected and len(recorded) == 64


def test_a_cassette_with_no_logic_digest_still_gets_its_own_module_digested():
    from cassette import conformance_failures

    class Bare(PediatricCassette):
        logic_digest = None                                                       # an optional member that is simply absent
    assert conformance_failures(PediatricCassette()) == []
    eng = ObserveClinicalEngine(cassette=Bare())
    eng.evaluate(VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    expected = hashlib.sha256("|".join([ENGINE_LOGIC_DIGEST, module_logic_digest(Bare.__module__)]).encode()).hexdigest()
    assert eng.audit_ledger.entries[-1]["data"]["logic_version"] == expected


def test_a_subclass_does_not_share_its_parents_stamp():
    """Review finding: the digest hashed the parent class, so a subclass whose hard_rule_fired always returned False got the same
    stamp. The module that defines the cassette (or the engine) is digested too, and a subclass defined in a test module is
    in a different module."""
    class Silent(PediatricCassette):
        def hard_rule_fired(self, outputs):
            return False
    plain, silent = ObserveClinicalEngine(), ObserveClinicalEngine(cassette=Silent())
    reading = VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24})
    plain.evaluate(reading)
    silent.evaluate(reading)
    assert (plain.audit_ledger.entries[-1]["data"]["logic_version"]
            != silent.audit_ledger.entries[-1]["data"]["logic_version"])

    class SubEngine(ObserveClinicalEngine):
        pass
    sub = SubEngine()
    sub.evaluate(reading)
    assert sub.audit_ledger.entries[-1]["data"]["logic_version"] != plain.audit_ledger.entries[-1]["data"]["logic_version"]


def test_the_decision_fingerprint_moves_with_the_logic(monkeypatch):
    reading = VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24})
    first = ObserveClinicalEngine().evaluate(reading).decision_fingerprint
    assert ObserveClinicalEngine().evaluate(reading).decision_fingerprint == first      # reproducible
    monkeypatch.setattr(observe_consolidated, "ENGINE_LOGIC_DIGEST", "0" * 64)
    assert ObserveClinicalEngine().evaluate(reading).decision_fingerprint != first       # the same reading, different logic


def test_the_cassette_digest_covers_its_rules_and_its_settings_defaults():
    one = PediatricCassette().logic_digest()
    assert one == PediatricCassette().logic_digest() and len(one) == 64
    assert one != ENGINE_LOGIC_DIGEST
    # a settings object that is NOT at its default does not change the digest of the CODE (it is recorded in identity_extras)
    from clinical_settings import ClinicalSettings
    assert PediatricCassette(settings=ClinicalSettings(critical_o2_below=90.0)).logic_digest() == one


def test_the_logic_stamp_module_is_listed_in_pyproject():
    import pathlib
    import re
    text = (pathlib.Path(__file__).parent / "pyproject.toml").read_text()
    assert "logic_stamp" in set(re.findall(r'"([a-z_0-9]+)"', text[text.index("py-modules"):]))


def test_what_the_core_digest_covers_is_pinned_so_dropping_a_module_needs_a_deliberate_edit_here():
    assert observe_consolidated.ENGINE_LOGIC_COVERS == ("observe_consolidated", "kalman_trajectory", "cassette", "logic_stamp")


def _src(module):
    return inspect.getsource(module)


def test_module_level_constants_and_helpers_are_covered_which_a_list_of_functions_missed():
    """Thirteen edits an independent review made, one at a time, to things the first (function-list) version did not hash. Each
    must change the digest of the module that holds it. (The module digests are what `ENGINE_LOGIC_DIGEST` is built from.)"""
    cases = [
        (observe_consolidated, "OperationalRegime.WARNING: 2,", "OperationalRegime.WARNING: 4,"),             # severity order
        (observe_consolidated, '"drift": RiskAdapters.drift,', '"drift": RiskAdapters.trajectory,'),          # the engine table
        (observe_consolidated, '"age_months": (0.0, 1500.0),', '"age_months": (0.0, 1400.0),'),               # context bounds
        (observe_consolidated, "HARD_RULE_CONFIRM_WINDOW = 3", "HARD_RULE_CONFIRM_WINDOW = 4"),
        (observe_consolidated, "return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)",
         "return isinstance(x, (int, float)) and not isinstance(x, bool)"),                                    # a helper
        (observe_consolidated, "return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()",
         "return hashlib.sha256(json.dumps(data, default=str).encode()).hexdigest()"),                          # the fingerprint
        (kalman_trajectory, "_WARMUP_UPDATES = 2", "_WARMUP_UPDATES = 3"),
        (kalman_trajectory, "_MAX_DT_HOURS = 24.0", "_MAX_DT_HOURS = 12.0"),
        (kalman_trajectory, "self.p = self.p + self.v * dt", "self.p = self.p + 0.5 * self.v * dt"),          # the filter itself
        (clinical_settings, "MAX_HARD_RULE_CONFIRM_WINDOW = 1000", "MAX_HARD_RULE_CONFIRM_WINDOW = 10"),
        (pediatric_cassette, "HARD_RULE_RISK = 0.5", "HARD_RULE_RISK = 0.6"),
    ]
    for module, old, new in cases:
        source = _src(module)
        assert old in source, (module.__name__, old)                              # the edit really applies (not vacuous)
        assert source_digest(source.replace(old, new, 1)) != source_digest(source), (module.__name__, old)


def test_the_walker_sees_what_it_used_to_skip():
    pairs = {
        "match singleton": ("match x:\n    case None:\n        pass\n", "match x:\n    case True:\n        pass\n"),
        "match capture name": ("match x:\n    case [a, *rest]:\n        pass\n", "match x:\n    case [a, *other]:\n        pass\n"),
        "global names": ("def f():\n    global a\n    a = 1\n", "def f():\n    global b\n    b = 1\n"),
        "except name": ("try:\n    pass\nexcept E as e:\n    pass\n", "try:\n    pass\nexcept E as err:\n    pass\n"),
        "comprehension async": ("async def f():\n    return [x async for x in y]\n", "async def f():\n    return [x for x in y]\n"),
        "f-string conversion": ('f"{x!r}"\n', 'f"{x!s}"\n'),
        "a local list named log is logic": ("log = []\nlog.append(1)\n", "log = []\n"),
    }
    for what, (a, b) in pairs.items():
        assert source_digest(a) != source_digest(b), what


def test_no_source_is_not_a_crash():
    assert module_logic_digest("no_such_module_anywhere") == "source-unavailable"
    assert logic_digest(len) != logic_digest(print)                                  # a builtin has no source: labelled, not raised


def test_an_installation_with_no_python_source_still_imports_and_says_so(monkeypatch):
    """Review finding: `ENGINE_LOGIC_DIGEST` is computed at import with `inspect.getsource`; with only compiled files that raised
    OSError and `import observe_consolidated` failed, which `main` never did."""
    def no_source(obj):
        raise OSError("could not get source code")
    monkeypatch.setattr(inspect, "getsource", no_source)
    module_logic_digest.cache_clear()
    try:
        assert module_logic_digest("observe_consolidated") == "source-unavailable"
        stamped = logic_digest(observe_consolidated, constants={"a": 1})
        assert len(stamped) == 64 and stamped != logic_digest(constants={"a": 1})
    finally:
        module_logic_digest.cache_clear()
