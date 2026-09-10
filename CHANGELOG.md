# CHANGELOG — OBSERVE / PERCEIVE hardening pass

## 1.3.0 (2026-09-08) — the seven residual risks

The closure report (1.2.0) named ten residual risks and ranked seven as
engineering work. All seven are closed or narrowed here; the record of
each is in `docs/closure/GOVERNANCE_BYPASS_REPORT.md` ("1.3.0").

- **R1/R4 signed boundary.** `ExecutionLedger(path, signer=)`,
  `ReceiptLog(path, signer=)` and `ConservationKernel.save/load(signer=)`
  (kernel 0.3.0) sign every entry, receipt and snapshot over its hash with
  a caller-held key (`conservation_kernel.signing.HmacSigner`, or any
  object with `key_id`, `sign`, `verify`). Opened with a key, a file
  refuses to load if any entry is unsigned, signed by another key, or does
  not verify. The consistent forgery that integrity alone accepted (edit,
  recompute the public hash chain) is refused.
- **R2 the orchestrator guards the executor itself.** `guard_executor=True`
  (default) wraps the caller's callable with `execution_guard.guarded`
  unless it already is; the guard's authorization is recorded under
  `execution_authorization`, not inside the committed result.
- **R6 one ledger file, many processes.** The execution ledger takes a file
  lock on every write and re-reads the file before every read and write,
  so two processes on one path see one ledger; an artifact already issued
  an execution in the file is refused a second one (`one_per_artifact`,
  default on), so a replay in a second process is a replay.
- **R7 receipt probe.** With receipts configured, the store is opened for
  append and synced before anything is issued; an unreachable store is a
  recorded refusal (`refused_by="receipt_store"`) before the action.
- **R3 PERCEIVE's ledger persisted.** `PerceiveGovernanceKernel(ledger_path=)`
  appends every audit entry to JSONL and re-verifies the chain on reopen,
  so `decision.in_perceive_ledger` is checked after a restart instead of
  skipped.
- **R5 strict profile.** `GovernanceOrchestrator(profile="strict")`
  requires a sealed scope, vitals, an attested event time, and refuses
  violations PERCEIVE only advised on. The profile and every flag are on
  the record (`handoff.profile`, `handoff.strict`).
- **R8 attested event time.** A source signs `artifact_id|event_time` with
  a registered key (`SentinelPerceiveAdapter.attest_event_time`); the
  orchestrator verifies against `source_signers`, records and commits
  `event_time_attested` and the key id (contract 1.2.0); a bad attestation
  is a temporal anomaly in the advisory profile and a refusal in strict.

The vertical slice runs strict, signed, with a persisted PERCEIVE ledger
and an attested source, and still explains the action after a restart.

Tests: 610 passed, 5 skipped (was 589): `test_authenticated_boundary.py`
(24). Requires conservation_kernel 0.3.0.

## 1.2.0 (2026-09-08) — CLOSE THE SYSTEM

The execution boundary is now enforced, the record is durable, and the
whole chain is explained after a restart. Reports in `docs/closure/`.

- `execution_guard.py`: an append-only, hash-chained `ExecutionLedger`; the
  orchestrator issues every `ExecutionContext` into it; `authorize_execution`
  recomputes the structure, matches the issuance field by field and marks
  single use; `guarded(func, ledger, kernel=)` wraps any executor. A forged,
  altered or replayed context is refused before the effect, also after a
  restart, and the refusal is ledgered.
- `governance_record.py`: `to_record` / `from_record` / `verify_record`,
  `record_hash` over a canonical form, `ReceiptLog` (hash-chained JSONL of
  every result); any alteration refuses to load.
- Contract 1.1.0: `event_time` (committed) and `ingested_at` (recorded) on
  the request; `execution_id` in the execution-context commitment;
  self-describing `ExecutionApproval`; `canonical_repr` for result hashing.
- Verifier: kernel membership requires a derived-by-transformation chain
  with the request among the roots; outcome present and committed
  unconditionally (including EXECUTION_FAILED); `execution_status`;
  `approval.names_request_artifact`. 34 checks.
- Conservation boundary: the adapter hands the kernel real propositions
  with a conservative mapping; the decision is born DECISION / MACHINE /
  NONE with a declared LINEAGE change. Requires conservation_kernel 0.2.0.
- Scope binding: a passed Gateway admission is verified (integrity, scope,
  artifact); `scope_sealed` and the admission summary are recorded;
  `require_declared_scope=True` refuses a bare claim.
