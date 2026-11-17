# Brief for counsel: erasing a patient from tamper-evident records (decision D14)

**Written by engineering for a lawyer and a privacy officer. This is not legal advice and decides nothing.** It states what the software
stores, why a normal "delete" does not work on it, which designs engineering could build, and the questions only counsel can answer first.
Facts below are INSPECTED in the source unless marked otherwise; designs are PROPOSED and nothing in the repository implements them.
Background: `DATA_PRIVACY_AND_RESEARCH.md`.

## 1. The conflict in one paragraph

The audit records are hash-chained on purpose: every entry commits to the one before it, so any edit or deletion of an old entry is
detectable. That tamper evidence is what lets an auditor trust the record of why a child was or was not paged. It also means a patient
cannot be erased by deleting their rows: removing or rewriting an entry breaks every later link. The records hold patient identifiers
(and, in one store, raw vitals and age). So the two goals, "provably unaltered" and "erasable on request", collide, and the choice
between them is partly a legal one.

## 2. What is stored and where

| Store | Holds | Chained | Persistent |
|---|---|---|---|
| OBSERVE audit ledger | patient id, raw vitals, exact age, caller context | yes (SHA-256) | memory, plus an owner-only archive file when bounded (PR 18) |
| PERCEIVE audit ledger | request snapshot including the patient (`subject_id`) and context | yes | append-only file, mode 0600 |
| Receipts | the whole orchestrator result: request, context, identity, verdict | yes, optionally signed | append-only file, mode 0600 |
| Escalation ticket log | patient id, regime, rules, audit hashes (no raw vitals) | yes, optionally signed | append-only file, mode 0600 |
| Alert payloads | patient id, stage, request ids | no | wherever the pager system keeps them |
| Log lines | a keyed pseudonym tag (PR 18); exception text can still carry other identifiers | no | whatever the host's log system keeps |
| HIPAA CSV exporter output | a pseudonym, hour-level time, regime, risk | no | wherever it is saved |

Not present: encryption at rest, per-patient keys, any retention limit, any lookup by patient for access or erasure requests (the GDPR
report function only prints counts and says requests are "serviceable via patient_id lookup"; it has no such logic). Whether a deployment
encrypts its disks, keeps backups or copies logs elsewhere is unknown to engineering.

## 3. Designs engineering could build (PROPOSED)

| Option | How it works | What it achieves | Costs and risks |
|---|---|---|---|
| **A. Crypto-shredding per patient** | Identifying and clinical fields are encrypted under a key per patient; the chain hashes the ciphertext. "Erase" means destroying that patient's key | The chain still verifies after erasure; the record shows an entry existed, not whose it was or what it held | Key management becomes the critical system: a lost key is permanent loss, a leaked key defeats erasure, backups of keys defeat it. Audit reads need the key, so the auditor loses sight of erased patients by design. Ciphertext may still count as personal data in some views (question 4) |
| **B. Separate erasable identity table** | Chained records carry only a pseudonym; a separate, mutable table maps pseudonym to patient | Erasure deletes the mapping; the chain is untouched | Re-identification risk from the remaining fields (raw vitals, times, rare ages); the table becomes the sensitive asset; pseudonym custody and collisions (48-bit today) must be solved |
| **C. Retention-limited sealing** | After a set period, old chain segments are exported to cold storage, checkpointed (their end hash is recorded in a signed anchor) and then destroyed or access-restricted | Bounds how long identifiers exist anywhere; the live chain still verifies from the anchor | Does not answer an erasure request inside the retention period; the anchor must be trusted |
| **D. Legal exemption instead of a technical fix** | Counsel concludes that these records fall under a medical-record retention or public-health exemption, so erasure does not apply | No engineering | Depends entirely on jurisdiction and facts; may not cover research use |

These can be combined (for example B for new data and C for retention). Engineering will build whatever counsel and the privacy officer
select; engineering recommends asking counsel first, because the right design depends on the answers below (the decision register says
"C first; then A").

## 4. Questions only counsel can answer

1. **Does a right to erasure apply at all** to these records, in the jurisdictions the hospital operates in, given medical-record retention
   duties? If so, to which stores (all of section 2, or only some)?
2. **What counts as erased?** Destroying a key? Removing the mapping to an identity? Do pseudonymous or ciphertext records still count as
   personal data?
3. **What retention period** applies to each store, and may a patient's records be erased inside it?
4. **Backups and copies:** are they in scope, and by what deadline?
5. **Research data:** if records were exported to a research enclave (`DATA_PRIVACY_AND_RESEARCH.md` section 5), does erasure reach those
   releases, and who holds the obligation?
6. **Evidence:** what must the hospital be able to show an auditor or the patient that an erasure happened (a signed record of the key
   destruction? a re-run of the drill D9 in section 4 of the privacy document)?
7. **Who decides** a conflict between an erasure request and a legal hold or an open safety investigation?

## 5. What engineering can promise now

- Nothing in the repository meets an erasure request today; do not state otherwise to patients or regulators.
- Whatever is chosen will be tested by an erasure drill (privacy document, test D9): erase one patient, then check every store, export
  and backup for any trace while confirming the chains still verify.
- Until a decision, keep identifiers out of places they need not be (done for logs in PR 18) and keep the files in a controlled,
  encrypted environment.

Return: a written answer to section 4, and which option or combination to build.
