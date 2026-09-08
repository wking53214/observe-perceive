# Governance Stack Audit, 2026-09-08

Run from a clean container with all 18 repos cloned beside each other at their
2026-09-08 07:00 UTC heads. Everything marked **[executed]** was run here;
everything else is from reading and is a hypothesis. A parallel session was
pushing audit-shaped fixes to observe-perceive main during this run; nothing
below double-counts them.

## Part C, the headline: what can be sold apart

**Short answer.** The stack is far less coupled than its READMEs and its own
chain diagram suggest. Only four real code edges tie modules together, and
three of them point at the same module, Conservation Kernel. Two "stages" of
the chain are names, not dependencies. The thing that actually blocks
packaging is not coupling; it is that five repos cannot declare or satisfy
their own dependencies, and one repo is a snapshot of three others.

### C1: independence, one row per repo

Isolated = fresh copy, no siblings on disk, own venv, only the repo's declared
deps installed (plus pytest). "With deps" = the same after installing the
undeclared third-party packages the errors named. Rubric R1..R4 is defined in
the prompt; sellable alone means all four hold.

| repo | standalone value (one sentence) | sibling path deps | sibling package deps | abs paths | isolated result [executed] | R1 R2 R3 R4 | sellable alone |
|---|---|---|---|---|---|---|---|
| conservation_kernel | Verifies a transformation against an evidence registry and writes a receipt; the one module every other one leans on. | none | none | none | 52 passed | y y y y | **yes** |
| CCC | Dependency-free recurrence and continuity check for claims. | none | none | none (one path string used as test data) | 135 passed, 1 xfailed | y y y y | **yes** |
| fortress-kernel | Constraint-enforcing control kernel over a protected state, numpy only. | none | none | none | 48 passed | y y y y | **yes** (one vacuous test fixed, see D) |
| AUGUR | Seeded closed-loop behavioural simulation with an HMAC audit log. | none | none | none | 34 passed | y y y y | **yes** |
| Triad-42 | Advisory three-lens cognitive review harness. | none | none | none | 105 passed | y y y y | **yes** |
| Governance_Gateway | Small governed-artifact admission and sealing boundary. | none | none | none | 45 passed after `pip install -e .`; in-place `pytest` without install: 3 collection errors | y y y y | **yes** (needs install step documented) |
| TIE | Source-preserving transformation of material into traceable intelligence. | `tie/src` on sys.path (own tree) | none | none | 13 passed | y y y ? | yes, thin suite |
| HERALD | Language to candidate claims with confidence gating. | none | none | none | 364 passed, 25 skipped, 1 failed (a timing test: 4.1x runtime scaling, flaky by construction) | y n y y | yes with caveat: 4 skips record missing features as skips |
| innovation_os | Idea capture, evaluation and traceable development framework. | none | none | none | 285 passed | y y y ? | yes; local `main` vs `origin/master` confusion is a release-hygiene blocker |
| ghost_tools | Structural-debt and missing-code scanners; the audit tooling itself. | none | none | none | 100 passed | y y y y | yes, separate product line |
| ATS | Applicant tracking plus gov4_kernel. | none | none | 2 (`/tmp`) | 35 passed | y y n ? | no: no version, README is an index, no stated API |
| observe-perceive | The chain orchestrator plus in-repo PERCEIVE policy kernel and OBSERVE pediatric-sepsis engine. | CCC, AUGUR, GEMS, Governance_Gateway via `../` (soft: skipif); sentinel_os `../sentinel_os` (nominal, never imported, `sentinel_perceive_adapter.py:39` has no caller) | **conservation_kernel** (hard: 9 test files import at module level; orchestrator Phase 3 calls it unconditionally, `governance_orchestrator.py:70,236`), fortress_unified (opt-in in code, hard at import in `test_fortress_*`) | `test_orchestrator_ccc_adapter.py:23` = `/home/wking53214/CCC` | 10 collection errors (9 conservation_kernel, 1 fortress); in place 475 passed, 5 skipped | n n y n | **no** alone; yes as "spine + Conservation Kernel" |
| sentinel_os | Artifact origin, custody and Postgres ledger service. | none | **conservation_kernel** (`sentinel_os/conservation/episode_source.py:21`, module level) | 9, all in one live test | 34 errors as cloned (no requirements file; needs psycopg2, anthropic, httpx, redis, cryptography, yaml); with deps: 664 passed, 39 failed, 37 errors, 228 skipped, every failure a Postgres connection | n n y ? | **no** as cloned: no dependency manifest, and the same Postgres absence makes some tests skip and others fail |
| GSA-815 | Governed adaptive processing and execution architecture. | **sentinel_os as a git submodule** at `vendor/sentinel_os` (hard: 17 collection errors until `git submodule update --init`) | none | 2 | 17 errors as cloned; root `pytest` crashes pytest itself: `gsa-governance-core/test_harness.py:137` raises SystemExit at collection (CI runs `Tests/` only); with submodule and deps: 121 passed, 6 errors | n n y ? | **no** alone; sells as "GSA-815 + sentinel_os" |
| GEMS | Gem transport and constitutional handoff validation. | none | **conservation_kernel** in `transport/gems_transport/{artifact,contracts,pipeline}.py` (hard for the transport package; the 10 tests never import it) | none | 10 passed (transport package untested) | n y y ? | no alone; core yes, transport needs Conservation Kernel |
| ecology | Personal computational memory over a harvested corpus with RAG. | none | none | `/home/wking53214/ecology`, `.../living-memory/src`, `.../Claude_History` in `scripts/run_history_index.py:22,27` and `run_rag.py:22` | 121 passed, 1 failed (test needs `corpus/ORIGIN.tsv`, so the suite depends on the data), 6 skipped (RAG extras), 4 errors | n n n n | **no**: personal paths, and the corpus (below) |
| OBSERVE | Industry-agnostic observability layer; in practice a downstream snapshot of three repos. | contains copies of observe-perceive (`observe_consolidated.py`, byte-identical), sentinel_os (`sentinel_os/`, 302 files only here, 2 differ) and GSA-815 files; `UPSTREAM.md` says so | none | 11 | 40 errors as cloned (same undeclared deps as sentinel_os); with deps: 4 collection errors from internal drift (`compute_decision_fingerprint` missing from its `observe_consolidated`, `EnqueueResult` missing from its `queue_schema`) | n n n n | **no**: it is a snapshot, not a module |
| TOUCHSTONE | "A specimen corpus. Not a system." (its own README) | none | none | 1 | no tests | n n n n | not software; ground-truth data product at most |

