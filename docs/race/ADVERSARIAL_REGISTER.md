# Adversarial failure and remediation register

One table of what was found, how it was found, what was done, and what is still open. Evidence classes: **EXECUTED** (reproduced by
running it), **INSPECTED** (read in source), **REVIEW** (found by an independent reviewer, then verified by me). Status: **FIXED (#n)**,
**MITIGATED**, **OPEN**, **DECIDED-NO-CHANGE**. "Found in my own work" rows are listed on purpose: a register that only lists other
people's mistakes is not a register.

## A. Found in the code on `main` and fixed

| ID | Finding | Evidence | Status |
|---|---|---|---|
| A1 | An approved CRITICAL escalation, and one that governance BLOCKED, returned to the caller and reached no person unless the caller looked | EXECUTED | FIXED #78: every escalation event the clinician's settings name opens a ticket |
| A2 | Unanswered pages had no lifecycle: no tier movement, no acknowledgement, no follow-up | INSPECTED | FIXED #74: tickets (raised, dispatched, delivered, acknowledged, overdue, exhausted), signed acknowledgements, no default timings |
| A3 | Nothing called `sweep()` and nothing noticed when it stopped, so an unanswered page never moved to the next tier | INSPECTED | FIXED #81: sweeper thread plus watchdog thread with its own alarm channel; every interval required |
| A4 | A real hypoxia pattern was rated STABLE with no escalation: SpO2 86 with fever, heart rate 138, respiratory rate 34 | EXECUTED | FIXED #79: a hard rule is held at WARNING when corroborated, repeated within the window, or the window is 1 |
| A5 | An OBSERVE crash propagated: no decision came back and nobody was told that the patient had not been assessed | EXECUTED | FIXED #80: a decision that says "assessment unavailable", marked as needing a person, and a person is told |
| A6 | OBSERVE moved a patient's escalation state before it recorded, so a crash part way could leave "already escalated" for a reading no ledger held and swallow the next critical reading as a repeat | INSPECTED, then REVIEW | FIXED #80: `evaluate` is all-or-nothing for tracked state (transactional) |
| A7 | A refused attempt and its retry shared one id; an action about patient A ran with patient B's vitals in advisory and strict; with no patient named PERCEIVE's subject was the artifact so per-patient limits never accumulated; the request commitment was the same for calm and CRITICAL vitals | EXECUTED | FIXED #83 (attempt id, patient binding, reading digest) |
| A8 | Advisory assessed the patient after the action; a request refused at any earlier stage had no assessment and no page; a failed action was never assessed; the commitment said nothing about the assessment | EXECUTED | FIXED #86 |
| A9 | Every age from 3 to 125 years was scored on the "child" norms and nothing said the patient was out of scope: a healthy 40 year old at heart rate 70 was flagged bradycardia | EXECUTED | FIXED #84 (population floor at WARNING with a named finding; all clinical lines now settings) |
| A10 | An SpO2 of 0 (usually a probe that is off, possibly a patient) carried no hint to check the probe | INSPECTED | FIXED #85 (an advisory note that never changes a regime) |

## B. Found by review in my own changes, fixed before merge

| ID | Finding | How | Fixed in |
|---|---|---|---|
| B1 | The first identity refusal returned before OBSERVE ran, so a CRITICAL reading on a mismatched request paged nobody | REVIEW | #83 |
| B2 | "Two different subjects" refused five adapters that legitimately put an entity in `subject_id` | REVIEW | #83 |
| B3 | Odd readings (a lock in `context`, `__slots__` objects) crashed the digest with no record | REVIEW | #83 |
| B4 | A nested orchestrator call overwrote the outer record's attempt id and identity | REVIEW | #83 |
| B5 | A dict reading skipped the patient comparison; an unbuildable mismatched request left no record and no page | REVIEW | #83 |
| B6 | The first reset-on-crash design dropped a tracked CRITICAL when the crash came before state moved | REVIEW | #80 (replaced by the transactional design) |
| B7 | The population floor reused the fault dedup window, so a standing adult would have been re-paged every 5 minutes | my probe | #84 (page once, again only after a reading in scope) |
| B8 | An improving adult was paged again as a "new escalation"; `require_age` did nothing beside `assess_anyway`; a hard-rule window of 1 was listed as a weakening although it confirms every hard rule at once | REVIEW | #84 |
| B9 | A verifier could be fooled into hiding a CRITICAL reading: request and verdict rewritten together with the (unkeyed) commitment recomputed; a caller could commit its own `observe_assessment` when there were no vitals | REVIEW | #86 (the verifier now checks OBSERVE's own ledger entry; a supplied assessment is dropped) |
| B10 | Whether a person was told about an OBSERVE crash was lost on every non-success record | REVIEW | #86 |
| B11 | Two statements in my own design document were false (a verifier "without the engine runs three checks"; "a caller cannot supply one") | REVIEW | #86 |

## C. Open

| ID | Finding | Evidence | Why it matters | Next step |
|---|---|---|---|---|
| C1 | **Known missed detections.** Three deterioration scenarios get no escalation at all (`gradual_hypoxia_child`, `reactive_airway_toddler`, `toddler_viral_fever`); four sensor-fault cases fail to escalate (`coordinated_lie_sepsis_masking`, `slow_o2_drift_downward`, `stuck_hr_baseline`, `stuck_o2_sensor`) | INSPECTED, pinned by `test_detection_record.py` (a changed set fails the build) | The engine's own record of what it gets wrong | Clinical owner decides which matter; each needs a rule or a documented limitation |
| C2 | One "child" row covers 3 to 18 years, so a healthy 12 year old at heart rate 70 is flagged bradycardia | EXECUTED | False alarms; trust | Sourced age bands, clinician sign-off (clinicians can already set `norms.child`) |
| C3 | Clinical constants have no stated provenance and are outside `PARAMETER_SET`, so two builds with different clinical behaviour can write the same `parameter_set_version` | EXECUTED, INSPECTED | Change control cannot rely on the stamp | Put every constant in `PARAMETER_SET` (see `CHANGE_CONTROL_AND_VALIDATION.md`) |
| C4 | PERCEIVE's audit hash covers `audit_id`, `timestamp`, `evaluated_gates`, `final_verdict` but not `request_snapshot` | INSPECTED (`perceive_consolidated.py:626-634`) | The patient id and request context stored in that ledger are not tamper-evident by its chain (the orchestrator's request commitment does cover the context) | Include the snapshot digest in the chain hash (changes every PERCEIVE hash once) |
| C5 | The default PERCEIVE manifest blocks a CRITICAL escalation for "micropatch: Physician approval required" | EXECUTED (canary probe: `ESCALATION BLOCKED for CANARY-PT-7731`) | The person is now paged (A1) but the block itself is a policy question | Decision: may CRITICAL bypass the micropatch gate? (asked, unanswered) |
| C6 | A per-patient daily escalation limit (`max_daily` 10 in the default manifest) can block an escalation | INSPECTED | A deteriorating patient's later escalation may be refused for a count, not for the patient | Decision: should a CRITICAL be exempt from the count? |
| C7 | The OBSERVE ledger is in memory, unbounded, and holds patient ids and raw vitals in plaintext | INSPECTED | Memory growth; no persistence; privacy (see `DATA_PRIVACY_AND_RESEARCH.md`) | Bounded or rotating sink, field-level protection |
| C8 | A `STATE_EVICTED` ledger write that fails can fail another patient's evaluation | INSPECTED (earlier in this campaign) | Cross-patient coupling | Decide whether the eviction record is best-effort |
| C9 | In advisory the action still proceeds when OBSERVE crashes (a person is told and "unavailable" is committed) | by design | Acting on no assessment | Strict refuses; a deployment chooses |
| C10 | In strict the reading is assessed before the event-time attestation is judged | EXECUTED (review) | A request later refused for attestation still moves tracked state | Build the request twice to judge attestation first (PROPOSED) |
| C11 | The production profile does not require clinical settings to be supplied or recorded; `GovernanceOrchestrator` uses whatever cassette its engine has | INSPECTED | A production run on silent defaults | Add to `production_problems` |
| C12 | Log lines carry patient ids; no encryption at rest; no retention; erasure cannot be met against hash-chained records; the pseudonym is 48 bits | EXECUTED (canary), INSPECTED | Privacy | See `DATA_PRIVACY_AND_RESEARCH.md` sections 3 and 4 |
| C13 | The CNS OMEGA gates accept hand-built verdicts | INSPECTED | A gate that lies is a gate you chose to trust | DECIDED-NO-CHANGE (CNS stays a thin contract layer) |
| C14 | The identity of the caller (who asked) is not covered: the authority and attestation model covers event time only | INSPECTED | Anyone who can call the orchestrator can supply readings | Out of scope here; needs its own design |
| C15 | Hard-rule defaults (window 3, heart rate alone counting as corroboration) are unvalidated starting points | open question | Missed or extra pages | Clinical owner |
| C16 | No clinical validation exists: no outcome-labelled replay, no sensitivity, no alarm burden | INSPECTED | Everything above is engineering evidence, not clinical evidence | `CHANGE_CONTROL_AND_VALIDATION.md` section 4 |
