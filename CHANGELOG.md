# CHANGELOG — OBSERVE / PERCEIVE hardening pass

## Unreleased

- **The "child" age group (3 to 18 years) split into preschool, school and adolescent (provisional proxies, not clinically validated).** One pair of limits could not fit 3 to 18 years (the 1st centile of heart rate falls from 70 to 43 across it). New groups and limits: preschool (3 to under 6 years) HR 65 to 135, RR < 33; school (6 to under 12) HR 50 to 125, RR < 27; adolescent (12 up) HR 45 to 115, RR < 26; SpO2 >= 92 and fever > 39.5 kept. The adolescent HR high (115) and RR high (26) are looser than the published 99th centiles (108, 23) because the faulted-channel placeholders (HR 110, RR 25) must stay inside every window. Breaking changes: `AGE_GROUP_UPPER_MONTHS` is now five bounds (3, 12, 36, 72, 144), `age_group_upper_months` must give five, and the `norms.child` key is refused with a message pointing to the new keys. `PARAMETER_SET_VERSION` and the pinned fingerprints were regenerated (Python 3.11, 3.12, 3.13). No clinician-review case moved.

- **Heart-rate, respiratory-rate and newborn-fever limits re-derived from published references (provisional proxies, not clinically validated).** The old low heart-rate limit rose with age (80, 90, 95, 100), the wrong way round, so a healthy 8-year-old at 85 was flagged bradycardic; the high limits for infants sat inside the healthy range (150 for a 4-month-old, where 150 is only the 75th centile and the median is 140). New `PEDIATRIC_NORMS` follow Fleming 2011 (centiles of healthy children), Goldstein 2005 (consensus) and AAP 2021 (fever from 38.0 C under 2 months); the sources and the method are in the comment above the table. SpO2 limits and the other fever limits were reviewed and left alone. The behavioral engine's base-risk heart-rate test is now age-aware (it was a flat >150 / <80). Because the parameter set changed, `PARAMETER_SET_VERSION` and the pinned fingerprints in `test_engine_gate_profile.py` were regenerated (on Python 3.11 and 3.12). Three clinician-review cases moved from caution to stable (S1, S5, S10); the direction and cost are written up at the top of section A of `docs/race/CLINICIAN_REVIEW_SHEET.md`. Tests that sat on the old boundaries were moved to the new ones. (The "child" group was split in the entry above.)

- **Selection is not evidence: OBSERVE's evidence check ported upstream (Step 14).** `FusedVerdict.active_engines` lists the engines that were SELECTED, abstaining ones included, and the gates here read it as evidence. Measured: with a cassette whose selected engines all abstain, a child with heart rate 190, SpO2 82, respiratory rate 55 and temperature 40.5 was scored STABLE at risk 0, and `ObserveCnsGate("omega")` and `HandoffBindingGate` both passed it. The shipped cassettes always select an engine that assesses (324 varied readings checked), so this is latent. OBSERVE's own connector already confirms evidence by re-running the recorded engines; that check is now here as `confirmed_evidence`, used when a gate is given the engine (`ObserveCnsGate("omega", engine=...)`) or the cassette (`HandoffBindingGate(..., cassette=...)`). It also catches a recorded engine output that was rewritten with every hash made to agree. Without the option, behaviour is unchanged, and the gap is a recorded assertion. Tests: 7 in `test_cns_evidence.py`; 7 mutants, none survived.

- **Two protections ported from the standalone PERCEIVE repository (Step 13).** (1) Shape before words: `PolicyGates.request_shape_problems`, run inside `boundary_gate` (every request type runs it), refuses a request with no subject or a non-text one, a non-text id, type or actor, a context that is not a mapping, a time that is not a datetime or has no zone, a context naming a different patient from the subject, or a content digest with no document. Measured before the change: seven of these were APPROVED, and three more were refused only because a gate crashed on them. Not checked: how far a time lies from now, because evaluation is a pure function of the request (recorded as an assertion). (2) Read-only records: `EventStore.replay` and `ManifestRegistry.get_manifest` returned live objects, so a caller could rewrite past request snapshots or change a registered policy away from the hash every audit entry cites; both now return copies, and `register_manifest` stores a copy (it still sets `manifest_hash` on the caller's object). The refusal-clearance lifecycle was not ported: this kernel has no way to clear a refusal. README states which PERCEIVE runs here and how the two designs relate. No existing test changed outcome. Tests: 23 in `test_perceive_request_shape.py`; 11 mutants, none survived.

- **OBSERVE and PERCEIVE composed as one CNS chain, and the handoff bound (Step 12).** Each CNS gate binds only the candidate it is handed, and a chain relates none of them: run together, a chain given one patient's reading and a request about another passed every gate (now a recorded assertion). New `HandoffBindingGate` (ALPHA, PERCEIVE's input) takes an `ObservationHandoff` (the vitals, OBSERVE's verdict, OBSERVE's own audit entry for it, and the PolicyRequest) and refuses unless they provably belong together: the entry is the one the verdict names and unaltered; the fingerprint recomputes from the recorded evidence; the state commitment matches; the evidence is about this patient and this reading; an abstention is RETRY, never stable; the request is about this patient, commits to this reading (`observation_digest`) and carries this assessment; and the reading is neither stale nor newer than the request beyond a skew. `max_age_s` and `clock_skew_s` have no default. New `test_cns_composition.py` (29 tests) runs OBSERVE's and PERCEIVE's gates on a real engine's verdict and a real kernel's ruling, and refuses 15 attacks on the handoff. Recorded limits: the gate does not see replay (the execution ledger does), and it proves the pieces agree, not that OBSERVE made them (the entry's hash takes no key; a fabricated whole passes). 12 mutants of the gate, none survived (two survivors first showed an untested commitment check and a check hidden by OBSERVE's own gate; tests were added).

