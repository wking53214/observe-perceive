"""
AUGUR (behavioural simulation) → chain Adapter

Runs a closed-loop simulation of what a governed quantity would do if a
proposed action were taken, and reports the result to the chain as a
simulation -- never as a measurement.

AUGUR, not fortress-kernel
--------------------------
These two were both called FORTRESS until 2026-09-07, which was a real
comprehension hazard once both ended up at different positions in the same
pipeline. `fortress-kernel` (`fortress_perceive_adapter.py`) is the
three-controller containment stage between PERCEIVE and Conservation, judging
a request that exists. AUGUR (`~/AUGUR`, `augur.Augur.run_cycle`) is a
closed-loop behavioural simulator: it steers a KPI toward a target over N
steps under a mandate layer that clamps how fast it may move, and reports
where it ended up, how much distortion accumulated, and what regime it
settled into. Different git lineages, different questions.

The new name states the constraint. A Roman augur read the omens before an
undertaking and could declare the auspices unfavourable, halting an action,
but could never compel one -- exactly the may_block / may-never-approve
asymmetry below.

Position: the screen, immediately after the door
-------------------------------------------------
This runs first among the judging stages -- after Governance Gateway admits
the artifact, before PERCEIVE evaluates it.

That placement follows from what the component can and cannot do. A
simulation may refuse and may never approve (see below), and a stage that can
only ever say "no" or "carry on" is exactly the shape a screen should have:
it cannot smuggle in an endorsement, and it removes doomed work before the
expensive normative stages spend anything on it.

It does not go first outright. The Gateway has to establish the artifact is
well-formed and untampered before anything simulates its trajectory --
projecting where a forged artifact would go is expensive work on input nobody
has established is real.

So the questions run cheapest-and-most-structural first:

    Gateway    is this a valid, untampered artifact?   (structural)
    AUGUR      if acted on, does it stay in control?   (predictive, veto-only)
    PERCEIVE   is this permitted?                      (normative)
    fortress-kernel  does the actual act stay contained?  (measured)
    Conservation     was the transformation conservative? (verification)

The governance property this seam is built around
-------------------------------------------------
A simulation is not evidence. It is a statement about a model, under
assumptions, given a seed. The sloppy version of this adapter lets a
simulated "STABLE" verdict act like a measurement and quietly become part of
the grounds for approval -- which is how a system ends up having approved
something on the strength of its own guess.

So the asymmetry is deliberate and enforced:

- A simulation showing instability **may block**. Predicting harm is a
  sufficient reason not to proceed, and being wrong about it costs a
  refusal.
- A simulation showing stability **may not approve**. It is at most an
  absence of predicted objection, and being wrong about *that* costs the
  thing the chain exists to prevent.

`SimulationOutcome.may_block` and `.may_approve` state this in the type
rather than leaving it to each caller's judgement, and `.may_approve` is
hard-coded False.

Abstention is explicit
----------------------
Most governed requests carry no trajectory to simulate -- an approval, a
claim, a data export have no initial state and target. The screen abstains on
those, and says so: `ScreenResult.abstained` is True and the reason is
recorded. A screen that silently passes everything it cannot evaluate is
worse than no screen, because the pass looks like coverage.

Everything derived from a run is stamped SIMULATION, which is CCC's own
epistemic status for exactly this and is deliberately not INFERRED: an
inference is drawn from evidence, and a model run is not evidence.

Replay
------
The seed and full config travel with the outcome. A simulation nobody can
re-run is an assertion, and the whole point of recording one is that a
reviewer can reproduce it and disagree.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _import_augur():
    """Resolve the AUGUR simulator: sibling checkout first."""
    path = os.path.join(os.path.dirname(__file__), "..", "AUGUR")
    if os.path.isdir(path) and path not in sys.path:
        sys.path.insert(0, path)
    try:
        from augur import Augur, SimulationConfig
    except ModuleNotFoundError:
        return None, None
    return Augur, SimulationConfig


Augur, SimulationConfig = _import_augur()

# Regimes the simulator reports that represent a predicted loss of control.
# Named rather than inferred from a score so the blocking condition is
# reviewable, and so a new regime name added upstream does not silently
# become "safe" by defaulting through.
UNSTABLE_REGIMES = frozenset({"UNSTABLE", "CRITICAL", "DIVERGENT", "CHAOTIC"})

# Distortion above this is treated as predicted loss of control even when the
# regime label looks benign. Matches the containment threshold used by the
# fortress-kernel stage, so AUGUR and fortress-kernel do not disagree about what
# "too distorted" means.
DISTORTION_CEILING = 0.8


@dataclass(frozen=True)
class SimulationOutcome:
    """What an AUGUR run says, and what it is allowed to be used for."""

    converged: bool
    final_state: float
    target: float
    final_error: float
    distortion: float
    regime: str
    steps_run: int
    seed: Optional[int]
    config: Dict[str, Any] = field(default_factory=dict)
    assumptions: List[str] = field(default_factory=list)
    limitations: List[str] = field(default_factory=list)

    # CCC's epistemic status for a model run. Not INFERRED: an inference is
    # drawn from evidence, and this is not evidence.
    epistemic_status: str = "SIMULATION"

    @property
    def predicts_instability(self) -> bool:
        return (
            self.regime.upper() in UNSTABLE_REGIMES
            or self.distortion > DISTORTION_CEILING
        )

    @property
    def may_block(self) -> bool:
        """A predicted loss of control is sufficient reason not to proceed."""
        return self.predicts_instability

    @property
    def may_approve(self) -> bool:
        """Never. A simulation predicting stability is an absence of predicted
        objection, not evidence that acting is safe. Approval has to rest on
        something that actually happened."""
        return False


class AugurScreenAdapter:
    """Runs a pre-decision behavioural simulation for the chain."""

    def __init__(self, seed: int = 42):
        if Augur is None:
            raise ImportError(
                "AUGUR not found. Clone it beside "
                "this repo (../AUGUR)."
            )
        self.seed = seed

    def simulate(
        self,
        initial_state: float,
        target: float,
        steps: int = 60,
        noise: float = 4.0,
    ) -> SimulationOutcome:
        """Simulate steering `initial_state` toward `target`.

        The seed is fixed per adapter instance so a run is reproducible;
        anything a reviewer cannot re-run is an assertion rather than a
        simulation.
        """
        # `shifted_target == target` is how a no-shift run is expressed: the
        # config requires target_shift_step to fall inside the run, so the
        # shift cannot be moved out of range -- it is neutralised by shifting
        # to the same value instead.
        config = SimulationConfig(
            initial_state=initial_state,
            initial_target=target,
            shifted_target=target,
            target_shift_step=max(1, steps // 2),
            total_steps=steps,
            seed=self.seed,
        )
        result = Augur(operational_seed=self.seed).run_cycle(
            noise_scale_coefficient=noise, config=config, record_history=False
        )

        final_error = float(result.get("final_error", 0.0))
        return SimulationOutcome(
            # "Converged" is a statement about this model run, not about the
            # world: the tracked quantity got within 5% of target inside the
            # step budget.
            converged=abs(final_error) <= abs(target) * 0.05,
            final_state=float(result.get("final_state", 0.0)),
            target=float(result.get("target", target)),
            final_error=final_error,
            distortion=float(result.get("distortion", 0.0)),
            regime=str(result.get("regime", "UNKNOWN")),
            steps_run=int(result.get("steps_run", 0)),
            seed=self.seed,
            config=dict(result.get("config", {}) or {}),
            assumptions=[
                "The tracked quantity responds to the modelled control law.",
                f"Noise is stationary at scale {noise}.",
                "No exogenous shock occurs within the step budget.",
                f"The step budget ({steps}) is long enough to observe settling.",
            ],
            limitations=[
                "A single seeded run, not a distribution over runs.",
                "The mandate layer's clamps are the modelled ones, not the "
                "deployed system's.",
                "Says nothing about whether the action is permitted, only "
                "about where the model goes if it is taken.",
            ],
        )

    def simulation_context(self, outcome: SimulationOutcome) -> Dict[str, Any]:
        """Context for the chain's gates.

        `simulated` is set so no downstream reader has to infer from the
        shape of the data that these numbers were generated rather than
        observed. Everything needed to re-run is included.
        """
        return {
            "simulated": True,
            "simulation_epistemic_status": outcome.epistemic_status,
            "simulation_regime": outcome.regime,
            "simulation_distortion": outcome.distortion,
            "simulation_converged": outcome.converged,
            "simulation_predicts_instability": outcome.predicts_instability,
            "simulation_seed": outcome.seed,
            "simulation_config": outcome.config,
            "simulation_assumptions": outcome.assumptions,
            "simulation_limitations": outcome.limitations,
            # Stated explicitly rather than left implicit, because the whole
            # risk at this seam is a simulation being read as grounds to act.
            "simulation_may_approve": outcome.may_approve,
        }

    def screen(
        self,
        initial_state: float,
        target: float,
        steps: int = 60,
        noise: float = 4.0,
    ) -> tuple:
        """Pre-decision screen: (proceed, outcome).

        `proceed` False means the simulation predicted loss of control and the
        caller should not run the chain. `proceed` True means only that the
        simulation raised no objection -- it is not an approval, and the chain
        still has to do its work.
        """
        outcome = self.simulate(initial_state, target, steps, noise)
        return (not outcome.may_block), outcome

    def screen_request(self, context: Optional[Dict[str, Any]]) -> "ScreenResult":
        """Screen a governed request from its context, as the chain's first
        judging stage.

        A request carries a trajectory to simulate only if the context names
        both a current state and a target. Most do not -- an approval, a
        claim, a data export have no such quantity -- and for those the screen
        abstains rather than passing.

        The distinction matters: `proceed` is True in both the "simulated and
        raised no objection" and "could not evaluate this" cases, but only one
        of them is coverage. `abstained` is what tells them apart, and it is
        recorded so a reviewer can see how much of the traffic this gate
        actually looked at.
        """
        context = context or {}
        state = context.get("current_state", context.get("initial_state"))
        target = context.get("target_state", context.get("target"))

        if state is None or target is None:
            return ScreenResult(
                proceed=True,
                abstained=True,
                reason=(
                    "no trajectory in the request: screening needs both a "
                    "current state and a target"
                ),
            )
        try:
            state = float(state)
            target = float(target)
        except (TypeError, ValueError):
            return ScreenResult(
                proceed=True,
                abstained=True,
                reason=(
                    f"trajectory values are not numeric "
                    f"(state={state!r}, target={target!r})"
                ),
            )

        outcome = self.simulate(
            state, target,
            steps=int(context.get("simulation_steps", 60)),
            noise=float(context.get("simulation_noise", 4.0)),
        )
        if outcome.may_block:
            return ScreenResult(
                proceed=False,
                abstained=False,
                outcome=outcome,
                reason=(
                    f"simulation predicts loss of control "
                    f"(regime {outcome.regime}, distortion {outcome.distortion:.3f})"
                ),
            )
        return ScreenResult(
            proceed=True,
            abstained=False,
            outcome=outcome,
            reason="simulation raised no objection",
        )


@dataclass(frozen=True)
class ScreenResult:
    """The screen's answer.

    `proceed` alone is not a verdict: it is True both when the simulation
    raised no objection and when there was nothing to simulate. `abstained`
    separates those, so "the screen passed it" is never mistaken for "the
    screen checked it".
    """
    proceed: bool
    abstained: bool
    outcome: Optional[SimulationOutcome] = None
    reason: str = ""

    @property
    def screened(self) -> bool:
        """Whether this gate actually evaluated the request."""
        return not self.abstained
