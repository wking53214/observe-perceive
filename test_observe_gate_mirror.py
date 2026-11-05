"""The OBSERVE gate in this repo follows OBSERVE's own rule for the two cases its audit found (OBS-5, OBS-6)."""

import pytest

from conftest import importorskip_cns

importorskip_cns("cns.gate", reason="needs the pinned CNS package")

from cns.gate import GateOutcome  # noqa: E402
from cns_connector import ObserveCnsGate  # noqa: E402
from test_cns_connector import make_verdict, make_vitals  # noqa: E402


def test_a_verdict_carrying_faults_is_a_partial_assessment_even_when_it_says_it_is_assessable():
    verdict = make_verdict(validation_faults=["heart_rate: implausible"], unassessable=False)
    result = ObserveCnsGate("omega").check(verdict)
    assert result.outcome is GateOutcome.RETRY and "partial assessment" in result.reason


def test_an_unassessable_verdict_with_no_listed_fault_still_says_why():
    result = ObserveCnsGate("omega").check(make_verdict(unassessable=True))
    assert result.outcome is GateOutcome.RETRY and "unassessable" in result.reason


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_reading_is_a_sensor_fault_to_re_read_and_the_refusal_is_still_bound(bad):
    result = ObserveCnsGate("alpha").check(make_vitals(heart_rate=bad))
    assert result.outcome is GateOutcome.RETRY, result.reason
    assert result.bound()


def test_a_normal_verdict_and_normal_vitals_still_pass():
    assert ObserveCnsGate("omega").check(make_verdict()).outcome is GateOutcome.PASS
    assert ObserveCnsGate("alpha").check(make_vitals()).outcome is GateOutcome.PASS
