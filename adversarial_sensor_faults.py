"""
Adversarial Sensor-Fault Test Suite
====================================

Robustness harness: stress-test OBSERVE with pathological sensor inputs.
Verifies the system fails *safely* (escalates or logs, never silent nonsense).

Fault categories:
  1. Non-finite: NaN, Inf, out-of-physical-range (already handled → should escalate)
  2. Stuck sensor: same reading repeatedly (detects via behavioral/drift)
  3. Sensor drift: slowly biasing value (Kalman should catch; trajectory detects)
  4. Transient spike: single outlier (Kalman innovation, Bayesian confidence)
  5. Coordinated corruption: multiple sensors lie consistently (tests fusion robustness)
  6. Timing anomaly: gaps or reordered readings (simulator doesn't handle; noted as limitation)

Each fault returns:
  - did_escalate: whether OBSERVE raised an alert
  - regime: the decision regime (stable/caution/warning/critical)
  - triggered_rules: which rules fired (shows diagnosis)
  - safety: "safe" (escalated/logged), "risky" (silent stable), "unknown" (needs review)

Use before production to verify the system degrades gracefully.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple

from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot


@dataclass
class FaultCase:
    """An adversarial fault scenario."""
    name: str
    description: str
    age_months: int
    vitals_sequence: List[Tuple[float, float, float, float]]  # (HR, O2, RR, temp)
    expected_behavior: str  # "escalate" or "stay_stable_with_warning"


@dataclass
class FaultEvaluationResult:
    case_name: str
    did_escalate: bool
    regime: str
    triggered_rules: List[str]
    safety_verdict: str  # "safe", "risky", "unknown"
    notes: str


# ============================================================================
# FAULT CASES
# ============================================================================

FAULT_CASES = [
    # Non-finite (already tested in unit tests, but included for completeness)
    FaultCase(
        name="nan_oxygen",
        description="Sensor returns NaN for O2 (e.g., disconnected probe)",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),  # normal
            (105, float("nan"), 26, 37.5),  # NaN
        ],
        expected_behavior="escalate",
    ),
    FaultCase(
        name="inf_heart_rate",
        description="HR sensor spikes to infinity (electrical spike)",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),
            (float("inf"), 97, 26, 37.5),
        ],
        expected_behavior="escalate",
    ),
    FaultCase(
        name="out_of_range_temp",
        description="Temperature reads 50°C (sensor malfunction)",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),
            (102, 97, 26, 50.0),
        ],
        expected_behavior="escalate",
    ),
    # Stuck sensor
    FaultCase(
        name="stuck_o2_sensor",
        description="O2 sensor stuck at 98 for 6 readings despite patient deterioration",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),  # normal
            (110, 98, 28, 37.5),  # patient deteriorating
            (120, 98, 32, 38.0),  # stuck sensor blinds us
            (130, 98, 36, 38.5),
            (140, 98, 40, 39.0),
            (150, 98, 44, 39.5),  # massive tachycardia + tachypnea, but O2 "stable"
        ],
        expected_behavior="escalate",  # other vitals + drift/behavioral catch it
    ),
    FaultCase(
        name="stuck_hr_baseline",
        description="HR sensor stuck at 100 while patient's actual HR varies (E.g. intermittent connection)",
        age_months=24,
        vitals_sequence=[
            (100, 97, 24, 37.0),
            (100, 96, 26, 37.5),  # stuck
            (100, 95, 28, 37.8),
            (100, 94, 30, 38.0),  # O2 clearly dropping, HR "flat"
            (100, 93, 32, 38.2),
        ],
        expected_behavior="escalate",  # O2 drift catches it
    ),
    # Sensor drift
    FaultCase(
        name="slow_o2_drift_downward",
        description="O2 sensor calibration drift: reads 1% too low every hour (causes false alarm risk)",
        age_months=24,
        vitals_sequence=[
            (100, 98.0, 24, 37.0),  # true O2 = 99
            (102, 96.8, 25, 37.2),  # true O2 = 97.8 (drift applied)
            (104, 95.6, 26, 37.4),
            (106, 94.4, 27, 37.6),
            (108, 93.2, 28, 37.8),
            (110, 92.0, 29, 38.0),
        ],
        expected_behavior="escalate",  # drift + behavioral detection
    ),
    FaultCase(
        name="slow_hr_drift_upward",
        description="HR sensor reads consistently 5 bpm high (systematic bias, not acute deterioration)",
        age_months=24,
        vitals_sequence=[
            (100, 97, 24, 37.0),  # true HR ~95
            (105, 97, 24, 37.0),  # true HR ~100
            (110, 97, 24, 37.0),  # true HR ~105
            (115, 97, 24, 37.0),  # true HR ~110
            (120, 97, 24, 37.0),  # true HR ~115
        ],
        expected_behavior="stay_stable_with_warning",  # elevated but no acute change
    ),
    # Transient spike
    FaultCase(
        name="single_o2_spike_down",
        description="O2 sensor glitch: single reading drops to 80, then recovers",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),
            (102, 96, 26, 37.5),  # normal variation
            (104, 80, 28, 37.6),  # spike (artifact)
            (105, 97, 27, 37.7),  # recovers
            (103, 98, 25, 37.6),
        ],
        expected_behavior="stay_stable_with_warning",  # single spike shouldn't escalate
    ),
    FaultCase(
        name="repeated_spikes_o2",
        description="Intermittent sensor contact: O2 spikes between 80 and 98 repeatedly",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),
            (102, 85, 26, 37.5),
            (104, 97, 28, 37.6),
            (106, 82, 30, 37.7),
            (108, 96, 32, 37.8),
            (110, 80, 34, 37.9),
        ],
        expected_behavior="escalate",  # accumulated surprise + adversarial detection
    ),
    # Coordinated corruption
    FaultCase(
        name="calibration_corruption_all_sensors",
        description="Calibration error: all sensors biased high (HR +10, RR +3, O2 -2, temp +0.5)",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),  # true: 90, 100, 21, 36.5
            (110, 96, 27, 37.5),
            (120, 94, 30, 38.0),
            (130, 92, 33, 38.5),
        ],
        expected_behavior="stay_stable_with_warning",  # consistent bias doesn't look acute
    ),
    FaultCase(
        name="coordinated_lie_sepsis_masking",
        description="Attacker/fault hides sepsis: HR low (actually ↑30), O2 high (actually ↓8), RR low (actually ↑15)",
        age_months=12,
        vitals_sequence=[
            (130, 98, 40, 37.0),  # true: 160, 90, 55, 38.5
            (130, 98, 40, 37.0),  # repeated lie
            (130, 98, 40, 37.0),
        ],
        expected_behavior="escalate",  # stuck sensor + no variation should trigger adversarial
    ),
]


# ============================================================================
# TEST SUITE
# ============================================================================

class AdversarialSensorTestSuite:
    """Run fault cases and measure safety of degradation."""

    def __init__(self):
        self.engine = ObserveClinicalEngine()
        self.base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def evaluate_fault_case(self, case: FaultCase) -> FaultEvaluationResult:
        """Run one fault case and classify the safety of the outcome."""
        final_regime = None
        escalated = False
        all_triggered = []

        for hour, (hr, o2, rr, temp) in enumerate(case.vitals_sequence):
            vitals = VitalsSnapshot(
                # ONE PATIENT, ONE IDENTITY, FOR THE WHOLE TIMELINE.
                # This used to append the hour, which handed the engine a
                # brand-new patient on every reading. Everything stateful
                # then became untestable by construction: the Kalman
                # trajectory tracker sat in warm-up on 100% of readings
                # (measured: 0 of 10, 0 of 13, 0 of 8 across the three
                # deterioration scenarios), and per-patient dwell and
                # hysteresis never engaged. A trend detector handed a fresh
                # patient each hour cannot detect a trend, and a stuck-sensor
                # detector cannot notice that readings are not changing when
                # no two readings belong to the same patient.
                patient_id=f"FAULT_{case.name}",
                timestamp=self.base_time + timedelta(hours=hour),
                heart_rate=hr,
                oxygen_saturation=o2,
                respiratory_rate=rr,
                temperature=temp,
                context={"age_months": case.age_months},
            )

            verdict = self.engine.evaluate(vitals)
            final_regime = verdict.regime
            if verdict.escalation_required:
                escalated = True
            all_triggered.extend(verdict.triggered_rules)

        # Classify safety
        safety = "unknown"
        notes = ""

        if case.expected_behavior == "escalate":
            if escalated:
                safety = "safe"
                notes = f"Correctly escalated (regime={final_regime.value})"
            else:
                safety = "risky"
                notes = f"FAILED TO ESCALATE on fault; stayed {final_regime.value}"

        elif case.expected_behavior == "stay_stable_with_warning":
            if not escalated:
                safety = "safe"
                notes = f"Correctly stable (regime={final_regime.value})"
            else:
                safety = "risky"  # conservative: premature escalation is safer than miss, but still a false alarm
                notes = f"False alarm escalation (regime={final_regime.value})"

        return FaultEvaluationResult(
            case_name=case.name,
            did_escalate=escalated,
            regime=final_regime.value if final_regime else "unknown",
            triggered_rules=all_triggered,
            safety_verdict=safety,
            notes=notes,
        )

    def run_all(self) -> Dict[str, FaultEvaluationResult]:
        """Run all fault cases."""
        return {c.name: self.evaluate_fault_case(c) for c in FAULT_CASES}

    def report(self) -> str:
        """Generate a robustness report."""
        results = self.run_all()

        lines = [
            "=" * 80,
            "ADVERSARIAL SENSOR-FAULT TEST SUITE — ROBUSTNESS REPORT",
            "=" * 80,
            "",
        ]

        safe = sum(1 for r in results.values() if r.safety_verdict == "safe")
        risky = sum(1 for r in results.values() if r.safety_verdict == "risky")
        unknown = sum(1 for r in results.values() if r.safety_verdict == "unknown")
        total = len(results)

        lines.append(
            f"OVERALL: {safe}/{total} safe, {risky}/{total} risky, {unknown}/{total} unknown"
        )
        lines.append("")

        for name, result in results.items():
            icon = (
                "✓" if result.safety_verdict == "safe"
                else "✗" if result.safety_verdict == "risky"
                else "?"
            )
            lines.append(
                f"{icon} {result.safety_verdict.upper():8} | {name}"
            )
            lines.append(f"    {result.notes}")
            if result.triggered_rules:
                lines.append(f"    Rules: {', '.join(result.triggered_rules[:3])}")
            lines.append("")

        lines.extend(
            [
                "=" * 80,
                "INTERPRETATION:",
                "  ✓ Safe: system escalated on fault or correctly ignored benign deviation",
                "  ✗ Risky: system failed to escalate on fault, or falsely alarmed",
                "  ? Unknown: behavior not clearly mapped to expected outcome",
                "",
                "RISK ASSESSMENT:",
                f"  • {risky} risky cases: review rules + Kalman sensitivity",
                f"  • {safe} safe cases: sensor robustness OK",
                "",
                "DEPLOYMENT READINESS:",
                "  • If risky ≤ 1: acceptable for shadow-mode (low false-negative risk)",
                "  • If risky ≥ 2: address sensor handling before production",
                "=" * 80,
            ]
        )

        return "\n".join(lines)


# ============================================================================
# DEMO
# ============================================================================

if __name__ == "__main__":
    suite = AdversarialSensorTestSuite()
    print(suite.report())
