# RED-TEAM REPORT — OBSERVE / PERCEIVE

Adversarial review of the consolidated clinical-AI system. Findings are ranked by
severity. Each was confirmed empirically against the running code (probe scripts)
or by direct code inspection. Status reflects what was done in this remediation
pass (see `CHANGELOG.md` for the change → test mapping).

Severity key: **CRITICAL** (patient-safety / silent wrong answer) ·
**HIGH** (security / compliance integrity) · **MEDIUM** (governance gap) ·
**LOW/INFO** (correctness of claims, hardening) · **GOOD** (verified strength).

---

## CRITICAL

### R1. Non-finite / physically impossible vitals score as "stable" (silent failure)
- **Trigger:** Feed `oxygen_saturation=NaN` (or `Inf`, or impossible values like
  O2=120, temp=1000, HR=−50). Confirmed: NaN O2 → `risk=0.000, regime=stable,
  escalation=False`. O2=120 and temp=1000 similarly under-scored.
- **Why:** Every comparison against `NaN` is `False`, so a garbage/missing reading
  bypasses every threshold check and emerges as a *confident* "stable" verdict. A
  sensor glitch makes a deteriorating patient look healthy.
- **Impact:** Missed deterioration on corrupt telemetry — the worst failure mode for
  an early-warning system, because it is silent.
- **Fix (WS2):** `validate_vitals` + a data-integrity gate in `evaluate()` route any
  non-finite / out-of-physical-range reading to an immediate WARNING escalation with
  an explicit `DATA_INTEGRITY_FAULT`, recorded in the audit ledger. Never scored as
  stable. Tests: `TestVitalsValidation`.

---

## HIGH

### R5. Manifest hash collision on policy-value tampering
- **Trigger:** Register a manifest with `{"escalation":{"max_daily":10}}`, then one
  with `max_daily=99999`. Confirmed: **identical** manifest hashes.
- **Why:** `_compute_hash` covered only version + policy_count + created_at, not the
  policy values.
- **Impact:** A threshold can be altered without changing the manifest hash —
  defeating the tamper-evidence the versioning exists for.
- **Fix (WS2b):** hash the full `policies` content. Collision confirmed resolved.

### R6. De-identification is reversible from source
- **Trigger:** With the hardcoded salt and low-entropy IDs, build a rainbow table
  `{pseudonymize(f"P{n:03d}"): f"P{n:03d}"}`. Confirmed: pseudonym reversed to the
  raw ID.
- **Why:** Salt was a source constant (`OBSERVE_SALT_V1`) while the GDPR exporter
  claimed pseudonyms were "reversible only by the controller holding the salt."
- **Impact:** PHI re-identification by anyone with the code; false GDPR claim.
- **Fix (WS2d):** salt is now a required runtime secret (`salt=` arg or
  `OBSERVE_DEID_SALT`), fails loud if absent; GDPR text aligned. Tests:
  `test_missing_salt_raises`, `test_salt_from_environment`.

### R3. Behavioral/syndrome engine not selected for severe RR / high fever
- **Trigger:** A deteriorating child (RR=55, temp=40.5) with borderline O2/HR
  (O2=93, HR=139). Confirmed: only the heuristic engine ran; behavioral/syndrome
  analysis never selected; sat at risk≈0.25.
- **Why:** Behavioral selection gated solely on `O2<92 or HR outside 90–140`.
- **Impact:** Severe tachypnea + fever could pass without syndrome assessment.
- **Fix (WS2e):** widened selection to also fire on `RR>35` or `temp>38.5` (the
  lowest syndrome cutoffs). **Thresholds are provisional and need pediatrician
  sign-off.** Tests: `TestBehavioralGatingWidened`.

---

## MEDIUM

### R-GOV. Wired governance was dead in the integrated path
- **Trigger:** Issue `escalate_patient` / `export_data` / `modify_rule` requests and
  inspect `applied_gates`. The rate-limit / consent / approval policies never ran.
- **Why:** `EscalationPolicy.can_escalate`, `DataExportPolicy.can_export`,
  `RuleModificationPolicy.can_modify` were unit-tested but invoked by no gate.
- **Impact:** PERCEIVE advertised rate-limited, consent-gated, approval-gated
  governance it did not enforce.
- **Fix (WS2a):** three new gates (`escalation_rate_policy`, `data_export_policy`,
  `rule_modification_policy`) wired into deterministic gate selection and consensus,
  backed by minimal deterministic `GovernanceState`. **Advisory by default** (no
  baseline verdict changes); enforce per policy via `PolicyEnforcementConfig`.
  Critical / `emergency_override` is never rate-limited. Tests:
  `TestEnforcementOnBlocks`, `TestAdvisoryGovernanceDefault`, `TestGovernanceState`.

### R-CIT. citadel hedging check blocks legitimate clinical language
- **Trigger:** Justification "patient might be septic" → rejected (substring match on
  "might"/"maybe"; also matches "mighty").
- **Impact:** Legitimate differential-diagnosis escalations blocked.
- **Fix (WS2f):** removed the hedging-as-violation rule; clarity is still enforced by
  length + matching-context checks. Test: `test_clinical_hedging_not_blocked`.

---

## LOW / INFO

### R-DET. "Same audit hash" determinism claim was false
- **Trigger:** Run identical inputs through two fresh engines: identical risk/regime,
  **different** audit hashes (the entry embeds `datetime.now()`).