- Failure semantics: a PERCEIVE crash is a recorded refusal; a receipt
  write failure after the action degrades explicitly (`receipt_error`);
  temporal anomalies recorded on every result.
- `vertical_slice.py`: one governed action through the real code, explained
  backwards from its effect after a restart, replayed three ways and refused
  three ways.
- Release: a kernel-only clean clone installs, imports and tests (494
  passed, 63 skipped); optional-pack tests skip on the pack, not the adapter.

Tests: 589 passed, 5 skipped (was 529). Attacks measured before and after
repair: 12 on the spine; see `docs/closure/GOVERNANCE_BYPASS_REPORT.md`.

## 1.1.0 (2026-09-08)

Production hygiene from the 90-day plan. `pyproject.toml` replaces
`setup.py`: version 1.1.0, the Conservation Kernel declared as the one hard
dependency, the optional chain stages (CCC, AUGUR, GEMS, Governance_Gateway)
as the `chain` extra and fortress-kernel as `fortress`, and every root module
listed so the wheel is not empty. CI checks out this repository alone and
installs from the manifest; the six sibling checkouts are gone. Measured in
an isolated copy with no siblings on disk: 529 passed, 5 skipped, zero
collection errors. The verified chain itself (contract 1.0.0, verifier,
sixteen attacks, demo) is from PR #18 and is unchanged here.

Scope: WS1 red-team (see `RED_TEAM_REPORT.md`), WS2 confirmed-defect fixes, WS3
opt-in additive upgrades, WS4 behavior-preserving cleanup. Python 3.8+, stdlib only.

**Test status: 249 passing** (241 from the first hardening pass + 8 from the
second-pass context-sanitization fix). Determinism verified: identical recorded
inputs → identical decisions **and** identical `decision_fingerprint` across
fresh runs.

A second red-team pass (see `RED_TEAM_REPORT.md`, "SECOND RED-TEAM PASS") closed
R-CTX, the context trust boundary the first pass flagged but left open: a
non-numeric context value (e.g. `age_months="?"`) crashed scoring, and a
physically impossible one (e.g. `previous_o2=200`) silently skewed it. A
`sanitize_context()` gate at the same boundary as `validate_vitals()` now drops
non-numeric, non-finite, or out-of-physical-range context values before scoring.

Underlying capabilities preserved: OBSERVE's 7-engine detection set; PERCEIVE's
unanimous-gate + SHA256-chained-audit + DGK-consensus model; the public surface
(`ClinicalGovernanceSystem.process_vitals`, `build_single_hospital_system`,
`build_multi_hospital_system`, `VitalsSnapshot`, `ClinicalDecision`); determinism.

---

## D. INTEGRATED NEW LEARNINGS (from a later round of pipeline rewrites)

Five external rewrites were reviewed; the genuine improvements were merged into the
hardened system without regressing it.

| ID | Learning | Decision | File(s) | Test |
|----|----------|----------|---------|------|
| NL-KAL | **Kalman trajectory engine** — constant-velocity state-space filter per vital; exposes smoothed velocity + innovation ("surprise") signals | **Integrated, opt-in** (`enable_kalman=True`, default OFF). Adapted to the `VitalsSnapshot` channels, made per-patient + deterministic. Corrected the prototype's ad-hoc velocity nudge to a proper 2-state update. Abstains during warm-up | `kalman_trajectory.py`; `observe_consolidated.py` (`enable_kalman`, `_get_kalman`, fusion hook) | `test_kalman_trajectory.py` |
| NL-FAILOPEN | **Fail-open on governance crash** — a PERCEIVE exception must not suppress a clinical escalation | **Integrated.** Scoped to the escalation path (failure mode is "extra alert," not "missed block"); flagged as `escalate_approved_fallback` with zero confidence so it is never mistaken for a clean approval | `clinical_governance_system.py` (`process_vitals`) | `TestGovernanceFailOpen` |
| NL-CTX | **Richer governance request context** (risk/regime/top-triggers) | **Integrated** — improves the SOX/PERCEIVE audit trail (records *why* an escalation fired) | `clinical_governance_system.py` (`_build_governance_request`) | covered by integration tests |
| NL-OBS-SLIM | Slimmed OBSERVE rewrite | **Rejected.** Drops the 7th engine, syndrome floor, clinical-safety bypass, hysteresis/dwell, and the audit chain. Would fail the suite and lose safety-critical machinery; contained no fix not already present | — | — |
| NL-PERC-SLIM | Slimmed PERCEIVE rewrite | **Rejected.** Un-wires the three governance policies, drops DGK consensus + content-bound manifest hash, and its audit `verify()` is a stub (`return True`) | — | — |

