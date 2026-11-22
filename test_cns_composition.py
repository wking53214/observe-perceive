"""OBSERVE and PERCEIVE composed as one CNS chain, and the handoff between them attacked.

Each gate in cns_connector.py binds only what it is handed. This file runs them together on real outputs (a real OBSERVE
engine's verdict and audit entry, a real PERCEIVE kernel's ruling) and shows two things:

  * without `HandoffBindingGate`, a chain handed one patient's reading and a request about another passes every gate
    (recorded as an assertion, because it is why the binding gate exists);
  * with it, every attack on the handoff is refused: patient or reading substitution, an altered fingerprint or entry,
    missing engine evidence, an unassessable or malformed reading, a stale or backdated request, a request carrying no
    assessment, and an assessment taken from another patient.

Two limits are recorded as assertions too: the gate does not see replay (the execution ledger does), and it proves the
pieces agree, not that OBSERVE made them.

Needs CNS; in CI's `cns` job a missing CNS is an error, elsewhere this skips.
"""
import dataclasses
import math
from datetime import datetime, timedelta, timezone

import pytest

from conftest import importorskip_cns

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.gate_chain")

from cns.gate import GateOutcome, resolve  # noqa: E402
from cns_composition.gate_chain import run_chain  # noqa: E402

from cns_connector import HandoffBindingGate, ObservationHandoff, ObserveCnsGate, PerceiveCnsGate  # noqa: E402
from governance_chain import observation_digest  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from observe_consolidated import ImmutableAuditLedger, ObserveClinicalEngine, VitalsSnapshot  # noqa: E402
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest, PolicyRequest  # noqa: E402

T0 = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)


def _vitals(patient="P001", at=T0, hr=142, spo2=91.0, rr=28, temp=38.9):
    return VitalsSnapshot(patient, at, hr, spo2, rr, temp, context={"age_months": 24})


def _request(vitals, verdict, *, subject=None, at=None, binding=None, digest="real"):
    context = {"patient_id": subject or vitals.patient_id,
               "observe_assessment": binding if binding is not None
               else GovernanceOrchestrator._assessment_binding(verdict, None)}
    if digest == "real":
        context["observation_digest"] = observation_digest(vitals)
    elif digest is not None:
        context["observation_digest"] = digest
    return PolicyRequest(request_id="req-1", request_type="escalate_patient", subject_id=subject or vitals.patient_id,
                         actor_id="sentinel", context=context, timestamp=at or vitals.timestamp + timedelta(seconds=5))


def _assess(engine, vitals):
    verdict = engine.evaluate(vitals)
    return verdict, dict(engine.audit_ledger.entries[-1])


def _kernel():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=T0, policies={}))
    return kernel


def _compose(handoff, *, bind=True):
    """OBSERVE's chain (vitals in, verdict out), then PERCEIVE's (request in, ruling out), resolved together."""
    observe = run_chain(alpha=[(ObserveCnsGate("alpha"), handoff.vitals)],
                        omega=[(ObserveCnsGate("omega"), handoff.verdict)])
    alpha = [(PerceiveCnsGate("boundary_gate", "alpha"), handoff.request)]
    if bind:
        alpha.insert(0, (HandoffBindingGate(max_age_s=300, clock_skew_s=5), handoff))
    ruling = _kernel().evaluate_request(handoff.request)
    perceive = run_chain(alpha=alpha, omega=[(PerceiveCnsGate("boundary_gate", "omega"), ruling)])
    results = observe.results + perceive.results
    return resolve(results), results


def _clean():
    vitals = _vitals()
    verdict, entry = _assess(ObserveClinicalEngine(), vitals)
    return ObservationHandoff(vitals, verdict, entry, _request(vitals, verdict))


def _reasons(results):
    return " | ".join(r.reason for r in results if r.outcome is not GateOutcome.PASS)


# ---- the composed chain works on real outputs -------------------------------------------------------------------

def test_a_real_handoff_passes_the_whole_composed_chain_and_every_verdict_is_bound():
    outcome, results = _compose(_clean())
    assert outcome is GateOutcome.PASS, _reasons(results)
    assert all(r.subject_digest for r in results)
    assert [r.gate for r in results] == ["observe.vitals_ingress", "observe.verdict_egress",
                                         "handoff.observe_to_perceive", "perceive.boundary_gate",
                                         "perceive.boundary_gate"]


def test_without_the_binding_gate_a_patient_substitution_passes_every_gate():
    """Why the binding gate exists: each gate binds only its own candidate, so nothing relates them."""
    h = _clean()
    swapped = dataclasses.replace(h, request=_request(h.vitals, h.verdict, subject="P002"))
    outcome, _ = _compose(swapped, bind=False)
    assert outcome is GateOutcome.PASS


