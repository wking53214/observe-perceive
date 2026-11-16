# Data classification, de-identification and research use

Evidence classes: **EXECUTED** (I ran it; output quoted), **INSPECTED** (read in source at the line named), **PROPOSED** (a design or
policy; nothing in the repository enforces it). This document covers the data classification and handling policy, the
privacy and de-identification architecture, the de-identification failure test plan, the hospital-to-research data pipeline and
research LLM governance, in one place because each depends on the one before it. **Nothing here is legal or compliance advice, and
no clause below has been reviewed by a privacy officer, an IRB or counsel.** Where a decision belongs to one of them it says so.

## 1. What patient data exists and where (INSPECTED, with the canary probe EXECUTED)

A canary patient id (`CANARY-PT-7731`, a CRITICAL reading with age) was run through `ClinicalGovernanceSystem` with the default
single-hospital build, and every output was searched for it.

| Place | Holds | Medium | Grows? | Redaction | Canary found |
|---|---|---|---|---|---|
| OBSERVE ledger (`observe_consolidated.ImmutableAuditLedger`) | `patient_id`, raw heart rate, SpO2, respiratory rate, temperature, `age_months`, and the history series the caller put in `context` | memory only (no file) | yes, no eviction | none (a SHA-256 chain gives tamper evidence, not privacy) | yes, with raw vitals |
| OBSERVE `export_json()` | the whole ledger | string returned to the caller | per call | none | yes, with raw vitals |
| `export_observe_audit()` | `patient_id`, timestamp, action, hash; vitals omitted | returned list | per call | the `data` field is dropped; the id is not | yes, no vitals |
| PERCEIVE audit ledger (`perceive_consolidated`) | `request_snapshot` with `subject_id` (the patient) and the request context | append-only JSONL, mode 0600 | yes | none | yes |
| `export_perceive_audit()` | the same | returned list | per call | none | yes |
| Receipts (`governance_record.ReceiptLog`) | the whole orchestrator result as plain data: request, context, identity, verdict | append-only JSONL, mode 0600, optional signature | yes | none (`record_hash` is integrity only) | not run here (needs the orchestrator); INSPECTED |
| Escalation ticket log (`human_escalation.EscalationLog`) | `patient_id` in the ticket context, regime, risk, rules, audit hashes (no raw vitals, by design) | append-only JSONL, mode 0600, fsync | yes, no pop or delete found | none; `acknowledgement_digest` holds no patient data | not run here; INSPECTED |
| Alert payloads (`alert_human` callback, desk channels) | `patient_id`, stage, request and attempt ids | callback argument | per alert | none | yes |
| Log lines (`logger`) | `patient_id` or `subject=` in at least these: `observe_consolidated.py:456, 1278, 1281, 1829, 1832, 1878`; `clinical_governance_system.py:283, 340, 354, 378` and more | whatever sink the host configures | depends on the sink | none | **yes: 2 of 11 captured lines** (`ESCALATION BLOCKED for CANARY-PT-7731 ...`) |
| Per-patient engine state (`_patient_policies` and four sibling dicts) | `patient_id` keys | memory | capped (`max_tracked_patients`, default 10000, LRU) | none | n/a |
| `compliance_exporters.HIPAAExporter.export_csv` | `PT-` pseudonym, hour-level time, action, risk, regime, a 16-char audit hash prefix | CSV text | per call | **pseudonymised and coarsened** | **no** (EXECUTED: header `pseudonym,date_hour,action,risk_score,regime,escalation_required,audit_hash`) |

Facts worth stating plainly:

- **The only de-identified output in the repository is the HIPAA CSV exporter.** Every ledger, receipt, ticket log, alert and
  most log lines carry the raw patient id, and the OBSERVE ledger carries raw vitals and age.