### Resolved design decision: WS3 observational-vs-influence
A rewrite proposed a strictly observational WS3 with a runtime deadman switch. Rather
than bolt on that switch, the boundary was made **structural**:
- `capacity_planning.py` is now **strictly observational telemetry** (Erlang-C
  forecaster + accuracy monitor only) with **zero decision impact**. Its outputs
  (`CapacityForecast`, `AccuracyReport`) are **frozen/immutable**, so a telemetry
  result cannot be fed back as a mutable control signal.
- The capacity-aware escalation **control** (`ReserveModulator`) was reclassified as a
  control component and **moved to `reserve_control.py`**, next to the PERCEIVE kernel
  that owns control decisions. Behavior is unchanged (opt-in, safe-direction-only,
  critical/syndrome exempt; any decision it changes is routed through PERCEIVE + audit).
- No deadman switch was added — the string-matching enforcement it proposed only
  catches a violation that announces itself. Module separation + immutable artifacts
  enforce the boundary instead.

Files: `capacity_planning.py` (telemetry, frozen artifacts), `reserve_control.py`
(new control module), `clinical_governance_system.py` (split imports). Tests:
`test_capacity_planning.py` (telemetry), `test_reserve_control.py` (control).

---

## A. BEHAVIOR-CHANGING (touches the decision / governance / compliance path)

Each item flips an output under some input. New capabilities default OFF; the
remainder are corrections to defects.

### WS2 — confirmed-defect fixes

| ID | Change | File(s) | Test |
|----|--------|---------|------|
| R1 | Non-finite / impossible vitals → WARNING+escalation (`DATA_INTEGRITY_FAULT`), never "stable" | `observe_consolidated.py` (`validate_vitals`, `_fault_verdict`, gate in `evaluate`) | `TestVitalsValidation` |
| R3 | Behavioral-engine selection widened to fire on `RR>35` or `temp>38.5` (**provisional thresholds**) | `observe_consolidated.py` (`select_engines`) | `TestBehavioralGatingWidened` |
| R5 | Manifest hash now covers full policy content (closes value-tamper collision) | `perceive_consolidated.py` (`_compute_hash`) | existing `test_manifest_registration_*` (still green; hash now content-bound) |
| R6 | De-id salt is a required runtime secret (`salt=` / `OBSERVE_DEID_SALT`); fail-loud | `compliance_exporters.py` (`_resolve_deid_salt`, `pseudonymize_patient_id`, `HIPAAExporter.export_csv`) | `test_missing_salt_raises`, `test_salt_from_environment` |
| R-GOV | 3 dormant policies wired as real gates: `escalation_rate_policy`, `data_export_policy`, `rule_modification_policy` — **advisory by default** | `perceive_consolidated.py` (`PolicyEnforcementConfig`, `GovernanceState`, 3 gate methods, `_select_gates`, `evaluate_request`); `clinical_governance_system.py` (`enforcement=`, `timestamp` passthrough) | `TestEnforcementOnBlocks`, `TestAdvisoryGovernanceDefault`, `TestGovernanceState` |
| R-CIT | citadel no longer rejects on hedging substrings ("might"/"maybe") | `perceive_consolidated.py` (`citadel`) | `test_clinical_hedging_not_blocked` |
| R-DET | Reproducible `decision_fingerprint` added; chained `immutable_hash` kept as tamper-evidence; FDA/GDPR/README wording corrected | `observe_consolidated.py` (`decision_fingerprint`, `FusedVerdict.decision_fingerprint`, `evaluate`); `compliance_exporters.py`; `README_CONSOLIDATED.md` | `TestDecisionFingerprint` |

**Design decisions (WS2):**
- **R-GOV advisory default.** Wiring rate limits would otherwise start blocking
  escalations that previously passed. To honor "opt-in / baseline unchanged" *and*
  patient safety, each policy runs in advisory mode (evaluates + logs, but approves)
  until enabled via `PolicyEnforcementConfig`. Export + rule controls are safe to
  enable in production; escalation limits are patient-safety sensitive and need
  clinical-governance sign-off. **Critical / `emergency_override` is never
  rate-limited.** Rate windows are derived deterministically from a caller-supplied
  `PolicyRequest.timestamp` (the clinical pipeline passes `vitals.timestamp`); history
  is recorded only after approval, so a request never counts against itself.
- **R-DET chose "both."** The chained ledger hash legitimately depends on insertion
  time + position (tamper-evidence), so it is intentionally not reproducible. Rather
  than weaken it, a separate wall-clock-free `decision_fingerprint` provides the
  reproducible decision hash FDA replay wants. Wording was corrected to distinguish
  the two.