# ---- every attack on the handoff, with the binding gate ----------------------------------------------------------

def _attack(name):
    h = _clean()
    v, fv, entry = h.vitals, h.verdict, h.audit_entry
    if name == "patient substitution":
        return dataclasses.replace(h, request=_request(v, fv, subject="P002"))
    if name == "reading substitution":
        other = _vitals(hr=88, spo2=99.0, rr=20, temp=36.8)
        return dataclasses.replace(h, vitals=other, request=_request(other, fv))
    if name == "request carries an altered fingerprint":
        binding = {**GovernanceOrchestrator._assessment_binding(fv, None), "decision_fingerprint": "f" * 64}
        return dataclasses.replace(h, request=_request(v, fv, binding=binding))
    if name == "verdict fingerprint altered":
        forged = dataclasses.replace(fv, decision_fingerprint="0" * 64)
        return dataclasses.replace(h, verdict=forged, request=_request(v, forged))
    if name == "audit entry edited":
        edited = {**entry, "data": {**entry["data"], "verdict": {**entry["data"]["verdict"], "regime": "STABLE"}}}
        return dataclasses.replace(h, audit_entry=edited)
    if name == "audit entry edited and rehashed":
        # The risk is lowered, the entry re-hashed and the verdict pointed at the new hash, so the hash checks agree:
        # only the fingerprint, recomputed from the edited evidence, can catch it.
        risk = entry["data"]["verdict"]["risk_score"]
        data = {**entry["data"], "verdict": {**entry["data"]["verdict"], "risk_score": risk / 2}}
        body = {k: val for k, val in {**entry, "data": data}.items() if k != "immutable_hash"}
        rehashed = {**body, "immutable_hash": ImmutableAuditLedger._digest(body)}
        return dataclasses.replace(h, audit_entry=rehashed,
                                   verdict=dataclasses.replace(fv, audit_hash=rehashed["immutable_hash"]))
    if name == "verdict state commitment altered":
        forged = dataclasses.replace(fv, state_commitment="c" * 64)
        return dataclasses.replace(h, verdict=forged, request=_request(v, forged))
    if name == "missing engine evidence":
        empty = dataclasses.replace(fv, active_engines=[])
        return dataclasses.replace(h, verdict=empty)
    if name == "unassessable reading":
        bad = _vitals(spo2=math.nan)
        verdict, e = _assess(ObserveClinicalEngine(), bad)
        return ObservationHandoff(bad, verdict, e, _request(bad, verdict))
    if name == "invalid vital type":
        bad = dataclasses.replace(v, heart_rate="fast")
        return dataclasses.replace(h, vitals=bad, request=_request(bad, fv))
    if name == "stale reading":
        return dataclasses.replace(h, request=_request(v, fv, at=v.timestamp + timedelta(hours=2)))
    if name == "request dated before its reading":
        return dataclasses.replace(h, request=_request(v, fv, at=v.timestamp - timedelta(hours=1)))
    if name == "request carries no assessment":
        return dataclasses.replace(h, request=_request(v, fv, binding={"status": "unavailable"}))
    if name == "request does not commit to the reading":
        return dataclasses.replace(h, request=_request(v, fv, digest=None))
    if name == "another patient's real assessment":
        engine = ObserveClinicalEngine()
        theirs = _vitals(patient="P002")
        verdict, e = _assess(engine, theirs)
        return ObservationHandoff(v, verdict, e, _request(v, verdict))
    raise AssertionError(name)


@pytest.mark.parametrize("attack,expected", [
    ("patient substitution", GateOutcome.TERMINAL_BREACH),
    ("reading substitution", GateOutcome.TERMINAL_BREACH),
    ("request carries an altered fingerprint", GateOutcome.TERMINAL_BREACH),
    ("verdict fingerprint altered", GateOutcome.TERMINAL_BREACH),
    ("audit entry edited", GateOutcome.TERMINAL_BREACH),
    ("audit entry edited and rehashed", GateOutcome.TERMINAL_BREACH),
    ("verdict state commitment altered", GateOutcome.TERMINAL_BREACH),
    ("missing engine evidence", GateOutcome.RETRY),
    ("unassessable reading", GateOutcome.RETRY),
    ("invalid vital type", None),                      # refused; RETRY or BREACH depending on which gate sees it first
    ("stale reading", GateOutcome.RETRY),
    ("request dated before its reading", GateOutcome.TERMINAL_BREACH),
    ("request carries no assessment", GateOutcome.RETRY),
    ("request does not commit to the reading", GateOutcome.TERMINAL_BREACH),
    ("another patient's real assessment", GateOutcome.TERMINAL_BREACH),
])
def test_every_attack_on_the_handoff_is_refused(attack, expected):
    outcome, results = _compose(_attack(attack))
    assert outcome is not GateOutcome.PASS, f"{attack} passed: {_reasons(results)}"
    if expected is not None:
        assert outcome is expected, f"{attack}: {outcome} ({_reasons(results)})"


