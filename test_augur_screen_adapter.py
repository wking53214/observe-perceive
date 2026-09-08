"""FORTRESS behavioural simulation as a pre-decision screen.

Most of these are about one governance property rather than about whether the
simulator runs: a simulation is not evidence, and the adapter must not let a
simulated verdict act like a measurement.

The asymmetry under test:
    predicted instability   -> may block
    predicted stability     -> may NOT approve

Being wrong about the first costs a refusal. Being wrong about the second
costs the thing the chain exists to prevent.
"""

import pytest
from dataclasses import dataclass as _dc
from datetime import datetime as _dt, timezone as _tz
from typing import List as _List
from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest

fortress_available = True
try:
    from augur_screen_adapter import (
        DISTORTION_CEILING,
        AugurScreenAdapter,
        SimulationOutcome,
        UNSTABLE_REGIMES,
    )
    import augur_screen_adapter as _adapter
    # The adapter imports without the pack and refuses at construction; the
    # sentinel is what says whether AUGUR itself resolved.
    fortress_available = _adapter.Augur is not None
except ImportError:  # pragma: no cover
    fortress_available = False

pytestmark = pytest.mark.skipif(
    not fortress_available, reason="AUGUR checkout not available"
)


def _outcome(**overrides) -> "SimulationOutcome":
    base = dict(
        converged=True, final_state=99.0, target=100.0, final_error=-1.0,
        distortion=0.05, regime="STABLE", steps_run=60, seed=42,
    )
    base.update(overrides)
    return SimulationOutcome(**base)


@pytest.fixture
def adapter():
    return AugurScreenAdapter(seed=42)


# ---------------------------------------------------------------------------
# The asymmetry: the reason this seam is shaped the way it is
# ---------------------------------------------------------------------------

def test_a_stable_simulation_may_never_approve():
    """The load-bearing rule. A simulation predicting stability is an absence
    of predicted objection, not evidence that acting is safe. Letting it
    approve is how a system ends up having approved something on the strength
    of its own guess."""
    assert _outcome(regime="STABLE", distortion=0.0, converged=True).may_approve is False


def test_no_outcome_of_any_kind_may_approve():
    """Hard-coded rather than conditional, so no future combination of fields
    can make it True."""
    for regime in ("STABLE", "UNSTABLE", "UNKNOWN", "CONVERGED"):
        for distortion in (0.0, 0.5, 0.99):
            assert _outcome(regime=regime, distortion=distortion).may_approve is False


def test_an_unstable_simulation_may_block():
    """The other half. Predicting loss of control is a sufficient reason not
    to proceed; being wrong about it costs only a refusal."""
    assert _outcome(regime="UNSTABLE").may_block is True


def test_excessive_distortion_blocks_even_under_a_benign_regime_label():
    """A regime label is a classification and can be wrong or newly-added.
    Distortion above the ceiling is treated as predicted loss of control
    regardless, so a benign-looking label cannot carry an unsafe run through."""
    assert _outcome(regime="STABLE", distortion=DISTORTION_CEILING + 0.01).may_block is True


def test_the_blocking_regimes_are_named_not_inferred():
    """Named so the blocking condition is reviewable, and so a regime added
    upstream does not silently become 'safe' by defaulting through."""
    assert "UNSTABLE" in UNSTABLE_REGIMES
    assert _outcome(regime="SOME_NEW_REGIME", distortion=0.0).predicts_instability is False, (
        "an unrecognised regime should fall to the distortion check, not be "
        "treated as unstable on the basis of its name"
    )


# ---------------------------------------------------------------------------
# A simulation is not evidence
# ---------------------------------------------------------------------------

def test_every_outcome_is_stamped_simulation_not_inferred():
    """SIMULATION is CCC's own status for a model run. INFERRED would be
    wrong: an inference is drawn from evidence, and this is not evidence."""
    assert _outcome().epistemic_status == "SIMULATION"
    assert _outcome().epistemic_status != "INFERRED"


def test_the_context_says_these_numbers_were_generated(adapter):
    """So no downstream reader has to infer from the shape of the data that
    it was simulated rather than measured."""
    context = adapter.simulation_context(_outcome())
    assert context["simulated"] is True
    assert context["simulation_epistemic_status"] == "SIMULATION"
    assert context["simulation_may_approve"] is False


def test_assumptions_and_limitations_travel_with_the_outcome(adapter):
    """A simulation reported without its assumptions is an assertion wearing
    a number's clothes."""
    outcome = adapter.simulate(initial_state=60.0, target=100.0, steps=20)
    assert outcome.assumptions, "no assumptions recorded"
    assert outcome.limitations, "no limitations recorded"
    context = adapter.simulation_context(outcome)
    assert context["simulation_assumptions"]
    assert context["simulation_limitations"]


# ---------------------------------------------------------------------------
# Replay
# ---------------------------------------------------------------------------

def test_the_same_seed_reproduces_the_same_run():
    """A simulation nobody can re-run is an assertion. The point of recording
    one is that a reviewer can reproduce it and disagree."""
    first = AugurScreenAdapter(seed=7).simulate(60.0, 100.0, steps=30)
    second = AugurScreenAdapter(seed=7).simulate(60.0, 100.0, steps=30)

    assert first.final_state == second.final_state
    assert first.regime == second.regime
    assert first.distortion == second.distortion


def test_the_seed_and_config_are_carried_so_the_run_can_be_reproduced(adapter):
    outcome = adapter.simulate(60.0, 100.0, steps=30)
    assert outcome.seed == 42
    assert outcome.config, "no config recorded -- the run cannot be reproduced"


# ---------------------------------------------------------------------------
# Running it
# ---------------------------------------------------------------------------

