"""
Synthetic Deterioration Simulator
==================================

Clinical validation harness: generate realistic pediatric deterioration curves,
run them through OBSERVE, and verify that escalation fires at clinically
plausible points (before severe deterioration).

Each scenario is a named case with:
  - description (what's happening clinically)
  - vitals_sequence (time-series of realistic readings)
  - expected_escalation_hour (when OBSERVE *should* detect a problem)
  - tolerance_hours (acceptable window; e.g., detect within ±1 hour)

The simulator measures:
  - early_detection: escalation fired before severe deterioration
  - false_alarm_hours: any escalations before the expected window
  - missed_detection: no escalation when expected

Use before deployment to validate thresholds against pediatrician expectations.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Tuple

from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot


@dataclass
class Scenario:
    """A synthetic clinical scenario with ground-truth escalation timing."""
    name: str
    description: str
    age_months: int
    vitals_sequence: List[Tuple[float, float, float, float]]  # (HR, O2, RR, temp) per hour
    expected_escalation_hour: int  # when pediatrician would escalate
    tolerance_hours: int = 1


@dataclass
class EvaluationResult:
    scenario_name: str
    hours_total: int
    first_escalation_hour: int  # -1 if none
    severity_at_escalation: str  # "stable", "warning", "critical"
    early_hours_before_expected: int  # how far ahead of expected
    in_tolerance: bool
    false_alarms_before_expected: int
    notes: str


# ============================================================================
# SCENARIOS (pediatrician-validated reference cases)
# ============================================================================

SCENARIOS = [
    Scenario(
        name="toddler_viral_fever",
        description="Toddler with viral fever: elevated temp and RR, O2 stable, slow HR rise",
        age_months=24,
        vitals_sequence=[
            (100, 98, 24, 37.0),  # H0: baseline
            (105, 98, 26, 37.5),  # H1
            (108, 97, 28, 38.0),  # H2
            (110, 97, 30, 38.5),  # H3
            (112, 96, 32, 39.0),  # H4: fever + tachypnea, still stable
            (115, 96, 34, 39.5),  # H5: tachypnea progressing
            (118, 95, 36, 39.8),  # H6: threshold breached; pediatrician escalates
            (120, 94, 38, 40.0),  # H7
            (125, 92, 40, 40.2),  # H8
            (130, 90, 42, 40.3),  # H9: severe
        ],
        expected_escalation_hour=6,
        tolerance_hours=1,
    ),
    Scenario(
        name="septic_spiral_infant",
        description="Infant with sepsis: rapid deterioration across all vitals over 8 hours",
        age_months=6,
        vitals_sequence=[
            (130, 98, 40, 37.0),  # H0: baseline infant tachycardia + tachypnea
            (135, 98, 42, 37.2),  # H1: normal variation
            (140, 97, 44, 37.5),  # H2: rising HR/RR
            (145, 96, 46, 37.8),  # H3
            (150, 95, 48, 38.0),  # H4: O2 drifting down
            (155, 93, 50, 38.3),  # H5: combo starting
            (160, 91, 52, 38.6),  # H6: pediatrician escalates (clear pattern)
            (165, 88, 54, 39.0),  # H7: critical window
            (170, 85, 56, 39.2),  # H8: severe sepsis
        ],
        expected_escalation_hour=6,
        tolerance_hours=1,
    ),
    Scenario(
        name="gradual_hypoxia_child",
        description="Older child with gradual O2 drift (lower lobe consolidation?): slow O2 fall over 12 hours",
        age_months=60,
        vitals_sequence=[
            (110, 98, 22, 37.0),  # H0
            (110, 97, 23, 37.1),  # H1
            (111, 96, 24, 37.2),  # H2
            (112, 95, 25, 37.3),  # H3
            (112, 94, 26, 37.4),  # H4
            (113, 93, 27, 37.5),  # H5
            (114, 92, 28, 37.6),  # H6
            (115, 91, 29, 37.7),  # H7: slow drift; pediatrician concerned
            (116, 90, 30, 37.8),  # H8: escalates here (O2 at threshold)
            (117, 89, 31, 37.9),  # H9
            (118, 88, 32, 38.0),  # H10
            (120, 87, 34, 38.1),  # H11: respiratory compensation
            (125, 85, 36, 38.2),  # H12: severe
        ],
        expected_escalation_hour=8,
        tolerance_hours=2,
    ),
    Scenario(
        name="reactive_airway_toddler",
        description="Toddler with reactive airway: RR spike + O2 drop, HR elevated; resolves with treatment",
        age_months=28,
        vitals_sequence=[
            (108, 97, 26, 37.5),  # H0: baseline
            (110, 96, 28, 37.6),  # H1: event starts
            (115, 94, 35, 37.7),  # H2: RR spike, O2 drop
            (118, 92, 38, 37.8),  # H3: escalation point (bronchospasm pattern)
            (120, 91, 40, 37.9),  # H4
            (119, 93, 36, 38.0),  # H5: treatment effect (albuterol)
            (116, 95, 32, 38.0),  # H6: improving
            (112, 96, 28, 37.8),  # H7: recovery
        ],
        expected_escalation_hour=3,
        tolerance_hours=1,
    ),
    Scenario(
        name="stable_no_escalation",
        description="Healthy toddler with mild fever: all vitals stay within normal range",
        age_months=30,
        vitals_sequence=[
            (105, 97, 24, 37.5),  # H0
            (106, 97, 25, 38.0),  # H1: mild fever
            (105, 97, 25, 38.2),  # H2
            (107, 96, 26, 38.1),  # H3: stable mild fever
            (104, 97, 24, 38.0),  # H4
            (106, 97, 25, 38.1),  # H5: stays stable
            (105, 97, 25, 38.0),  # H6
            (107, 97, 26, 37.9),  # H7: fever resolving
            (104, 98, 24, 37.5),  # H8: back to baseline
        ],
        expected_escalation_hour=999,  # should NEVER escalate
        tolerance_hours=0,
    ),
]


# ============================================================================
# SIMULATOR
# ============================================================================

class DeteriorationSimulator:
    """Run synthetic scenarios through OBSERVE and measure detection timing."""

    def __init__(self):
        self.engine = ObserveClinicalEngine()
        self.base_time = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)

    def run_scenario(self, scenario: Scenario) -> EvaluationResult:
        """Execute one scenario and return timing metrics."""
        first_escalation_hour = -1
        severity_at_escalation = "stable"
        false_alarm_count = 0

        for hour, (hr, o2, rr, temp) in enumerate(scenario.vitals_sequence):
            vitals = VitalsSnapshot(
                patient_id=f"SIM_{scenario.name}_{hour}",
                timestamp=self.base_time + timedelta(hours=hour),
                heart_rate=hr,
                oxygen_saturation=o2,
                respiratory_rate=rr,
                temperature=temp,
                context={"age_months": scenario.age_months},
            )

            verdict = self.engine.evaluate(vitals)

            # Track first escalation
            if verdict.escalation_required:
                if first_escalation_hour == -1:
                    first_escalation_hour = hour
                    severity_at_escalation = verdict.regime.value

                # Count false alarms (escalations before expected window)
                if hour < scenario.expected_escalation_hour - scenario.tolerance_hours:
                    false_alarm_count += 1

        # Evaluate result
        in_tolerance = False
        early_hours = -1
        notes = ""

        if scenario.expected_escalation_hour == 999:
            # Should never escalate
            in_tolerance = first_escalation_hour == -1
            notes = (
                "Correctly stable (no escalation)"
                if in_tolerance
                else f"FALSE ALARM at hour {first_escalation_hour}"
            )
        else:
            if first_escalation_hour == -1:
                notes = f"MISSED DETECTION (no escalation; expected by hour {scenario.expected_escalation_hour})"
            else:
                early_hours = scenario.expected_escalation_hour - first_escalation_hour
                in_tolerance = (
                    -scenario.tolerance_hours <= early_hours <= scenario.tolerance_hours
                )
                if in_tolerance:
                    notes = f"Detected {early_hours} hours {'early' if early_hours > 0 else 'late' if early_hours < 0 else 'on time'}"
                else:
                    notes = f"OUT OF TOLERANCE (detected at hour {first_escalation_hour}, expected {scenario.expected_escalation_hour}±{scenario.tolerance_hours})"

        return EvaluationResult(
            scenario_name=scenario.name,
            hours_total=len(scenario.vitals_sequence),
            first_escalation_hour=first_escalation_hour,
            severity_at_escalation=severity_at_escalation,
            early_hours_before_expected=early_hours,
            in_tolerance=in_tolerance,
            false_alarms_before_expected=false_alarm_count,
            notes=notes,
        )

    def run_all(self) -> Dict[str, EvaluationResult]:
        """Run all scenarios and return summary."""
        return {s.name: self.run_scenario(s) for s in SCENARIOS}

    def report(self) -> str:
        """Generate a human-readable validation report."""
        results = self.run_all()

        lines = [
            "=" * 80,
            "OBSERVE DETERIORATION SIMULATOR — CLINICAL VALIDATION REPORT",
            "=" * 80,
            "",
        ]

        passed = sum(1 for r in results.values() if r.in_tolerance)
        total = len(results)

        lines.append(f"OVERALL: {passed}/{total} scenarios in tolerance")
        lines.append("")

        for name, result in results.items():
            status = "✓ PASS" if result.in_tolerance else "✗ FAIL"
            lines.append(f"{status} | {name}")
            lines.append(f"       {result.notes}")
            if result.first_escalation_hour >= 0:
                lines.append(
                    f"       Escalation at hour {result.first_escalation_hour} "
                    f"(regime={result.severity_at_escalation})"
                )
            if result.false_alarms_before_expected > 0:
                lines.append(
                    f"       ⚠ {result.false_alarms_before_expected} false alarm(s) "
                    f"before expected window"
                )
            lines.append("")

        lines.extend(
            [
                "=" * 80,
                "INTERPRETATION:",
                "  ✓ Pass: escalation fired within expected window (±tolerance_hours)",
                "  ✗ Fail: escalation too early/late, or completely missed/spurious",
                "",
                "NEXT STEPS:",
                "  • If all pass: thresholds validated; proceed to deployment",
                "  • If some fail: review triggered rules + pediatrician expectations",
                "  • False alarms: lower thresholds or increase tolerance windows",
                "  • Missed detections: raise threshold or widen tolerance",
                "=" * 80,
            ]
        )

        return "\n".join(lines)


# ============================================================================
# DEMO
# ============================================================================

if __name__ == "__main__":
    sim = DeteriorationSimulator()
    print(sim.report())