### C2: the shuffle interface

**Contract usage [executed by reading every adapter].** Twelve adapters.
Four speak the shared `governance_contracts` types end to end
(sentinel→perceive, perceive→conservation, conservation→gsa815,
gsa815→observe). Eight translate pairwise or wrap: fortress (consumes
`GovernanceDecision`, emits its own result), augur (context dict → `ScreenResult`),
gateway (`AdmissionResult`), CCC (result dict → `OrchestrationFinding`), and the
four entrypoints (gems, herald, tie, innovation) which build a duck-typed
artifact with a `_Metadata` mimicking a Sentinel artifact and call
`orchestrate_request`. The de facto **input** contract is therefore "an object
with `artifact_id`, `content`, `metadata.origin_status.value`,
`metadata.authority_status.value`, `metadata.epistemic_status.value`,
`metadata.parent_artifact_ids`", not a typed class. The de facto **output** is
an untyped dict with 8 return sites and a key set that varies by path (only
`status`, `reason`, `governance_decision`, `audit_chain` appear on all).

**Stage removal [executed].** Constructing the orchestrator with a stage set to
`None` and running one request:

| removed | result |
|---|---|
| PERCEIVE (`perceive=None`) | raises `AttributeError` at request time: structurally mandatory |
| Conservation (`conservation_kernel=None`) | REJECTED, reason still reads "Conservation Kernel rejected decision: 'NoneType'..." but the record now carries `stage_error` and `stage_refused=False` (fixed in e2f0683); still mandatory, now honest |
| OBSERVE (`observe_engine=None`, no vitals) | REJECTED before OBSERVE for an unrelated reason in this probe; when the chain reaches Phase 6 it skips with `observe_enforced=False` (2b8403b): optional in practice, mandatory in name |
| GSA-815 | never imported: Phase 5 calls the `gsa815_operation_func` the caller passes in (`governance_orchestrator.py:411`). A name, not a dependency |
| sentinel_os | never imported: the lazy importer has no caller. A name, not a dependency |
| fortress-kernel, AUGUR | opt-in by constructor flag, as documented |

