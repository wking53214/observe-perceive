# Identity binding: design note

Status: DESIGN ONLY. No code changes in this document's PR. Decisions are listed at the end; nothing here is implemented until
they are answered. Evidence classes: EXECUTED (I ran it and the output is quoted), INSPECTED (read in source), PROPOSED.

## The question

When the orchestrator governs "escalate patient P", which things is that decision actually *about*, and does anything stop the
pieces from disagreeing? A governance decision is only worth recording if it is bound to the thing it judged. Today it is bound to
the **artifact and its context**, and to very little else.

## What is bound today

| Thing | Where it comes from | Covered by the request commitment? |
|---|---|---|
| The artifact id and its content hash | the artifact | yes |
| The context (including `patient_id` if the caller put one there) | the caller's dict | yes |
| The event time (and who attested it) | the artifact | yes |
| The request id | `request_id = artifact.artifact_id` (INSPECTED: `sentinel_perceive_adapter.py:81`) | yes, but it is the artifact id |
| The patient PERCEIVE reasons about | `context.patient_id`, else `context.subject_id`, else the **artifact id** (INSPECTED: `governance_request_to_policy_request`) | only if the caller supplied it |
| The vitals OBSERVE assesses | the `vitals_snapshot` argument | **no** (INSPECTED: `governance_chain.request_state` lists no observation) |
| OBSERVE's assessment | computed after PERCEIVE and Conservation (strict: just before the action; advisory: after it) | **no** |

## What I measured

All EXECUTED against `main` at `abc80ab`, with the real orchestrator (advisory unless stated).

1. **A refused attempt and its retry share one id.** Two requests on artifact `ART-1`: the first executed, the second was refused by
   the Conservation Kernel ("duplicate artifact"). Both carry `request_id = "ART-1"`, and `ReceiptLog.find(request_id="ART-1")` returns
   **2** receipts. Alerts and tickets are keyed by that id too. (The kernel does stop a second *execution* on one artifact, so
   this is not a double-execution bug; it is that attempts cannot be told apart by id, and that the request's identity is the artifact's.)
2. **The action can be about one patient and the assessment about another.** Context `patient_id = "PATIENT-A"`, vitals for
   `"PATIENT-B"` (critical): status `APPROVED_AND_EXECUTED`; the request names A, OBSERVE assessed only B (regime `critical`), and
   **no field in the result names the mismatch**. The **strict** profile does the same: `APPROVED_AND_EXECUTED`, order
   `['observe', 'executed']`, regime `critical`.
3. **With no patient in the context, PERCEIVE's subject is the artifact.** Request with vitals for `PATIENT-C` and no `patient_id`:
   `PERCEIVE subject_id used: ART-3`. PERCEIVE's escalation limits are keyed by `subject_id` (INSPECTED:
   `perceive_consolidated.py` `escalation_window_counts(request.subject_id, ...)`), so a patient's daily and hourly limits **never
   accumulate** when each request is a new artifact; they accumulate per artifact, which is one request.
4. **The request commitment does not cover the observation.** Same artifact, same context, vitals calm vs CRITICAL: the request's
   `state_commitment` is **identical** (`True`); the regimes were `stable` and `critical`. So the commitment and the PERCEIVE ruling that carries it say nothing about which reading, or which patient's, justified the action
   (whether the Conservation approval chains to it is unchecked; see D3).
5. **Advisory runs the action, then OBSERVE.** Order `['ACTION', 'OBSERVE']`; the action ran, then OBSERVE said `critical`. Strict
   runs OBSERVE first (OP-1) but a CRITICAL does not gate the action (it notifies; see `CLINICAL_ESCALATION_NOTIFICATION.md`).

Two other things this note was asked to cover, and what I found:

- **A request refused before OBSERVE ran has no assessment and so no notification.** Already a recorded limitation (#78, #80). It is
  a direct consequence of finding 5's ordering, and the same change that fixes the ordering closes it.
- **The CNS OMEGA gates accept hand-built verdicts** (`cns_governed_decision.py`). That layer is optional, is not called by the
  orchestrator, and its gates are caller-supplied code: a gate that lies is a gate you chose to trust. CNS stays a thin contract layer
  (your rule), and its verdict digests are tamper evidence, not authentication. **No change proposed.** Stated here so it is a decision,
  not an omission.

## Options and recommendation

### D1. What identifies one attempt?

| Option | What it does | Cost |
|---|---|---|
| **A. Add `attempt_id` (Recommended)** | A new id per `orchestrate_request` call, on the result, the receipt envelope, and every alert and ticket. `request_id` stays the artifact id. | Additive. I would keep it out of the request commitment and out of the hashed record, because it differs on every run and I expect that putting it in would break canonical replay (to be confirmed in 10a by trying it); it would live in the receipt envelope beside `written_at`. |
| B. Make `request_id` unique per attempt | The id itself changes. | Changes every commitment, PERCEIVE and kernel linkage, and recorded fixtures. Large and breaking. |
| C. Leave it | | The gap stays. |

### D2. How is the patient bound?

Facts to bind: the patient the **action** is about (context), the patient the **observation** is about (vitals), and the subject
PERCEIVE reasons about.

| Option | What it does |
|---|---|
| **A. Derive, then require agreement (Recommended)** | The patient is `vitals.patient_id` when vitals are supplied. It is written into the request context (so it is **committed**) when the caller gave none, and PERCEIVE's subject becomes it (so limits accumulate per patient). If the caller **also** gave a `patient_id` or `subject_id` and it differs, the request is **refused** as an identity mismatch, before PERCEIVE, with both ids in the record. With no vitals and no patient, the subject stays the artifact and the record says `subject_source: "artifact"`. |
| B. Refuse only in strict and production | As A, but the advisory profile records the mismatch and carries on. |
| C. Leave it | |

My recommendation on **where to refuse**: all profiles. A mismatch is never a legitimate request, it is a governance decision
about one patient made with another's data, and "advisory records, only refusals refuse" was written for checks a hospital might
reasonably want to tune, not for one that makes the record false. This is the decision I most want you to make. Option B preserves the
advisory profile's old stance; I think it is wrong here.

### D3. How is the observation bound, and in what order?

| Option | What it does | Cost |
|---|---|---|
| **A. Bind the reading now (10a, Recommended first step)** | Add `observation_digest`, a hash of the supplied vitals, to the request context, so the request commitment, and the PERCEIVE ruling that carries it, cover *which reading*. Whether the later Conservation and execution approvals chain to that commitment I have not checked; 10a must confirm it, and if they do not, binding the approval is part of 10a. | Changes the commitment of every request that carries vitals. I expect record verification to stay consistent because it recomputes from the record, but that is not yet verified; the visible cost is test fixtures that pin commitments. I will confirm both and list every test touched. |
| **B. Run OBSERVE first, in every profile, and bind the assessment (10b)** | OBSERVE runs before PERCEIVE. Its `decision_fingerprint` and state commitment go into the request, so the approval covers the *assessment*, not just the input. A request refused later still has its assessment and its notification (closing the "refused before OBSERVE ran" gap). | Larger. It changes the ordering the advisory profile has always had, so OBSERVE's stateful side effects (the escalation event, the ledger entry) happen for requests the governance later refuses. That is correct (the patient's physiology does not depend on the action's fate), but it is a real semantic change and needs its own design review and its own crash policy per profile. |
| C. Leave the ordering | | Findings 4 and 5 stay. |

### D4. The CNS OMEGA gates

No change (see above). Option if you disagree: have the optional pipeline recompute and compare a verdict's subject digest against
the context it was called with, which makes a hand-built verdict *inconsistent* rather than *impossible*. Not recommended: it adds
code to a layer the orchestrator does not use.

## Recommended plan

- **PR 10a** (small, additive): `attempt_id` (D1-A); the patient rule (D2-A) with `identity_mismatch` refusal and `subject_source`;
  `observation_digest` in the context (D3-A). Each of findings 1 to 4 becomes a test that **fails on `main` and passes after**, using the
  same inputs as the probes above.
- **PR 10b** (larger, after 10a is merged and you have seen its blast radius): OBSERVE first in every profile, assessment bound into
  the approval, one crash policy per profile (strict refuses; advisory proceeds and tells a person, as today), and the refused-request
  notification gap closed.

Splitting them keeps the first change reviewable and keeps the contentious ordering change from riding on the uncontroversial
identity fix. The cost is that finding 5 stays until 10b.

## What each step must NOT change

- A request with no vitals and no patient behaves as today (subject = artifact), and says so in the record.
- A request whose context patient **agrees** with the vitals patient behaves as today, apart from the added digest.
- The CNS gates, the Conservation Kernel and PERCEIVE's gate logic are untouched.
- No default is invented: nothing here has a timing, a threshold or a clinical value.

## Risks I can already see

- **Fixtures that pin commitments** will change with `observation_digest`; that is the price of D3-A and I will list each one.
- **Callers that put a different patient in context than in vitals** (a demo, a test double) will be refused by D2-A. That is the
  point, but it is a behaviour change for them.
- **`patient_id` and `subject_id` both in context but different**: today `patient_id` wins silently. Under D2-A that is also a
  mismatch. I would refuse it; it is the same defect inside the context.
- **10b's side effects**: OBSERVE state advances for refused requests. If an integrator relies on OBSERVE state staying untouched
  by a refused request, that changes.
- **Not covered by any of this:** the identity of the *caller* (who asked). That is the authority and attestation model, which
  already exists for event time and is not extended here.

## Decisions needed

1. **Where does an identity mismatch refuse?** All profiles (recommended) or strict and production only.
2. **Staging.** 10a now and 10b later (recommended), both together, or 10a only and leave the ordering.
3. **Bind `observation_digest` into the commitment now?** Yes (recommended; it is the only way the approvals cover which reading) or
   no (then only `attempt_id` and the patient rule ship).