- **Reconciliation on a schedule (Step 11).** `python -m reconciler run --ledger --effects --state --clock-skew --abandoned-after --key-env --key-id` reads the signed ledger (checking every signature) and an effect log (JSON lines), prints each new violation that needs a person as a JSON line, and records it in a state file replaced in one step (`load_raised`, `save_raised`). Exit 0: nothing new. Exit 2: a person is needed, including a ledger that does not verify, a missing or malformed effect log, or a state file that cannot be written (then it says the same violations will print again). Exit 3: misconfigured. Policy numbers and the key have no default. A violation is marked raised when printed, so the scheduler must treat a failure to deliver exit-2 output as its own alarm. `docs/KEY_CUSTODY.md` records that this CLI holds the ledger secret. Tests: 11 in `test_reconciler_run.py`, one running it as its own process; eight mutants, none survived (one survivor showed the unwritable-state path was untested; the test was added).

- **Key custody map (Step 10, docs and a test).** New `docs/KEY_CUSTODY.md`: every key this package signs or verifies with, who signs, who checks, and what a thief could do, with what a deployer should decide per key. The rule it rests on: `check_signature` only calls `verify`, so the four slots that only check (registry actor keys, desk responder keys, event-time sources, `check_heartbeat`) can hold a key that cannot sign, and then the checker cannot forge. New `test_key_custody.py` (5 tests) proves that by giving each slot an object whose `sign` raises. Named plainly: with HMAC every checker can forge; the heartbeat CLI builds an HMAC signer; the execution ledger key is the most valuable, because forging it can hide a bypass from the reconciler. README points to the map. No code changed.

- **One operational chain, end to end (Step 9, test only).** New `test_operational_chain.py` drives a real production-profile orchestrator, with every record on disk, through the order a bad day would use the PR #110 parts: a governed action reconciles clean; a direct call around the guard is found by the reconciler and paged, and the ticket is in the signed escalation log; the runner stops, and a separate process running `python -m heartbeat check` sees a healthy beat and then silence, and its alarm is raised on a different (on-call) desk; the registry and desk restart from disk, the ticket is still open, and the bypass is not paged again (what was paged is read back from the durable log); the responder acknowledges, is revoked, and the same key is refused on the next ticket, and the revocation survives a restart. The heartbeat writer in this test uses the real wall clock, because its checker is a separate process on it (as in a deployment); this keeps the test valid under CI's future-clock run. Four links broken on purpose (the bypass join, the desk's authority check, the heartbeat age check, the durable de-duplication), each failed the test. No code changed.

- **README no longer says there is no actor registry (Step 8, docs only).** Two lines written before PR #110 still said the repository has no identity registry or grant lifecycle. They now say the orchestrator does not own identity authority, the repository provides a signed role registry that `production` requires at acknowledgement, and what the registry does not prove.

- **A heartbeat so something outside the process notices it stop (Step 7).** The in-process watchdog dies with its process. New `heartbeat.py`: `HeartbeatWriter`, given to `SweepRunner(heartbeat=...)`, writes a signed status file after every watchdog pass (replaced atomically; a failed write is counted, never raised). `check_heartbeat` and `python -m heartbeat check PATH --max-age S --key-env VAR --key-id ID` (exit 0 healthy, 2 alarm, 3 misconfigured) run elsewhere and alarm on a missing, unreadable, forged, edited, stale, future-dated or other-runner beat, and on a runner that reports itself not running, stale, with undelivered alarms, or with delivery stuck. The signed time stops an old healthy beat being replayed. The production profile refuses a sweep runner with no `HeartbeatWriter`; strict and advisory are unchanged; with no heartbeat the runner behaves as before. Not provable here: that anything runs the check, or that its alarm reaches a person. Test edit caused by this step: `runner_for()` in `test_production_profile.py` now gives the runner a `HeartbeatWriter`; nothing else about it changed. Tests: 25 in `test_heartbeat.py` (one runs the check as a separate process) and two in `test_production_profile.py`; nine mutants, none survived.

- **Production holds acknowledgements to the actor registry (Step 6).** The production profile now refuses an escalation desk with no live authority, an authority that is not `ActorRegistry.authority(role)` (any other callable), a responder on the policy who does not hold the role now, or a responder whose desk key is not the key the registry bound to them (`responder_authority_problems`). `ActorRegistry.authority` now returns a `RegistryAuthority` (same answers as before, now identifiable). Strict and advisory are unchanged. Test edit caused by this step: the `desk()` helper in `test_production_profile.py` now passes `authority=` from a registry in which `nurse-1` holds `responder`; nothing else about it changed. Four new tests; the five checks are mutation-checked.

- **Reconciler: an effect is unauthorized until a join finds its slip (Step 5).** New `reconciler.py`. It joins the signed execution ledger against an effect log the deployer supplies, in an in-memory SQLite database, with one named query per kind of disagreement: `bypass` (an effect with no slip), `forged` (a slip never issued), `unconsumed_use` (a slip the guard never consumed), `substitution` (a different artifact from the one approved), `replay` (one slip, several effects), `early` (an effect before its slip was consumed, beyond a given clock skew), `claimed_no_effect` (consumed, nothing in the log) and `abandoned` (issued, unused, older than a given age). `escalate` raises one desk ticket per violation that needs a person, once per violation across runs. It refuses a ledger whose chain does not verify and a malformed effect log; the policy numbers have no default. What it cannot do: prevent a bypass, or see an effect the effect log omits. Tests: 22 in `test_reconciler.py`, one per join plus an end-to-end run where the governed path reconciles clean and a direct call to the action is reported as a bypass; every join mutation-checked (11 mutants, none survived).

- **The escalation desk takes an optional live authority check (Step 4).** `EscalationDesk(authority=...)` is asked at acknowledgement whether the responder may act now; anything but `True`, or an exception, refuses. A key on file says who signed; the authority says whether they still may. With no authority the desk behaves as before. Tests: 8 in `test_desk_authority.py`. No existing test changed.

