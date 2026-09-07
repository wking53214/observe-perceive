"""
Capacity & Accuracy Telemetry (WS3 — strictly observational)
============================================================

Two independent, OPT-IN, STRICTLY OBSERVATIONAL components. This module has ZERO
decision impact: it reads, computes, and reports, but never influences control flow.
(The capacity-aware escalation *control* — `ReserveModulator` — was reclassified as a
control component and moved to `reserve_control.py`, next to the PERCEIVE kernel that
owns control decisions.)

  1. ErlangCapacityForecaster — translate a predicted high-risk patient volume into a
     required-clinician count + staffing headroom + alert level. Purely advisory.
     Abstains (returns a SAFE/no-alert forecast) when given no demand.

  2. AccuracyMonitor — track predictions vs. observed outcomes and report
     sensitivity / specificity / PPV / NPV / F1, with simple drift detection.
     Abstains with an explicit "insufficient_data" status rather than fabricating
     metrics from too few samples.

The observational boundary is enforced structurally: both outputs (`CapacityForecast`,
`AccuracyReport`) are immutable (frozen) artifacts, so a telemetry result cannot be
fed back as a mutable control signal.

Overlap note: abstention-based fusion already exists in observe_consolidated.py
(BayesianFusion excludes abstaining engines; adapters set abstained=True). It is
NOT re-implemented here — that capability is already present and superior, being
integrated directly into fusion.

Python 3.8+, stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


# ============================================================================
# 1. ERLANG-C CAPACITY FORECASTER (advisory)
# ============================================================================

class CapacityAlert(Enum):
    SAFE = "safe"          # current staffing meets the required headroom
    HIGH = "high"          # within one clinician of the required count
    CRITICAL = "critical"  # understaffed for the predicted high-risk volume


@dataclass(frozen=True)
class CapacityForecast:
    predicted_high_risk: float
    offered_load_erlangs: float
    required_clinicians: int
    current_clinicians: Optional[int]
    headroom: Optional[int]            # current - required (None if current unknown)
    expected_wait_minutes: float
    wait_probability: float
    alert: CapacityAlert
    notes: List[str] = field(default_factory=list)


class ErlangCapacityForecaster:
    """Erlang-C staffing translation. ADVISORY ONLY — never alters a clinical decision.

    Models predicted high-risk patients as an M/M/c queue: offered load (Erlangs) =
    predicted_count * minutes_per_response / window_minutes. Returns the smallest
    clinician count meeting both a wait-probability and an expected-wait target.
    """

    def __init__(self, minutes_per_response: float = 20.0, window_minutes: float = 60.0,
                 target_wait_probability: float = 0.2, target_wait_minutes: float = 5.0,
                 max_clinicians: int = 500):
        if minutes_per_response <= 0 or window_minutes <= 0:
            raise ValueError("minutes_per_response and window_minutes must be > 0")
        self.minutes_per_response = minutes_per_response
        self.window_minutes = window_minutes
        self.target_wait_probability = target_wait_probability
        self.target_wait_minutes = target_wait_minutes
        self.max_clinicians = max_clinicians

    @staticmethod
    def _erlang_b(c: int, a: float) -> float:
        """Numerically stable Erlang-B via the standard recursion. a = offered load."""
        b = 1.0
        for k in range(1, c + 1):
            b = (a * b) / (k + a * b)
        return b

    @classmethod
    def _erlang_c(cls, c: int, a: float) -> float:
        """Probability an arrival must wait (Erlang-C), derived from Erlang-B."""
        if c <= a:
            return 1.0  # unstable: demand >= servers
        b = cls._erlang_b(c, a)
        denom = c - a * (1.0 - b)
        if denom <= 0:
            return 1.0
        return (c * b) / denom

    def forecast(self, predicted_high_risk: float,
                 current_clinicians: Optional[int] = None) -> CapacityForecast:
        notes: List[str] = []

        if predicted_high_risk is None or predicted_high_risk <= 0:
            # Abstain: no demand → no staffing pressure.
            return CapacityForecast(
                predicted_high_risk=0.0, offered_load_erlangs=0.0, required_clinicians=0,
                current_clinicians=current_clinicians,
                headroom=None if current_clinicians is None else current_clinicians,
                expected_wait_minutes=0.0, wait_probability=0.0, alert=CapacityAlert.SAFE,
                notes=["No predicted high-risk volume; no staffing pressure."],
            )

        offered_load = predicted_high_risk * self.minutes_per_response / self.window_minutes

        required = max(1, math.ceil(offered_load))
        wait_prob = 1.0
        wait_minutes = float("inf")
        while required <= self.max_clinicians:
            wait_prob = self._erlang_c(required, offered_load)
            wait_minutes = (wait_prob * self.minutes_per_response / (required - offered_load)
                            if required > offered_load else float("inf"))
            if wait_prob <= self.target_wait_probability and wait_minutes <= self.target_wait_minutes:
                break
            required += 1
        else:
            notes.append(f"Hit max_clinicians={self.max_clinicians}; targets not met.")

        headroom = None if current_clinicians is None else current_clinicians - required
        if current_clinicians is None:
            alert = CapacityAlert.SAFE
            notes.append("Current staffing unknown; required count is advisory only.")
        elif headroom is not None and headroom >= 0:
            alert = CapacityAlert.SAFE
        elif headroom is not None and headroom >= -1:
            alert = CapacityAlert.HIGH
        else:
            alert = CapacityAlert.CRITICAL

        return CapacityForecast(
            predicted_high_risk=float(predicted_high_risk),
            offered_load_erlangs=round(offered_load, 3),
            required_clinicians=required,
            current_clinicians=current_clinicians,
            headroom=headroom,
            expected_wait_minutes=round(wait_minutes, 2) if math.isfinite(wait_minutes) else float("inf"),
            wait_probability=round(wait_prob, 4),
            alert=alert,
            notes=notes,
        )


# ============================================================================
# 2. RESERVE MODULATOR (safety-constrained escalation sensitivity)
# ============================================================================
# RESERVE MODULATOR — MOVED OUT
# ============================================================================
# The capacity-aware escalation control component (ReserveModulator) used to live
# here. It was reclassified as a CONTROL component and moved to `reserve_control.py`
# (PERCEIVE-adjacent), because this module is strictly observational telemetry and a
# telemetry layer must never influence control flow. Import it from reserve_control:
#     from reserve_control import ReserveModulator, ModulationAction, ModulationDecision


# ============================================================================
# 3. ACCURACY MONITOR (prediction vs. outcome, with drift detection)
# ============================================================================

@dataclass(frozen=True)
class AccuracyReport:
    status: str                       # "ok" or "insufficient_data"
    n: int
    sensitivity: Optional[float] = None
    specificity: Optional[float] = None
    ppv: Optional[float] = None
    npv: Optional[float] = None
    f1: Optional[float] = None
    tp: int = 0
    fp: int = 0
    tn: int = 0
    fn: int = 0
    drift_detected: bool = False
    notes: List[str] = field(default_factory=list)


class AccuracyMonitor:
    """Records (predicted_escalate, actual_deterioration) pairs and reports metrics.

    Abstains (status="insufficient_data") until at least one positive AND one negative
    outcome have been observed — metrics over a single class are meaningless. Drift is
    flagged when recent-window sensitivity drops materially below the overall figure.
    """

    def __init__(self, min_samples: int = 10, drift_window: int = 50,
                 drift_drop_threshold: float = 0.15):
        self.min_samples = min_samples
        self.drift_window = drift_window
        self.drift_drop_threshold = drift_drop_threshold
        self._records: List[Tuple[bool, bool]] = []  # (predicted_escalate, actual_deterioration)

    def record(self, predicted_escalate: bool, actual_deterioration: bool) -> None:
        self._records.append((bool(predicted_escalate), bool(actual_deterioration)))

    @staticmethod
    def _confusion(records: List[Tuple[bool, bool]]) -> Tuple[int, int, int, int]:
        tp = sum(1 for p, a in records if p and a)
        fp = sum(1 for p, a in records if p and not a)
        tn = sum(1 for p, a in records if not p and not a)
        fn = sum(1 for p, a in records if not p and a)
        return tp, fp, tn, fn

    @staticmethod
    def _sensitivity(tp: int, fn: int) -> Optional[float]:
        return tp / (tp + fn) if (tp + fn) > 0 else None

    def report(self) -> AccuracyReport:
        n = len(self._records)
        positives = sum(1 for _, a in self._records if a)
        negatives = n - positives

        if n < self.min_samples or positives == 0 or negatives == 0:
            return AccuracyReport(
                status="insufficient_data", n=n,
                notes=[f"Need >= {self.min_samples} samples with both outcome classes "
                       f"(have {n}: {positives} positive, {negatives} negative)."],
            )

        tp, fp, tn, fn = self._confusion(self._records)
        sensitivity = self._sensitivity(tp, fn)
        specificity = tn / (tn + fp) if (tn + fp) > 0 else None
        ppv = tp / (tp + fp) if (tp + fp) > 0 else None
        npv = tn / (tn + fn) if (tn + fn) > 0 else None
        f1 = (2 * ppv * sensitivity / (ppv + sensitivity)
              if ppv and sensitivity and (ppv + sensitivity) > 0 else None)

        # Drift: compare recent-window sensitivity against overall sensitivity.
        drift = False
        notes: List[str] = []
        if n >= self.drift_window:
            recent = self._records[-self.drift_window:]
            r_tp, _, _, r_fn = self._confusion(recent)
            recent_sens = self._sensitivity(r_tp, r_fn)
            if (recent_sens is not None and sensitivity is not None
                    and (sensitivity - recent_sens) >= self.drift_drop_threshold):
                drift = True
                notes.append(f"Sensitivity drift: recent {recent_sens:.2f} vs overall "
                             f"{sensitivity:.2f} (drop >= {self.drift_drop_threshold}).")

        return AccuracyReport(
            status="ok", n=n,
            sensitivity=round(sensitivity, 4) if sensitivity is not None else None,
            specificity=round(specificity, 4) if specificity is not None else None,
            ppv=round(ppv, 4) if ppv is not None else None,
            npv=round(npv, 4) if npv is not None else None,
            f1=round(f1, 4) if f1 is not None else None,
            tp=tp, fp=fp, tn=tn, fn=fn, drift_detected=drift, notes=notes,
        )


if __name__ == "__main__":
    fc = ErlangCapacityForecaster()
    print("Forecast (8 high-risk, 3 clinicians):", fc.forecast(8, current_clinicians=3))
    am = AccuracyMonitor(min_samples=4)
    for p, a in [(True, True), (True, False), (False, False), (True, True)]:
        am.record(p, a)
    print("Accuracy:", am.report())
