# CNS governed decision demo: a recorded run

This page records one real run of `examples/cns_governed_decision_demo.py`. The demo builds one `GovernedPipeline` from the real CNS gates of five libraries and runs eleven scenarios through it: governance_gateway, ccc and augur judge before the work, a stub work function writes a release summary, and dit and conservation_kernel judge the result. Each scenario prints every verdict (gate, position, outcome, reason), which gates were not evaluated and why, how often each gate was asked, whether the work ran, and the single final decision. The output below is unedited.

The layer it exercises is `cns_governed_decision.py`, described in the README section "Governed decision across libraries (optional)". It combines verdicts. It does not replace `GovernanceOrchestrator`, and it only works for libraries that ship a CNS connector.

## Command

Run in a scratch virtual environment (Python 3.11.15) holding cns 1.4.0 (commit 3b465dbc, the tag v1.4.0 commit) and the five libraries, each installed with its `[cns]` extra from a scratch copy of its local clone at the branch commit: dit 087b0c7, governance_gateway d2cb0b6, ccc 3728858, augur 7022ead, conservation_kernel 5f35dfe. observe-perceive was not installed; its working tree (commit e1ed52a plus the files that add this page) was on PYTHONPATH. The command, from an empty directory:

```text
cd /tmp/claude-0/-home-user/57a8c82a-b67c-57bc-9f2c-73e4ac1675f5/scratchpad/demo_run/cwd
PYTHONPATH=/tmp/claude-0/-home-user/57a8c82a-b67c-57bc-9f2c-73e4ac1675f5/scratchpad/demo_run/obs /tmp/claude-0/-home-user/57a8c82a-b67c-57bc-9f2c-73e4ac1675f5/scratchpad/demo_run/venv_full/bin/python \
    /tmp/claude-0/-home-user/57a8c82a-b67c-57bc-9f2c-73e4ac1675f5/scratchpad/demo_run/obs/examples/cns_governed_decision_demo.py
```

From a checkout of this repository the equivalent is `PYTHONPATH=. python examples/cns_governed_decision_demo.py` after `pip install -r requirements-demo.txt`.

## Output

Exit status 0, nothing on stderr, 323 lines. Unedited:

