# Production readiness: scorecard, roadmap, open decisions and unknowns

Status date: 2026-11-15, `main` after #86 is merged (this document assumes #85 and #86 merge; both have green CI). Evidence classes:
**EXECUTED**, **INSPECTED**, **PROPOSED**. Companion documents (all in `docs/race/`): `CLINICAL_SAFETY_AND_VALIDATION.md`,
`CHANGE_CONTROL_AND_VALIDATION.md`, `DATA_PRIVACY_AND_RESEARCH.md`, `ADVERSARIAL_REGISTER.md`.

## 1. The answer

**This is not ready for clinical use, and none of the work so far changes that on its own.** What has been built is a governed,
auditable, fail-loud engineering core: escalation reaches a person and is followed up; failures are told, not swallowed; decisions are bound
to the patient, the reading and the assessment; clinicians can set the lines. What does not exist is **evidence that the clinical rules are
right**: no outcome-labelled validation, no clinician sign-off on any rule, no measured alarm burden, and a measured design property (a single
abnormal channel rarely pages) that a clinician must judge. Engineering can make the system safe to *evaluate*; only clinicians and a
validation study can make it safe to *rely on*.

Rating scale: **0 absent, 1 partial, 2 built and tested (engineering evidence), 3 independently verified in operation, 4 validated for clinical use.**
Nothing is above 2 and most clinical rows are 0 or 1. A reader should not average these.

## 2. Scorecard

