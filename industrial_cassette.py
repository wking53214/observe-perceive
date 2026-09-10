"""industrial_cassette.py -- rotating equipment, as a cassette.

NOT a product, and NOT disposable. This is the second domain, and its
job is permanent: to hold the core honest. If the engine can escalate a
failing pump without one clinical line executing, the seam is real. If
it cannot, the seam is decoration and this file says so by failing.

WHY A SECOND CASSETTE LIVES IN THIS TREE FOREVER
------------------------------------------------
One cassette cannot demonstrate that a core is domain-agnostic, because
a core coupled to its only domain passes every test that domain can
write. The claim is only checkable with two, running against the same
core in the same CI invocation.

Keeping this on a branch, or in another repository, would remove exactly
that property: a core change that breaks it would go green on the main
line and nobody would learn until someone checked the other place out.
Measured 2026-09-10 across this library -- three repositories import a
cassette layer that exists nowhere, 86 findings, because the seam lived
somewhere no CI could see it.

The domain shares no vocabulary with medicine. Subjects are assets, not
patients. Readings are vibration, bearing temperature and oil pressure,
not vitals. The hard rule is ISO 10816 vibration severity, not oxygen
saturation. Nothing here is translated from the clinical cassette; it is
written the way an industrial engineer would answer the same seven
questions.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from cassette import ChannelModel
from observe_consolidated import RiskOutput, regime_distribution

#: ISO 10816-3 zone boundary for medium machines: above this a machine is
#: in the "unacceptable" zone and shutdown is warranted. The industrial
#: equivalent of a reading that is never noise.
VIBRATION_UNACCEPTABLE_MM_S = 7.1

#: Bearing temperature above which lubricant breaks down.
BEARING_CRITICAL_C = 95.0

#: Below this, the bearing is not receiving oil.
OIL_PRESSURE_MIN_BAR = 1.0

HARD_RULE_ENGINE = "iso10816"
HARD_RULE_RISK = 0.5


@dataclass(frozen=True)
class AssetReading:
    """One reading from one machine. Note there is no patient_id."""
    asset_id: str
    timestamp: datetime
    vibration_mm_s: float
    bearing_temp_c: float
    oil_pressure_bar: float
    context: Dict[str, Any] = field(default_factory=dict)


def _iso10816(obs: AssetReading) -> RiskOutput:
    """Vibration severity: the domain's hard rule."""
    triggered: List[str] = []
    score = 0.0
    if obs.vibration_mm_s > VIBRATION_UNACCEPTABLE_MM_S:
        triggered.append(f"ISO10816_UNACCEPTABLE: {obs.vibration_mm_s} mm/s "
                         f"(>{VIBRATION_UNACCEPTABLE_MM_S})")
        score += 0.5
    elif obs.vibration_mm_s > 4.5:
        triggered.append(f"ISO10816_UNSATISFACTORY: {obs.vibration_mm_s} mm/s")
        score += 0.2
    if obs.oil_pressure_bar < OIL_PRESSURE_MIN_BAR:
        triggered.append(f"OIL_STARVATION: {obs.oil_pressure_bar} bar")
        score += 0.3
    return RiskOutput(HARD_RULE_ENGINE, min(score, 1.0), 0.9,
                      regime_distribution(min(score, 1.0)), triggered, obs.timestamp)


def _thermal(obs: AssetReading) -> RiskOutput:
    triggered: List[str] = []
    score = 0.0
    if obs.bearing_temp_c > BEARING_CRITICAL_C:
        triggered.append(f"BEARING_CRITICAL: {obs.bearing_temp_c}C")
        score = 0.7
    elif obs.bearing_temp_c > 80.0:
        triggered.append(f"BEARING_ELEVATED: {obs.bearing_temp_c}C")
        score = 0.3
    return RiskOutput("thermal", score, 0.8, regime_distribution(score),
                      triggered, obs.timestamp)


def _load_history(obs: AssetReading) -> RiskOutput:
    """Abstains without history -- the same contract the clinical
    trajectory adapter uses, expressed in this domain's terms."""
    history = obs.context.get("recent_vibration") or []
    if len(history) < 3:
        return RiskOutput("load_history", 0.0, 0.2, regime_distribution(0.0),
                          ["insufficient vibration history; adapter abstains"],
                          obs.timestamp, abstained=True)
    rising = history[-1] > history[0]
    score = 0.4 if rising else 0.05
    return RiskOutput("load_history", score, 0.7, regime_distribution(score),
                      [f"vibration trend {'rising' if rising else 'flat'}"],
                      obs.timestamp)


class IndustrialCassette:
    """Rotating equipment health. Seven answers, no medicine."""

    name = "rotating_equipment"
    version = "0.1.0"

    def subject_id(self, obs: AssetReading) -> str:
        return obs.asset_id

    def validate(self, obs: AssetReading) -> List[str]:
        faults: List[str] = []
        for field_name in ("vibration_mm_s", "bearing_temp_c", "oil_pressure_bar"):
            value = getattr(obs, field_name)
            if not isinstance(value, (int, float)) or value != value:
                faults.append(f"{field_name} is not a finite number: {value!r}")
            elif value < 0:
                faults.append(f"{field_name} is negative: {value}")
        return faults

    def context_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return {"ambient_temp_c": (-50.0, 80.0), "rpm": (0.0, 30000.0)}

    def context_list_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return {"recent_vibration": (0.0, 100.0)}

    def select_engines(self, obs: AssetReading, recent_entropy: float) -> List[str]:
        engines = [HARD_RULE_ENGINE, "thermal"]
        if recent_entropy > 0.6 or obs.context.get("recent_vibration"):
            engines.append("load_history")
        return engines

    def engines(self) -> Mapping[str, Callable[[AssetReading], RiskOutput]]:
        return {HARD_RULE_ENGINE: _iso10816, "thermal": _thermal,
                "load_history": _load_history}

    def hard_rule_fired(self, outputs: List[RiskOutput]) -> bool:
        for out in outputs:
            if out.engine_name == HARD_RULE_ENGINE:
                return out.risk_score >= HARD_RULE_RISK
        return False

    def labels(self) -> Mapping[str, str]:
        return {"record_kind": "asset_assessment",
                "safety_bypass": "EQUIPMENT_SAFETY_BYPASS"}

    def channel_model(self) -> Mapping[str, ChannelModel]:
        """This domain's instruments. Vibration rising is bad; oil pressure
        FALLING is bad; bearing temperature is slow-moving and quiet, so it
        gets far less process noise than a heart rate would."""
        return {
            "vibration_mm_s": ChannelModel(0.3, 0.03, 1.0,
                                           adverse_direction=+1, adverse_rate=0.5, weight=0.25),
            "bearing_temp_c": ChannelModel(0.05, 0.005, 0.2,
                                           adverse_direction=+1, adverse_rate=2.0, weight=0.20),
            "oil_pressure_bar": ChannelModel(0.05, 0.005, 0.1,
                                             adverse_direction=-1, adverse_rate=0.3, weight=0.25),
        }

    def channels(self, obs: AssetReading) -> Dict[str, float]:
        return {"vibration_mm_s": obs.vibration_mm_s,
                "bearing_temp_c": obs.bearing_temp_c,
                "oil_pressure_bar": obs.oil_pressure_bar}