**Reordering and substitution [executed].** Conservation without PERCEIVE
works: a hand-built `GovernanceDecision` passed to
`PerceiveConservationAdapter.verify_perceive_decision` returns APPROVED,
verified=True, with a receipt. The adapter reads only contract fields. So any
stage that emits a `GovernanceDecision` can feed Conservation. Nothing supports
reordering inside `orchestrate_request` itself: it is one 344-line linear
function (ghost_buster flags it) with no stage registry.

**Coupling graph, code edges only, labelled with the line that creates them:**

```
observe-perceive ──hard pkg──▶ conservation_kernel   perceive_conservation_adapter.py:24; 9 tests at module level
observe-perceive ──opt-in──▶ fortress-kernel          fortress_perceive_adapter.py:42 (module-level import when adapter loaded)
observe-perceive ──opt-in──▶ AUGUR                    augur_screen_adapter.py:103
observe-perceive ──soft ../──▶ Governance_Gateway     gateway_admission_adapter.py:58
observe-perceive ──soft ../──▶ GEMS                   gems_governance_adapter.py:50
observe-perceive ──soft ../──▶ CCC                    orchestrator_ccc_adapter.py:53
observe-perceive ┄nominal┄▶ sentinel_os               sentinel_perceive_adapter.py:39 (never called)
observe-perceive ┄nominal┄▶ GSA-815                   governance_orchestrator.py:411 (caller-supplied callable)
observe-perceive ┄duck-typed, zero┄▶ HERALD, TIE, innovation_os
sentinel_os ──hard pkg──▶ conservation_kernel         sentinel_os/conservation/episode_source.py:21
GEMS(transport) ──hard pkg──▶ conservation_kernel     transport/gems_transport/artifact.py, contracts.py, pipeline.py
GSA-815 ──hard submodule──▶ sentinel_os               .gitmodules vendor/sentinel_os; 17 collection errors without it
OBSERVE ──vendored copy──▶ observe-perceive, sentinel_os, GSA-815   UPSTREAM.md, VENDORED.md
isolated nodes: CCC, ATS, Triad-42, TOUCHSTONE, ecology, ghost_tools, HERALD, TIE, innovation_os, AUGUR, fortress-kernel, Governance_Gateway
```

**Minimum sellable units.**
1. **Conservation Kernel** alone. It is the hub; sell it first and everything
   downstream becomes an add-on.
2. **Governance Spine** = observe-perceive + Conservation Kernel. Optional
   packs: fortress-kernel, AUGUR, Governance_Gateway, CCC, GEMS core. Cannot be
   split further today: PERCEIVE and the orchestrator live in one repo, and
   Conservation is called unconditionally.
3. **Ledger** = GSA-815 + sentinel_os (+ Conservation Kernel) with Postgres and
   Redis. Cannot be split: GSA-815 vendors sentinel_os as a submodule.
4. Six **singletons** already sellable alone: CCC, fortress-kernel, AUGUR,
   Triad-42, Governance_Gateway, HERALD (with its skips fixed). ghost_tools is a
   tooling product on its own. innovation_os is a product on its own once its
   default branch is settled.
