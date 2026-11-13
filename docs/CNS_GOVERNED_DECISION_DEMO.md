# CNS governed decision demo: a recorded run

This page records one real run of `examples/cns_governed_decision_demo.py`. The demo builds one `GovernedPipeline` from the real CNS gates of four libraries and runs eleven scenarios through it: governance_gateway and ccc judge before the work, a stub work function writes a release summary, and dit and conservation_kernel judge the result. Each scenario prints every verdict (gate, position, outcome, reason), which gates were not evaluated and why, how often each gate was asked, whether the work ran, and the single final decision. The output below is unedited.

The layer it exercises is `cns_governed_decision.py`, described in the README section "Governed decision across libraries (optional)". It combines verdicts. It does not replace `GovernanceOrchestrator`, and it only works for libraries that ship a CNS connector.

## Command

Run with Python 3.13.18, cns 4.0.0 (tag v4.0.0) and the four libraries installed without dependencies at the exact commits in `requirements-demo.txt`: governance_gateway ad8b644, ccc 9806573, dit c801374, conservation_kernel 4e7c0d9. observe-perceive was not installed; its working tree was on PYTHONPATH. The command, from an empty directory:

```text
cd "$(mktemp -d)"
PYTHONPATH=/path/to/observe-perceive:/path/to/installed/libraries python \
    /path/to/observe-perceive/examples/cns_governed_decision_demo.py
```

From a checkout of this repository the equivalent is `PYTHONPATH=. python examples/cns_governed_decision_demo.py` after `pip install -r requirements-demo.txt`.

## Output

Exit status 0, nothing on stderr, 284 lines. Unedited:

```text
CNS governed decision demo
==========================

Installed: cns 4.0.0, governance-gateway 0.1.0, cognitive-continuity-constitution 0.1.0, dit 13.1.0, conservation-kernel 0.3.0

The pipeline (one GovernedPipeline; each gate is the library's own CNS connector):
  ALPHA governance_gateway: Admits the source batch release record only if it is well formed,
                            carries provenance, authority, an epistemic status and an explicit valid
                            scope, and its SHA-256 seal still matches its content, so a record
                            edited after sealing (and not resealed) never reaches the summary work.
  ALPHA ccc: Before the summary is written, refuses any request to file the assistant's
             machine-generated status summary as human-established historical record, because CCC
             lets a model propose but never establish.
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
  wording         what the work writes (hedged: 'may be')
  transformation  the typed record conservation_kernel judges (refused: the shelf-life estimate restated as fact; malformed: not a TransformationCandidate)

APPROVED means every gate in this pipeline passed. It does not mean the summary is correct.

--- S1: Everything passes ---
  Every gate is handed its passing candidate.
  plan: source=ok filing=ok wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. dit  OMEGA  PASS
       reason: (none)
    4. conservation  OMEGA  PASS
       reason: PASS_WITH_DECLARED_TRANSFORMATION (unverifiable: semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: released (195 characters)
  final decision: APPROVED
  decision digest: a55bff7f985fd5fe
  expected: APPROVED with 1 work call(s): ok

--- S2: An OMEGA text gate refuses retryably ---
  The work writes a hedge ('may be'). dit answers RETRY: a reworded summary could pass.
  conservation_kernel passes.
  plan: source=ok filing=ok wording=hedged transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. dit  OMEGA  RETRY
       reason: hedging: Subjective hedging / unverified statements detected: 'may'.
    4. conservation  OMEGA  PASS
       reason: PASS_WITH_DECLARED_TRANSFORMATION (unverifiable: semantic_content_equivalence)
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: RETRY
  decision digest: 8cfb4867a05169d5
  expected: RETRY with 1 work call(s): ok

--- S3: An OMEGA gate refuses terminally; every other gate passed ---
  The work restates the shelf-life estimate as fact. conservation_kernel answers TERMINAL_BREACH.
  The two ALPHA gates and dit all passed, and the refusal still decides the whole run.
  plan: source=ok filing=ok wording=ok transformation=refused
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. dit  OMEGA  PASS
       reason: (none)
    4. conservation  OMEGA  TERMINAL_BREACH
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
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: 6c7ab0c4eb958d99
  expected: TERMINAL_BREACH with 1 work call(s): ok

--- S3b: A retry and a terminal breach together ---
  dit answers RETRY and conservation_kernel TERMINAL_BREACH. A RETRY does not stop evaluation, and
  the terminal verdict wins.
  plan: source=ok filing=ok wording=hedged transformation=refused
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. dit  OMEGA  RETRY
       reason: hedging: Subjective hedging / unverified statements detected: 'may'.
    4. conservation  OMEGA  TERMINAL_BREACH
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
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: 9a0fbf5a88fef717
  expected: TERMINAL_BREACH with 1 work call(s): ok

--- S4: An ALPHA gate refuses: the work is never called ---
  ccc is asked to file the summary as human-established history and refuses. The ALPHA gate before
  it passed. The work does not run and neither OMEGA gate is asked.
  plan: source=ok filing=refused wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  TERMINAL_BREACH
       reason: CCC-RATIFICATION-001: machine-generated material cannot be ingested as
               human-established history/evidence
  not evaluated:
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 83c9d56e8baa8825
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S5: Two ALPHA gates would refuse; the first terminal one stops the rest ---
  The source record was edited after sealing (governance_gateway refuses) and the assistant asks to
  file its summary as human-established history (ccc would refuse). After the first TERMINAL_BREACH
  no later gate is asked, because a gate may have side effects.
  plan: source=refused filing=refused wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  TERMINAL_BREACH
       reason: INTEGRITY_FAILURE
  not evaluated:
    ccc.transition: short-circuit: governance_gateway returned TERMINAL_BREACH
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=0 dit=0 conservation=0
  for comparison, asked outside the pipeline on the same candidate:
    ccc.transition  ALPHA  TERMINAL_BREACH: CCC-RATIFICATION-001: machine-generated material cannot
                                            be ingested as human-established history/evidence
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 96155439b2210601
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S6: A required ALPHA library is missing ---
  The ccc slot is left empty, as if ccc were not installed. A required gate that is missing is a
  refusal, not a skipped check. (Simulated by this demo: the gate is withheld, nothing stands in for
  it.)
  plan: source=ok filing=ok wording=ok transformation=ok
  library withheld: ccc
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate unavailable: required gate 'ccc.transition' is not installed or not supplied
  not evaluated:
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=0 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: ad2505dba45b0bdf
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S6b: A required OMEGA library is missing ---
  The conservation_kernel slot is left empty. A result that cannot be judged is not produced, so the
  work is not started even though every ALPHA gate passed.
  plan: source=ok filing=ok wording=ok transformation=ok
  library withheld: conservation_kernel
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. conservation  OMEGA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate unavailable: required output gate 'conservation' is not installed or not
               supplied, so the work was not started
  not evaluated:
    dit: not evaluated: the work did not run, a required output gate is unavailable
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 1126f711171547ef
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S7: An ALPHA gate raises ---
  ccc is handed text instead of an Attempt and its real gate raises TypeError. The pipeline does not
  crash: the exception becomes a TERMINAL_BREACH.
  plan: source=ok filing=malformed wording=ok transformation=ok
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate 'ccc.transition' raised TypeError: CCC gates judge an Attempt (see
               ccc.cns_connector.attempt); got str
  not evaluated:
    dit: not evaluated: the work did not run, an alpha gate did not pass
    conservation: not evaluated: the work did not run, an alpha gate did not pass
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=0 conservation=0
  work executed: NO (work calls: 0)
  result: none, the work did not run
  final decision: TERMINAL_BREACH
  decision digest: 1f85ffd2be661e6b
  expected: TERMINAL_BREACH with 0 work call(s): ok

--- S7b: An OMEGA gate raises after the work ---
  The work returns text instead of a TransformationCandidate, and the real conservation_kernel gate
  raises TypeError. The work did run, so the run is recorded as executed, and the result is
  withheld.
  plan: source=ok filing=ok wording=ok transformation=malformed
  verdicts, in the order they were evaluated:
    1. governance_gateway  ALPHA  PASS
       reason: (none)
    2. ccc.transition  ALPHA  PASS
       reason: admitted: no constitutional rule refused this transition
    3. dit  OMEGA  PASS
       reason: (none)
    4. conservation  OMEGA  TERMINAL_BREACH   [issued by the combining layer, not by a gate]
       reason: gate 'conservation' raised TypeError: the conservation gate judges a
               TransformationCandidate; got str
  not evaluated: none
  gate check() calls: governance_gateway=1 ccc.transition=1 dit=1 conservation=1
  work executed: yes (work calls: 1)
  result: withheld
  final decision: TERMINAL_BREACH
  decision digest: 9141b896761dd076
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

The same command was run a second time with PYTHONHASHSEED set to 12345, and the two outputs are byte-identical (md5 779a4e1ea346c323088029eef27ea851, sha256 d991c52725d7465bcc196f53ee1cad982f3c97e3e07c05dcb5565a8bc3124d74). Nothing in the output varies from run to run: it carries no timestamps, no generated ids and no paths, and the digests are over content only. The working directory was empty before and after each run.

## What the output shows, and what it does not

- S1 to S3b: the combined outcome follows the strictest verdict (R4). A RETRY from dit alone gives RETRY; a terminal refusal from conservation_kernel decides the run even though three other gates passed; a RETRY and a terminal refusal together give TERMINAL_BREACH.
- S4 and S5: after an ALPHA refusal the work is never called (work calls 0) and neither OMEGA gate is asked (check() calls 0). In S5 the second refusing gate is not asked because the first terminal verdict stops its end; the comparison line shows that ccc, asked outside the pipeline on the same candidate, does refuse.
- S6 and S6b: a required gate that is missing is a refusal. These two are simulated by the demo, which leaves the slot empty as if the library were not installed; nothing stands in for the missing gate. The verdict is marked as issued by the combining layer.
- S7 and S7b: the real ccc and conservation_kernel gates raise TypeError when handed the wrong kind of object. The pipeline turns that into a TERMINAL_BREACH and does not crash. In S7b the work did run, so the record says executed and the result is withheld.
- S8: an edited text no longer binds to the verdict issued for the original, and an edited decision record no longer matches its digest. A verdict rebuilt by hand with the recomputed digest does bind, so the digest is tamper-evidence, not tamper-proofing.

Limits. The work is a stub, not a model; it builds the summary and its typed record from a plan. APPROVED means every gate in the pipeline passed, not that the summary is correct: the conservation kernel verifies the typed envelope, not the prose. No scenario was dropped for lack of fixture support, but no fixture can drive a RETRY from an ALPHA library (ccc's RETRY path needs an artifact id that a zero-argument candidate cannot name), so that case is not shown.