- **Persistent files are protected by file mode (0600) only, and only when this code creates them** (`os.open(..., O_CREAT, 0o600)`; a pre-existing file keeps its old mode). No encryption at rest, no key management, no retention limit.
- **PERCEIVE's audit hash does not cover the request snapshot** (`perceive_consolidated.py:626-634` hashes `audit_id`, `timestamp`,
  `evaluated_gates`, `final_verdict`). The patient id and the request context in that ledger are therefore not tamper-evident by
  that chain, though the request commitment in the orchestrator record does cover the context (since #83 and #86). See the register.
- **`context` is free-form caller data** and is copied into ledgers and receipts. Nothing prevents a caller putting a name or a
  free-text note in it.

## 2. Classification (PROPOSED)

| Tier | What | Examples in this repository | Default handling |
|---|---|---|---|
| **T0 public-safe** | no person-level data | counts, regimes, settings digests, parameter-set version, cassette version, hashes of readings, compliance summary | may leave the controlled environment |
| **T1 pseudonymous** | a stable pseudonym, coarsened time, no free text | the HIPAA CSV | research use under the pipeline in section 5 |
| **T2 identifiers** | `patient_id`, request/attempt/ticket ids that join to a patient, alert payloads, ticket context | ticket log, receipts, PERCEIVE ledger, alerts, most log lines | controlled environment only; access logged |
| **T3 clinical content** | raw vitals, exact age, history series, free-form `context` | OBSERVE ledger and its export, receipts' request context | controlled environment only; strictest access; never in logs or LLM prompts |

A request id equals the artifact id, which an integrator may derive from a patient or encounter: **treat ids as T2 until the
integrator documents that they are not.** (The open question about the ticket log's classification is the same question.)

## 3. De-identification architecture (what exists, what is missing)

**Exists (INSPECTED):** `pseudonymize_patient_id` returns `PT-` plus 12 hex of SHA-256(`salt:patient_id`); the salt must be passed or
set in `OBSERVE_DEID_SALT` and the code refuses to run with a guessable default; `deidentify_timestamp` coarsens to the hour.

**Gaps, each PROPOSED to close and none done:**

1. **Pseudonym length.** 12 hex characters is 48 bits. By the birthday bound a collision between two patients becomes likely around
   2^24 (about 16.7 million) pseudonyms, which is within reach of a large network over years. Longer pseudonym, or check for
   collisions at export.
2. **Construction.** A secret-prefixed SHA-256 is not a keyed MAC; HMAC-SHA-256 is the standard construction. (Length extension does
   not help an attacker here, but a reviewer will ask.)
3. **Salt custody and rotation.** One salt per controller keeps pseudonyms linkable across exports (intended) and makes the salt the
   single secret whose loss re-identifies everyone. There is no rotation procedure and no separation between the salt holder and the
   analyst.
4. **Quasi-identifiers.** Hour-level time plus a rare age and a rare regime can re-identify. The CSV drops age, but nothing measures
   re-identification risk (k-anonymity or equivalent) before export.
5. **The CSV is the only exporter that de-identifies.** `export_json`, `export_observe_audit`, `export_perceive_audit` are the easy
   ones to call, and they do not.
6. **Erasure against an immutable ledger.** `GDPRExporter.export_report` takes only counts and prints that access and erasure requests are
   "serviceable via patient_id lookup"; it has no lookup logic. The ledgers are append-only and hash-chained, so deleting an entry breaks the chain by design. A right-to-erasure request cannot be
   met by editing them. The honest options (**a decision for counsel, not for code**): keep identifiers out of the chained records and
   hold them in a separate erasable table keyed by the pseudonym; or crypto-shred per patient (encrypt T2/T3 fields under a per-patient
   key and destroy the key). Neither exists.
7. **Log lines.** Patient ids reach logs (EXECUTED above). A redacting log filter, or logging a pseudonym, is a one-file change but a
   deployment decision about where the real id is needed for operations.

## 4. De-identification failure test plan (PROPOSED; each test names the failure it would catch)

Run with a canary id, a canary age and a canary free-text value in `context`, through the real pipeline:

| # | Test | Passes when | Today |
|---|---|---|---|
| D1 | Search every exporter output (CSV, JSON, report text) for the canary id, canary age, canary text | none found in any T1 output | HIPAA CSV: passes for id (EXECUTED); age and free text not yet tested |
| D2 | Search all log output at DEBUG for the canary | none found, or only a pseudonym | **fails** (EXECUTED: 2 of 11 lines) |
| D3 | Pseudonym stability: same id and salt give the same pseudonym; a different salt gives a different one; no salt raises | all three | exists in code; test coverage not audited |
| D4 | Collision check at export: two ids map to one pseudonym | export refuses, or lengthens the pseudonym | **no check** |
| D5 | Salt absent or empty at export time | refuses | passes (code raises) |
| D6 | Quasi-identifier scan: for each output row count rows sharing (pseudonym-free) tuple `(date_hour, regime, action)`; flag any tuple with fewer than k rows | no tuple below the k the privacy officer sets | **not implemented; k is not chosen** |
| D7 | Free-text leakage: put a canary in `context["note"]`, export via every route | canary absent from T1 outputs | not tested; `context` is copied into receipts (INSPECTED) |
| D8 | Re-identification by join: join the CSV to a synthetic roster on (hour, regime) | cannot isolate individuals above the risk bound | not implemented |
| D9 | Erasure drill: erase one patient, then verify every store, export and backup | no T2/T3 trace; chains still verify | **cannot pass** with the current chained records (see 3.6) |
| D10 | Retention: a file older than the retention period is removed or sealed | enforced by a job | **no retention exists** |

## 5. Hospital to research data pipeline (PROPOSED; no pipeline exists in this repository)

Trust zones: **Z1 clinical** (OBSERVE, PERCEIVE, the desk; T2 and T3 live here) then **Z2 de-identification service** (holds the salt;
the only component allowed to read T2/T3 for export) then **Z3 research enclave** (T1 only; analysts never see Z1).

1. A research request names a purpose and a cohort rule; the privacy officer and, where required, the IRB approve it. **The approval
   record is outside this library.**
2. Z2 pulls the cohort from Z1 by pseudonym rule, applies sections 3 and 4 (D1 to D8 must pass), and writes a **data release
   manifest**: cohort rule digest, row count, settings digest and parameter-set version in force when each row was made (these exist today in the records), the
   exporter version, the test results, the approver ids.
3. Z3 receives T1 and the manifest only. The manifest's digest is recorded in Z1 so a release is traceable to what left.
4. Nothing flows from Z3 back into Z1 decisions without the model change control in `CHANGE_CONTROL_AND_VALIDATION.md`.

## 6. Research LLM governance (PROPOSED; nothing in this repository calls an LLM)

An LLM used on this data is an outside processor of whatever it is shown.

- **Input boundary:** only T0 and T1. Never T2 or T3, never raw `context`, never ticket text. Enforce by an allow-list of fields, not a
  deny-list.
- **No clinical authority:** an LLM output never sets a regime, a page, a threshold or a setting. It may draft analysis a human reads.
- **Provenance:** every prompt and output is stored with the digest of the exact input, the model identifier and version, and the
  manifest digest of the release it saw; a result without these is not citable.
- **Vendor terms:** retention, training-on-inputs and data location are contract questions for counsel; the default stance is "not
  permitted to train on or retain inputs".
- **Evaluation before use:** a fixed set of synthetic, canary-seeded cases (section 4) shows the allow-list holds before any real
  release; any leak is a stop.
- **Review:** a named person reads each output used outside the enclave.

## 7. What I could not determine

- Whether any real deployment sets `OBSERVE_DEID_SALT`, configures log redaction, or encrypts the disk the JSONL files sit on.
- The retention periods, k for the quasi-identifier test, and the IRB and contract terms: all decisions for people, not code.
- Whether `request_id` / artifact ids ever encode a patient: depends on each integrator.
