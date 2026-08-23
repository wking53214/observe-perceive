# OBSERVE + PERCEIVE — Consolidated Clinical Governance System

A pediatric physiological early-warning system (**OBSERVE**) wired to an
AI-governance kernel (**PERCEIVE**), consolidated from a 19-file modular layout
into a small set of self-contained files with no third-party dependencies
(Python 3.8+ stdlib only).

```
vitals
  → OBSERVE        7-engine fused risk assessment + per-patient escalation policy
  → escalation?    clinical-safety bypass for hard rules & dangerous syndromes
       → PERCEIVE  6-gate unanimous governance + optional multi-node consensus
  → ClinicalDecision  (links OBSERVE audit hash ⇄ PERCEIVE audit hash)
```

---

## The files

| # | File | Role |
|---|------|------|
| 1 | `observe_consolidated.py` | Clinical engine: 7 risk adapters, calibrated fusion, per-patient policy, immutable audit, async scheduler |
| 2 | `perceive_consolidated.py` | Governance kernel: 6 policy gates + 3 wired policy gates, unanimous consensus, DGK multi-node, immutable audit |
| 3 | `clinical_governance_system.py` | **Integration surface** — the single object a hospital wires against |
| 4 | `compliance_exporters.py` | HIPAA / FDA 510(k) / SOX / GDPR exporters |
| 5 | `capacity_planning.py` | **Optional, strictly observational** — Erlang-C capacity forecaster + accuracy monitor (zero decision impact; frozen artifacts) |
| 6 | `reserve_control.py` | **Optional control (PERCEIVE-adjacent)** — reserve-modulated escalation; safe-direction-only, routed through PERCEIVE + audit |
| 7 | `kalman_trajectory.py` | **Optional, opt-in** — Kalman (state-space) trajectory engine; enable via `ObserveClinicalEngine(enable_kalman=True)` |
| — | `test_*.py` | OBSERVE / PERCEIVE / integration / clinical-governance / compliance / capacity / reserve-control / kalman tests |

**249 tests, all passing.** Run them with: `python3 -m pytest`

---

## Quick start

```python
from datetime import datetime, timezone
from observe_consolidated import VitalsSnapshot
from clinical_governance_system import build_single_hospital_system

system = build_single_hospital_system()

decision = system.process_vitals(VitalsSnapshot(
    patient_id="P001",
    timestamp=datetime.now(timezone.utc),
    heart_rate=168, oxygen_saturation=83.0,
    respiratory_rate=46, temperature=39.5,
    context={"age_months": 12},
))

print(decision.regime)              # "critical"
print(decision.action)              # "escalate_approved"
print(decision.observe_audit_hash)  # links to clinical assessment
print(decision.perceive_audit_hash) # links to governance decision
```

Multi-hospital deployment (emergency overrides require cross-site quorum):

```python
from clinical_governance_system import build_multi_hospital_system
system = build_multi_hospital_system(["nch", "partner-a", "partner-b"])
```

---

## OBSERVE — the 7 risk engines

| Engine | What it assesses | Data needed |
|--------|------------------|-------------|
| `heuristic` | Age-adjusted PEWS thresholds (O2, HR, RR, temp) | Vitals only |
| `bayesian` | Age-banded z-score deviation, continuous likelihood | Vitals + age |
| `trajectory` | Per-minute momentum of vitals | Prior readings |
| `drift` | Baseline shift vs rolling history | History window |
| `behavioral` | Named syndromes (septic / respiratory / hypovolemic shock) | Vitals |
| `adversarial` | Sensor-fault detection (streaks, implausible rates) | Recent readings |
| `physiological_reserve` | 6-axis systems physiology (topology/capacity/resource/integrity/phase/instability) | Rich telemetry (gated) |

**The adapter contract (plug-and-play):** every engine returns a `RiskOutput`
with a `risk_score`, then pipes through the shared `regime_distribution()`
calibration. Engines swap; calibration is centralized. This is what lets you
drop in a new industry adapter without touching fusion.

### Key safety properties (each test-locked)

1. **Per-patient state isolation** — escalation cooldowns are keyed by `patient_id`.
   One patient's lock can never suppress another's critical alert.
2. **Clinical-safety bypass** — a `CRITICAL_O2` reading or a confirmed shock
   syndrome escalates *immediately*, skipping dwell/hysteresis confirmation.
3. **Abstention exclusion** — an engine with no data to assess is excluded from
   fusion entirely, so a chorus of "no data" can't dilute a real detection.
