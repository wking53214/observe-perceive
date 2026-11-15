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
| `request_id` is the artifact id, so a refused attempt and its retry are indistinguishable by id | Every `orchestrate_request` call has an **`attempt_id`** (random UUID by default; `attempt_id_factory=` to set it). It is on the result (inside the hashed record, so it cannot be changed afterwards), the receipt envelope (`ReceiptLog.find(attempt_id=)`), every alert payload and every ticket. `request_id` is unchanged. A nested call (an executor that calls the orchestrator again) does not change what the outer call records. |
| The action can be about patient A while the assessed vitals are patient B, in advisory and strict | The request is **refused** before PERCEIVE, in every profile, with `refused_by: "identity"` and an `identity_mismatch` record naming what the context claimed and the vitals' patient. The request is built first, so the record names the artifact and can be audited. Nothing runs. It is **not** an ordinary refusal: it means an integration is sending one patient's data with another's request, so **a person is told** (stage `identity`, naming the vitals' patient). And the reading is **still assessed** and, if it is an escalation, paged for the patient the vitals are for, so a deteriorating child is not lost with the refused request. |
| With no patient in the context PERCEIVE's subject was the artifact, so per-patient limits never accumulated | With vitals and no named patient (and no `subject_id` entity), the vitals' patient is written into the (copied) context, so it is **committed** and becomes PERCEIVE's subject. A `patient_id` the caller gave is committed as **text** (`5` and `"5"` are one patient), so limits do not split. Measured: three requests for one patient count 3 in that patient's daily window (before: 0). |
| The request commitment was identical for calm and CRITICAL vitals | `observation_digest` (a hash of every field of the snapshot, in the canonical form the other commitments use) is in the request context, so the commitment, and the PERCEIVE ruling that carries it, cover **which reading**. A caller-supplied digest must match (compared without case or surrounding space). |

**Only `patient_id` is compared with the vitals.** PERCEIVE gives `patient_id` precedence over `subject_id`, and five adapters (approval target, GEMS task, herald and TIE source) put a **non-patient entity** in `subject_id`. A first version compared both and refused those adapters; an independent review found it. Now `subject_id` is never compared, a request that names only an entity is left as it was (PERCEIVE reasons about the entity; nothing is invented for it), and `identity` records both what was committed and the `observation_patient`.

Every result records `identity: {patient_id, subject_source, observation_digest, observation_patient}`, where `subject_source` is `context`, `vitals` or `artifact`. A context that is not a mapping is refused the same way (it used to crash).

## The three things the design note left unverified

- **"An `attempt_id` in the hashed record would break canonical replay": false.** `to_record` hashes the whole result, and the handoff
  already carries `issued_at`, which differs on every run, so two runs' records are never byte-identical anyway. `attempt_id` is inside
  the record, which is better: the record hash now authenticates it.
- **"Record verification stays consistent when a digest is added to the context": true.** Verification recomputes from the record; every
  existing chain and receipt test passes unchanged, and a receipted record verifies from the file alone (`test_a_receipted_record_with_the_new_fields_verifies_from_the_file_alone`).
- **"Whether the later approvals chain to the request commitment": still not checked.** What is bound is the request commitment and the
  PERCEIVE ruling that carries it. Whether the Conservation approval and the execution approval chain to that commitment is part of 10b.

## What the review found in the first version, all fixed

- **Blocker: a CRITICAL reading was dropped with no page.** The refusal returned before OBSERVE, so a deteriorating child's reading reached nobody. Now it is assessed and paged (see above).
- **Blocker: ordinary adapter calls were refused.** The `subject_id` rule above.
- **A reading that cannot be copied crashed the request with no record** (a lock in `vitals.context`; a duck-typed reading with `__slots__`). The digest now reads fields one by one, never raises, and marks a reading it cannot digest as `undigestable:<type>`.
- **A nested call overwrote the outer record's attempt id and identity.** Saved and restored around every call.
- **An identity refusal could not be audited** (`request.present` failed for every one). Now it carries its request.
- Smaller: a `patient_id` of `0` selected different subjects in two places; the same id as an int and as text split a patient's limits; both fixed by committing the id as text.

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
- **The check applies to readings that carry a `patient_id`.** The industrial cassette's readings have an `asset_id` and are not checked; binding by the engine cassette's own `subject_id` is a follow-up.
- **Exact text.** `"P001 "`, `"p001"` and `"P001"` are three different patients: an integration with sloppy ids will be refused loudly, which is the point.
- **No verifier recomputes `observation_digest` from the reading OBSERVE evaluated**, so the record cannot yet prove which reading was used.
- **The digest compares dictionary keys as text** (`{1: x}` and `{"1": x}` share a digest).
- **The assessment is not yet bound**, only the input reading. A request cannot yet say "approved because OBSERVE found X".
- The CNS gates in `cns_governed_decision.py` are unchanged, as decided.