```text
CNS governed decision demo
==========================

Installed: cns 1.4.0, governance-gateway 0.1.0, cognitive-continuity-constitution 0.1.0, augur 1.0.0, dit 13.1.0, conservation-kernel 0.3.0

The pipeline (one GovernedPipeline; each gate is the library's own CNS connector):
  ALPHA governance_gateway: Admits the source batch release record only if it is well formed,
                            carries provenance, authority, an epistemic status and an explicit valid
                            scope, and its SHA-256 seal still matches its content, so a record
                            edited after sealing (and not resealed) never reaches the summary work.
  ALPHA ccc: Before the summary is written, refuses any request to file the assistant's
             machine-generated status summary as human-established historical record, because CCC
             lets a model propose but never establish.
  ALPHA augur: Before the summary is written, simulates the planned action's scenario and vetoes it
               if the simulation predicts an unstable or critical regime at its last step.
  OMEGA dit: Checks the released status summary text for first-person wording, hedging, affective
             claims (feel, hope, believe) and a missing figure or cause, and refuses to let a
             summary that reports a belief leave.
  OMEGA conservation_kernel: Checks that the released status summary conserves the claims of the
                             source batch release record, and refuses a summary that restates an
                             estimate as established fact without verification or authorization.
  work  stub: writes a release summary and its transformation record from the plan and counts its calls. It is not a model.

What the plan controls (ok / refused / malformed / hedged), one field per gate:
  source          the batch record admitted by governance_gateway (refused: edited after sealing)
  filing          the status ccc is asked to file the summary under (refused: HISTORICAL_RECORD; malformed: not an Attempt)
  scenario        the planned action augur simulates (refused: a violent one)
  wording         what the work writes (hedged: 'may be')
  transformation  the typed record conservation_kernel judges (refused: the shelf-life estimate restated as fact; malformed: not a TransformationCandidate)

APPROVED means every gate in this pipeline passed. It does not mean the summary is correct.

--- S1: Everything passes ---
  Every gate is handed its passing candidate.
  plan: source=ok filing=ok scenario=ok wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. dit  OMEGA  PASS
       reason: (none)
    5. conservation  OMEGA  PASS
       reason: PASS_WITH_DECLARED_TRANSFORMATION (unverifiable: semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: released (195 characters)
  final decision: APPROVED
  decision digest: ea8f3fdb7fc2371e
  expected: APPROVED with 1 work call(s): ok

--- S2: An OMEGA text gate refuses retryably ---
  The work writes a hedge ('may be'). dit answers RETRY: a reworded summary could pass.
  conservation_kernel passes.
  plan: source=ok filing=ok scenario=ok wording=hedged transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. dit  OMEGA  RETRY
       reason: hedging: Subjective hedging / unverified statements detected: 'may'.
    5. conservation  OMEGA  PASS
       reason: PASS_WITH_DECLARED_TRANSFORMATION (unverifiable: semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: RETRY
  decision digest: b19aa0b0abd93801
  expected: RETRY with 1 work call(s): ok

--- S3: An OMEGA gate refuses terminally; every other gate passed ---
  The work restates the shelf-life estimate as fact. conservation_kernel answers TERMINAL_BREACH.
  The three ALPHA gates and dit all passed, and the refusal still decides the whole run.
  plan: source=ok filing=ok scenario=ok wording=ok transformation=refused
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. dit  OMEGA  PASS
       reason: (none)
    5. conservation  OMEGA  TERMINAL_BREACH
       reason: REJECT: UNDECLARED_CHANGE[p-shelf-life]: output changed a protected dimension without
               declaring it; UNDECLARED_CHANGE[p-shelf-life]: output changed a protected dimension
               without declaring it; UNDECLARED_CHANGE[p-shelf-life]: output changed a protected
               dimension without declaring it; NO_INDEPENDENT_VERIFICATION[p-shelf-life]: promotion
               to fact/observation requires independently supplied verification;
               MISSING_EPISTEMIC_AUTHORIZATION[p-shelf-life]: promotion into strong epistemic status
               requires a matching human authorization; UNCERTAINTY_COLLAPSE[p-shelf-life]:
               uncertainty cannot disappear without independent verification (unverifiable:
               semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: 1a9fa7e1eb8ae53a
  expected: TERMINAL_BREACH with 1 work call(s): ok

--- S3b: A retry and a terminal breach together ---
  dit answers RETRY and conservation_kernel TERMINAL_BREACH. A RETRY does not stop evaluation, and
  the terminal verdict wins.
  plan: source=ok filing=ok scenario=ok wording=hedged transformation=refused
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. dit  OMEGA  RETRY
       reason: hedging: Subjective hedging / unverified statements detected: 'may'.
    5. conservation  OMEGA  TERMINAL_BREACH
       reason: REJECT: UNDECLARED_CHANGE[p-shelf-life]: output changed a protected dimension without
               declaring it; UNDECLARED_CHANGE[p-shelf-life]: output changed a protected dimension
               without declaring it; UNDECLARED_CHANGE[p-shelf-life]: output changed a protected
               dimension without declaring it; NO_INDEPENDENT_VERIFICATION[p-shelf-life]: promotion
               to fact/observation requires independently supplied verification;
               MISSING_EPISTEMIC_AUTHORIZATION[p-shelf-life]: promotion into strong epistemic status
               requires a matching human authorization; UNCERTAINTY_COLLAPSE[p-shelf-life]:
               uncertainty cannot disappear without independent verification (unverifiable:
               semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: ba478227703751a6
  expected: TERMINAL_BREACH with 1 work call(s): ok

--- S4: An ALPHA gate refuses: the work is never called ---
  augur is handed a violent scenario and vetoes it. The two ALPHA gates before it passed. The work
  does not run and neither OMEGA gate is asked.
  plan: source=ok filing=ok scenario=refused wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  TERMINAL_BREACH
       reason: refused: the simulation predicts regime CRITICAL at its last step (final error
               86.008, distortion 0.212). A predicted instability is sufficient reason not to
               proceed, and nothing in AUGUR repairs it.
  not evaluated:
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: f328133900e45263
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S5: Two ALPHA gates would refuse; the first terminal one stops the rest ---
  The source record was edited after sealing (governance_gateway refuses) and the assistant asks to
  file its summary as human-established history (ccc would refuse). After the first TERMINAL_BREACH
  no later gate is asked, because a gate may have side effects.
  plan: source=refused filing=refused scenario=ok wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  TERMINAL_BREACH
       reason: INTEGRITY_FAILURE
  not evaluated:
    ccc.transition: short-circuit: governance_gateway returned TERMINAL_BREACH
    augur_screen: short-circuit: governance_gateway returned TERMINAL_BREACH
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=0 augur_screen=0 dit=0 conservation=0
  for comparison, asked outside the pipeline on the same candidate:
    ccc.transition  ALPHA  TERMINAL_BREACH: CCC-RATIFICATION-001: machine-generated material cannot
                                            be ingested as human-established history/evidence
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 37894c8757d6802c
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S6: A required ALPHA library is missing ---
  The ccc slot is left empty, as if ccc were not installed. A required gate that is missing is a
  refusal, not a skipped check. (Simulated by this demo: the gate is withheld, nothing stands in for
  it.)
  plan: source=ok filing=ok scenario=ok wording=ok transformation=ok
  library withheld: ccc
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate unavailable: required gate 'ccc.transition' is not installed or not supplied
  not evaluated:
    augur_screen: short-circuit: ccc.transition returned TERMINAL_BREACH
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=0 augur_screen=0 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 92acca5db26347f5
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S6b: A required OMEGA library is missing ---
  The conservation_kernel slot is left empty. A result that cannot be judged is not produced, so the
  work is not started even though every ALPHA gate passed.
  plan: source=ok filing=ok scenario=ok wording=ok transformation=ok
  library withheld: conservation_kernel
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. conservation  OMEGA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate unavailable: required output gate 'conservation' is not installed or not
               supplied, so the work was not started
  not evaluated:
    dit: not evaluated: the work did not run, a required output gate is unavailable
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 7264b357b4d4d6bd
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S7: An ALPHA gate raises ---
  ccc is handed text instead of an Attempt and its real gate raises TypeError. The pipeline does not
  crash: the exception becomes a TERMINAL_BREACH.
  plan: source=ok filing=malformed scenario=ok wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate 'ccc.transition' raised TypeError: CCC gates judge an Attempt (see
               ccc.cns_connector.attempt); got str
  not evaluated:
    augur_screen: short-circuit: ccc.transition returned TERMINAL_BREACH
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=0 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 61e2f19ec7f37b0f
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S7b: An OMEGA gate raises after the work ---
  The work returns text instead of a TransformationCandidate, and the real conservation_kernel gate
  raises TypeError. The work did run, so the run is recorded as executed, and the result is
  withheld.
  plan: source=ok filing=ok scenario=ok wording=ok transformation=malformed
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. augur_screen  ALPHA  PASS
       reason: no objection: the simulation ended in regime STABLE at its last step (final error
               9.855, distortion 0.000). This is an absence of objection, not evidence that acting
               is safe. AUGUR cannot approve. Only the last step is judged: earlier steps of the run
               may have been UNSTABLE or CRITICAL.
    4. dit  OMEGA  PASS
       reason: (none)
    5. conservation  OMEGA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate 'conservation' raised TypeError: the conservation gate judges a
               TransformationCandidate; got str
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 augur_screen=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: a3b2ebe154904e7b
  expected: TERMINAL_BREACH with 1 work call(s): ok

--- S8: A verdict or a decision record is edited after the fact ---
  The text approved in S1 is edited afterwards (dissolution 96.0% becomes 99.9%), and the digest of
  the edited text is recomputed. The decision record of S3 is edited (TERMINAL_BREACH becomes pass)
  and its digest recomputed.
  dit verdict binds to the text it judged: True
  dit verdict binds to the edited text, digest recomputed: False
  a verdict built by hand with the recomputed digest binds: True
    That last line is the limit: the digest is tamper-evidence, not tamper-proofing. It catches a
    verdict moved onto other text, not someone who rebuilds the verdict.
  S3 record: digest recomputes to the recorded digest: True
  S3 record edited to say pass: digest recomputes to the recorded digest: False
  expected: the edited text does not bind, the edited record does not verify: ok

--- Summary ---
  scenario  final decision    work calls  expected
  S1        APPROVED          1           ok
  S2        RETRY             1           ok
  S3        TERMINAL_BREACH   1           ok
  S3b       TERMINAL_BREACH   1           ok
  S4        TERMINAL_BREACH   0           ok
  S5        TERMINAL_BREACH   0           ok
  S6        TERMINAL_BREACH   0           ok
  S6b       TERMINAL_BREACH   0           ok
  S7        TERMINAL_BREACH   0           ok
  S7b       TERMINAL_BREACH   1           ok
  S8        (tamper check)    -           ok

ALL SCENARIOS AS EXPECTED
```

