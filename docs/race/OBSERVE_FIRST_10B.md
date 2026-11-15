# OBSERVE first (identity binding, part 2 of 2)

Evidence classes: EXECUTED (run, output quoted), INSPECTED (read in source), PROPOSED. Follows `IDENTITY_BINDING_DESIGN.md`
(D3-B) and `IDENTITY_BINDING_10A.md`.

## What was wrong (measured on `main`)

1. **Advisory assessed the patient after the action.** Order `['ACTION', 'OBSERVE']` (EXECUTED in the design note). A crash in
   OBSERVE, or a CRITICAL reading, was found only once the action had happened.
2. **A request refused before OBSERVE ran had no assessment and no notification.** A CRITICAL reading on a request the kernel
   refused (duplicate artifact) paged nobody: EXECUTED here, `test_a_critical_reading_on_a_request_the_kernel_refuses_is_still_paged`
   fails on `main` and passes now.
3. **An action that raised was never followed by an assessment in advisory.** The failed record had `observe_verdict: None`.
4. **The request commitment, and the PERCEIVE ruling and approvals chained to it, said nothing about the assessment** (only, since
   10a, about the reading).

## What changed

- **One assessment per call, first, in every profile.** After the identity is bound and before the request is built, the reading
  is assessed once; every later stage reads that verdict. Nothing runs OBSERVE after the action any more.
- **The assessment is bound into the request.** `context["observe_assessment"]` is `{status, decision_fingerprint, state_commitment,
  regime, escalation_required, unassessable}` (or `{status: "unavailable", error_type}` when OBSERVE crashed, or absent when there
  are no vitals). It is plain data inside the request context, so the request commitment, the PERCEIVE ruling whose commitment has
  it as parent, and everything chained to that ruling cover it.
- **A caller cannot supply one.** An `observe_assessment` in the caller's context that is not the one OBSERVE made is an identity
  mismatch (refused in every profile, still assessed and told, like the 10a mismatches).
- **Crash policy, per profile.** Strict and production: refuse before PERCEIVE is asked, tell a person, `raise_on_stage_error`
  re-raises. Advisory: proceed without an assessment, tell a person (once, naming the patient), commit "unavailable", and record
  `observe_error`. A reading OBSERVE itself cannot stand behind (unassessable, or no engine contributed evidence) is refused in
  strict before PERCEIVE and is **not** refused in advisory (as before).
- **Every record carries the assessment, or the failure to make one**, whatever stage refused it. A refused request's CRITICAL
  reading is paged by the existing clinical notification, which already read `_seen.verdict`.
- **A failed action is observed.** Its outcome commits the verdict, so the chain verifies and is complete. (It used to be neither
  valid-and-complete nor, in strict, even valid: the outcome was built without the verdict the record carried.)
- **A verifier can check the assessment.** `verify_result(..., observe=engine)` adds `assessment.matches_verdict` (the request's
  committed fingerprint, state commitment and regime are the record's verdict's), `assessment.in_observe_ledger`,
  `assessment.matches_reading` (OBSERVE's ledger entry holds the very reading the request names, by `observation_digest`),
  `observe_ledger.integrity`, and `assessment.none_recorded` (a request that committed "no assessment" cannot carry one). The
  orchestrator passes its own engine. `RecordedVerdict` keeps the two fields so a receipted record verifies from the file.

## Do the approvals cover the assessment? (the design note's open question, answered)

INSPECTED, then EXECUTED. The request commitment is computed over the request fields including the context
(`governance_chain.request_state`). PERCEIVE's decision commitment has the request commitment as its parent
(`perceive_consolidated.py`, `compute_state_commitment(parent_commitment=request.state_commitment, ...)`). The Conservation
decision names that decision's id, and the execution approval chains to the Conservation audit hash and names the decision
(`verify_result` checks each link). So the approvals cover the assessment **transitively, by id, through PERCEIVE's decision**, not by
holding the fingerprint in their own hash. EXECUTED: rewriting `observe_assessment.regime` on a finished record fails
`request.state_commitment` (`test_the_request_commits_to_the_assessment_and_a_changed_one_breaks_verification`).

## What changes for an integrator

- **Advisory order** is now OBSERVE then the action. Strict was already that order.
- **OBSERVE state advances for requests that are later refused** (a duplicate artifact, a PERCEIVE rejection). That is correct (the
  patient's physiology does not depend on the action's fate) but it is a real change: the escalation event, the tracked regime and the
  ledger entry now happen for a refused request, and the page goes out for it.
- **Every request that carries vitals has a different commitment** (the assessment is in the context). Fixtures that pin one change.
- **Strict refusals for an unassessable reading or an OBSERVE crash now happen before PERCEIVE is asked**, so such a request no
  longer consumes a PERCEIVE ruling or a Conservation approval before being refused, and `governance_decision` is `None` on those records.
- **Failed executions are `audit_chain_valid` and complete.** Two existing tests asserted the opposite (see the PR).

## Limitations and unknowns

- An OBSERVE crash in advisory means the action proceeds on no assessment, as before (it is told to a person and committed as
  unavailable, not hidden). A deployment that wants a refusal uses strict.
- The check `assessment.matches_reading` needs the engine; without it the other three checks still run, and the ledger checks do not.
- The commitment binds the assessment OBSERVE reported, not that the verdict is clinically right.
- OBSERVE's own ledger is in memory in this library; a verifier in another process needs the ledger entry exported (as `export_json`).
- Two OBSERVE crashes in one request (the reading and a second assessment for a mismatch) cannot happen now: the reading is assessed once.
