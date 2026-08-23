"""
Reserve-Modulated Escalation Control (PERCEIVE-adjacent)
========================================================

This is a CONTROL component, deliberately kept SEPARATE from the strictly
observational telemetry in `capacity_planning.py`.

Why the split: a telemetry layer must never influence control flow. The capacity
forecaster and accuracy monitor honor that — they have zero decision impact. The
`ReserveModulator`, by contrast, *can* change an escalation decision (it adjusts
escalation sensitivity from available clinical reserve). Mixing it into the
observability module blurs that boundary, so it lives here, next to the PERCEIVE
governance kernel that owns control decisions.

Enforcement is structural, not a runtime "deadman switch":
  * the observability artifacts (`CapacityForecast`, `AccuracyReport`) are immutable,
    so a telemetry output cannot be fed back as a mutable control signal;
  * any decision this modulator changes is routed through PERCEIVE's gates + audit
    chain by the integration layer (`ClinicalGovernanceSystem.process_vitals`), so
    its influence is governed and logged exactly like a baseline escalation.

Safety posture (unchanged by the move):
  * opt-in — no effect unless a modulator is attached and a utilization is supplied;
  * critical regimes / confirmed syndromes are ALWAYS exempt;
  * by default it can only LOWER the escalation threshold (escalate MORE when reserve
    is high — the safe direction); it never suppresses below baseline unless
    `allow_load_shedding=True`, which needs clinical-governance sign-off.

Python 3.8+, stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModulationAction(Enum):
    ESCALATE = "escalate"                       # base decision said escalate — honored
    ESCALATE_PROACTIVE = "escalate_proactive"   # added by high reserve (safe direction)
    MONITOR = "monitor"                         # no escalation
    DEFER = "defer"                             # load-shed (only if explicitly enabled)


@dataclass
class ModulationDecision:
    action: ModulationAction
    base_should_escalate: bool
    effective_threshold: float
    utilization: float
    exempt: bool                # True if critical/syndrome (never modulated down)
    note: str


class ReserveModulator:
    """Capacity-aware escalation sensitivity.

    `utilization` in [0,1] is the fraction of clinical capacity currently in use
    (1.0 = saturated). Behavior:
      * exempt (critical regime OR confirmed syndrome) -> always ESCALATE;
      * if the base pipeline already escalates -> ESCALATE (honored);
      * else, when reserve is high (low utilization), the escalation threshold is
        LOWERED so borderline risk escalates proactively (ESCALATE_PROACTIVE) —
        this is the only modulation enabled by default, and it can only ADD alerts;
      * raising the threshold to SUPPRESS (DEFER) a non-exempt warning escalation is
        gated behind `allow_load_shedding` (default OFF) and needs governance sign-off.

    Default posture therefore never suppresses an escalation that the baseline would
    have raised; it only makes the system more sensitive when capacity is available.
    """

    def __init__(self, base_threshold: float = 0.5, sensitivity: float = 0.2,
                 allow_load_shedding: bool = False, load_shed_utilization: float = 0.9):
        self.base_threshold = base_threshold
        self.sensitivity = sensitivity
        self.allow_load_shedding = allow_load_shedding
        self.load_shed_utilization = load_shed_utilization

    def effective_threshold(self, utilization: float) -> float:
        u = max(0.0, min(1.0, utilization))
        # High reserve (u low) -> threshold below base (more sensitive).
        # High utilization (u high) -> threshold at/above base ONLY if load-shedding on.
        delta = self.sensitivity * (0.5 - u) * 2.0  # u=0 -> +sensitivity (lower thr); u=1 -> -sensitivity
        thr = self.base_threshold - delta
        if not self.allow_load_shedding:
            thr = min(thr, self.base_threshold)  # never raise above base when shedding off
        return max(0.0, min(1.0, thr))

    def modulate(self, risk_score: float, base_should_escalate: bool,
                 is_critical_or_syndrome: bool, utilization: float) -> ModulationDecision:
        thr = self.effective_threshold(utilization)
        u = max(0.0, min(1.0, utilization))

        if is_critical_or_syndrome:
            return ModulationDecision(
                ModulationAction.ESCALATE, base_should_escalate, thr, u, True,
                "Critical/syndrome: exempt from capacity modulation; always escalate.")

        if base_should_escalate:
            # Optional load-shedding may DEFER a non-exempt escalation under saturation.
            if self.allow_load_shedding and u >= self.load_shed_utilization:
                return ModulationDecision(
                    ModulationAction.DEFER, base_should_escalate, thr, u, False,
                    f"Load-shedding ON and utilization {u:.2f} >= {self.load_shed_utilization}: "
                    "non-critical escalation deferred (queued). REQUIRES governance sign-off.")
            return ModulationDecision(
                ModulationAction.ESCALATE, base_should_escalate, thr, u, False,
                "Baseline escalation honored.")

        # Base did not escalate: high reserve can add a proactive escalation.
        if risk_score >= thr:
            return ModulationDecision(
                ModulationAction.ESCALATE_PROACTIVE, base_should_escalate, thr, u, False,
                f"Reserve available (utilization {u:.2f}); risk {risk_score:.2f} >= "
                f"lowered threshold {thr:.2f}: proactive escalation.")

        return ModulationDecision(
            ModulationAction.MONITOR, base_should_escalate, thr, u, False,
            f"Risk {risk_score:.2f} below effective threshold {thr:.2f}; continue monitoring.")


if __name__ == "__main__":
    rm = ReserveModulator()
    print("low risk, lots of reserve:",
          rm.modulate(0.45, base_should_escalate=False, is_critical_or_syndrome=False, utilization=0.1))
    print("critical, saturated (exempt):",
          rm.modulate(0.1, base_should_escalate=False, is_critical_or_syndrome=True, utilization=1.0))
