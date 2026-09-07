"""Hardening tests added after the second red-team pass on OBSERVE.
Closes R-CTX: the context trust boundary the prior pass flagged but left open."""
from datetime import datetime
from observe_consolidated import (
    VitalsSnapshot, ObserveClinicalEngine, sanitize_context,
)


def _vitals(**ctx):
    return VitalsSnapshot(patient_id="p", timestamp=datetime.now(),
        heart_rate=110, oxygen_saturation=98, respiratory_rate=24,
        temperature=37.0, context=ctx)


def test_non_numeric_context_does_not_crash():
    """[MEDIUM/R-CTX] A non-numeric context scalar must not crash scoring.
    Before the fix, age_months='x' raised TypeError deep in the engine."""
    eng = ObserveClinicalEngine()
    for bad in ("notanumber", {"x": 1}, [1, 2], None):
        v = _vitals(age_months=bad)
        verdict = eng.evaluate(v)  # must not raise
        assert 0.0 <= verdict.risk_score <= 1.0
    print("PASS: non-numeric context scalar does not crash scoring")


def test_nan_context_dropped():
    """[MEDIUM/R-CTX] NaN/Inf context scalars are dropped, not trusted."""
    clean, notes = sanitize_context({
        "age_months": 12, "previous_o2": float("nan"),
        "baseline_hr": float("inf"), "time_delta_seconds": float("-inf"),
    })
    assert "previous_o2" not in clean
    assert "baseline_hr" not in clean
    assert "time_delta_seconds" not in clean
    assert clean["age_months"] == 12
    assert len(notes) == 3
    print("PASS: NaN/Inf context scalars dropped")


def test_out_of_range_context_dropped():
    """[MEDIUM/R-CTX] A physically impossible context value (e.g. SpO2 200%)
    is dropped so it cannot skew the score."""
    clean, _ = sanitize_context({
        "previous_o2": 200,      # impossible
        "baseline_hr": -100,     # impossible
        "previous_temp": 1000,   # impossible
        "age_months": 12,
    })
    assert "previous_o2" not in clean
    assert "baseline_hr" not in clean
    assert "previous_temp" not in clean
    assert clean["age_months"] == 12
    print("PASS: out-of-range context scalars dropped")


def test_out_of_range_context_does_not_skew_score():
    """[MEDIUM/R-CTX] Confirm at the engine level: an impossible previous_o2
    yields the same score as having no previous_o2 at all."""
    eng = ObserveClinicalEngine()
    with_garbage = eng.evaluate(_vitals(age_months=12, previous_o2=200)).risk_score
    without = eng.evaluate(_vitals(age_months=12)).risk_score
    assert with_garbage == without
    print("PASS: out-of-range context does not skew the score")


def test_bad_list_elements_filtered():
    """[MEDIUM/R-CTX] Non-finite / out-of-range elements are filtered from
    numeric list context, keeping the good ones."""
    clean, _ = sanitize_context({
        "history_o2": [95, float("nan"), 97, 200, 96],  # nan and 200 are bad
    })
    assert clean["history_o2"] == [95, 97, 96]
    print("PASS: bad list elements filtered, good ones kept")


def test_non_numeric_keys_pass_through():
    """Context keys that are not numeric signals (e.g. 'alert') pass through."""
    clean, _ = sanitize_context({"alert": "crying", "age_months": 12})
    assert clean["alert"] == "crying"
    assert clean["age_months"] == 12
    print("PASS: non-numeric context keys pass through unchanged")


def test_context_not_a_dict_replaced():
    """A non-dict context is replaced with an empty dict, with a note."""
    clean, notes = sanitize_context("not a dict")
    assert clean == {}
    assert len(notes) == 1
    print("PASS: non-dict context replaced with empty")


def test_valid_context_unchanged():
    """Regression: fully valid context survives sanitization intact."""
    ctx = {"age_months": 12, "previous_o2": 98, "baseline_hr": 110,
           "history_o2": [97, 98, 96], "alert": "calm"}
    clean, notes = sanitize_context(ctx)
    assert clean == ctx
    assert notes == []
    print("PASS: valid context unchanged")


if __name__ == "__main__":
    tests = [
        test_non_numeric_context_does_not_crash,
        test_nan_context_dropped,
        test_out_of_range_context_dropped,
        test_out_of_range_context_does_not_skew_score,
        test_bad_list_elements_filtered,
        test_non_numeric_keys_pass_through,
        test_context_not_a_dict_replaced,
        test_valid_context_unchanged,
    ]
    print("=" * 64)
    for t in tests:
        t()
    print("=" * 64)
    print(f"ALL {len(tests)} CONTEXT-SANITIZATION TESTS PASSED")