5. **Never apart, and what would change it**: GSA-815/sentinel_os (replace the
   submodule with a pip dependency on a published sentinel_os), spine/Conservation
   (make Phase 3 opt-in the way fortress is, and stop importing the kernel at
   module level in tests), GEMS transport/Conservation (same). OBSERVE and
   TOUCHSTONE are not modules and should not be in the packaging conversation.

**README vs code.** The chain diagram lists eight stages; two are not wired
(GSA-815, sentinel_os) and one is optional in practice (OBSERVE). OBSERVE's
README describes an "industry-agnostic observability architecture"; the repo is
a vendored snapshot that fails to import its own copy. The clinical READMEs are
carefully hedged ("representative example", "demonstration environment"); no
unbacked FDA or 510(k) claim was found in README text, and the checklist code
was fixed in 717c2e2. The evidence gap is in the tests instead (Part B).

### C3: commercial blockers the graph does not show

| repo | license | third-party code / provenance | secrets and data (counts) | version / changelog / API |
|---|---|---|---|---|
| all 18 | Apache 2.0 except observe-perceive and fortress-kernel (MIT) | | | |
| ecology | Apache | `corpus/`: 360 harvested source files, 872M, from other systems; one file matched a private-key pattern (`corpus/master-patch.patch`); no license or provenance manifest found beyond `corpus/ORIGIN.tsv` which a test depends on | 24 files match secret-ish words | no version, no changelog |
| OBSERVE | Apache | vendored copies of 3 repos, `NOTICE` present; 3 files match key patterns, all in `sentinel_os/Tests/test_tls_security.py` and a `.patch` (test fixtures, not credentials, by name) | 99 files match secret-ish words; `certs/` directory (empty) | no version |
| sentinel_os | Apache | | 45 files match secret-ish words; no key patterns | no version, no dependency manifest |
| GSA-815 | Apache | submodule of sentinel_os | 27 files match secret-ish words; no key patterns | no version |
| TOUCHSTONE | Apache | flattened specimens of other systems | 9 | no version |
| spine | MIT | | 2 tracked logs (`augur_audit.log`, `fortress_audit.log`) that grow on every test run [executed] | CHANGELOG yes, no version |
| the rest | | | 0 | versions 0.1.0..2.2.0; only conservation_kernel has a CHANGELOG |

The word-pattern counts are grep hits on "api_key"/"secret"/"token", mostly
code that handles those things. The real-pattern sweep (AWS keys, `sk-`,
private-key headers) hit only test fixtures and two `.patch` files; those two
should be read by a person before anything ships.

## Part B: probes

### B1 confirmed vacuous, by mutation [executed]

| test | mutation | result | fixed |
|---|---|---|---|
| observe-perceive `test_perceive_consolidated.py:76` `test_safety_critical_requires_three_approvals` | `approval_required` 3 → 100 in `perceive_consolidated.py:181` | still passed | yes, b53fa3c on audit branch |
| fortress-kernel `test_fortress_unified.py:403` `test_blending_coefficient_control` (F841 `result`) | freeze the coefficient update at `fortress_unified.py:504` | still passed | yes, f00aed0 local only |

Unconfirmed (read, not mutated): Triad-42 `tests/test_provenance.py:98` (F841
`src`: fixture registered and never checked); sentinel_os
`Tests/test_twin_receiver_auth.py:87` (F841 `resp`, needs Postgres to run);
CCC `tests/test_semantic.py:317,322,330` (`is None` is the meaningful value
there, not vacuous). Ruff F841/E722/F401 totals per repo are in
`scratch_raw/*/ruff_stats.txt`; E722 is zero everywhere.

A mechanical scan for tests with no assertion flagged 89 in OBSERVE, 60 in the
spine, 11 in fortress-kernel; on reading, those are unittest `self.assert*`
calls the scanner did not count. Discarded.

### B2 controls with a bypass [executed unless marked]

