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

from dataclasses import replace
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from cassette import ChannelModel
from kalman_trajectory import _PEDIATRIC_CHANNEL_MODEL

from observe_consolidated import (
    _NUMERIC_CONTEXT_LIST_BOUNDS,
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

#: What a faulted channel is read as so the OTHER channels can still be
#: assessed. Each value is inside every PEDIATRIC_NORMS window and clear of
#: every heuristic, bayesian, behavioral and select_engines trigger, so a
#: masked channel is a finding in no engine; the fault itself is reported
#: by the core. A placeholder, never an estimate of the child.
_VITALS_NEUTRAL: Dict[str, float] = {
    "heart_rate": 110.0,
    "oxygen_saturation": 98.0,
    "respiratory_rate": 25.0,
    "temperature": 37.0,
}

#: Every context key an engine here reads for a channel. Dropped with the
#: channel, so trajectory, drift, adversarial and the instability axis
#: abstain on it instead of setting a faulted sensor's past against the
#: placeholder (masking O2 to 98 beside previous_o2=90 fabricated an
#: uptrend in the fork's first cut).
_CHANNEL_CONTEXT_KEYS: Dict[str, Tuple[str, ...]] = {
    "oxygen_saturation": ("previous_o2", "baseline_o2", "history_o2", "recent_o2_readings"),
    "heart_rate": ("previous_hr", "baseline_hr", "history_hr", "hr_history"),
    "respiratory_rate": ("previous_rr",),
    "temperature": ("previous_temp",),
}


class PediatricCassette:
    """Pediatric deterioration: the original domain, behind the seam."""

    name = "pediatric_deterioration"
    version = "1.1.0"  # 1.1.0: supplies mask_faults

    # -- identity -----------------------------------------------------
    def subject_id(self, obs: Any) -> str:
        return obs.patient_id

    # -- trust boundary -----------------------------------------------
    def validate(self, obs: Any) -> List[str]:
        return validate_vitals(obs)

    def mask_faults(self, obs: Any, faults: List[str]) -> Optional[Any]:
        """The reading to assess in place of a faulted one, or None.

        validate_vitals names the channel before the first '=' of every
        fault; that grammar is this domain's, which is why the parse is
        here and not in the core. None when a fault names no channel we
        know (we cannot say which channel is poisoned) or when every
        channel is faulted (nothing is left to assess).
        """
        faulted: Set[str] = set()
        for fault in faults:
            channel = fault.split("=", 1)[0]
            if channel not in _VITALS_NEUTRAL:
                return None
            faulted.add(channel)
        if not faulted or faulted == set(_VITALS_NEUTRAL):
            return None
        dropped = {key for channel in faulted for key in _CHANNEL_CONTEXT_KEYS[channel]}
        context = obs.context if isinstance(obs.context, dict) else {}
        kept = {k: v for k, v in context.items() if k not in dropped}
        return replace(obs, context=kept, **{c: _VITALS_NEUTRAL[c] for c in faulted})

    def context_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return _NUMERIC_CONTEXT_SCALAR_BOUNDS

    def context_list_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return _NUMERIC_CONTEXT_LIST_BOUNDS

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

    def labels(self) -> Mapping[str, str]:
        """The exact strings this domain's ledger already contains.

        Not modernised. The audit ledger is append-only and hash-chained,
        so renaming a record kind would leave historical entries
        unmatchable by any query written against the new one. A refactor
        does not get to rewrite a governed vocabulary.
        """
        return {"record_kind": "clinical_assessment",
                "safety_bypass": "CLINICAL_SAFETY_BYPASS"}

    def channel_model(self) -> Mapping[str, ChannelModel]:
        """Reused, not restated: the same table the tracker has always
        used, so the numbers cannot drift apart from the clinical ones."""
        return _PEDIATRIC_CHANNEL_MODEL

    # -- trajectory ------------------------------------------------------
    def channels(self, obs: Any) -> Dict[str, float]:
        """The numeric series worth tracking over time in this domain."""
        return {
            "heart_rate": obs.heart_rate,
            "oxygen_saturation": obs.oxygen_saturation,
            "respiratory_rate": obs.respiratory_rate,
            "temperature": obs.temperature,
        }
