# Identity binding, step 10a: what is implemented

Status: implemented in `governance_orchestrator.py`, `governance_chain.py`, `governance_record.py` and `clinical_notification.py`
(PR 10a). The design, the measurements and the options are in `IDENTITY_BINDING_DESIGN.md` (PR 10); the three decisions below were
yours. Evidence class: EXECUTED by `test_identity_binding.py` (22 tests; 17 fail on the previous behaviour, the other 5 are
no-regression checks) and a mutation check of 33 changes with none surviving.

## Your decisions, as implemented

1. **An identity mismatch refuses in every profile**, advisory included.
2. **Staged:** this is 10a. Running OBSERVE first in every profile, and binding the assessment (10b), is a separate change.
3. **The reading's digest is bound into the request commitment.**

## What changed

| Finding measured on `main` | Now |
|---|---|
| `request_id` is the artifact id, so a refused attempt and its retry are indistinguishable by id | Every `orchestrate_request` call has an **`attempt_id`** (random UUID by default; `attempt_id_factory=` to set it). It is on the result (inside the hashed record, so it cannot be changed afterwards), the receipt envelope (`ReceiptLog.find(attempt_id=)`), every alert payload and every ticket. `request_id` is unchanged. |
| The action can be about patient A while the assessed vitals are patient B, in advisory and strict | The request is **refused** before PERCEIVE, in every profile, with `refused_by: "identity"` and an `identity_mismatch` record naming what the context claimed and the vitals' patient. Nothing runs, OBSERVE never sees the vitals, and nobody is paged (a refusal, not a crash). It also refuses a context that names two different subjects (`patient_id` and `subject_id`), and a caller-supplied `observation_digest` that is not the digest of the vitals. |
| With no patient in the context PERCEIVE's subject was the artifact, so per-patient limits never accumulated | With vitals and no named patient, the vitals' patient is written into the (copied) context, so it is **committed** and becomes PERCEIVE's subject. Measured: three requests for one patient now count 3 in that patient's daily window (before: 0, each keyed by its own artifact). |
| The request commitment was identical for calm and CRITICAL vitals | `observation_digest` (a hash of every field of the snapshot, in the canonical form the other commitments use) is in the request context, so the commitment, and the PERCEIVE ruling that carries it, cover **which reading**. Calm and CRITICAL vitals now give different commitments; the same reading gives the same one. |

Every result also records `identity: {patient_id, subject_source, observation_digest}`, where `subject_source` is `context`, `vitals` or
`artifact`. With no vitals and no named patient the subject is still the artifact, and the record now says so.

## The three things the design note left unverified

- **"An `attempt_id` in the hashed record would break canonical replay": false.** `to_record` hashes the whole result, and the handoff
  already carries `issued_at`, which differs on every run, so two runs' records are never byte-identical anyway. `attempt_id` is inside
  the record, which is better: the record hash now authenticates it.
- **"Record verification stays consistent when a digest is added to the context": true.** Verification recomputes from the record; every
  existing chain and receipt test passes unchanged, and a receipted record verifies from the file alone (`test_a_receipted_record_with_the_new_fields_verifies_from_the_file_alone`).
- **"Whether the later approvals chain to the request commitment": still not checked.** What is bound is the request commitment and the
  PERCEIVE ruling that carries it. Whether the Conservation approval and the execution approval chain to that commitment is part of 10b.

## Existing tests edited (each because it contained the mismatch this step now refuses)

Six tests passed a context patient that disagrees with the vitals' patient (`P001` from `make_vitals()`), incidentally: their assertions are
about OBSERVE ordering and crash handling. Only the patient argument changed, to `P001`: `test_audit_p5_fixes.py` (five calls: `p5-ok`,
`p5-op1`, `p5-op2`, `p5-op2b`, `p5-op3`) and `test_observe_crash.py` (`oc-6`). No assertion changed.

## Behaviour changes callers will see

- A request whose context patient differs from the vitals' patient is refused (it used to execute).
- With vitals and no named patient, the request's context (and so its commitment) now contains `patient_id` and `observation_digest`.
- Any request with vitals has a different commitment from before.
- `orchestrate_request` no longer mutates the dict it was given (it used to remove `gateway_admission` from it).
- Comparison is exact text: `"p001"` and `"P001"` are different patients, and `5` and `"5"` are the same.

## Limits

- **The patient is only as trustworthy as the caller who names it.** This binds the request's own statements to each other; it does not
  authenticate who asked, and it cannot tell that two coherent-looking ids are the wrong patient.
- **OBSERVE still runs after PERCEIVE and Conservation** (before the action in strict, after it in advisory), so a request refused earlier
  has no assessment and no notification. That is 10b.
- **The assessment is not yet bound**, only the input reading. A request cannot yet say "approved because OBSERVE found X".
- The CNS gates in `cns_governed_decision.py` are unchanged, as decided.
