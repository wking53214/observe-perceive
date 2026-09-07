"""
Kalman Trajectory Engine (OPTIONAL OBSERVE adapter)
===================================================

State-space upgrade to the linear trajectory adapter. A constant-velocity Kalman
filter per vital separates signal (deterioration) from noise (sensor jitter) and
exposes two interpretable signals:

  * velocity   — estimated rate of change (smoothed trend)
  * innovation — measurement minus prediction (how "surprising" a reading is)

Adapted from a sepsis-domain prototype to this system's `VitalsSnapshot` contract
(heart_rate, oxygen_saturation, respiratory_rate, temperature). Two corrections vs.
the prototype, made so it can be fused safely:

  1. PROPER 2-STATE UPDATE. The prototype applied an ad-hoc scalar velocity nudge
     (`kalman_gain/(1+1e-6) * innovation`). This uses the correct constant-velocity
     update K = P·Hᵀ·S⁻¹ over the full 2×2 covariance, so the velocity estimate is
     actually driven by the position–velocity cross-term.
  2. DETERMINISTIC + PER-PATIENT. State is keyed per patient and `dt` is derived from
     successive `VitalsSnapshot.timestamp`s. Given an identical reading SEQUENCE for a
     patient, the output is identical (no randomness in the engine — the prototype's
     randomness lived only in its demo's synthetic data).

The engine ABSTAINS during warm-up (until it has a prior reading to predict from),
so a cold tracker never dilutes fusion with a confident "stable."

CLINICAL NOTE: the velocity thresholds below are PROVISIONAL engineering defaults and
require pediatrician sign-off. The innovation-surprise signal is unit-agnostic (a
z-score of measurement vs. prediction) and carries low clinical-judgment content.

stdlib only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple


# Per-channel Kalman tuning: (process noise position, process noise velocity,
# measurement noise). Generous noise = trust the trend less, the data more.
_CHANNEL_TUNING = {
    "heart_rate":        {"q_p": 0.5, "q_v": 0.05, "r": 2.0},
    "oxygen_saturation": {"q_p": 0.2, "q_v": 0.02, "r": 1.0},
    "respiratory_rate":  {"q_p": 0.4, "q_v": 0.04, "r": 1.5},
    "temperature":       {"q_p": 0.02, "q_v": 0.002, "r": 0.05},
}

# PROVISIONAL velocity thresholds (per hour) — pending pediatrician sign-off.
# (direction, magnitude, risk weight). direction -1 = falling is bad, +1 = rising is bad.
_VELOCITY_RULES = {
    "oxygen_saturation": (-1, 1.0, 0.20),
    "heart_rate":        (+1, 10.0, 0.15),
    "respiratory_rate":  (+1, 5.0, 0.15),
    "temperature":       (+1, 0.5, 0.10),
}

_WARMUP_UPDATES = 2          # suppress scoring until this many updates seen
_MAX_DT_HOURS = 24.0         # clamp implausibly large gaps (avoids variance blow-up)


@dataclass
class _KalmanChannel:
    """Constant-velocity Kalman state for one vital: x = [position, velocity]."""
    q_p: float
    q_v: float
    r: float
    p: float = 0.0
    v: float = 0.0
    # 2x2 covariance
    p00: float = 4.0
    p01: float = 0.0
    p10: float = 0.0
    p11: float = 1.0
    initialized: bool = False

    def step(self, z: float, dt: float) -> Tuple[float, float]:
        """Predict by dt, then update with measurement z. Returns (innovation, z_score)."""
        if not self.initialized:
            # Initialize position to first observation; leave velocity at 0 with
            # moderate uncertainty. Deterministic.
            self.p = z
            self.initialized = True
            return 0.0, 0.0

        # ---- Predict (time update) ----
        self.p = self.p + self.v * dt
        # P = F P Fᵀ + Q, with F = [[1, dt], [0, 1]]
        p00 = self.p00 + dt * (self.p10 + self.p01) + dt * dt * self.p11 + self.q_p
        p01 = self.p01 + dt * self.p11
        p10 = self.p10 + dt * self.p11
        p11 = self.p11 + self.q_v
        self.p00, self.p01, self.p10, self.p11 = p00, p01, p10, p11

        # ---- Update (measurement update), H = [1, 0], R = r ----
        innovation = z - self.p
        s = self.p00 + self.r                      # innovation variance
        if s <= 1e-12:
            return innovation, 0.0
        k0 = self.p00 / s                          # Kalman gain (position)
        k1 = self.p10 / s                          # Kalman gain (velocity) — cross-term driven
        self.p = self.p + k0 * innovation
        self.v = self.v + k1 * innovation
        # P = (I - K H) P
        new_p00 = (1.0 - k0) * self.p00
        new_p01 = (1.0 - k0) * self.p01
        new_p10 = self.p10 - k1 * self.p00
        new_p11 = self.p11 - k1 * self.p01
        self.p00, self.p01, self.p10, self.p11 = new_p00, new_p01, new_p10, new_p11

        z_score = abs(innovation) / math.sqrt(s)
        return innovation, z_score


class PatientKalmanTracker:
    """Per-patient Kalman trajectory tracker across the four VitalsSnapshot channels.

    Deterministic: identical (value, timestamp) sequences yield identical outputs.
    """

    def __init__(self) -> None:
        self._channels: Dict[str, _KalmanChannel] = {
            name: _KalmanChannel(**_CHANNEL_TUNING[name]) for name in _CHANNEL_TUNING
        }
        self._last_ts: Optional[datetime] = None
        self._n_updates = 0
        self._surprise_window: List[float] = []  # recent mean-innovation z-scores

    def update(self, heart_rate: float, oxygen_saturation: float,
               respiratory_rate: float, temperature: float,
               timestamp: datetime) -> Dict[str, object]:
        """Process one reading; return {score, confidence, triggered, abstained, details}."""
        # Derive dt (hours) from the previous reading; clamp implausible gaps.
        if self._last_ts is None:
            dt = 1.0
        else:
            dt = (timestamp - self._last_ts).total_seconds() / 3600.0
            if dt <= 0:
                dt = 1.0
            dt = min(dt, _MAX_DT_HOURS)
        self._last_ts = timestamp

        values = {
            "heart_rate": heart_rate,
            "oxygen_saturation": oxygen_saturation,
            "respiratory_rate": respiratory_rate,
            "temperature": temperature,
        }

        triggered: List[str] = []
        score = 0.0
        z_scores: List[float] = []
        details: Dict[str, float] = {}

        for name, ch in self._channels.items():
            innovation, z = ch.step(values[name], dt)
            z_scores.append(z)
            details[f"{name}_velocity"] = round(ch.v, 4)
            details[f"{name}_innovation"] = round(innovation, 4)

            # Warm-up: do not score until we have a real prediction history.
            if self._n_updates < _WARMUP_UPDATES:
                continue

            # Signal 1: sustained velocity (PROVISIONAL thresholds).
            if name in _VELOCITY_RULES:
                direction, mag, weight = _VELOCITY_RULES[name]
                if (direction < 0 and ch.v <= -mag) or (direction > 0 and ch.v >= mag):
                    triggered.append(f"KALMAN_VELOCITY: {name} {ch.v:+.2f}/h (provisional)")
                    score += weight

            # Signal 2: innovation surprise (unit-agnostic z-score).
            innovation_risk = z / (z + 3.0)
            if innovation_risk > 0.3:
                triggered.append(f"KALMAN_SURPRISE: {name} innovation {innovation:+.2f} (z={z:.2f})")
                score += 0.15

        # Signal 3: accumulated surprise across the recent window.
        mean_z = sum(z_scores) / len(z_scores) if z_scores else 0.0
        self._surprise_window.append(mean_z)
        if len(self._surprise_window) > 12:
            self._surprise_window = self._surprise_window[-12:]
        if self._n_updates >= _WARMUP_UPDATES and len(self._surprise_window) >= 6:
            recent_mean = sum(self._surprise_window[-6:]) / 6.0
            recent_risk = recent_mean / (recent_mean + 3.0)
            if recent_risk > 0.15:
                triggered.append(f"KALMAN_ACCUMULATED_SURPRISE: {recent_risk:.3f} over recent window")
                score += 0.20

        abstained = self._n_updates < _WARMUP_UPDATES
        self._n_updates += 1

        # Confidence grows out of warm-up as the filter settles.
        confidence = 0.2 if abstained else min(0.6 + 0.05 * (self._n_updates - _WARMUP_UPDATES), 0.85)

        return {
            "score": max(0.0, min(score, 1.0)),
            "confidence": confidence,
            "triggered": triggered or (["KALMAN: warming up"] if abstained else ["KALMAN: nominal trajectory"]),
            "abstained": abstained,
            "details": details,
        }


if __name__ == "__main__":
    from datetime import timezone, timedelta
    tr = PatientKalmanTracker()
    base = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    # Stable then a rising-HR / falling-O2 spiral
    seq = [(85, 98), (86, 98), (88, 97), (95, 96), (108, 94), (120, 92), (132, 90)]
    for i, (hr, o2) in enumerate(seq):
        r = tr.update(hr, o2, 30, 37.5, base + timedelta(hours=i))
        print(f"h{i}: score={r['score']:.2f} abstain={r['abstained']} "
              f"hr_v={r['details']['heart_rate_velocity']:+.2f} o2_v={r['details']['oxygen_saturation_velocity']:+.2f}")
