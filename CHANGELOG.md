# CHANGELOG — OBSERVE / PERCEIVE hardening pass

## Unreleased

- **The behavioral-engine gate is a named, validated setting.** PR-2a of OBSERVE's
  `RECONCILE_UPSTREAM_PLAN.md`, the upstream half of the owner's decision to keep OBSERVE's
  narrower clinical rule until a pediatrician signs off upstream's wider one.
  `PediatricCassette(engine_gate=...)` takes `widened_provisional` (the default: the
  existing rule, which also runs the behavioral engine for a respiratory rate above 35 or a
  temperature above 38.5, and is **provisional, pending pediatrician sign-off**) or
  `conservative_legacy` (the behavioral engine only for oxygen below 92, or heart rate above
  140 or below 90, exactly OBSERVE's rule). An unknown name raises and never falls back. The
  core records a cassette's `identity_extras` under the cassette identity in the stored
  entry; the cassette supplies `engine_gate` there **only** for a non-default gate, so a
  decision made under `conservative_legacy` is distinguishable in the audit entry and in the
  fingerprint and cannot be relabelled as default, while every default entry keeps its exact
  shape and hash (no new parameter-set era, no change to any existing fingerprint). The two
  profiles disagree on 5,280 of 21,168 points of a dense synthetic grid, all of them
  readings with normal oxygen and heart rate but a high respiratory rate or temperature.
  New tests in `test_engine_gate_profile.py`; no existing test changed.
- **Strict refuses `guard_executor=False`; the two paths are documented.** The strict
  profile now refuses, at construction, the switch that turns the pre-action executor
  guard off (advisory may still use it). README gains "Which path is authoritative":
  the orchestrator is the live path, the CNS governed-decision pipeline is an optional
  separate check that the orchestrator does not call, plus what strict does and does
  not guarantee. `cns_governed_decision.py` says the same in its docstring.
- **An executor cannot declare itself guarded, and strict needs a guarded kernel.**
  The orchestrator used to skip wrapping an executor whose `execution_guard`
  attribute was truthy, so a function carrying that label ran with no ledger check.
  `guarded()` now records each wrapper it makes, and the orchestrator skips wrapping
  only for a wrapper made for its own ledger (`execution_guard.guard_ledger_of`); the
  attribute is kept for compatibility but no longer trusted. A wrapper made for a
  different ledger is wrapped again, so it fails closed. The strict profile now
  refuses, at construction, a Conservation Kernel with no root guard (advisory is
  unchanged). Three strict tests in `test_authenticated_boundary.py` built their
  strict orchestrator on the unguarded fixture kernel; they now use a kernel with a
  permissive root guard, which is the only change to them. New:
  `test_guard_provenance.py`.
- **The examples run the real root guard.** `demo_why.py` and `vertical_slice.py`
  build the kernel with `RootAdmission` and pass it to the orchestrator, in place of
  `allow_unguarded_roots=True`. The slice's reloaded kernel holds a closed guard: a
  restore does not ask the guard about saved roots, and nothing new can register. New
  `root_admission.require_root_admission()` checks for a CNS with `DecisionGuard` and
  the kernel's CNS connector up front; without them each example exits 2 and names
  the install command instead of failing partway through a request. The example tests
  skip without CNS and run in the `cns` job, where a skip is a failure; the new
  `test_examples_need_cns.py` runs in both jobs. Test fixtures for other components
  still use `allow_unguarded_roots=True`.

- **The parameter-set label is `observe.parameters/v2`, and the safety-bypass
  note is fingerprinted.** Two engines used `observe.parameters/v1` for different
  content (this core's, and OBSERVE's own engine), so the same label named two
  parameter sets. This core is now `v2`. In the same change, the safety-bypass note
  (`CLINICAL_SAFETY_BYPASS: ...`) is recorded in the decision data under
  `safety_bypass` and so is part of `decision_fingerprint`. It is present only when
  a bypass note exists, so a clean entry keeps its shape; both decision-data
  builders (`evaluate` and the no-overlay fault verdict) carry it. Because the
  label feeds `PARAMETER_SET_VERSION`, every fingerprint and every
  `parameter_set_version` changes once, and bypass entries change again because of
  the note. A verifier that recomputes the fingerprint from a stored audit entry
  still agrees, since the note is inside the audit data. Fingerprint inputs are not
  rounded (unchanged). **Assumption:** no persisted evidence from before this change
  exists that a verifier must still recognize; if any does, it carries the `v1`
  label or the older version hash and needs an era-aware verifier. Consumers
  elsewhere in the stack (the beta engine, the delta ledger, OBSERVE's risk adapter)
  hash their own inputs and do not depend on this payload. New tests in
  `test_parameters_v2_and_bypass_note.py`; no existing test changed.

- **A root can be registered only after a roster approves that document.**
  New `root_admission.py`: `RootAdmission` is the callable a kernel holds as its
  `root_guard`. It is closed. For one request the adapter asks a CNS roster
  (PERCEIVE's request and ruling, the kernel's own root admission, and the
  super gate's same-document check) for a decision; on a go it opens a fresh
  one-use `DecisionGuard` for that exact document on the calling thread, and
  closes it again whatever happened. Pass the same object to the kernel
  (`ConservationKernel(root_guard=admission)`) and to the orchestrator
  (`root_admission=admission`). A root written straight to that kernel is
  refused. The roster's PERCEIVE seat says a ruling exists for the document, not
  that PERCEIVE approved it, because the orchestrator registers the root of a
  refused request too and that record is kept. Needs the `cns` extra, now pinned
  to CNS 3ec3ffc (v1.9.0 predates `DecisionGuard`). `allow_unguarded_roots=True`
  is still used by the examples and test fixtures that run without CNS.
- The kernel pin is now 7b08c79, the commit on the kernel's main. PR #56 pinned
  the PR branch head, which squash-merging left off main, and
  `requirements-demo.txt` still pinned an older kernel, so CI's `cns` job failed.
  One existing assertion changed with the CNS pin
  (`test_cns_governed_decision.py::CNS_PIN`).

- PERCEIVE evaluation exceptions are unavailable, not approved. A
  clinical escalation still proceeds, but ClinicalDecision.governance_approved
  remains None, the action is escalate_governance_unavailable, and PERCEIVE
  records a non-approving failure entry bound to the request in its hash-chained
  audit ledger. If audit storage also fails, the escalation remains visible and
  the returned decision records that audit evidence is unavailable. Consumers
  that handled the former escalate_approved_fallback action must handle the new
  unavailable action explicitly; it never represents authorization.

- **The orchestrator tells a person when a stage crashes.**
  `GovernanceOrchestrator` takes an optional `alert_human` callable. It is
  called, with a dict (`stage`, `stage_error`, `request_id`, `artifact_id`),
  only when a stage raises instead of reaching a verdict: the simulation screen,
  PERCEIVE, the Conservation Kernel, execution approval, or the receipt-store
  probe. Under `raise_on_stage_error` it is called before the exception is
  raised. The record of such a stage error gains `human_alert` ("delivered",
  "failed" or "no_channel"); a failing or missing channel is logged at CRITICAL
  and never suppresses or alters the rejection. Ordinary refusals and
  successes never call it and gain no field, and the handoff stamp is
  unchanged, so their records (and receipt hashes) are identical to before.
  Optional, not required. No existing test was edited. (The executor-crash and
  lost-receipt cases are covered by the next entry.)

- **The orchestrator also tells a person when the executor crashes or a receipt
  is lost.** Two failures that were left out of the entry above now call
  `alert_human` too, with the same dict and the same `delivered` / `failed` /
  `no_channel` record. (1) The caller's own executor raising after approval
  (stage `execution`): the alert comes before any `raise_on_execution_error`
  re-raise, and the `EXECUTION_FAILED` record gains `human_alert`. (2) A failure
  appending the receipt after the action already ran (stage `receipt_append`):
  the result keeps its outcome, nothing is undone or retried, and it gains
  `human_alert`, or `receipt_human_alert` when a stage-error alert is already on
  it, so neither is lost. A failing or missing channel is logged at CRITICAL
  and never changes the outcome. Records on every other path, and the handoff
  stamp, are unchanged. No existing test was edited.
- **BREAKING: a ClinicalGovernanceSystem needs an alert channel.**
  Constructing one without `alert_human` now raises `ValueError`, unless
  `allow_no_alert_channel=True` is passed. `build_single_hospital_system` and
  `build_multi_hospital_system` take both arguments. The opt-out keeps the
  earlier behaviour: a governance failure is logged at CRITICAL and the
  decision records `human_alert == "no_channel"`. Test edits (setup only, no
  assertion changed): the 15 `build_single_hospital_system()` and
  `build_multi_hospital_system(...)` calls in `test_clinical_governance_system.py`
  and the one in `test_compliance_exporters.py` pass the opt-out; the two demos
  (`clinical_governance_system.py`, `compliance_exporters.py`) pass it too
  because they have no pager. Three new tests cover the refusal, a system with
  a channel, and the opt-out staying loud.
- **Fail loud when governance is unavailable.** `ClinicalDecision` now carries
  `governance_status` (`not_evaluated`, `approved`, `blocked`, `unavailable`),
  the field to read instead of the true/false/blank `governance_approved`.
  `governance_evaluated` is now True only when a verdict exists, so it is False
  when PERCEIVE crashed (one existing assertion changed to match). A new optional
  `alert_human` callback on `ClinicalGovernanceSystem` is called for every
  escalation that governance could not judge. `ClinicalDecision.human_alert`
  records the result: `delivered`, `failed` (also recorded as
  `HUMAN_ALERT_FAILED` in the violations and logged at CRITICAL) or
  `no_channel` (logged at CRITICAL). A failing alert never suppresses the
  escalation. Callers that read `governance_evaluated` as "governance was
  attempted" must read `governance_status` instead.
- The FDA 510(k) checklist cited `test_governance_exception_fails_open_for_escalation`,
  which #54 renamed, so its test-name check was failing on main. It now cites
  `test_governance_exception_preserves_escalation_without_approval`, and its
  "fails open" wording is corrected to match the behaviour.


- **OBSERVE partial-assessment fault overlay** (ported from the OBSERVE
  fork, its T2b findings). A reading with a faulted channel is no longer
  left unscored when the cassette can say which channel is bad. A cassette
  may now supply one optional member, `mask_faults(obs, faults)`
  (`cassette.OPTIONAL`, `cassette.PartiallyAssessable`,
  `cassette.optional_member`; `conformance_failures` names it only when it
  is present and not callable). The pediatric cassette, now 1.1.0,
  supplies it: each faulted channel is read as a neutral value that is a
  finding in no engine (`_VITALS_NEUTRAL`) and the context keys that carry
  that channel's history are dropped (`_CHANNEL_CONTEXT_KEYS`), so the
  channels that still work are scored. The core then overlays the fault:
  the regime is the most severe of the masked candidate, WARNING and the
  tracked regime; the EscalationPolicy is read and never written on a
  faulted reading; `risk_score` is floored to `REGIME_RISK_FLOOR` of that
  regime on every such reading; confidence is multiplied by
  `UNASSESSABLE_CONFIDENCE_PENALTY` (0.5); the reading's entropy is not
  carried; the Kalman tracker is not updated. Paging on a faulted reading
  is deduplicated per subject by reading time over
  `ESCALATION_LOCK_SECONDS`, with a sensor page and a page for a hard rule
  or syndrome on a valid channel kept apart, so a sensor page never
  silences a real emergency. When the cassette cannot attribute a fault,
  or every channel is faulted, nothing is scored and the record says so;
  the policy is still not written. `FusedVerdict` gains
  `validation_faults` and `unassessable`. On a faulted reading the decision
  payload records the reading as it arrived, `validation_faults`,
  `verdict.unassessable` and `assessed_vitals`, all inside the
  fingerprint; clean entries keep their shape. Rules keep the
  `DATA_INTEGRITY_FAULT: ` prefix. The industrial cassette is unchanged
  and keeps the plain fault verdict (which, like before, still writes
  WARNING into the policy). `REGIME_RISK_FLOOR` and
  `UNASSESSABLE_CONFIDENCE_PENALTY` join `PARAMETER_SET`, so
  `PARAMETER_SET_VERSION` changes; with the cassette version bump,
  fingerprints of earlier records do not match. One pre-existing
  assertion changed: `test_nan_vital_rejected` asserted
  `active_engines == []` ("nothing was scored"), the behaviour this entry
  replaces; it now asserts `validation_faults` and `unassessable`, as the
  fork's original does. The seven T2b tests are ported in
  `T2b_PartialAssessmentOverlayIsSafe`; the port's own decisions are
  pinned in `test_fault_overlay.py`. Full suite: 671 passed, 59 skipped,
  5 xfailed (was 622 passed). `ruff check .` is clean.
  Follow-up from the red team: when the dedup window cannot be measured
  (a missing, naive-against-aware or non-datetime reading time) a repeated
  sensor page is held, a bypass still pages, and the window restarts from
  that reading; before, such readings raised or paged on every call.
  Known limitation: the physiological reserve engine reads rich-telemetry
  context keys (`_PHYSIO_AXIS_KEYS`, `hr_history`) that `sanitize_context`
  does not bound, so a non-numeric value there raises. That was already
  true of clean readings; the old fault path never reached the engines,
  so a faulted reading with such context used to return a verdict and
  now raises too. Bounding those keys needs clinical ranges and is left
  to their owner.

- **OBSERVE parameter-set version and per-subject state commitments**
  (ported from the OBSERVE fork, finding I-3 of its resilience assessment).
  The core's calibration surface is now named constants
  (`REGIME_DISTRIBUTION_BANDS`, `REGIME_CRITICAL_FLOOR_DEFAULT`,
  `DRIFT_CRITICAL_FLOOR`, `ESCALATION_DWELL_THRESHOLD`,
  `ESCALATION_LOCK_SECONDS`, joining the existing norms, bounds and drift
  sigma) collected in `PARAMETER_SET`; its SHA-256, `PARAMETER_SET_VERSION`,
  is stamped on every `FusedVerdict` and, with the cassette's name and
  version, folded into the fingerprinted decision payload. Two builds with
  different constants, or different cassettes, no longer produce identical
  fingerprints on identical inputs. Each verdict also carries
  `predecessor_state_commitment` and `state_commitment`
  (`governance_contracts.compute_state_commitment` over the subject and the
  fingerprint, chained per subject; the head is evicted with the LRU policy
  state). Both are recorded in the audit entry, so a verifier can replay
  the fingerprint and the commitment from the stored entry alone.
  Fingerprints of records written before this change do not match the new
  ones. `test_observe_invariants.py` ports the fork's invariants suite
  (42 tests); the seven assertions that encode the fork's partial-assessment
  fault overlay were ported afterwards (see the entry above).

- **Chain and fortress dependencies pinned to commits.** CCC, AUGUR, GEMS,
  Governance_Gateway and fortress-kernel were unpinned, so each install
  took that day's default branch. GEMS `bb4cf40` (2026-09-20) deleted
  `HumanAuthorityGuard`, `HandoffValidator` and `governance/constitution.py`,
  which `gems_governance_adapter.py` imports. From then on every fresh
  `pip install -r requirements.txt` errored the 16 tests in
  `test_gems_governance_adapter.py`, while CI stayed green because it had
  not re-run since 2026-09-11. GEMS is now pinned to `273aeea`, that
  commit's parent. The other four are pinned to the default-branch commits
  the suite passed against on 2026-09-24. Fresh install, then
  `python -m pytest -q`: 681 passed, 5 xfailed (was 665 passed, 16
  errors). `ruff check .` is clean.

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
| NL-FAILOPEN | Clinical escalation continues after governance failure | Historical behavior, superseded by the Unreleased PERCEIVE unavailable outcome above. A governance exception preserves the clinical escalation without setting approval. | clinical_governance_system.py (process_vitals) | TestGovernanceUnavailable |
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