- **Impact:** Overstated determinism claim an FDA reviewer would flag.
- **Fix (WS2c):** added a reproducible `decision_fingerprint` (wall-clock-free) for
  replay; kept the chained `immutable_hash` as the (intentionally non-reproducible)
  tamper-evidence hash; corrected README + FDA/GDPR wording. Tests:
  `TestDecisionFingerprint`.

### R-CTX. Context fields are an implicit trust boundary
- **Observation:** Scoring consumes caller-supplied context (`alert`, `baseline_*`,
  `history_*`). On a borderline, non-syndrome case, `alert="crying"` can induce a
  benign reduction. (On true syndromes it cannot — see G2.)
- **Status:** Documented, **not** code-fixed. Whoever populates `context` is inside
  the trust boundary; full provenance validation of context is out of scope for this
  pass and noted in "intentionally not changed."

### R-CONC. Per-patient state mutated without locking
- **Observation:** `evaluate()` mutates per-patient `OrderedDict` / entropy dict.
  Safe under a single asyncio loop (no `await` inside), unsafe under a thread pool.
- **Fix (hardening):** structural mutations now guarded by a lock; behavior identical
  single-threaded. Residual: same-patient concurrent readings should be serialized by
  the caller (documented in README).

### R-DOS. Audit ledger growth
- **Observation:** The immutable ledger grows unbounded by design.
- **Status:** **Not** changed — persistence/checkpointing is a deployment concern
  (see "intentionally not changed"). Per-patient cooldown/entropy state is already
  LRU-bounded; scheduler queues and the provisional store are already bounded.

---

## VERIFIED STRENGTHS (no change needed)

- **G1. Audit-chain tamper detection works.** Forging a past entry's risk score makes
  `verify_integrity()` return `False`. Confirmed.
- **G2. Syndrome floor defeats benign-reduction injection on true positives.** A
  crash-level case with `alert="crying"` still resolves `critical / risk=1.0` — the
  dangerous-pattern floor and benign-suppression logic hold.
- **G3. Abstention exclusion** keeps a chorus of "no data" engines from diluting a
  real detection (pre-existing; relied upon, not duplicated by WS3).

---

# SECOND RED-TEAM PASS

A follow-up adversarial pass targeting the gaps the first pass explicitly left
open, rather than re-finding what it already fixed. The first pass hardened the
VITALS against NaN/garbage (R1/R2) and flagged the CONTEXT dict as an
unvalidated trust boundary (R-CTX) that it did NOT code-fix. This pass closes
R-CTX and re-verifies the rest.

## MEDIUM

### R-CTX (now fixed). Context was an unvalidated trust boundary
- **First-pass status:** documented, not code-fixed. "Whoever populates `context`
  is inside the trust boundary."
- **What the probe found:** the context dict feeds many numeric values into
  scoring (`age_months`, `previous_o2/hr/rr/temp`, `baseline_o2/hr`,
  `time_delta_seconds`, `history_o2/hr`, `recent_o2_readings`). Two concrete
  failures:
  1. **Crash:** a non-numeric scalar such as `age_months="?"` or
     `previous_o2={...}` raised `TypeError` deep in an engine
     (`get_age_group: '<' not supported between 'str' and 'int'`). A buggy
     upstream EHR field could crash scoring.
  2. **Silent skew:** a physically impossible value such as `previous_o2=200`
     (200% oxygen saturation) was finite, so it passed the NaN guard and
     subtly influenced the risk score.
- **Fix:** a `sanitize_context()` function at the same boundary as
  `validate_vitals()`, called at the top of `evaluate()`. Scalar numeric context
  keys are dropped if non-numeric, bool, non-finite, OR outside their physical
  range (the physiological keys reuse the same bounds as the vitals). Numeric
  list keys have bad elements filtered. A dropped value is treated as absent,
  which every engine already handles, so scoring degrades gracefully instead of
  crashing or trusting garbage.
- **Verification:** an impossible `previous_o2=200` now yields the SAME score as
  having no previous reading at all (the value is dropped), and a non-numeric
  `age_months` no longer crashes. Confirmed at both the `sanitize_context` unit
  level and the `evaluate()` engine level.
- **Tests:** `test_context_sanitization.py` (8 tests): non-numeric does not
  crash, NaN/Inf dropped, out-of-range dropped, out-of-range does not skew the
  score, bad list elements filtered, non-numeric keys pass through, non-dict
  context replaced, valid context unchanged.

## RE-VERIFIED (first-pass fixes still hold)

- **R1/R2 (vitals NaN/impossible):** still routed to a fault verdict, never
  scored as stable. The new context sanitization sits AFTER this gate, so a
  faulted reading is rejected before context is even examined.
- **Division/time_delta vectors:** `time_delta_seconds` of 0, 0.0, or negative
  did not cause a division error.
- **NaN in context scalars:** already handled gracefully by the engines (every
  comparison against NaN is False); now also explicitly dropped by the sanitizer
  so the behavior is intentional rather than incidental.

## Result of second pass

Baseline entering this pass: 241 tests. After closing R-CTX: **249 tests, all
passing.** One MEDIUM trust-boundary gap closed (crash + silent-skew on context),
the rest of the first pass re-verified. The `pytest.ini` collection allowlist was
updated to include the new test file.