| control | location | bypass condition | prod enforcing/total | test enforcing/total | recorded? | refused vs crashed distinguishable? |
|---|---|---|---|---|---|---|
| Clinical detection tests | spine `test_deterioration_simulator.py:34,46,58`, `test_adversarial_sensor_faults.py:40,70` | a missed or late escalation calls `skipTest`; suite reports 475 passed, 5 skipped | n/a | 0/5 fail on a miss | as a skip reason only | n/a; now `OBSERVE_STRICT_CLINICAL=1` fails instead (2ce17cc) |
| Gateway scope check | spine `governance_orchestrator.py` Phase 5 | no `gateway_scope` in context | 1/5 production callers declare a scope; 0 production constructions set `require_declared_scope` | 6/15 | yes (`scope_enforced`, 9ea72f9) | yes |
| OBSERVE monitoring | spine Phase 6 | no `vitals_snapshot` | 0/5 production callers pass vitals | 4/15 | yes (`observe_enforced`, 2b8403b) | yes |
| Conservation / execution stage | spine Phase 3, 4 `except Exception` | stage absent or crashed | n/a | | yes (`stage_error`, e2f0683) | yes, by field; the reason string still says "rejected" |
| Postgres-gated tests | sentinel_os, GSA-815, OBSERVE | no Postgres | | 228 skip, 39 fail, 37 error on the same absence | as skips | inconsistent: the same missing service is a skip in some files and a failure in others |
| HERALD REGISTRY skips | `Tests/test_hulk_100.py:364,380,395,609` | the feature under test does not exist | | 4 tests skip when a function is absent | as skip reason | a missing feature reads as a skipped test |
| ecology RAG tests | `tests/rag/*.py:10-11` importorskip lancedb | extras not installed | | 6 of 6 skip in isolation | as skips | |
| ecology injection tests | `tests/security/test_injection.py` | without pytest-asyncio | | with plugin 4 pass; without, **4 fail** (not "fail to collect" as the prompt said; corrected) | loud | |
| pytest config | spine `pytest.ini:14` `env =` | pytest-env absent (CI does not install it) | | "Unknown config option: env" in the isolated run | warning only | |
| Hard-coded machine path | spine `test_orchestrator_ccc_adapter.py:23`; ecology `scripts/run_history_index.py:22`, `run_rag.py:22` | any machine but the author's | | | no | |
| Suite crash | GSA-815 `gsa-governance-core/test_harness.py:137` SystemExit at collection | `pytest` from repo root | | CI runs `Tests/` and never sees it | no | |
| Governance_Gateway in place | `pytest` without `pip install -e .` | package not importable | | 3 collection errors | no | |

### B3 tests that mutate the repository [executed]

Only observe-perceive: `augur_audit.log` modified by every run (`fortress_audit.log`
when fortress tests run). Both tracked. Every other repo's tree was clean after
its suite.

## Part A: ghost_tools

Baselines were regenerated by the parallel session on 2026-09-08 for most repos
("Regenerate the ghost baseline against this repo's real path"); ids are
content hashes and a copy of CCC at another path matched its baseline 74/74
[executed], so the new-since-baseline numbers below are real, not path drift.

| repo | new | in baseline | notable | voids ≥ 0.4 |
|---|---|---|---|---|
| OBSERVE | 244 | 483 | | 4 (its own `*_source.py` files, fastapi) |
| conservation_kernel | 132 | 10 | mostly near-duplicate experiment functions | 0 |
| observe-perceive | 85 | 0 (baseline stale: 0 of 62 entries match) | `orchestrate_request` 344 lines; 5 copy-pasted `orchestrator` fixtures across adapter tests | 3 (ccc, gems, governance_gateway: the soft siblings) |
| innovation_os | 84 | no baseline | | 0 |
| TOUCHSTONE | 47 | 0 | specimens, not code | 11 |
| ecology | 38 | 2 | | 36, of which 33 at 0.40 point into `corpus/` |
| ATS | 32 | 0 | | 1 |
| sentinel_os | 25 | 374 | | 1 (fastapi) |
| GSA-815 | 24 | 79 | | 9 at 0.50: `queue_schema`, `outcome_v1`, `event_v1`, `circuit_breaker`, `operational_resilience` (live in the submodule) |
| ghost_tools | 24 | no baseline | | 1 |
| fortress-kernel | 23 | 0 | | 0 |
| GEMS | 19 | 0 | | 0 |
| Triad-42 | 5 | 37 | | 0 |
| Governance_Gateway | 4 | 1 | | 0 |
| TIE | 2 | 0 | | 0 |
| CCC | 1 | 74 | | 0 |
| AUGUR | 0 | 9 | | 2 (`_archive/` references) |
| HERALD | 0 | 73 | | 0 |