- **Actor registry (Step 3).** New `actor_registry.py`: grant, delegate and revoke a role, each event signed by the actor who makes it against the current chain head (so it cannot be replayed) and hash-chained to the last. Roots are named at construction with no default; the log carries key ids, never key material; a registry that cannot re-prove its history on load does not start. A delegation must end, cannot outlast the delegator's grant, cannot be re-delegated, and ends when the delegator is revoked; `registry:admin` cannot be delegated. `holds(actor, role, at=...)` answers for a past moment too. Not modelled: key rotation, multi-process writers, proof that a key is held by the person it names. Tests: 25 in `test_actor_registry.py`, mutation-checked (a surviving mutant showed that backdating a root's grant was untested; the test was added).

- **The production profile checks the kernel's evidence guard against a written contract (Step 2).** `production_profile.evidence_guard_problems` refuses a kernel with no evidence guard, a guard that is not callable, one that crashes on a startup canary, or one that does not answer exactly `False` to a record from an unvouched actor that declares itself independent verification (`lambda r: True`, `lambda r: r.independent`, a truthy non-bool). `refuse_all_evidence` and a deployer's guard that refuses unvouched actors pass. The contract is in the module docstring. It proves a guard refuses self-declared evidence; it does not prove a deployer's guard accepts the right evidence, which stays the deployer's responsibility. Tests: seven new in `test_production_profile.py`. No existing test changed.

- **`make_ccc_system` states its unguarded choice (Step 1).** Newer CCC refuses to be built without a `write_permission` and a `human_verifier`. This package records machine findings only, so with none given it builds the system with `allow_unguarded_writes=True` and `allow_unverified_humans=True`; a deployer can pass their own `write_permission` and `human_verifier`. An older CCC is built as before. Found because a newer sibling `../CCC` failed ten tests. One new test; no existing test changed.

- **Strict also needs a running sweep runner (Task A, second half).** `profile="strict"` now refuses to start without a `SweepRunner` that is running and bound to the same escalation desk, the same check production makes (it now lives in one function, `sweep_runner_problems` in `production_profile.py`, used by both). Without it a page nobody answers would never move to the next tier, and nobody would be told that sweeping had stopped. Advisory is unchanged. Production behaviour is unchanged. Tests: strict orchestrators in the suite and in `vertical_slice.py` now get a running runner (`strict_wiring` and `make_test_runner` in `conftest.py`); four new tests in `test_production_profile.py` cover a missing runner, a runner bound to another desk, a stopped runner, and advisory still running without either. README corrected to say strict and production both require it.

- **A CRITICAL reading is never silent (Task A).** `profile="strict"` now refuses to start without an escalation desk (`escalations=...`), as `profile="production"` already did: a CRITICAL reading reaches a person only through a desk (`alert_human` is called only when a stage fails). Advisory, the default, still runs without a desk, but a CRITICAL escalation event then logs `CRITICAL READING, NO ONE TOLD` and the record gains `clinical_notification` with status `no_channel` (reason `critical_regime`). A CRITICAL reading still does not stop the action. Tests: strict orchestrators in the suite now get a desk (`make_test_desk` in `conftest.py`, and a printing desk in `vertical_slice.py`); the old "strict with no alert channel says no_channel" test became "strict refuses to start without a desk"; the "without a desk adds nothing" test now covers a CRITICAL reading (marked) and a stable one (nothing added). Production behaviour is unchanged. Not changed: the clinician's review of the thresholds. (The sweep runner question that was open here is answered and built; see the entry above.)

- **Register wording and a clinical gaps briefing (docs only).** `ADVERSARIAL_REGISTER.md`: rows that were decided or built now say so (C8 fixed in #89, C9 and C10 decided to keep as documented,
  C15 notes the changed defaults, C16 notes the counting tools exist, the C6 note and the pull-request note at the top are brought up to date). New `docs/race/CLINICAL_GAPS_BRIEFING.md`:
  seven clinical gaps in plain language, each with the one decision it needs from the clinical owner and a suggested order. No code, tests or numbers invented.

- **Hypothermia comment corrected (register C18), behaviour unchanged.** A comment in the engine said HYPOTHERMIA is a hard rule. It scores 0.4 and the hard-rule line is
  0.5, so alone it is not one; with one more finding (a raised respiratory rate adds 0.15) it is. The comment now says so, and new `test_hypothermia_scoring.py` pins both
  facts (three tests, mutation-checked: six breakages, none survived). Comment-only change in the engine: the logic digest ignores comments, so no pinned fingerprint moved.
  Whether a lone low temperature should page remains a clinical decision (review sheet S7).

- **GEMS pin moved to the same commit on GEMS main's rewritten history (CI repair).** Every CI install began failing with `git checkout -q f70da70: pathspec did not match`: the
  pinned GEMS commit is no longer reachable from any branch on GitHub (GEMS now has only `main`, whose history was rewritten; the old branch that also held the commit is gone).
  The same commit on the new history is `f7d40fc7c0a6b38b5a2d7073ee59a5686db65d8e` ("Tighten HUMAN_AUTHORIZATION to a HUMAN-origin allowlist (#13)"). The two trees differ by one
  file only, an 8445-line notes document under `docs/`; no code differs. `pyproject.toml` and the README now pin the full SHA. No code or test changed.