4. **Syndrome floor** — a confirmed dangerous pattern floors the fused risk at
   its detected severity; it cannot be averaged below it.
5. **Bounded memory** — per-patient state is LRU-capped (default 10k patients).

---

## PERCEIVE — the 6 governance gates

`boundary_gate` · `citadel` (intent) · `fortress` (content safety) ·
`invariant_validator` · `sentinel` (anomaly) · `micropatch` (emergency override)

- **Unanimous consensus:** every selected gate must approve.
- **Gate selection is deterministic** by request type.
- **DGK multi-node consensus** adds cross-site quorum (default 2/3) for
  `emergency_override` and critical rule changes in multi-hospital deployments.

---

## Audit & compliance

Both OBSERVE and PERCEIVE keep **independent SHA256-chained audit ledgers**.
Any post-hoc tampering breaks the chain and is caught by `verify_integrity()`.

`compliance_exporters.py` renders the ledgers into:
- **HIPAA** — de-identified event log (pseudonymized IDs, hour-coarsened timestamps)
- **FDA 510(k)** — validation report with determinism attestation + engine utilization
- **SOX** — governance decision log (who/what/when/result)
- **GDPR** — Article 30-style data-processing record

---

## Determinism

Every risk engine is a pure function of recorded telemetry + context. Given
identical recorded inputs, the system produces identical decision **outputs**
(risk, regime, escalation) and an identical reproducible **`decision_fingerprint`**
(a SHA256 of the wall-clock-free decision payload). No randomness in the decision
path — a prerequisite for FDA validation and forensic replay.

Note the two distinct hashes:

- **`decision_fingerprint`** — reproducible across runs/processes for identical
  inputs. Use it for decision replay verification.
- **`immutable_hash`** (the chained ledger hash) — additionally binds insertion
  timestamp and chain position for tamper-evidence, and is therefore *intentionally
  not* reproducible across runs. Use it for tamper detection (`verify_integrity()`).

---

## Hardening & operational controls

**Input validation.** Non-finite (NaN/Inf) or physically impossible vitals are
treated as a data-integrity fault, never scored as "stable." A fault routes to an
immediate WARNING escalation (so a human checks the patient/sensor) and is recorded
in the audit ledger. Severity is the safe default for an early-warning system; a
quieter sensor-fault channel is advisable in production.

**Context sanitization.** The `context` dict (previous readings, baselines,
age, history lists) is also an input boundary. A second red-team pass (see
`RED_TEAM_REPORT.md`) closed the gap where a non-numeric context value crashed
scoring and a physically impossible one silently skewed it. `sanitize_context()`
runs at the same boundary as vitals validation and drops any context value that
is non-numeric, non-finite, or outside its physical range, treating it as absent
(which the engines already handle) rather than trusting garbage.

**Wired governance (opt-in, advisory by default).** `EscalationPolicy`,
`DataExportPolicy`, and `RuleModificationPolicy` are wired as real PERCEIVE gates
(`escalation_rate_policy`, `data_export_policy`, `rule_modification_policy`). They
run in **advisory** mode by default — each evaluates and logs what it *would* decide
but approves, so baseline behavior is unchanged. Enable enforcement per policy via
`PolicyEnforcementConfig`:

```python
from perceive_consolidated import PolicyEnforcementConfig
from clinical_governance_system import ClinicalGovernanceSystem

system = ClinicalGovernanceSystem(enforcement=PolicyEnforcementConfig(
    enforce_export_controls=True,       # safe to enable in production
    enforce_rule_modification=True,     # safe to enable in production
    enforce_escalation_limits=False,    # patient-safety sensitive: needs clinical sign-off
))
```

Critical / `emergency_override` escalations are **never** rate-limited (life-safety).

**De-identification salt.** `pseudonymize_patient_id` / `HIPAAExporter.export_csv`
require a runtime salt, supplied via the `salt=` argument or the `OBSERVE_DEID_SALT`
environment variable. They fail loudly if neither is present — a guessable default
would make pseudonyms reversible.

**Concurrency contract.** `ObserveClinicalEngine.evaluate` is safe under a single
asyncio event loop (no `await` inside it). Per-patient structural state is now
guarded by a lock so it is also safe under a thread pool. Two readings for the
**same** patient in flight concurrently should still be serialized by the caller.

---

## Clinical Validation Tools

### Real-data validation results (honest baseline)

On a retrospective set of **500 real patients from PhysioNet**, the most
defensible three-channel configuration scored approximately **73% sensitivity
and 10% specificity**. In plain terms: it caught roughly three of every four
deteriorating patients, but raised a high rate of false alarms. That false-alarm
burden is real and is not hidden here.

