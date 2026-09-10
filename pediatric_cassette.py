"""pediatric_cassette.py -- the sepsis/deterioration domain, as a cassette.

Everything here is an answer to a question the core is not entitled to
have an opinion about: what a subject is, what a fault is, what is
physically possible, which engines are worth running, and what is so
unambiguous that waiting for a second reading is itself the harm.

Nothing here is new. Every rule is the rule observe_consolidated.py
already applied; this module gives it a name and a boundary so a second
industry can supply its own answers without touching the engine.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from observe_consolidated import (
    _NUMERIC_CONTEXT_SCALAR_BOUNDS,
    _has_physiological_telemetry,
    ObserveClinicalEngine,
    RiskOutput,
    validate_vitals,
)

#: Engines that assess this domain. Same table the engine has always used.
_ENGINES = dict(ObserveClinicalEngine.ENGINE_MAP)

#: Risk at or above which the heuristic's verdict is a hard rule rather
#: than a vote. Lifted verbatim from the escalation path so there is one
#: definition, reachable by a test -- it previously lived as a literal
#: inside `evaluate` where nothing could observe it.
HARD_RULE_RISK = 0.5

#: The engine whose hard rules carry that weight in this domain.
HARD_RULE_ENGINE = "heuristic"


class PediatricCassette:
    """Pediatric deterioration: the original domain, behind the seam."""

    name = "pediatric_deterioration"
    version = "1.0.0"

    # -- identity -----------------------------------------------------
    def subject_id(self, obs: Any) -> str:
        return obs.patient_id

    # -- trust boundary -----------------------------------------------
    def validate(self, obs: Any) -> List[str]:
        return validate_vitals(obs)

    def context_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return _NUMERIC_CONTEXT_SCALAR_BOUNDS

    # -- what to run ---------------------------------------------------
    def select_engines(self, obs: Any, recent_entropy: float) -> List[str]:
        """Deterministic engine selection based on entropy + explicit triggers."""
        engines = ["heuristic"]

        if recent_entropy > 0.6 or obs.context.get("force_heavy"):
            engines += ["bayesian", "trajectory", "drift"]

        if "previous_o2" in obs.context or "previous_hr" in obs.context:
            if "trajectory" not in engines:
                engines.append("trajectory")

        if obs.context.get("recent_o2_readings"):
            engines.append("adversarial")

        # Behavioral/syndrome analysis must run whenever ANY syndrome could
        # fire, not only on O2/HR. Septic shock keys on RR>35 + fever;
        # respiratory distress on RR>45. Thresholds match the lowest syndrome
        # cutoffs. PROVISIONAL -- pending pediatrician sign-off.
        if (obs.oxygen_saturation < 92.0 or obs.heart_rate > 140 or obs.heart_rate < 90
                or obs.respiratory_rate > 35 or obs.temperature > 38.5):
            engines.append("behavioral")

        if (recent_entropy > 0.6 or obs.context.get("force_heavy")
                or _has_physiological_telemetry(obs)):
            engines.append("physiological_reserve")

        seen = set()
        ordered = []
        for e in engines:
            if e not in seen:
                seen.add(e)
                ordered.append(e)
        return ordered

    def engines(self) -> Mapping[str, Callable[[Any], RiskOutput]]:
        return _ENGINES

    # -- what is never noise -------------------------------------------
    def hard_rule_fired(self, outputs: List[RiskOutput]) -> bool:
        """A CRITICAL_O2 reading or equivalent: escalate now, do not wait.

        The core cannot know which readings are never noise. This is the
        domain saying so, and -- unlike the literal it replaces -- it is
        a callable a test can put a value into.
        """
        for out in outputs:
            if out.engine_name == HARD_RULE_ENGINE:
                return out.risk_score >= HARD_RULE_RISK
        return False

    # -- trajectory ------------------------------------------------------
    def channels(self, obs: Any) -> Dict[str, float]:
        """The numeric series worth tracking over time in this domain."""
        return {
            "heart_rate": obs.heart_rate,
            "oxygen_saturation": obs.oxygen_saturation,
            "respiratory_rate": obs.respiratory_rate,
            "temperature": obs.temperature,
        }