## Repeat runs

The same command was run a second time, and the two outputs are byte-identical (md5 40f1aa4cec4480d8cd21f9207f08fd0e, sha256 643fb93c2c3a7271b9b3839f402e17969ba20bd469f8eebd6ae1db1e19be8faf). Nothing in the output varies from run to run: it carries no timestamps, no generated ids and no paths, and the digests are over content only. The same bytes also came out with PYTHONHASHSEED set to 0 and to 12345, from a second environment built with `pip install -r requirements-demo.txt` (which installs the branch refs and resolved to the same five commits), and on Python 3.13.12. The working directory was empty before and after each run: AUGUR appends audit lines to a log, and the demo's fixture sends them to a temporary file in the system temp directory instead of the working directory (that file is not deleted afterwards).

## What the output shows, and what it does not

- S1 to S3b: the combined outcome follows the strictest verdict (R4). A RETRY from dit alone gives RETRY; a terminal refusal from conservation_kernel decides the run even though four other gates passed; a RETRY and a terminal refusal together give TERMINAL_BREACH.
- S4 and S5: after an ALPHA refusal the work is never called (work calls 0) and neither OMEGA gate is asked (check() calls 0). In S5 the second refusing gate is not asked because the first terminal verdict stops its end; the comparison line shows that ccc, asked outside the pipeline on the same candidate, does refuse.
- S6 and S6b: a required gate that is missing is a refusal. These two are simulated by the demo, which leaves the slot empty as if the library were not installed; nothing stands in for the missing gate. The verdict is marked as issued by the combining layer.
- S7 and S7b: the real ccc and conservation_kernel gates raise TypeError when handed the wrong kind of object. The pipeline turns that into a TERMINAL_BREACH and does not crash. In S7b the work did run, so the record says executed and the result is withheld.
- S8: an edited text no longer binds to the verdict issued for the original, and an edited decision record no longer matches its digest. A verdict rebuilt by hand with the recomputed digest does bind, so the digest is tamper-evidence, not tamper-proofing.

Limits. The work is a stub, not a model; it builds the summary and its typed record from a plan. APPROVED means every gate in the pipeline passed, not that the summary is correct: AUGUR says it cannot approve, and the conservation kernel verifies the typed envelope, not the prose. No scenario was dropped for lack of fixture support, but no fixture can drive a RETRY from an ALPHA library (ccc's RETRY path needs an artifact id that a zero-argument candidate cannot name, and augur reserves RETRY for scenarios it cannot screen), so that case is not shown.