| # | Area | Rating | Evidence | What holds it back |
|---|---|---|---|---|
| 1 | Escalation reaches a person | 2 | #74, #78, #81; settings, desk tiers, signed acknowledgements, sweep runner, watchdog; 1492 tests | never run against a real pager, real latency, or a soak test (unknown); a CRITICAL is BLOCKED (and paged) unless the integration supplies `physician_approved`, by design (D3) |
| 2 | Failure handling | 2 | #80 transactional evaluate; crash told per profile (#86); faulted channels never read as stable | advisory still acts on no assessment (D12) |
| 3 | Decision integrity and audit | 2 | #83, #86: attempt id, patient binding, reading digest, assessment bound and verified against OBSERVE's ledger; receipts verify from file | PERCEIVE audit hash excludes the snapshot (C4); the caller's identity is not covered (C14); the request commitment is unkeyed |
| 4 | Clinician control and change control | 1 | #84 settings with digest and weakening flags | constants outside `PARAMETER_SET` (C3); no release process; no clinical owner named |
| 5 | Clinical detection quality | 0 | the pinned detection record is the engine's own admission: 3 missed scenarios, 4 unsafe fault cases (C1); nine probe rows (C17) | no validation study exists |
| 6 | Clinical rule approval | 0 | 33 register rows, none approved | needs the clinical owner |
| 7 | Alarm burden | 0 | none measured; no metric function (only `open_tickets()`) | needs a report over the ticket log and a unit pilot |
| 8 | Privacy and data protection | 1 | HIPAA CSV exporter pseudonymises; salt required; files 0600 | raw ids in logs, ledgers, tickets (EXECUTED); no encryption at rest, retention or erasure path; 48-bit pseudonym |
| 9 | Security of the control plane | 1 | optionally signed tickets, acknowledgements and receipts (the signing scheme is the Conservation Kernel's `signing` package, which is not in this repository, so I did not verify it is HMAC); one-writer lock | no authentication of callers (C14); keys and salt custody are the deployer's |
| 10 | Operations | 1 | sweep runner with watchdog; settings from a file | OBSERVE ledger is memory-only and unbounded (C7); no deployment, monitoring, backup, or capacity evidence; per-patient state capped at 10000 |
| 11 | Test evidence | 2 | 1492 passing tests on the #86 branch (1449 on `main` before #85 and #86), future-clock pass, ruff, mutation checks per PR, independent reviews | two test files I could not run locally (CI runs them); tests are engineering regression, not clinical |
| 12 | Regulatory and quality system | 0 | not assessed | whether this is a regulated medical device is a decision for regulatory counsel; I assumed nothing |
| 13 | Documentation and training | 1 | design notes and PR documents exist per change; this set | no runbooks, no clinician-facing guide, no training |

## 3. Roadmap (PROPOSED; ordered by what unblocks the most risk)

**Phase 0: decisions only (no code).** Name the clinical owner. Answer D1 to D8 below. Decide whether this is evaluated in silent mode first.

**Phase 1: make it safe to evaluate (engineering, small PRs, each with a table of what changed).**
1. Put every clinical constant into `PARAMETER_SET`, record the source revision (C3).
2. Alarm-burden report from the ticket log (read-only) (scorecard 7).
3. Include the request snapshot in PERCEIVE's chain hash (C4).
4. Require clinical settings in the production profile (C11); a bounded or rotating OBSERVE ledger sink (C7).
5. Log redaction and a pseudonym in log lines (C12); pseudonym length and collision check; HMAC construction.
6. Fixtures for the nine probe rows and the three missed scenarios, marked as what they are.

**Phase 2: clinical.** The validation plan (`CLINICAL_SAFETY_AND_VALIDATION.md` section 2): event definitions, de-identified replay, silent mode, sign-off per register row. Age bands 3 to 18 as an output of this phase, not a guess before it.

**Phase 3: operate.** Pilot on one unit with a named responder group; measure burden and acknowledgement; adjust settings through change control; runbooks and training; a regulatory determination before any claim of clinical use.

## 4. Open decisions (lettered; my recommendation first, reason after)

| ID | Decision | Options | Recommendation |
|---|---|---|---|
| D1 | Hard-rule confirmation window | A keep 3 (waits for one repeat); B set 1 (every SpO2 < 88 holds WARNING at once) | **B for a first evaluation unit**, because a lone SpO2 < 88 is a possible emergency and the cost is some probe-off pages; keep 3 only if the unit's burden says otherwise. A clinical call. |
| D2 | Heart rate alone counts as corroboration of a hard rule | A yes (today); B no, require RR or temperature | **B**: heart rate usually shares the probe with SpO2, so it is weak corroboration. |
| D3 | A CRITICAL is BLOCKED unless the integration supplies `physician_approved` (by design: a patient-safety override needs a physician). Keep that, or change the policy | A keep, and the integration supplies approval; B change the policy | **A, plus page either way (true since #78)**: it is a conscious clinical-governance rule, documented in the register (C5). Answered: "page, and decide the block as policy". |
| D4 | (Corrected.) Is a CRITICAL exempt from the escalation count | It already is on the CRITICAL path: `emergency_override` has no rate gate by design, and the count is advisory by default and never read from the manifest. The remaining gap is a CRITICAL arriving as `escalate_patient` | **Exempt it there too** (answered yes). My original framing, that a count could refuse a CRITICAL, was wrong. |
| D5 | Classify the patient id in the ticket log | A T2 identifier; B T1 | **A** (`DATA_PRIVACY_AND_RESEARCH.md`). |
| D6 | A failed `STATE_EVICTED` write fails another patient's evaluation | A best-effort (log and continue); B fail | **A**: one patient's eviction record must not block another's assessment. |
| D7 | Adults and older adolescents | A keep the floor (WARNING plus finding); B build an adult cassette; C raise `max_age_months` per unit | **A now, B only if the hospital will monitor adults here** (a clinical cassette needs its own sign-off). |
| D8 | The nine single-channel rows | A accept as designed; B add single-channel WARNING rules for specific danger signs; C more engines or history | **Clinical owner decides row by row**; I would expect B for the severe rows (HR 45 or 200, SpO2 below the age norm, T 34.5, RR 70), but I am not the clinician. **Status: the review sheet is prepared (`CLINICIAN_REVIEW_SHEET.md`) and each row is pinned by `test_single_channel_behaviour.py`; waiting for the clinical owner's marks.** |
| D9 | Constants into the version stamp | Answered yes, and done in PR 17 as `logic_version` (a digest of the scoring code's behaviour, not a list of constants) | Done. |
| D10 | PERCEIVE hash covers the snapshot | Answered yes, and done in PR 17 (`hash_version` 2; older entries load unchanged) | Done. |
| D11 | Production profile requires settings, log redaction, a bounded ledger | Answered yes, and done in PR 18 | Done. |
| D12 | Advisory proceeds when OBSERVE crashes | A yes (told, committed as unavailable); B refuse | **A for advisory, B is strict**; a deployment picks its profile. |
| D13 | Strict judges attestation before assessing | A build the request twice; B accept | **B** until a threat model shows the extra state movement matters. |
| D14 | Right to erasure against immutable chains | A crypto-shred per patient; B separate erasable id table; C counsel decides | **C first**; then A. **Status: the counsel brief is prepared (`COUNSEL_BRIEF_ERASURE.md`); waiting for counsel.** |
| D15 | Who is the clinical owner and what is their authority | a named role | a prerequisite for everything in Phase 2. **Status: the role description is prepared (`CLINICAL_OWNER_ROLE.md`); nobody is named.** |

## 5. Unknowns I could not resolve

- Whether any deployment exists, with which profile, settings, pager and persistence.
- Real alarm burden, real acknowledgement times, real false alarm rate: no data.
- Whether OBSERVE state survives a restart in practice (the ledger is memory-only; per-patient tracked state is too).
- The regulatory status and the hospital's risk-management process.
- Whether the two test files that only CI runs (`test_cns_governed_decision_demo.py`, `test_gems_governance_adapter.py`) cover anything this document depends on.