- **fortress-kernel pin moved off the published audit key.** The `fortress` extra pinned fortress-kernel 4105ccb5, which signed every
  audit record with the published default key `fortress-key`, so anyone could forge records that verify. It now pins fortress-kernel
  Main at f5d15b28a2a842a37c38f30dcde6ee3f47f361b7 (c361569 removed the default; records also carry `key_id` and
  `signature_algorithm`). `FortressPerceiveAdapter` takes `audit_key=`, else reads `FORTRESS_AUDIT_KEY`, and installs that key on the
  kernel's ledger before anything is recorded; with neither, fortress-kernel signs with a random per-process key (records verify only
  in that process). `profile="production"` with `fortress_controller=` now refuses to start unless the FORTRESS ledger has a configured
  key of at least 32 bytes that is not the published `fortress-key`; a whitespace-only `audit_key=` counts as none. The orchestrator
  builds the FORTRESS adapter before the production check (it used to be built after). New
  `test_fortress_adapter_audit_key.py` and two new tests in `test_production_profile.py`; no pre-existing test edited.

- **Three hand-off documents (docs only).** `docs/race/COUNSEL_BRIEF_ERASURE.md` (decision D14: the conflict between tamper-evident chains and erasure, what each
  store holds, four designs with their costs, and seven questions for counsel), `docs/race/CLINICAL_OWNER_ROLE.md` (decision D15: what the clinical owner
  decides, signs and should refuse to sign, with the document that puts each decision to them) and `docs/race/REAL_DATA_REPLAY_CHECKLIST.md` (handling rules and
  steps for the first replay of real readings and a pilot's desk log). No code, no tests; decision rows D14 and D15 note the status.

- **Ticket-log burden report (new tool, no behaviour change).** `ticket_burden.py` reads an escalation desk's hash-chained log read-only and reports tickets
  by kind, reason and regime; per day and per patient-day with a ticket; time to first dispatch and to acknowledgement (acknowledged tickets only,
  beside the unacknowledged share); acknowledged, exhausted and open tickets and the age of the oldest open one; tickets needing more than one tier,
  overdue events and failed dispatches; re-paging within an hour; and concentration in a few patients. It checks the chain from the first event,
  reports only the verified part on a break (exit status 2), says whether the log is signed (signatures are not verified there), requires a source
  label and never prints a patient identifier. New `test_ticket_burden.py` (18 tests, a real desk on a controlled clock plus forged but well-formed
  logs that isolate each chain check) and a section in `docs/race/ALARM_BURDEN.md`. No engine or desk code changed; no pre-existing test edited.

- **Alarm-burden replay (new tool, no behaviour change).** `alarm_burden.py` replays a CSV of readings through a fresh OBSERVE engine under given
  clinical and notification settings and counts escalation events, the subset that would reach a person, events per patient-day (mean, median,
  p90, p99, max), and breakdowns by regime, kind, age group and rule, plus first-reading events, repeats within an hour and concentration in
  the busiest patients. The source of the readings is required and printed; a patient identifier is never printed. New `test_alarm_burden.py`
  (19 tests, hand-derived counts) and `docs/race/ALARM_BURDEN.md`. **It showed a gap in the review sheet**: the "Paged" column meant an
  escalation event, but the default notification settings tell a person only at CRITICAL, so S11 and S12 (WARNING) reach nobody; the sheet's
  wording is corrected and a fourth question (at what regime should a person be told?) added. No engine code changed and no pre-existing test edited.

- **Privacy hardening for production (decision D11).** (1) A patient identifier in a log line is now `log_redaction.patient_tag`: `pt:` plus 12
  hex of HMAC-SHA256 (key: `OBSERVE_LOG_SALT`, else a per-process secret), in the engine, the governance system, the notification desk and
  the identity refusal; `OBSERVE_LOG_RAW_PATIENT_IDS=1` is the troubleshooting switch. Measured before: a canary id was in 2 of 11 captured
  lines (and in alert payloads, ledgers and exports, which this does not change). (2) OBSERVE's ledger can be bounded:
  `ObserveClinicalEngine(ledger_max_entries=..., ledger_archive_path=...)`. Past the bound plus a tenth, the oldest entries are appended to an
  owner-only, fsynced archive and leave memory; the chain continues across the boundary; `verify_integrity(full=True)` reads the archive;
  `verify_result` still finds an archived assessment; an archive that cannot be written keeps every entry in memory and reports it, without
  taking the engine's lock. Default unchanged (unbounded, in memory). The ledger append is now locked. (3) `profile="production"` now also
  requires `clinical_settings=` (the object the clinical owner approved; the engine's cassette must hold exactly those, by digest, and the
  digest is recorded in every handoff), a bounded ledger with an archive, and no raw patient ids in logs. **Edited pre-existing tests:**
  `test_production_profile.py` (the shared correct-wiring helper now supplies the settings and a bounded engine; no assertion changed),
  `test_engine_gate_profile.py` (the pinned fingerprints, regenerated for 3.11 and 3.12, because `observe_consolidated` changed and the
  logic stamp covers it). Also: a log message lost an em dash. Documents: `DATA_PRIVACY_AND_RESEARCH.md`, `ADVERSARIAL_REGISTER.md`,
  `PRODUCTION_READINESS.md`. **Review round (an independent review found four ledger blockers, all fixed and pinned):** a restart over an
  existing archive now checks it from the first entry and continues its chain (before, the new ledger started again at genesis, so the full
  check was false for good); a half-written last line from a crash is moved to `<archive>.torn` and reported as degraded, and a damaged or
  symlinked archive is refused at start (`LedgerArchiveError`); a failed fsync no longer writes entries twice and a write that fails part
  way is truncated back; reading the whole ledger while it rotates is one consistent snapshot (the archive is read only up to its committed
  length); data with integer keys is hashed and archived through a JSON round trip, so it verifies after archiving (the hash of an
  ordinary entry is unchanged); the archive is opened without following links and tightened to owner-only. New `flush_to_archive()` for a
  clean shutdown (entries still in memory when a process dies are not in the archive: a documented limit). The two exports and
  `verify_result` read archived entries, and a verifier that meets an unreadable archive fails a check instead of raising; a current record is
  found without reading the archive. Log lines that carry exception text now replace the patient ids in hand with their tags (an identity
  mismatch with no alert channel logged both ids). The attested-settings check needs exactly `ClinicalSettings` and a `PediatricCassette`
  (a subclass that overrides `digest`, or a cassette that only claims `settings`, no longer passes). The degraded-event counter is locked.
  Known limits: `OBSERVE_LOG_RAW_PATIENT_IDS` is checked when the orchestrator starts, not on every log line; one process owns an archive.

- **Clinician review sheet (decision D8).** New `docs/race/CLINICIAN_REVIEW_SHEET.md`: twelve illustrative readings with what the engine
  does today, a mark column (fine / page at WARNING / page at CRITICAL / other) and a column for the threshold wanted, the settings that
  already exist, the numbers that need acceptance, and the three questions only a clinical owner can answer. New
  `test_single_channel_behaviour.py` pins the engine's current regime sequence and first page for each row, and checks that the sheet
  has a row for every case, so a change to behaviour must be acknowledged in both. No engine code changed and no pre-existing test edited.

- **Two integrity gaps closed (decisions D9 and D10).** (D10) PERCEIVE's audit hash covered `audit_id`, `timestamp`, `evaluated_gates` and
  `final_verdict` only, so the patient id and request context in `request_snapshot`, the gate outputs and the manifest version and hash
  could be rewritten with the chain still valid. Entries now carry `hash_version`: 1 is the old hash (every entry written before, which
  loads unchanged), 2 also covers those fields (new entries). `verify_chain_integrity` now uses the one versioned `_hash`. (D9) Two
  builds with different clinical behaviour could write the same `parameter_set_version` (the SpO2 line 88 and the hypothermia line 35 were
  not in it). New `logic_stamp.logic_digest` hashes what scoring code does (syntax tree: node kinds, names, constants and scalars; not
  comments, formatting, docstrings or logging; identical on Python 3.11 to 3.13, which `ast.dump` is not), over WHOLE MODULES
  (`observe_consolidated`, `kalman_trajectory`, `cassette`, `logic_stamp`, `pediatric_cassette`, `clinical_settings`, plus the module of
  any subclass), because a hand-picked list of functions missed 13 clinically meaningful edits in review. Every decision records
  `logic_version` in its provenance, inside the fingerprinted payload. **This changes every recorded decision fingerprint once, on
  purpose,** and from now on whenever those modules change (a non-clinical edit too: that is the price of missing none). The
  cassette version stays 1.1.0. The PERCEIVE hash is also computed over a JSON-normalised snapshot, so it is the same in memory and after
  a reload (integer keys sorted differently before) and no longer raises on a tuple or mixed key. **Edited pre-existing tests:** `test_engine_gate_profile.py` (the 14 pinned fingerprints, regenerated for Python 3.12 and for 3.11, and
  `logic_version` added to the expected entry keys), `test_fault_overlay.py` (`logic_version` added to the expected entry keys). Nothing
  about any reading's verdict changed. Documents: `CHANGE_CONTROL_AND_VALIDATION.md`, `ADVERSARIAL_REGISTER.md`, `DATA_PRIVACY_AND_RESEARCH.md`.

- **Four policy defaults changed, on the clinician's instruction.** (D1) The hard-rule confirmation window now defaults to 1: a lone SpO2
  below 88 holds WARNING at once (was 3: wait for a repeat). A cassette that supplies no window keeps the core's 3. Any window above 1 is
  now listed as weaker than the default. Cost, measured: `single_o2_spike_down` (a lone artefact) is now an accepted false alarm in the
  pinned detection record. (D2) New setting `heart_rate_corroborates` (default false): a heart-rate finding alone no longer corroborates a
  hard rule; respiratory rate or temperature is needed. (D4) A CRITICAL that OBSERVE itself found (the orchestrator's own `observe_assessment`, authoritative) is exempt from the escalation count
  when it reaches PERCEIVE as an ordinary escalation, is not counted against the day, and the exemption is on the record but is not an
  "advisory violation"; a `severity` or `regime` a caller wrote is a claim and does not exempt (review: it could dodge the count). The
  emergency path was already exempt by design. The exemption becomes active for orchestrator requests once PR 13 (OBSERVE first) merges,
  because that is what writes `observe_assessment`. (D6) A failed `STATE_EVICTED` ledger write no longer fails another patient's assessment: it is
  logged at CRITICAL, kept in `degraded_events` (with a `degraded_dropped` count past 100) and given to an optional `on_degraded`
  hook AFTER the assessment that found it finishes, outside every lock (review: a hook run mid-assessment widened a race and a
  re-entrant hook crashed it); `_get_policy` also no longer fails if another evaluation evicts the patient between its two steps.
  `ClinicalGovernanceSystem` tells a person through its desk. **Known limit (listed in the register):** an entry with empty
  `identity_extras` was made under the defaults of its source revision; the cassette version stays 1.1.0 to keep recorded
  fingerprints, and the parameter-set change (D9) will close it. (D3, no code change) A CRITICAL is
  BLOCKED unless the integration supplies `physician_approved`, by design; a person is paged either way. **Edited pre-existing tests (each
  asserted the old default or its exact behaviour):** `test_hard_rule_floor.py` (the shared `run` and `engine` helpers and six
  constructions now name window 3, the mechanism they test; the default-window test now asserts 1; the identity and immutability tests
  use window 3), `test_observe_crash.py` (one test names window 3), `test_clinical_settings.py` (default values and weakening
  directions), `test_detection_record.py` (`single_o2_spike_down` added to the risky set with the reason). No assertion about the
  mechanism changed. Documents: `HARD_RULE_FLOOR.md`, `CLINICIAN_SETTINGS_AND_POPULATION.md`.


- **OBSERVE now runs first, in every profile, and the request commits to what it found.** Measured on `main`: advisory assessed the patient
  after the action; a request refused at any earlier stage had no assessment and no page (a CRITICAL reading on a request the kernel refused
  paged nobody); an action that raised was never followed by an assessment; and the request commitment said nothing about the assessment.
  Now the reading is assessed once, before PERCEIVE, and `context["observe_assessment"]` (fingerprint, state commitment, regime, escalation,
  unassessable; or `unavailable` with the error type) is part of the request, so the PERCEIVE ruling and the approvals chained to it cover it.
  Crash policy: strict refuses before PERCEIVE and tells a person; advisory proceeds, tells a person and commits that there is none. Every
  record carries the verdict or the failure whatever stage refused it, so a refused request's CRITICAL reading is paged. A failed action is
  observed, so its chain is now valid and complete. `verify_result(..., observe=engine)` checks the assessment against the record, OBSERVE's
  ledger and the reading. **Visible changes:** advisory order is OBSERVE then action; OBSERVE state advances for requests later refused;
  every request with vitals has a new commitment; strict OBSERVE refusals happen before PERCEIVE (`governance_decision` is `None`); in strict the reading is assessed before the event-time attestation is judged (the request must exist to be attested, and it commits to the assessment); a caller-supplied `observe_assessment` is dropped, never committed. Edited
  pre-existing tests: `test_chain_closure.py::test_a_failed_execution_carries_a_committed_outcome_that_says_it_failed` and
  `test_chain_failure_paths.py::test_execution_failure_is_a_recorded_state_that_keeps_the_approval` (a failed execution's chain is now valid
  and complete, no longer "not complete"; nothing else in them changed). Document: `docs/race/OBSERVE_FIRST_10B.md`.
- **A probe that may be off is now said so.** An SpO2 of 0 is far more often a disconnected probe than a patient with no oxygen, and
  may be both. A reading at or below `ClinicalSettings.probe_off_spo2_at_or_below` (default 0; `None` switches it off) now carries
  `POSSIBLE_SENSOR_DISCONNECT: ... check the probe and check the patient. The reading is not downgraded.` in its rules and, only
  then, under `advisory_notes` in the audit entry. It never changes a regime, a score or a page (a zero is still a hard rule), so
  the only difference is the note, and an entry with no note keeps its shape. New cassette hook `advisory_notes` (optional). This is
  the last item of the old session branch `ccr-2064e451-0djr9b` that was not on `main`. No pre-existing test edited. Document:
  `docs/race/CLINICIAN_SETTINGS_AND_POPULATION.md`.

- **Clinicians can now adjust the clinical lines, and a patient above the supported population is no longer scored silently as a
  child.** Measured on `main`: every age from 36 months to 125 years used the "child" norms, so a healthy 40 year old at heart rate 70
  was reported with `BRADYCARDIA: 70 < 100` and nothing said the patient was out of scope. New `clinical_settings.ClinicalSettings`
  (unknown keys, wrong types and values that break an engine assumption raise; frozen; a digest covers every value): population bound
  and action, `require_age`, age-group boundaries, the norms table, the SpO2 hard line, the hypothermia line, and the three
  hard-rule values. `PediatricCassette(settings=...)`, `ClinicalGovernanceSystem(settings=...)` and both builders take it (a path, a
  mapping or an object); a bad or missing file stops the system rather than running on the defaults. Every `ClinicalDecision` has
  `settings_digest`; every non-default value is named in the OBSERVE record's cassette identity, and the ones that alert less are
  listed and logged. **New default behaviour:** a reading above 216 months is assessed exactly as before but reported at WARNING at
  least with the finding `OUTSIDE_SUPPORTED_POPULATION`, paged once (again only after a reading in scope), never lowering a tracked regime
  (`outside_population=assess_anyway` or `max_age_months` turns it off). Engines gained keyword arguments with the old values as
  defaults; the hard-rule cassette arguments still work and still record under the same names (given beside `settings` they raise).
  No pre-existing test edited. Not done: school-age and adolescent norm rows (clinical content, needs sign-off), the remaining
  constants listed in the document. Document: `docs/race/CLINICIAN_SETTINGS_AND_POPULATION.md`.

- **A governance decision is now bound to the attempt, the patient and the reading.** Measured on `main`: a refused attempt and its retry
  shared one `request_id` (it is the artifact id); an action about patient A executed with patient B's vitals in advisory and in strict,
  with nothing naming it; with no patient in the context PERCEIVE's subject was the artifact, so a patient's per-subject limits never
  accumulated; and the request commitment was identical for calm and CRITICAL vitals. Now every `orchestrate_request` call has an
  `attempt_id` (on the result, the receipt envelope, every alert and ticket); a request whose context patient disagrees with the vitals'
  patient (or carries a wrong `observation_digest`) is refused in every profile before anything runs; the vitals'
  patient is bound into the request when none is named, so limits accumulate per patient; and an `observation_digest` of the reading is in
  the request, so the commitment covers which reading. Only `patient_id` is compared with the vitals (five adapters legitimately put a
  non-patient entity in `subject_id`); a mismatch also tells a person and still assesses and pages the reading for the vitals' patient. Results record `identity` with a `subject_source`. `orchestrate_request` no longer
  mutates the caller's context dict. Edited pre-existing tests (they contained the mismatch): five calls in `test_audit_p5_fixes.py` and
  one in `test_observe_crash.py` now pass patient `P001`, the vitals' patient; no assertion changed. Not done (10b): OBSERVE first in every
  profile and binding the assessment. Document: `docs/race/IDENTITY_BINDING_10A.md`.

- **Something now calls `sweep()`, and something notices when it stops.** `EscalationDesk` has no timer, so an unanswered page
  never moved to the next tier and nobody knew. `escalation_runner.SweepRunner` runs a sweeper thread and a watchdog thread. The
  watchdog alarms, through a channel of its own, on: no clean sweep for `max_silence_s` (including a hung sweep, and a desk that
  does not answer, since the desk's lock is held across its disk writes); a dead sweeper thread (restarted); a ticket nobody could be
  reached for (once each); the desk's log failing; and the recovery. Alarms are coalesced (one pending per kind), delivered by one
  thread at a time, and retried when the channel is down, so a dead channel is neither a thread leak nor a storm. Every interval is
  required (no defaults), as is `max_tickets_per_sweep`; a silence limit a healthy busy sweep could exceed is refused.
  `GovernanceOrchestrator(sweep_runner=...)` is required by `profile="production"`: a running runner bound to the same desk. Edited
  pre-existing test (the profile now requires it): `test_production_profile.py` fixture `wiring()` builds and starts a runner.
  Not done: it cannot prove the alarm channel reaches a person, cannot watch itself, and dies with the process. Document:
  `docs/race/ESCALATION_SWEEP_RUNNER.md`.

- **An OBSERVE crash is never a quiet one, and never moves a patient's tracked state.** OBSERVE moves a patient's escalation
  state before it records the reading, so a crash part way left "already escalated, locked" for a reading no ledger holds and the
  patient's next CRITICAL was swallowed as a repeat; `ClinicalGovernanceSystem` let the exception propagate (no decision, nobody
  told); and the orchestrator's advisory path recorded `observe_error` and told nobody. Now `ObserveClinicalEngine.evaluate` is
  all-or-nothing for the patient's tracked state (snapshot on entry, restore on any failure, the error re-raised unchanged): a
  crash after the state moved is undone, and a crash before it moved changes nothing, so a patient tracked at CRITICAL is never
  silently dropped (a first design that reset the patient on every crash did that; an independent review found it).
  `process_vitals` returns a decision with regime `assessment_unavailable`, `escalation_required` True, action
  `escalate_assessment_unavailable` and its own `GovernanceStatus.ASSESSMENT_UNAVAILABLE`, and a person is always told (a desk
  ticket naming the patient, not switchable off, or the legacy `alert_human`). The orchestrator's advisory path now alerts too,
  and `observe`-stage alerts name the patient. Behaviour change: with no OBSERVE engine configured, an advisory request that
  carries vitals now raises an alert every time. No existing test edited. Not done: the advisory path still runs OBSERVE after the
  action. Document: `docs/race/OBSERVE_CRASH_HANDLING.md`.

- **A hard rule that fusion would dilute is held at WARNING once confirmed.** A hard rule (the pediatric `CRITICAL_O2`,
  SpO2 below 88%) skipped the dwell only when the fused regime was already WARNING or CRITICAL, so any `previous_*` history,
  which adds the trajectory engine and pulls the average down, could fuse it away. Measured: a child with SpO2 86%, fever
  39.1, HR 138 and RR 34, with 15 minutes of history, was rated STABLE (risk 0.33) with no escalation; now WARNING with an
  escalation event. A hard rule is confirmed by another abnormal channel, by repeating within `hard_rule_confirm_window`
  readings (default 3), or at once when the clinician sets the window to 1; a lone uncorroborated reading is judged as
  before (it may be an artefact). The floor only raises (a patient tracked at CRITICAL stays CRITICAL), the fused risk is
  floored to match, and the lift is in the decision's `safety_bypass` note. `PediatricCassette` takes
  `hard_rule_confirm_window`, `hard_rule_corroboration` and `hard_rule_corroboration_margin`, validated, recorded in the
  cassette identity when not at their defaults and fixed once built; the cassette version and every recorded fingerprint are
  unchanged. A patient is not evicted while a hard rule is pending; a duplicate, replayed or older reading is never a repeat.
  Edited pre-existing test (it asserted the exact behaviour changed): `test_detection_record.py` no longer lists
  `repeated_spikes_o2` among the recorded unsafe cases. Not done: adults are still scored on pediatric norms; the default
  window is a clinician's decision. Document: `docs/race/HARD_RULE_FLOOR.md`.

- **An escalation event now reaches a person, per settings the clinician controls.** Until now an approved CRITICAL
  returned `escalate_approved` and told nobody, and a CRITICAL that governance BLOCKED (the default for a first
  CRITICAL without `physician_approved`) returned `escalate_blocked` and told nobody either; only "governance crashed"
  alerted. `clinical_notification.py` adds `ClinicalNotificationSettings` (`min_regime` default critical, and switches
  for severity increase, blocked escalation, and unassessable reading, all defaulting on) and one decision rule used
  by both `ClinicalGovernanceSystem` and `GovernanceOrchestrator`. With an `EscalationDesk` the event opens a ticket and
  the decision or record gains `clinical_notification` (`reason`, `status`, `ticket`); the orchestrator sets it before
  the receipt so the receipt records it. Only events notify, so a patient who stays critical is not paged again.
  Without a desk, `ClinicalGovernanceSystem` now also calls `alert_human` for a blocked escalation. Notification
  never changes an outcome. Edited pre-existing test (it asserted the exact behaviour changed):
  `test_clinical_governance_system.py::test_status_is_named_on_every_normal_path_and_no_alert_fires` (a blocked
  escalation now alerts; an approved one still does not), and `test_production_profile.py::test_something_that_is_not_a_desk_is_refused`
  (a non-desk `escalations` is now refused at construction, as `guard_executor=False` already was, and by the profile's
  own check). An independent review found a failing legacy pager corrupting the PERCEIVE audit chain, proactive
  escalations paging every reading, and an event lost when strict re-raises; each is fixed and tested. Not done: a request refused before OBSERVE ran has no
  verdict and so no notification; whether CRITICAL may bypass the `micropatch` physician gate is left as a
  governance decision. Document: `docs/race/CLINICAL_ESCALATION_NOTIFICATION.md`.

- **AUGUR's simulation screen is removed from the chain.** The optional veto-only screen
  (`simulation_screen`, `simulation_seed`, the PHASE 1b stage, the `simulation_screen` record key) is gone, and so
  are `augur_screen_adapter.py`, its test file and the demo stand-in `fixture_augur.py`; they are buried verbatim in
  the Graveyard repo (`observe-perceive/2026-11-13-augur-screen-adapter`). The demo chain goes from five gates to
  four (governance_gateway, ccc, dit, conservation_kernel), the `augur` pins leave `pyproject.toml` and
  `requirements-demo.txt`, and CI no longer makes an `AUGUR_AUDIT_KEY`. `demo_why.py` keeps step 6 as an
  explicit "no stage" line so the ten-step numbering holds. Edited pre-existing tests (they assert the exact
  behaviour changed): `test_cns_governed_decision_demo.py` (gate list, S1 to S7b expectations, S4 now refused by ccc,
  fixture file set, requirements set, script test no longer needs the key, fixture file count 8 to 7);
  removed: `test_a_crashing_screen_refuses_and_says_it_crashed` in `test_chain_failure_paths.py` and
  `test_augur_screen_adapter.py` (both test only the removed stage). Dated audit and closure documents still
  mention AUGUR on purpose: they are history.

- **A person who is told can be followed up, and "delivered" stops meaning "a function returned".**
  `human_escalation.py` adds an `EscalationDesk`. Every event that used to call `alert_human` (a crashed
  stage, a lost receipt, a failed execution) opens a ticket in a hash-chained, signed log. A ticket is
  `dispatched` when a channel callable returns, `delivered` only when the channel returns its own receipt id,
  and `acknowledged` only through a signature by a named responder over a digest bound to that ticket. A
  channel can never acknowledge. A raising or hung channel (`dispatch_timeout_s`) moves to the next tier at
  once; an unanswered ticket moves up on `sweep()` and ends `exhausted`, open and loud. A log that cannot be
  written never stops a page; a ticket stranded by a crash is picked up by the next sweep; one writer per log.
  There are no default timings. `GovernanceOrchestrator(escalations=desk)` replaces `alert_human` (both is
  refused) and the record gains `escalation_ticket`; `human_alert` says `delivered` only for a channel receipt,
  `dispatched` for a callable that merely returned. The legacy callable behaves as before. `profile="production"`
  now requires a valid desk. Two independent reviews found defects in the first versions (a log failure silencing the page, tickets stranded after a crash, a restart bricked by routine changes); each has a test
  that fails without its fix, and a mutation check of the module leaves no surviving mutant but two argued
  equivalents. Edited pre-existing tests (they assert the exact behaviour changed): `test_production_profile.py`
  fixture `wiring()` and the parametrized case that asserted "no alert_human channel".
  Not done: a scheduler and watchdog for `sweep()`, and tickets for clinical escalations (next change).
  Document: `docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md`.

- **`profile="production"` proves its wiring or does not start.** The strict profile asked only that the
  Conservation Kernel have *a* root guard, so strict plus `lambda artifact: True` executed an action with no
  CNS roster anywhere. `production` is strict plus a startup proof (`production_profile.py`): CNS at v2.3.0
  or later; the kernel's root guard is the `RootAdmission` handed to the orchestrator (identity); a canary the
  roster must refuse is refused, registers nothing and leaves the guard closed; receipts and the execution
  ledger are durable and signed; an alert channel, OBSERVE and event-time source keys are configured.
  Every problem is reported at once. Advisory and strict are unchanged. (A human-notification policy is now
  required too; see the entry above.) Documents: `docs/race/GOVERNED_EXECUTION_PROFILE.md`,
  `docs/race/CNS_ADMISSION_AND_IDENTITY_AUDIT.md`.

- **`pyproject.toml` lists `cassette` and `pediatric_cassette`.** Both are imported at module level
  by listed modules (`observe_consolidated` imports `cassette`), and neither was in `py-modules`, so
  an installed copy could not import the package. A test walks the imports of every listed module
  and fails on any local module that is not listed.

- **A cooldown no longer hides a worsening patient.** On `main`, a patient escalated at WARNING who
  worsened to CRITICAL was held at WARNING for the whole 300 s lock and, with a hard-rule bypass,
  changed regime but raised no escalation event (`escalation_required=False`, action
  `continue_monitoring`); a bypass could also lower a patient tracked at CRITICAL. Now the lock
  suppresses only repeats at the same severity, any move up into WARNING or CRITICAL is an escalation
  event (an already-escalated patient who worsens is acted on at the first reading, no dwell; a first
  escalation from below still needs its dwell), a bypass never lowers a tracked regime, and every verdict and decision names its
  `escalation_kind` (`new_escalation`, `severity_increase`, `de_escalation`) and `previous_regime`.
  Existing entries and fingerprints are unchanged (the two new kinds are recorded only when they
  occur). One existing test edited: `test_escalation_lock_prevents_immediate_re_escalation` asserted
  that a CRITICAL candidate is refused during the lock. Notification, receipt, acknowledgement and
  human-governed recovery are not part of this change. See `docs/race/ESCALATION_INTEGRITY_DESIGN.md`
  and `docs/race/ESCALATION_HAZARD_ANALYSIS.md`.

- **The demo requirements install the libraries without their `[cns]` extras.** CI's `cns` job
  had failed at install since the CNS pin moved to v2.3.0 (run 137): `requirements-demo.txt`
  installed Governance Gateway, CCC, DIT and the Conservation Kernel with their `[cns]` extra, and
  at those commits each extra pins CNS v2.2.0, which pip cannot install beside v2.3.0. They are
  now installed from the same commits without the extra, as AUGUR already was, and CNS comes from
  the one explicit v2.3.0 line. README, the `pyproject.toml` comment and the false comment in
  `requirements-demo.txt` now say what is true. The default test job and the future-clock pass run
  with `-rs`, so skipped tests are listed. One test edited: the requirements test asserted the extra.
  Evidence and what was not verified: `docs/race/DEPENDENCY_COMPATIBILITY_REPORT.md`.

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