Every void at 0.50 is "module reached for and not there"; every 0.40 void is a
flattened or archived file. None of the voids I looked at in observe-perceive,
GSA-815 or OBSERVE is a lost class: each resolves to a sibling, a submodule, or
an `_archive`. Raw output: `scratch_raw/<repo>/ghost.txt`, `voids.txt`.

## Part D: commits

| repo | branch | commit | pushed |
|---|---|---|---|
| observe-perceive | audit/governance-stack-2026-09 | b53fa3c three-approvals test asserts acceptance | yes |
| observe-perceive | audit/governance-stack-2026-09 | 2ce17cc opt-in `OBSERVE_STRICT_CLINICAL` for the five known-gap skips | yes |
| fortress-kernel | audit/governance-stack-2026-09 | f00aed0 blending-coefficient test asserts control | **no**: push refused (403); this session's GitHub access covers observe-perceive only |
| observe-perceive | claude/prompt-red-blue-team-sj9a31 | cf3413c the revised audit prompt | yes |

## Executed vs inferred, and what was not done

**Executed:** every isolated and in-place suite run (18 repos, three of them
rerun with deps and one with its submodule); the four stage-removal probes; the
Conservation-without-PERCEIVE probe; both B1 mutations and their post-fix
re-mutations; the B3 dirty-tree check; ghost_buster and blackhole on all 18; the
baseline portability test; the ecology injection test with and without
pytest-asyncio; the secret-pattern sweeps; the call-site counts.

**Inferred (read only):** standalone-value sentences; the hard/soft label on
GEMS transport (no test exercises it); the "test fixture, not credential"
reading of the three key-pattern hits in OBSERVE.

**Not done:** Part B1 mutations beyond two (budget); B2 call-site ratios for
controls outside the spine; a Postgres service to run the 300 gated tests in
sentinel_os, GSA-815 and OBSERVE; `tools/stranded_work.sh` (meaningless on
fresh clones); ghost_writer reports; reading the two `.patch` files that matched
a private-key pattern. The 18 per-repo agents that were meant to do the
judgment work per repo were killed by a session rate limit before writing
anything; the mechanical sweep replaced them, so the per-repo B2 depth is lower
than the prompt asked for outside observe-perceive.

## Addendum, later the same day: the full-library blackhole run