This is much lower than the synthetic-data performance (pediatric synthetic
validation showed ~100% sensitivity / ~85% specificity), and the gap is the
point: synthetic data flatters a detector, real physiology does not. The
thresholds in this system are provisional and require clinician-guided tuning
against the deploying site's own population before any real use. This is a
research prototype with an honestly-reported real-data baseline, not an approved
or clinically-validated medical device.

Before deployment, two test harnesses support pre-flight sign-off:

### Deterioration Simulator (`deterioration_simulator.py`)

Synthetic clinical scenarios with ground-truth escalation timings. Validates that
OBSERVE detects realistic pediatric deterioration curves (sepsis spirals, hypoxia,
fever + tachypnea, reactive airway, etc.) within expected windows. Use this to:

- **Before pilot:** Verify thresholds against pediatrician expectations
- **After pilot:** Benchmark sensitivity on real cases, retrain if drift detected

Run: `python3 deterioration_simulator.py` for a clinical validation report.
Expected result before deployment: ≥80% of scenarios detected within tolerance.

### Adversarial Sensor-Fault Test Suite (`adversarial_sensor_faults.py`)

Robustness stress-tests: non-finite inputs, stuck sensors, drift, transient spikes,
and coordinated corruption. Verifies the system **fails safely** (escalates or logs
gracefully) rather than producing silent nonsense. Use this to:

- **Before production:** Ensure sensor faults don't blind the system
- **During ops:** Reference when investigating unexpected alert patterns

Run: `python3 adversarial_sensor_faults.py` for a robustness report.
Expected result before production: ≤1 "risky" cases (failures to escalate on faults).

---

### Strictly observational telemetry (`capacity_planning.py`)

Two opt-in components with **zero decision impact** — they read, compute, and report
but never influence control flow. Their outputs are immutable (frozen), so a telemetry
result cannot be fed back as a control signal:

- **`ErlangCapacityForecaster`** — predicted high-risk volume → required clinicians,
  staffing headroom, and a SAFE/HIGH/CRITICAL alert.
- **`AccuracyMonitor`** — prediction-vs-outcome tracking with sensitivity / specificity
  / PPV / NPV / F1 and drift detection. Abstains (`status="insufficient_data"`) rather
  than reporting metrics from too few samples.

### Reserve-modulated escalation control (`reserve_control.py`, PERCEIVE-adjacent)

`ReserveModulator` is a **control** component (it can change an escalation decision), so
it lives next to the PERCEIVE kernel rather than in the telemetry module. Capacity-aware
escalation sensitivity, safety-constrained: critical/syndrome cases are exempt; by
default it can only *add* proactive escalations (the safe direction) and never suppress
one. Load-shedding (deferring non-critical escalations under saturation) is behind an
explicit flag (off by default) and needs governance sign-off. Any modulation that changes
the outcome is routed through PERCEIVE and audited like a baseline escalation.

Abstention-based fusion is **not** part of these modules — it already exists in OBSERVE
(`BayesianFusion` excludes abstaining engines), and is not duplicated.

### Optional Kalman trajectory engine (`kalman_trajectory.py`)

A constant-velocity Kalman filter per vital, enabled via
`ObserveClinicalEngine(enable_kalman=True)` (default OFF). When on, a stateful,
per-patient `trajectory_kalman` adapter joins fusion, contributing a smoothed
velocity signal plus an innovation ("surprise") signal that flags when a reading
diverges from its predicted trajectory. It is deterministic (identical reading
*sequences* → identical output) and abstains during warm-up so a cold tracker never
dilutes fusion. Velocity thresholds are provisional and need pediatrician sign-off;
the innovation signal is unit-agnostic.

### Fail-open governance

If the PERCEIVE layer raises during an escalation, the pipeline **fails open** — the
escalation proceeds and is flagged `escalate_approved_fallback` with zero confidence
and a `GOVERNANCE_FAILURE_FALLBACK` note. The failure mode of an escalation request is
an extra clinician alert, not a missed block, so failing open is the safe direction;
the explicit flag keeps it from being mistaken for a clean approval.

---

## Notes

- `legacy/` holds the pre-consolidation 19-file modular implementation, archived
  for reference. It is excluded from test collection via `pytest.ini`.
- Clinical thresholds are evidence-informed defaults and require pediatrician
  validation before any clinical deployment. The widened behavioral-engine gating
  (RR / temperature triggers) is provisional and likewise needs sign-off.