def test_a_real_run_produces_a_usable_outcome(adapter):
    outcome = adapter.simulate(initial_state=60.0, target=100.0, steps=60)
    assert outcome.steps_run > 0
    assert outcome.regime
    assert isinstance(outcome.converged, bool)


def test_screen_returns_proceed_plus_the_outcome_that_justified_it(adapter):
    """`proceed` True means the simulation raised no objection -- not that it
    approved anything. The outcome comes back either way so the caller records
    what the screen actually saw."""
    proceed, outcome = adapter.screen(initial_state=60.0, target=100.0, steps=30)
    assert isinstance(proceed, bool)
    assert outcome.epistemic_status == "SIMULATION"
    assert proceed is not outcome.may_block


def test_a_screen_that_predicts_instability_says_do_not_proceed():
    """Constructed directly rather than by finding inputs that destabilise the
    model: the rule under test is the adapter's, not the simulator's."""
    unstable = _outcome(regime="DIVERGENT", distortion=0.95)
    assert unstable.may_block is True
    assert unstable.may_approve is False


# ---------------------------------------------------------------------------
# As the chain's first judging stage
# ---------------------------------------------------------------------------




@_dc
class _S:
    value: str


@_dc
class _M:
    origin_status: _S
    authority_status: _S
    epistemic_status: _S
    parent_artifact_ids: _List[str]


@_dc
class _A:
    artifact_id: str
    content: str
    metadata: _M


def _artifact(aid="sim-001"):
    return _A(aid, "escalate: sustained tachycardia",
              _M(_S("SENTINEL"), _S("SYSTEM"), _S("INFERRED"), []))


def _orch(screen=True):
    k = PerceiveGovernanceKernel()
    k.register_manifest(PolicyManifest("sim-manifest", "1.0.0", _dt.now(_tz.utc), {}))
    return GovernanceOrchestrator(k, ConservationKernel(), ObserveClinicalEngine(),
                                  simulation_screen=screen)


def test_the_screen_abstains_on_a_request_with_no_trajectory(adapter):
    """Most governed requests have no state and target to simulate -- an
    approval, a claim, an export. The screen must say it abstained rather than
    reporting a pass it did not earn."""
    result = adapter.screen_request({"patient_id": "P001"})
    assert result.proceed is True
    assert result.abstained is True
    assert result.screened is False
    assert "trajectory" in result.reason


def test_abstention_is_distinguishable_from_a_clean_pass(adapter):
    """`proceed` is True in both cases; only `screened` separates 'looked at
    it and had no objection' from 'could not evaluate it'. A screen that
    cannot tell those apart reports coverage it does not have."""
    abstained = adapter.screen_request({})
    evaluated = adapter.screen_request({"current_state": 60.0, "target_state": 100.0,
                                        "simulation_steps": 20})
    assert abstained.proceed is evaluated.proceed is True
    assert abstained.screened is False
    assert evaluated.screened is True


def test_non_numeric_trajectory_values_abstain_rather_than_crash(adapter):
    result = adapter.screen_request({"current_state": "sixty", "target_state": 100.0})
    assert result.abstained is True
    assert "numeric" in result.reason


def test_the_screen_runs_before_perceive_in_the_chain():
    """Ordering is the point of putting it here: doomed work costs one model
    run instead of the whole chain."""
    orch = _orch()
    calls = []
    real_screen = orch.simulation_screen.screen_request
    real_perceive = orch.sentinel_adapter.evaluate_through_perceive

    orch.simulation_screen.screen_request = lambda *a, **k: (calls.append("screen"), real_screen(*a, **k))[1]
    orch.sentinel_adapter.evaluate_through_perceive = lambda *a, **k: (calls.append("perceive"), real_perceive(*a, **k))[1]

    orch.orchestrate_request(_artifact(), "escalate", lambda c: {"status": "executed"},
                             context={"patient_id": "P001",
                                      "current_state": 60.0, "target_state": 100.0,
                                      "simulation_steps": 20})
    assert calls == ["screen", "perceive"]


def test_a_screen_refusal_halts_before_perceive_evaluates():
    """Veto-only means the veto has to actually stop things."""
    orch = _orch()
    perceive_ran = []
    real = orch.sentinel_adapter.evaluate_through_perceive
    orch.sentinel_adapter.evaluate_through_perceive = lambda *a, **k: (perceive_ran.append(1), real(*a, **k))[1]

    from augur_screen_adapter import ScreenResult
    orch.simulation_screen.screen_request = lambda ctx: ScreenResult(
        proceed=False, abstained=False, reason="simulation predicts loss of control"
    )

    result = orch.orchestrate_request(_artifact(), "escalate", lambda c: {"status": "executed"},
                                      context={"patient_id": "P001"})
    assert result["status"] == "REJECTED"
    assert "Simulation screen refused" in result["reason"]
    assert result["governance_decision"] is None, "PERCEIVE reported a decision it never made"
    assert not perceive_ran, "PERCEIVE ran after the screen refused"


def test_the_screen_is_off_by_default():
    """A missing FORTRESS checkout must never be a hard failure for the rest
    of the chain."""
    orch = _orch(screen=False)
    assert orch.simulation_screen is None
    result = orch.orchestrate_request(_artifact(), "escalate", lambda c: {"status": "executed"},
                                      context={"patient_id": "P001"})
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["simulation_screen"] is None


def test_the_screens_verdict_is_carried_into_the_result():
    """So the record shows what the screen saw, including when it abstained."""
    orch = _orch()
    result = orch.orchestrate_request(_artifact(), "escalate", lambda c: {"status": "executed"},
                                      context={"patient_id": "P001"})
    screen = result["simulation_screen"]
    assert screen is not None
    assert screen.abstained is True