- **R1 severity.** A data-integrity fault escalates (WARNING) by default — for an
  early-warning system, surfacing an unmeasurable patient beats silently ignoring one.
  A quieter sensor-fault channel is noted as a production option.

### WS3 — optional additive upgrades (opt-in, default OFF)

| ID | Change | File(s) | Test |
|----|--------|---------|------|
| W3-CAP | `ErlangCapacityForecaster` — advisory staffing forecast (no decision impact) | `capacity_planning.py`; `ClinicalGovernanceSystem.forecast_capacity` | `TestErlangForecaster` |
| W3-RES | `ReserveModulator` — capacity-aware escalation sensitivity; safe-direction-only by default; critical/syndrome exempt; load-shedding behind explicit flag | `capacity_planning.py`; `process_vitals(utilization=...)` routes any outcome change through PERCEIVE + audit | `TestReserveModulatorSafety` |
| W3-ACC | `AccuracyMonitor` — sensitivity/specificity/PPV/NPV/F1 + drift; abstains on insufficient data | `capacity_planning.py`; `ClinicalGovernanceSystem.record_outcome` / `accuracy_report` | `TestAccuracyMonitor` |

**WS3 notes.** All three are inert unless the system is constructed with them, so
baseline behavior is unchanged. The reserve modulator's only default-enabled effect
is *adding* proactive escalations when reserve is high (it never suppresses below
baseline unless `allow_load_shedding=True`). **Overlap avoided:** abstention-based
fusion already exists in `BayesianFusion` and is *not* re-implemented.

---

## B. BEHAVIOR-PRESERVING (no decision changes)

| Change | File(s) |
|--------|---------|
| Added `threading.Lock` guarding per-patient structural state (thread-safety; identical single-threaded behavior) | `observe_consolidated.py` |
| `PolicyRequest.timestamp` added as optional field (default `None`); additive, backward-compatible | `perceive_consolidated.py` |
| `FusedVerdict.decision_fingerprint` / `ClinicalDecision.capacity_*` added as optional fields with defaults | `observe_consolidated.py`, `clinical_governance_system.py` |
| Removed a redundant no-op self-assignment in the no-escalation fast path | `clinical_governance_system.py` |
| Imports (`threading`, `Tuple`, `os`) and documentation/comments | multiple |
| README: file inventory, test count, determinism/hardening/opt-in/concurrency sections | `README_CONSOLIDATED.md` |

### Sanctioned test updates (behavior-changing fixes require matching tests)
- `test_perceive_consolidated.py`: gate-selection assertions for `escalate_patient`,
  `modify_rule`, `export_data` updated to include the new policy gates;
  `test_hedging_language_rejected` → `test_clinical_hedging_not_blocked` (asserts
  approval).
- `test_integration_consolidated.py`: `escalate_patient` gate-selection assertion
  updated to include `escalation_rate_policy`.
- `test_compliance_exporters.py`: de-id calls now pass an explicit salt; added
  missing-salt + env-salt tests.

---

## C. INTENTIONALLY NOT CHANGED (with reasons)

- **Clinical threshold values** (pediatric norms, syndrome cutoffs, and the new
  behavioral RR/temp triggers). These are clinical-judgment calls; the new triggers
  are marked provisional and **require pediatrician sign-off** before deployment.
- **Escalation-limit enforcement left OFF by default.** Enabling it can defer a
  warning-level escalation; flipping it on is a clinical-governance decision, not an
  engineering default. (Export + rule-modification enforcement are safe to enable and
  documented as such.)
- **Audit-ledger persistence / truncation.** The unbounded in-memory ledger is a
  deployment/storage concern (checkpoint + persist + verify), out of scope for a
  code-hardening pass; flagged in the red-team report.
- **Full context-provenance validation.** Caller-supplied `context` (alert, baselines,
  history) is an architectural trust boundary; signing/attesting context is a larger
  design change deferred here. The syndrome floor already prevents context-injection
  from suppressing true positives.
- **Cross-module name reuse** (`EscalationPolicy`, `ImmutableAuditLedger` exist in both
  OBSERVE and PERCEIVE) and **long methods** (`evaluate`, `evaluate_request`) were left
  as-is. Renaming public symbols or restructuring the orchestrators risks behavior
  drift and large diffs for no functional gain; clarified via docstrings instead.
- **DGK multi-node consensus model** and the **unanimous-gate / chained-audit**
  architecture — preserved unchanged per the capability-preservation constraint.
