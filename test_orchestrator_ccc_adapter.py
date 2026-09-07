"""Recording orchestrated governance decisions into CCC.

The chain decides one request at a time and cannot see that it is refusing the
same class of thing for the third time this month. CCC can. These tests pin
the seam between them, and in particular the distinction that took several
attempts to get right:

    a re-observation of the same content  != an independent occurrence
    (duplicate, does not escalate)           (recurrence, escalates)

Getting that wrong in either direction breaks the feature. Too-templated
excerpts and every occurrence is absorbed as a duplicate, so nothing ever
escalates. Too-unique excerpts and nothing ever clusters, so nothing ever
escalates either. The excerpt content is the whole ballgame, so it is tested
directly rather than only through outcomes.
"""

import sys
from datetime import datetime, timezone

import pytest

sys.path.insert(0, "/home/wking53214/CCC")

from orchestrator_ccc_adapter import OrchestrationFinding, OrchestratorCCCAdapter  # noqa: E402

ccc_available = True
try:
    from ccc import AnalysisStage, CCCSystem
    from ccc.matching import MINIMUM_MATCH_LENGTH, anti_probability_of_coincidental_match
except ModuleNotFoundError:  # pragma: no cover - CCC is a sibling checkout
    ccc_available = False

pytestmark = pytest.mark.skipif(not ccc_available, reason="CCC checkout not available")


# ---------------------------------------------------------------------------
# Stand-ins for the orchestrator's own result shape. Deliberately not the real
# orchestrator: this seam consumes a plain result dict, and proving that is
# part of the contract.
# ---------------------------------------------------------------------------

class _Approval:
    def __init__(self, value):
        self.value = value


class _Decision:
    def __init__(self, subject, approval="approved", violations=None, gates=None):
        self.request_id = subject
        self.decision_id = f"perceive-{subject}"
        self.approval = _Approval(approval)
        self.unanimous_consensus = approval == "approved"
        self.applied_gates = gates or ["boundary_gate", "sentinel", "escalation_rate_policy"]
        self.violations = violations or []
        self.timestamp = datetime(2026, 5, 14, 9, 30, tzinfo=timezone.utc)


class _Fortress:
    def __init__(self, decision="APPROVED", distortion=0.0, regime="NOMINAL"):
        self.controller = "ENERGY"
        self.decision = decision
        self.distortion = distortion
        self.regime = regime
        self.audit_verified = True


def _approved(subject, distortion=0.0):
    return {
        "status": "APPROVED_AND_EXECUTED",
        "governance_decision": _Decision(subject),
        "fortress_result": _Fortress(distortion=distortion),
        "conservation_decision": None,
        "forensic_proof": None,
    }


def _refused(subject, violation, distortion):
    return {
        "status": "REJECTED",
        "reason": f"escalation rate exceeded for {subject}",
        "governance_decision": _Decision(subject, "rejected", [violation]),
        "fortress_result": _Fortress("REJECTED", distortion, "DEGRADED"),
        "conservation_decision": None,
        "forensic_proof": None,
    }


def _comparison_text(result):
    """What CCC will actually match on: the joined evidence excerpts."""
    finding = OrchestratorCCCAdapter.orchestration_to_finding(result)
    return "\n".join(excerpt for _source, excerpt in finding.evidence)


# ---------------------------------------------------------------------------
# The finding shape
# ---------------------------------------------------------------------------

def test_a_refusal_is_a_finding_too_not_only_an_approval():
    """A governance rule refusing the same case over and over is the more
    interesting signal of the two. If only approvals were recorded, the
    recurrence engine would never see it."""
    finding = OrchestratorCCCAdapter.orchestration_to_finding(
        _refused("case-A", "rate: 7 in window, limit 5", 0.31)
    )
    assert finding.verified is True
    assert "refused" in finding.conclusion.lower()
    assert finding.source_material  # traceable to the systems that produced it


def test_event_time_is_the_decisions_own_timestamp():
    finding = OrchestratorCCCAdapter.orchestration_to_finding(_approved("case-A"))
    assert finding.event_start_date == "2026-05-14"
    assert finding.event_end_date == "2026-05-14"


def test_a_result_with_no_decision_is_not_recorded():
    """Nothing happened worth recording; do not manufacture a finding."""
    adapter = OrchestratorCCCAdapter(CCCSystem())
    assert adapter.record({"status": "REJECTED", "reason": "no kernel"}) is None


def test_confidence_reflects_how_many_stages_attested():
    """Confidence is stage coverage, not a belief about correctness -- a run
    that only reached PERCEIVE is a weaker record than a full chain."""
    thin = OrchestratorCCCAdapter.orchestration_to_finding(
        {"status": "REJECTED", "reason": "x", "governance_decision": _Decision("case-A")}
    )
    thick = OrchestratorCCCAdapter.orchestration_to_finding(_approved("case-A"))
    assert thin.confidence < thick.confidence


# ---------------------------------------------------------------------------
# The comparison surface -- why the excerpts look the way they do
# ---------------------------------------------------------------------------

