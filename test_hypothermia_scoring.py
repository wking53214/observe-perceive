"""test_hypothermia_scoring.py -- what low body temperature does on its own, and with company (register row C18).

A source comment used to say HYPOTHERMIA is a hard rule. It is not: it scores 0.4 and the hard-rule line is 0.5, so a neonate at 34.5 C with
every other channel normal is stable and is never paged (also pinned as S7 in test_single_channel_behaviour.py). With one more finding it
does reach the line. These tests pin both facts, so the comment and the behaviour cannot drift apart again. They do NOT say the
behaviour is right: whether a lone low temperature should page is a clinical decision (docs/race/CLINICIAN_REVIEW_SHEET.md, S7).
"""
from datetime import datetime, timedelta, timezone

from observe_consolidated import ObserveClinicalEngine, RiskAdapters, VitalsSnapshot
from pediatric_cassette import PediatricCassette

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)
NEONATE_MONTHS = 1


def snap(hr=140, rr=40, temp=34.5, o2=97, minute=0, patient="P"):
    return VitalsSnapshot(patient, T0 + timedelta(minutes=minute), hr, o2, rr, temp, {"age_months": NEONATE_MONTHS})


def test_hypothermia_scores_below_the_hard_rule_line_on_its_own():
    out = RiskAdapters.heuristic(snap())
    assert any(r.startswith("HYPOTHERMIA") for r in out.triggered_rules)
    assert out.risk_score == 0.4 and not PediatricCassette().hard_rule_fired([out])


def test_hypothermia_with_one_more_finding_reaches_the_hard_rule_line():
    out = RiskAdapters.heuristic(snap(rr=60))                        # a raised respiratory rate adds 0.15
    assert any(r.startswith("HYPOTHERMIA") for r in out.triggered_rules) and any(r.startswith("TACHYPNEA") for r in out.triggered_rules)
    assert round(out.risk_score, 2) == 0.55 and PediatricCassette().hard_rule_fired([out])


def test_what_that_means_for_a_patient_alone_never_paged_with_company_paged_at_once():
    alone, company = ObserveClinicalEngine(), ObserveClinicalEngine()
    alone_events = [alone.evaluate(snap(minute=k)) for k in range(3)]
    company_events = [company.evaluate(snap(rr=60, minute=k)) for k in range(3)]
    assert [v.regime.value for v in alone_events] == ["stable"] * 3 and not any(v.escalation_required for v in alone_events)
    assert [v.regime.value for v in company_events] == ["warning"] * 3
    assert [v.escalation_required for v in company_events] == [True, False, False]       # paged on the first reading only