Part A ran blackhole_extrapolator 0.4 on the 18 repos one at a time. Between
Part A and this addendum the tool went through three releases on the strength
of what the corpus taught it (ghost_tools PRs #8, #9, #10; version 0.5.2):
ecosystem awareness (an import a sibling checkout or a declared dependency
provides is wiring, not a void), debris archaeology (a flattened file's class
and def headers read back in token order, with parameter lists and return
annotations), rename candidates (an undefined name matched against every
surviving signature by keywords, unpack count, value flow and methods, never
by name), and four fixes the first full-library run exposed in the tool
itself. This addendum is the run after those fixes: all 18 checkouts, each
scanned with the other 17 as siblings.

### Result by repo

| repo | voids (0.4, alone) | voids (0.5.2, ecosystem) | wiring | what the voids are |
|---|---|---|---|---|
| ecology | 45 | 44 | 28 | 33 flattened corpus files, each with its interface recovered; 10 undeclared third-party packages; one real bug (below) |
| TOUCHSTONE | 35 | 25 | 0 | 11 flattened specimens with interfaces; two renames found (below); typing names used without import in superseded specimens |
| AUGUR | 10 | 9 | 1 | two flattened `_archive/` files with interfaces; seven names used only by those archive drafts |
| OBSERVE | 14 | 7 | 82 | three flattened files; three packages the vendored requirements omit; one real bug (below) |
| GSA-815 | 5 | 5 | 94 | all five trace to the uninitialised `vendor/sentinel_os` submodule; the tool says so on each |
| ATS | 4 | 3 | 3 | openai, voyageai, sentence-transformers imported and not declared |
| sentinel_os | 8 | 0 | 111 | clean once its nested requirements file is read |
| observe-perceive | 5 | 0 | 17 | clean; the last nominal dependency became wiring once sentinel_os was a sibling |
| the other 10 | 0 | 0 | 2 | clean |
| **total** | 126 | **93** | 338 | |

The four active repos (observe-perceive, conservation_kernel,
Governance_Gateway, ghost_tools) report zero voids. Of the five frozen repos,
fortress-kernel, CCC and sentinel_os report zero; AUGUR's nine are all inside
its own `_archive/` folder; GSA-815's five are one missing submodule.

### Findings for the record [executed]

1. **OBSERVE has drifted from the sentinel_os it vendors.** Its
   `sentinel_os/GSA.py` calls the dataclass `replace` function at lines 3706
   and 4149 without importing it (the file imports only `dataclass` and
   `field`); the live sentinel_os has no `GSA.py` at all. Its
   `sentinel_os/requirements.txt` omits httpx, redis and the opentelemetry
   packages the vendored code imports; the live sentinel_os declares all
   three. This is Part C's "vendored snapshot" finding with the specific
   divergence attached. OBSERVE is archived; nothing to push.
2. **Ecology's `corpus/AI_Governance_OS_V7.py` uses the `re` module at line
   320 without importing it.** Archived corpus file; nothing to push.
3. **GSA-815 cannot be dependency-checked from a clone** until
   `git submodule update --init` runs: its requirements file is a single
   `-r` include into the submodule. Already recorded in Part C1 as the
   manifest gap; the tool now names the cause on every affected void.
4. **ATS imports three embedding packages it does not declare**
   (`ats_embeddings.py`: openai, voyageai, sentence_transformers). Archived.
5. **Two renames in TOUCHSTONE's superseded specimens**, found by shape and
   not by name: `initialize_hybrid_cluster` is `initialize_network_cluster`
   (keyword `node_count`, three values unpacked against a three-tuple return,
   two of them flowing into `QuorumConsensusEngine` parameters of the declared
   types, shared name tokens) and `UnifiedGovernanceKernel` is
   `GovernanceKernel` (all four keyword arguments are its parameters). Both
   definitions live in the flattened `quorum_state_governance_source.py` the
   callers never name. Zero rename candidates on the other 17 repos.

### What the run found in the tool [executed, fixed]

Each first-run defect changed a count on a repo above and is fixed with a
regression test in ghost_tools 0.5.2: a namespace package holding only
sub-packages was not importable (ecology's `src`, 38 false signals);
requirements files below the root were never read and `-r` includes were
not followed (most of sentinel_os and OBSERVE); a declared distribution was
not matched to the module it provides (psycopg2-binary, opentelemetry-api);
a requirements line beginning with `http` was skipped as a URL, which
swallowed `httpx<0.28` and was sentinel_os's last void; dict-literal `True`
and `False` in flattened files were read as type annotations.

### Still noise, and known

Typing names (`Any`, `Dict`) used without import inside TOUCHSTONE's
superseded specimens are reported as never built; they are defects of the
specimens, not of the archive. Third-party packages a repo imports and does
not declare are reported as never built, which is the correct reading of the
evidence and the wrong reading of the world; the fix is a manifest in the
repo, not a change to the tool.

**Executed:** the 18-checkout ecosystem run, three times (before and after
each fix batch); the by-hand confirmation of findings 1, 2 and 5 against the
files. **Inferred:** nothing in this addendum.
