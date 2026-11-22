"""Selection is not evidence: an OBSERVE verdict whose engines all abstained must never pass as an assessment.

`active_engines` lists the engines that were SELECTED, abstaining ones included. With a cassette whose selected engines
all abstain, the engine reports STABLE at risk 0. The shipped cassettes always select an engine that assesses, so this is
latent, but the gates used to infer evidence from selection. OBSERVE's own connector already re-runs the recorded engines
to confirm evidence; this is that check, ported upstream as an option (`engine=` / `cassette=`).

Needs CNS; in CI's `cns` job a missing CNS is an error, elsewhere this skips.
"""
import dataclasses
import logging
from datetime import datetime, timedelta, timezone

import pytest

from conftest import importorskip_cns

importorskip_cns("cns.gate")

from cns.gate import GateOutcome  # noqa: E402

from cns_connector import HandoffBindingGate, ObservationHandoff, ObserveCnsGate, confirmed_evidence  # noqa: E402
from governance_chain import observation_digest  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from observe_consolidated import (ImmutableAuditLedger, ObserveClinicalEngine, VitalsSnapshot,  # noqa: E402
                                  decision_fingerprint)
from pediatric_cassette import PediatricCassette  # noqa: E402
from perceive_consolidated import PolicyRequest  # noqa: E402

T0 = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)


class OnlyHistoryEngines(PediatricCassette):
    """Selects only engines that abstain on a first reading (they need history)."""

    def select_engines(self, obs, recent_entropy):
        return ["trajectory", "drift"]


def _sick_child():
    return VitalsSnapshot("P001", T0, 190, 82.0, 55, 40.5, context={"age_months": 24})


def _handoff(engine, vitals):
    verdict = engine.evaluate(vitals)
    entry = dict(engine.audit_ledger.entries[-1])
    request = PolicyRequest(request_id="req-1", request_type="escalate_patient", subject_id=vitals.patient_id,
                            actor_id="sentinel", timestamp=vitals.timestamp + timedelta(seconds=5),
                            context={"patient_id": vitals.patient_id, "observation_digest": observation_digest(vitals),
                                     "observe_assessment": GovernanceOrchestrator._assessment_binding(verdict, None)})
    return ObservationHandoff(vitals, verdict, entry, request)


@pytest.fixture(autouse=True)
def _quiet():
    logging.disable(logging.CRITICAL)
    yield
    logging.disable(logging.NOTSET)


def test_an_all_abstaining_assessment_reads_as_stable_with_engines_listed():
    engine = ObserveClinicalEngine(cassette=OnlyHistoryEngines())
    verdict = engine.evaluate(_sick_child())
    assert verdict.regime.value == "stable" and verdict.risk_score == 0.0
    assert verdict.active_engines == ["trajectory", "drift"] and not verdict.unassessable


def test_without_the_engine_the_gates_still_read_selection_as_evidence():
    """Recorded gap: the behaviour kept when the option is not used."""
    engine = ObserveClinicalEngine(cassette=OnlyHistoryEngines())
    h = _handoff(engine, _sick_child())
    assert ObserveCnsGate("omega").check(h.verdict).outcome is GateOutcome.PASS
    assert HandoffBindingGate(max_age_s=300, clock_skew_s=5).check(h).outcome is GateOutcome.PASS


def test_with_the_engine_an_all_abstaining_assessment_is_refused():
    engine = ObserveClinicalEngine(cassette=OnlyHistoryEngines())
    h = _handoff(engine, _sick_child())
    at_omega = ObserveCnsGate("omega", engine=engine).check(h.verdict)
    assert at_omega.outcome is GateOutcome.RETRY and "abstained or unconfirmed: trajectory, drift" in at_omega.reason
    at_handoff = HandoffBindingGate(max_age_s=300, clock_skew_s=5, cassette=engine.cassette).check(h)
    assert at_handoff.outcome is GateOutcome.RETRY and "every engine OBSERVE selected abstained" in at_handoff.reason


def test_with_the_engine_a_real_assessment_still_passes():
    engine = ObserveClinicalEngine()
    h = _handoff(engine, _sick_child())
    assert ObserveCnsGate("omega", engine=engine).check(h.verdict).outcome is GateOutcome.PASS
    assert HandoffBindingGate(max_age_s=300, clock_skew_s=5,
                              cassette=engine.cassette).check(h).outcome is GateOutcome.PASS


def test_a_verdict_the_engine_never_recorded_is_a_breach():
    engine = ObserveClinicalEngine()
    other = ObserveClinicalEngine().evaluate(_sick_child())
    result = ObserveCnsGate("omega", engine=engine).check(other)
    assert result.outcome is GateOutcome.TERMINAL_BREACH and "no entry" in result.reason


def test_a_recorded_engine_output_that_was_rewritten_is_caught_by_the_re_run():
    """Rewritten so every hash and fingerprint agrees: only re-running the engine shows the record is not true."""
    engine = ObserveClinicalEngine()
    h = _handoff(engine, _sick_child())
    data = dict(h.audit_entry["data"])
    data["outputs"] = [{**o, "risk": 0.0} if o["engine"] == "heuristic" else o for o in data["outputs"]]
    payload = {k: v for k, v in data.items()
               if k not in ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")}
    data["decision_fingerprint"] = decision_fingerprint(payload)
    body = {k: v for k, v in {**h.audit_entry, "data": data}.items() if k != "immutable_hash"}
    entry = {**body, "immutable_hash": ImmutableAuditLedger._digest(body)}
    verdict = dataclasses.replace(h.verdict, decision_fingerprint=data["decision_fingerprint"],
                                  audit_hash=entry["immutable_hash"])
    request = dataclasses.replace(h.request, context={
        **h.request.context, "observe_assessment": GovernanceOrchestrator._assessment_binding(verdict, None)})
    forged = ObservationHandoff(h.vitals, verdict, entry, request)
    assert HandoffBindingGate(max_age_s=300, clock_skew_s=5).check(forged).outcome is GateOutcome.PASS   # gap
    result = HandoffBindingGate(max_age_s=300, clock_skew_s=5, cassette=engine.cassette).check(forged)
    assert result.outcome is GateOutcome.TERMINAL_BREACH and "recorded output of heuristic" in result.reason


def test_confirmed_evidence_sorts_engines_honestly():
    engine = ObserveClinicalEngine(cassette=OnlyHistoryEngines())
    engine.evaluate(_sick_child())
    contributed, silent, problem = confirmed_evidence(engine.cassette, engine.audit_ledger.entries[-1]["data"])
    assert (contributed, sorted(silent), problem) == ([], ["drift", "trajectory"], "")
