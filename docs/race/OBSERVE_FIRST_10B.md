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
- **A caller cannot supply one.** An `observe_assessment` in the caller's context (a replayed record, or a claim) is **dropped**, with
  a WARNING, and the orchestrator's own is the only one committed; with no vitals, none is committed. It is dropped and not refused
  because an assessment cannot be replayed (the engine's state has moved on), so refusing would turn every retry harness into a false
  identity alert. (A first version refused it as a mismatch; review showed that with no vitals it was committed anyway.)
- **Crash policy, per profile.** Strict and production: refuse before PERCEIVE is asked, tell a person, `raise_on_stage_error`
  re-raises. Advisory: proceed without an assessment, tell a person (once, naming the patient), commit "unavailable", and record
  `observe_error`. A reading OBSERVE itself cannot stand behind (unassessable, or no engine contributed evidence) is refused in
  strict before PERCEIVE and is **not** refused in advisory (as before).
- **Every record that reaches the stamp carries the assessment, or the failure to make one**, and whether a person was told of that
  failure (`observe_human_alert`, `observe_escalation_ticket`, under their own keys so a record's own stage alert does not displace
  them). A refused request's CRITICAL reading is paged by the existing clinical notification, which already read `_seen.verdict`.
  Not covered: `INVALID_REQUEST` (no artifact, so nothing is assessed) and a request the adapter cannot build with no identity
  mismatch (it raises; an OBSERVE crash is still told to a person first, but there is no record to carry anything).
- **A failed action is observed.** Its outcome commits the verdict, so the chain verifies and is complete. (It used to be neither
  valid-and-complete nor, in strict, even valid: the outcome was built without the verdict the record carried.)
- **A verifier can check the assessment.** Always (from the record alone): `assessment.well_formed` (a committed assessment has a
  known status and names a reading; nothing else in the committed request is a statement OBSERVE made), `assessment.matches_verdict`
  (the request's committed fingerprint, state commitment, regime, escalation and unassessable flags are the record's verdict's), and
  `assessment.none_recorded`. With `verify_result(..., observe=engine)`: `assessment.in_observe_ledger`,
  `assessment.matches_ledger_verdict` (OBSERVE's own entry says the same regime and flags: the request commitment is unkeyed, so a
  forger can rewrite the request and the verdict together and recompute it, and only the engine's own entry catches that),
  `assessment.matches_reading` (the entry holds the very reading, by the `observation_digest` the request **commits**, not an
  uncommitted copy) and `observe_ledger.integrity`. The orchestrator passes its own engine. `RecordedVerdict` keeps the two fields so a
  receipted record verifies from the file. `verify_record` does not take an engine: a record verified from a file alone runs only the
  always-on checks.

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
- **In strict, OBSERVE now runs before the event-time attestation is judged** (found in review). EXECUTED: an unattested artifact with an unassessable reading was refused as `source_attestation` with no OBSERVE
  ledger entry on `main`; it is now refused as `observe`, with one entry. A healthy reading on an unattested artifact is still refused as
  `source_attestation`, but now with one entry (`test_in_strict_a_reading_is_assessed_even_when_the_request_is_then_refused_for_attestation`).
  The request commits to the assessment, so the
  assessment is made before the request exists, and attestation is a property of the built request. The reading is the caller's
  argument, not the artifact's claim; the cost is that a request that will be refused for attestation still moves the patient's tracked
  state and can use up their escalation event (which is also paged). PROPOSED, not done: judge attestation first by building the request
  twice.
- **Strict refusals for an unassessable reading or an OBSERVE crash now happen before PERCEIVE is asked**, so such a request no
  longer consumes a PERCEIVE ruling or a Conservation approval before being refused, and `governance_decision` is `None` on those records.
- **Failed executions are `audit_chain_valid` and complete.** Two existing tests asserted the opposite (see the PR).

## Limitations and unknowns

- An OBSERVE crash in advisory means the action proceeds on no assessment, as before (it is told to a person and committed as
  unavailable, not hidden). A deployment that wants a refusal uses strict.
- The ledger checks need the engine; without it only `assessment.well_formed`, `assessment.matches_verdict` and `assessment.none_recorded` run.
- The commitment binds the assessment OBSERVE reported, not that the verdict is clinically right.
- OBSERVE's own ledger is in memory in this library; a verifier in another process needs the ledger entry exported (as `export_json`).
- Two OBSERVE crashes in one request (the reading and a second assessment for a mismatch) cannot happen now: the reading is assessed once.