def test_the_binding_gate_alone_names_why_for_each_substitution():
    gate = HandoffBindingGate(max_age_s=300, clock_skew_s=5)
    for attack, words in [("patient substitution", "different patient"), ("reading substitution", "different reading"),
                          ("request carries an altered fingerprint", "different assessment"),
                          ("audit entry edited", "altered"), ("audit entry edited and rehashed", "does not recompute"),
                          ("verdict state commitment altered", "state commitment"),
                          ("another patient's real assessment", "different patient")]:
        result = gate.check(_attack(attack))
        assert result.outcome is GateOutcome.TERMINAL_BREACH and words in result.reason, (attack, result.reason)


@pytest.mark.parametrize("attack", ["missing engine evidence", "unassessable reading"])
def test_the_binding_gate_alone_never_reads_an_abstention_as_stable(attack):
    """In the full chain OBSERVE's own gates refuse these first; the binding gate must hold without them too."""
    result = HandoffBindingGate(max_age_s=300, clock_skew_s=5).check(_attack(attack))
    assert result.outcome is GateOutcome.RETRY and "no assessment is not a stable one" in result.reason, result.reason


# ---- placement and configuration ---------------------------------------------------------------------------------

def test_the_binding_gate_cannot_be_seated_at_the_wrong_end():
    h = _clean()
    with pytest.raises(ValueError, match="wrong slot"):
        run_chain(alpha=[(PerceiveCnsGate("boundary_gate", "alpha"), h.request)],
                  omega=[(HandoffBindingGate(max_age_s=300, clock_skew_s=5), h)])


@pytest.mark.parametrize("kwargs", [{"max_age_s": 0, "clock_skew_s": 5}, {"max_age_s": -1, "clock_skew_s": 5},
                                    {"max_age_s": math.inf, "clock_skew_s": 5}, {"max_age_s": 300, "clock_skew_s": -1},
                                    {"max_age_s": True, "clock_skew_s": 5}])
def test_the_policy_numbers_have_no_default_and_must_be_sane(kwargs):
    with pytest.raises(ValueError):
        HandoffBindingGate(**kwargs)
    with pytest.raises(TypeError):
        HandoffBindingGate()


def test_a_handoff_of_the_wrong_shape_is_a_breach_not_a_crash():
    h = _clean()
    gate = HandoffBindingGate(max_age_s=300, clock_skew_s=5)
    assert gate.check(dataclasses.replace(h, audit_entry="not an entry")).outcome is GateOutcome.TERMINAL_BREACH
    assert gate.check(h.request).outcome is GateOutcome.TERMINAL_BREACH


# ---- limits, recorded as assertions -----------------------------------------------------------------------------

def test_the_gate_does_not_see_replay_the_execution_ledger_does():
    """The same valid handoff passes twice: the gate is stateless. Refusing a second use of one approval is the
    execution ledger's job (test_execution_guard.py), not this gate's."""
    h = _clean()
    assert _compose(h)[0] is GateOutcome.PASS and _compose(h)[0] is GateOutcome.PASS


def test_the_gate_proves_the_pieces_agree_not_that_observe_made_them():
    """An assessment fabricated whole (an entry, a verdict and a request made to agree) passes: the entry's hash takes no
    key. Proving origin needs OBSERVE's ledger head or a signature."""
    h = _clean()
    original = h.audit_entry["data"]["verdict"]
    data = {**h.audit_entry["data"], "verdict": {**original, "risk_score": original["risk_score"] / 2}}
    from observe_consolidated import decision_fingerprint
    payload = {k: val for k, val in data.items()
               if k not in ("decision_fingerprint", "predecessor_state_commitment", "state_commitment")}
    data["decision_fingerprint"] = decision_fingerprint(payload)
    body = {k: val for k, val in {**h.audit_entry, "data": data}.items() if k != "immutable_hash"}
    entry = {**body, "immutable_hash": ImmutableAuditLedger._digest(body)}
    verdict = dataclasses.replace(h.verdict, decision_fingerprint=data["decision_fingerprint"],
                                  audit_hash=entry["immutable_hash"], risk_score=data["verdict"]["risk_score"])
    assert data["decision_fingerprint"] != h.verdict.decision_fingerprint          # a real fabrication, not a copy
    fabricated = ObservationHandoff(h.vitals, verdict, entry, _request(h.vitals, verdict))
    assert HandoffBindingGate(max_age_s=300, clock_skew_s=5).check(fabricated).outcome is GateOutcome.PASS
