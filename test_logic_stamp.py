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
from logic_stamp import logic_digest, source_digest
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
    assert digest(BASE) == "fd9f0e28158ea2763c2bd584b828e08cc4da72b09227350385db3d5962517226"


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


def test_every_decision_records_the_logic_version_and_it_is_the_core_and_the_cassette_together():
    eng = ObserveClinicalEngine()
    eng.evaluate(VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    recorded = eng.audit_ledger.entries[-1]["data"]["logic_version"]
    expected = hashlib.sha256((ENGINE_LOGIC_DIGEST + PediatricCassette().logic_digest()).encode()).hexdigest()
    assert recorded == expected and len(recorded) == 64


def test_a_cassette_with_no_logic_digest_gets_the_cores_alone():
    from cassette import conformance_failures

    class Bare(PediatricCassette):
        logic_digest = None                                                       # an optional member that is simply absent
    bare = Bare()
    assert conformance_failures(PediatricCassette()) == []
    eng = ObserveClinicalEngine(cassette=bare)
    eng.evaluate(VitalsSnapshot("P1", T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    assert eng.audit_ledger.entries[-1]["data"]["logic_version"] == hashlib.sha256(ENGINE_LOGIC_DIGEST.encode()).hexdigest()


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


def test_what_the_core_digest_covers_is_pinned_so_dropping_something_clinical_needs_a_deliberate_edit_here():
    assert observe_consolidated.ENGINE_LOGIC_COVERS == (
        "validate_vitals", "sanitize_context", "get_age_group", "regime_distribution", "_max_severity",
        "RiskAdapters", "RiskAdaptersPhysiological", "BayesianFusion", "EscalationPolicy",
        "ObserveClinicalEngine.select_engines", "ObserveClinicalEngine._evaluate", "ObserveClinicalEngine._fault_page",
        "ObserveClinicalEngine._fault_verdict", "PatientKalmanTracker")