def test_procedural_detail_stays_out_of_the_comparison_surface():
    """Gate names and stage counts describe *how* a decision was reached, not
    what happened. They are identical in every record, so putting them in the
    excerpts makes two unrelated cases look like the same finding. They belong
    in `method`, which CCC does not compare."""
    result = _approved("case-A")
    text = _comparison_text(result)
    finding = OrchestratorCCCAdapter.orchestration_to_finding(result)

    assert "escalation_rate_policy" not in text, "gate names leaked into the excerpts"
    assert "escalation_rate_policy" in finding.method, "gate names should be recorded in method"


def test_two_genuinely_different_cases_stay_under_the_duplicate_floor():
    """The measured property this whole excerpt design exists to satisfy.

    Earlier attempts produced 110-, 71- and 62-character identical spans
    between two cases that genuinely differed -- correctly read as duplicates,
    which meant no third occurrence ever escalated. Terse excerpts carrying
    only case-specific values keep every shared run under the floor."""
    a = _comparison_text(_refused("case-A", "rate: 7 in window, limit 5", 0.31))
    b = _comparison_text(_refused("case-B", "rate: 12 in window, limit 5", 0.62))

    match = anti_probability_of_coincidental_match(a, b)
    assert match.match_length < MINIMUM_MATCH_LENGTH, (
        f"{match.match_length}-char identical span between two different cases "
        f"-- they will be absorbed as duplicates and never escalate"
    )
    assert match.implausible_as_coincidence is False


def test_identical_routine_approvals_are_duplicates_not_a_pattern():
    """The other direction, and it is correct behaviour rather than a
    limitation. Two clean approvals of structurally identical requests produce
    genuinely identical measurements -- same gates, zero violations, zero
    distortion. Nothing distinguishes them but an arbitrary id. Routine
    operation recurring is not a pattern that should reach a human, and CCC
    reading them as re-observations is the honest answer."""
    adapter = OrchestratorCCCAdapter(CCCSystem())
    first = adapter.record(_approved("case-A"))
    second = adapter.record(_approved("case-B"))

    assert "duplicate detection" in second.method
    assert first.stage is AnalysisStage.ANOMALY, "a routine approval was escalated"


# ---------------------------------------------------------------------------
# The loop that matters: recurrence -> PATTERN -> road sign
# ---------------------------------------------------------------------------

def test_a_recurring_refusal_escalates_and_raises_a_road_sign():
    """Three different subjects refused for the same governance reason.

    Occurrence 2 advances the representative to PATTERN (a machine may make
    that call). Occurrence 3 raises a REPEATED_RETURN road sign -- APM's
    halt-and-review pressure, an observable indicator rather than a
    conclusion. Nothing here reaches MANDATE; that stays human-only."""
    adapter = OrchestratorCCCAdapter(CCCSystem())

    adapter.record(_refused("case-A", "rate: 7 in window, limit 5", 0.31))
    second = adapter.record(_refused("case-B", "rate: 12 in window, limit 5", 0.62))
    third = adapter.record(_refused("case-C", "rate: 9 in window, limit 5", 0.47))

    assert "recurrence" in second.method, "second occurrence was not clustered"
    assert "recurrence" in third.method

    stages = [d.stage for d in adapter.ccc.store.discoveries.values()]
    assert AnalysisStage.PATTERN in stages, "no representative advanced to PATTERN"
    assert AnalysisStage.MANDATE not in stages, "the machine reached MANDATE -- human-only"

    signs = list(adapter.ccc.store.road_signs.values())
    assert len(signs) == 1, f"expected one road sign on the 3rd occurrence, got {len(signs)}"
    assert signs[0].category.value == "repeated_return"
    assert signs[0].is_conclusion is False, "a road sign is an indicator, not a conclusion"


def test_recurrence_survives_a_restart_when_the_store_is_persisted(tmp_path):
    """Recurrence only means anything across runs, and across runs means a
    persisted store. An in-process CCCSystem() accumulates nothing once the
    process ends, so a deployment that constructs one per request would detect
    no patterns at all while appearing to work."""
    path = tmp_path / "ccc-state.json"

    first_session = OrchestratorCCCAdapter(CCCSystem(persistence_path=str(path)))
    first_session.record(_refused("case-A", "rate: 7 in window, limit 5", 0.31))
    first_session.ccc.store.save()

    # A separate session, as a restarted service would be.
    second_session = OrchestratorCCCAdapter(CCCSystem(persistence_path=str(path)))
    assert len(second_session.ccc.store.discoveries) == 1, "prior state did not load"

    second = second_session.record(_refused("case-B", "rate: 12 in window, limit 5", 0.62))
    assert "recurrence" in second.method, (
        "the second occurrence did not cluster with the one from the previous "
        "session -- recurrence is not surviving a restart"
    )


def test_everything_recorded_here_is_machine_originated():
    """Nothing on this path may assert human authority. The discovery enters
    as an ANOMALY from a MODEL actor; only a human moves anything to MANDATE."""
    adapter = OrchestratorCCCAdapter(CCCSystem())
    record = adapter.record(_approved("case-A"))
    assert record.machine_origin is True
    assert record.stage is AnalysisStage.ANOMALY
