<!-- Note to the person running this, not to the auditor:
     Run from the local CLI on the machine that holds the checkouts, with
     `--model claude-fable-5-1`. Suggested effort: high for the sweep, xhigh
     for Part C. The 1M-token context holds the whole stack; cache reads are
     cheap, so re-reading a cached prefix across a long session is fine.
     This prompt cannot run as written in a Claude Code web session: that
     container holds one repo and no siblings. See Part 0. -->

# Governance Stack Audit

---

## Operating instructions

You are operating autonomously. The user is not watching in real time and cannot
answer questions mid-task, so asking "Want me to…?" will block the work. For
reversible actions that follow from this request, proceed without asking. Stop
only for destructive actions or genuine scope changes.

Before you start, say in a line what you're about to do; brief updates while you
work help the user follow along. Close with a short recap that stands on its own:
what you found, what you did, and what's next.

First privately list what you need next; then request every item that doesn't
depend on another's result in this one response. This sweep is almost entirely
independent reads across many repositories. Batch them.

The number of tokens used to edit files is best minimized. When it will not
affect the end result, surgically edit a file rather than rewrite the whole thing.

**This is an audit. The deliverable is findings, not fixes.** Do not fix what you
find unless Part D says to. Report every finding with a file:line citation and a
one-line statement of what breaks. Where you quote code or documents, mark the
quotation as a quotation.

**Every claim must be executed, not inferred.** This ecosystem's characteristic
defect is a check that passes without doing its job, and the same failure mode
applies to an audit of it. If you assert a guard fires, trigger it. If you assert
a test is vacuous, show the broken implementation it still passes (Part B1 says
how). A finding you only read is a hypothesis; label it as one.

**Do not trust the inventories in this prompt.** The repo list, the adapter
list, and the test counts below are the author's recollection. Enumerate from
disk (`ls ~`, `ls *_adapter.py`, `pytest --co -q | tail -1`) and report where
the prompt was wrong. A coupling graph drawn from a stale list is a wrong graph.

---

## PART 0 — setup and budget

Every repo is expected at `~/<name>`, checked out beside each other, because the
adapters resolve siblings at `../<name>`. Before anything else:

```bash
ls ~                                   # confirm the checkouts are there
python3 -m pip install pytest ruff==0.15.22 pytest-env
python3 -m pip install ~/conservation_kernel ~/fortress-kernel   # consumed as packages, not siblings
```

- A repo that will not set up in ten minutes is recorded as a Part C1 finding
  ("could not be made to run: <error>") and left alone. Do not spend the budget
  on environment repair.
- Part A output is large. Write every raw ghost_tools and ruff output to files
  under your scratchpad directory. Put only the summary tables in context, cap
  each at ten rows per repo, and cite the file for the rest. Part A must not eat
  the budget Part C needs.
- Time and token priority is Part C, then B, then A, then D. If you are running
  short, say so and cut A first.

---

## The stack (all under `~`)

```
observe-perceive   the integration spine — orchestrates the chain
sentinel_os        artifact origin + ledger        GSA-815      execution
conservation_kernel  transformation verification   CCC          recurrence/continuity
ecology            retrieval + corpus (872M)       HERALD       language → candidate claims
Governance_Gateway admission/sealing               fortress-kernel  containment controllers
AUGUR              behavioural simulation screen   GEMS         gem transport/constitution
TIE                evidence-preserving source      TOUCHSTONE   non-synthetic ground truth
ATS                applicant tracking + gov4_kernel   Triad-42  review/challenge
OBSERVE            clinical risk engine            innovation_os  (has a production adapter in observe-perceive)
```

That is 18 named repos, not 17; earlier versions of this prompt said 17 and
innovation_os was the one missing. Reconcile against `ls ~` and use whatever
count is real. The Part C table has one row per real repo.

Chain order, for reference: Gateway (is it real?) → AUGUR (should this proceed?)
→ PERCEIVE (is it permitted?) → fortress-kernel (is it contained?) →
Conservation (is it conservative?) → GSA-815 (execute) → OBSERVE (watch) → CCC
(seen before?).

Only AUGUR through OBSERVE live inside `orchestrate_request`. Gateway and CCC sit
outside it: Gateway admission is done by an adapter *before* it calls the
orchestrator, and CCC recording is done by an adapter *after*. Do not look for
a Gateway phase or a CCC phase inside the orchestrator; there is none.

---

## PART A — ghost_tools across the stack

`~/ghost_tools` holds four tools. Run from the ghost_tools directory:

```bash
python3 -m ghost_buster.cli <path>              # present-and-wrong: structural debt
python3 -m ghost_buster.cli <path> --json       # machine-readable FindingSet
python3 -m ghost_writer.cli <findings.json>     # turn a FindingSet into a report
python3 -m blackhole_extrapolator.cli <path>    # absent-and-shaped: infer missing code
tools/stranded_work.sh                          # commits existing only on this disk
```

**Most repos already carry `.ghost_baseline.json`.** Use `--baseline` so you
report *new* structural debt rather than re-reporting what was accepted months
ago. A report dominated by known findings is a report nobody reads. Note which
repos have no baseline; that is itself a row in the table.

`--exclude DIRNAME` is repeatable and you will need it:

- **`ecology/corpus/` is DATA, not code.** 360 files of harvested source from
  other systems, including files whose line breaks were destroyed. Linting it
  produces 7,515 invalid-syntax errors. It is already excluded in `pytest.ini`
  and `ruff.toml`; exclude it from ghost_tools too.
- Exclude `vendor/`, `.venv`, `venv`, `node_modules`, `github_archive/`, and
  `legacy/`.
- Exclude tracked log and ledger files: `*_audit.log`, `*.jsonl`, `*.db`. They
  are data, and they are also a finding (see B3).
- `GSA-815/vendor/sentinel_os/` is a vendored copy. Findings there belong to
  sentinel_os, not GSA-815. Do not double-count.

For `blackhole_extrapolator`, the highest-value targets are the flattened
(zero-newline) files. **Do not conclude that a flattened file lost anything
without checking the whole ecosystem for its class names.** Three were traced in
September 2026 and all three had lost nothing; a `*_source.py` beside a
`*_adapter.py` is an ancestor, not a casualty, and the adapter is usually a
renamed rewrite sharing near-zero class names. Compare against every repo, never
against the adapter.

Deliverable: one table of new-since-baseline findings per repo (ten rows max
each, file pointer for the rest), plus every void `blackhole_extrapolator`
reports with a shape confidence ≥ 0.4.

---

## PART B — the probes

These target the ecosystem's characteristic defect: **something satisfies its
check without doing its job.** Roughly fifteen instances surfaced incidentally in
a single day, which says the base rate is high and says nothing about coverage.
These probes are the mechanical version.

### B1 — checks that compute and never assert

Sweep every repo:

```bash
python3 -m ruff check <repo> --select F841,E722,F401 --statistics
```

- **F841** (assigned, never used) inside a test is the strong signal. Real
  examples found: a test named `test_regime_classification` computed a
  medium-error result and asserted only on the low one; a test named
  `test_borderline_case_respects_dwell` took three readings and asserted on the
  first and third, leaving the middle free to thrash, the exact failure it
  existed to catch.
- **F841 in production code** found a dropped `request_id` that silently
  degraded recurrence de-duplication.
- **E722** (bare `except`) swallows `KeyboardInterrupt` and `SystemExit`
  alongside whatever it meant to catch.

Then go beyond ruff, because ruff cannot see these:

1. Tests whose assertions sit behind an `if` that is never true. Three such
   tests were found guarding on `status == "APPROVED_AND_EXECUTED"` in a path
   where that was never reached.
2. Tests that assert only on a hand-written list where the real set is
   derivable. A guard that must be updated by hand when the thing it guards
   changes is not a guard. Look for a literal list in a test that restates an
   enum, a config, or a set defined in the source.
3. Assertions on `is not None` or `> 0` where the meaningful property is the
   value.

**Proof protocol. The proof of a vacuous test is a broken implementation that
it still passes. Deleting an assertion proves nothing, because a deleted
assertion cannot fail.** For each candidate, apply the mutation that matches its
shape, run, then revert the mutation:

- **Guarded assertion** (`if cond: assert ...`): delete the *guard*, keep the
  assertion, run. If the test now FAILS, the guard was hiding a failing
  assertion: confirmed. If it still passes, the guard was dead but harmless: not
  a finding.
- **Weak or absent assertion** (`is not None`, `> 0`, an F841 result never
  checked): break the implementation in the way the test's *name* says it should
  catch (return the wrong regime, skip the middle reading, drop the field), run.
  If the test still passes: confirmed. Record the exact mutation.
- **Hand-written list restating a derivable set**: add one member to the source
  enum or config, run. If the test still passes: confirmed.

Report confirmed and unconfirmed separately, each confirmed row carrying the
mutation that proved it.

### B2 — controls with a bypass path that nothing reports

The higher-value probe, and the harder one. The pattern: a check exists, is
correct, and is skipped in a condition nobody records.

**Calibration example, already fixed.** `governance_orchestrator.py` refused a
READ_ONLY artifact loudly but executed an artifact with *no* declared scope
silently. Measured: 16 call sites, 1 declared a scope. This was fixed in
observe-perceive commit 9ea72f9 (2026-09-07): an undeclared scope now logs a
warning, every result carries `scope_enforced`, and `require_declared_scope=True`
is an opt-in strict mode. **Do not re-report it.** Use it two ways: as a
regression check (confirm the fix still holds), and as the template for the
shape every Part D fix must take.

Search every repo for this shape:

- `if X is not None and <check>`: what happens when `X` is None?
- `if config.enabled:` / `if self.enforce:`: what is the default, and is the
  disabled case recorded anywhere?
- `try: <check> except: pass`: a check that cannot fail.
- **`except Exception` wrapped around a stage call that converts a programming
  error into a domain verdict.** Calibration instance, already fixed: with
  `conservation_kernel=None` the orchestrator returned `REJECTED` with reason
  "Conservation Kernel rejected decision: 'NoneType' object has no attribute
  'verify_perceive_decision'". The stage was absent; the record said it refused.
  Fixed in observe-perceive e2f0683: the adapters raise named refusals, every
  REJECTED result from those phases carries `stage_refused`, `stage_error` and
  `conservation_enforced`, and `raise_on_stage_error=True` is the opt-in strict
  mode. The reason string is unchanged, so the distinction lives in the fields,
  not the text. **Do not re-report it in observe-perceive.** Apply the same
  question to every other repo: for every `REJECTED`/`DENIED` path, can the
  record distinguish "stage refused" from "stage absent or crashed" without
  parsing free text? If it cannot, that is the finding.
- `pytest.mark.skipif` and `importorskip`: a skipped test reports green.
  **Which skips are load-bearing?** One found: without `pytest-asyncio` the
  injection-rejection *security* tests do not fail, they fail to collect.
- **Test infrastructure that reads as wired and is not.** Confirmed instance:
  observe-perceive's `pytest.ini` carries an `env = PYTHONPATH=...` block that
  pytest ignores with "Unknown config option: env" because `pytest-env` is not
  installed locally or in CI (still live at 717c2e2). Look for: config keys that need an absent plugin,
  `sys.path`/`PYTHONPATH` entries naming directories that do not exist, and
  hard-coded absolute paths (`grep -rn "/home/wking53214\|/Users/"`). One is
  known and still live: `test_orchestrator_ccc_adapter.py:23` inserts
  `/home/wking53214/CCC`. That test passes on exactly one machine.
- Early `return` before a validation.
- A gate keyed on a value that no caller supplies.

For each: **count the call sites, and count how many take the enforcing path,
with production and test call sites counted separately.** The 16-call-site
example above mixed both; of the files calling `orchestrate_request`, five are
production adapters and four are tests carrying fifteen calls. The production
ratio is what a buyer cares about; the test ratio says what the suite covers.
Report both.

Report as: control, location, bypass condition, prod enforcing/total, test
enforcing/total, whether the bypass is recorded in the result/log/audit trail,
and whether a REJECTED record on this path can be told apart from a crash.

**Already fixed on observe-perceive main as of 2026-09-08, all following the
9ea72f9 shape (warning + `<x>_enforced` field + opt-in strict flag). Verify
each still holds; do not re-report any of them:**

| commit  | what was silent                                                    |
|---------|--------------------------------------------------------------------|
| 9ea72f9 | undeclared artifact scope executed                                 |
| e52cc81 | a test guarded away the warning-escalation path                    |
| 215fd5f | PERCEIVE advisory refusal dropped from the verdict                 |
| 2b8403b | OBSERVE skipped whenever no vitals passed (every prod caller)      |
| e2f0683 | Conservation / GSA-815 stage crash filed as a refusal              |
| 64f0e54 | Conservation adapter ledger refusal filed as a crash               |
| c4551af | Innovation adapter approval with no signer counted as human review |
| b778a38 | PERCEIVE citadel approval carried a key but named no reviewer      |
| ddca375 | TIE adapter restated a coverage gap TIE already declared           |
| 717c2e2 | 510(k) checklist items marked ready with no test behind them       |
| 3ff4569 | three-approvals test could not tell 3 from 100; five clinical missed detections filed as skips (now `OBSERVE_STRICT_CLINICAL=1` fails) |

The last ten are what the first pass of this audit found in one repo in one
day. Expect the same density elsewhere. The full first-pass result, all
parts, is `docs/audit/AUDIT_REPORT_2026-09-08.md` on main: read it before
running so you extend it rather than repeat it.

### B3 — tests that mutate the repository

A mechanical probe. In each repo:

```bash
git status --short > /dev/null && python3 -m pytest -q -p no:cacheprovider; git status --short
```

Any tracked file that changed is a finding: the suite writes into the
repository. Known instance: observe-perceive tracks `augur_audit.log` and
`fortress_audit.log`, both appended on every run; the scope-check commit
unintentionally added 480 log lines. An HMAC ledger that grows in git history is
neither a ledger nor a fixture. Report file, growth per run, and whether any
test reads it back (if one does, the suite has order-dependent state).

---

## PART C — commercial modularity audit

**This is the part the user most wants and the part no tool can do for you.**

The commercial thesis is that a customer buys the modules they need rather than
the whole stack. That requires two properties, and they must be verified
separately, plus a third set of blockers that no coupling graph shows.

### C1 — Independent capability

**Does each module deliver value standing alone, with no sibling present?**

For each repo, establish:

1. **Does it import a sibling?** Three distinct mechanisms, each its own column:
   (a) `sys.path` manipulation toward `../<sibling>`, including lazy imports
   inside functions (this stack uses them heavily); (b) a sibling consumed as a
   **pip-installed package** (`from conservation_kernel import ...` at module
   level is this, and it is the one that hard-breaks observe-perceive's suite);
   (c) a hard-coded absolute path. Cite the line for every edge.
2. **Does its own test suite pass in isolation?** Fresh clone into a temp dir
   with no siblings and no sibling packages installed, then:

   ```bash
   python3 -m pytest -q -rs -p no:cacheprovider 2>&1 | tail -40
   ```

   Report three numbers: passed, skipped, collection errors. **A suite that is
   green because it skipped does not pass in isolation.** Known answer for one
   repo: observe-perceive in a clean container, measured at 9ea72f9, had 9 of
   23 test files fail to collect, all on the Conservation Kernel import, and
   four more files skip themselves when a sibling is absent. Re-measure on the
   current head. Assume nothing; verify each.
3. **What does it actually do alone?** State the standalone value proposition in
   one sentence per module, or state that there isn't one.
4. **Hard vs soft dependency.** A module that degrades gracefully without a
   sibling is sellable alone. One that raises `ImportError` at import time is
   not. One that *runs* without the sibling but returns wrong or misleading
   results (see the Conservation example in B2) is the worst case: classify it
   as hard and say why.
5. **Sellable alone, by rubric.** Yes only if all four hold, each with a
   citation:
   - imports with no sibling on disk and no sibling package installed;
   - isolated suite has zero collection errors, under 10 percent skips, and the
     skipped tests are not the security or integration tests;
   - a documented entrypoint a customer would call (CLI, API, or class);
   - the README's headline claim is exercised by at least one passing test.

Output: one row per repo. Columns: module, standalone value in one sentence,
sibling-path dependencies, sibling-package dependencies, absolute-path
dependencies, isolated result (passed/skipped/errors), rubric items met (0 to 4),
**sellable alone yes/no**.

### C2 — Shuffle interface

**Can a customer buy modules 3, 7 and 11 and have them compose?**

The chain has a fixed order and per-seam adapters. **Enumerate them with
`ls ~/observe-perceive/*_adapter.py | grep -v '^test_'`; do not use any list
from memory.** At
last count there were twelve: sentinel→perceive, perceive→conservation,
conservation→gsa815, gsa815→observe, fortress→perceive, augur screen, gateway
admission, orchestrator→CCC, and four chain entrypoints (gems, herald,
innovation, tie) that call `orchestrate_request` from production code. Earlier
versions of this prompt listed eight and omitted the entrypoints; a coupling
graph that starts from eight is wrong.

Establish:

1. **Is the interface uniform, or is every seam bespoke?** Read
   `governance_contracts.py`. How much of the chain speaks those shared types
   (`GovernanceRequest`, `GovernanceDecision`, `ConservationDecision`,
   `ExecutionApproval`), and how much is adapter-specific translation? Count
   adapters that convert to the shared types vs adapters that translate
   pairwise.
2. **Can a stage be removed?** fortress-kernel and AUGUR are opt-in
   (`fortress_controller=None`, `simulation_screen=False`). Is that pattern
   available for the others, or are PERCEIVE / Conservation / GSA-815 / OBSERVE
   structurally mandatory? Remove each and record what happens, in three
   categories: *fails at construction*, *fails at request*, or *runs and returns
   a misleading verdict*. Conservation was the third before e2f0683; it now
   still rejects every request but the record carries `stage_error` instead
   of claiming a refusal. Confirm, and check the other three.
3. **Can a stage be reordered or substituted?** If a customer wants Conservation
   without PERCEIVE, does anything support that? Does any stage assume an
   upstream stage's output shape beyond the shared contract?
4. **What is the actual coupling graph?** Produce it. Nodes = modules, edges =
   hard dependency, labelled with the file:line that creates the edge and the
   mechanism (sibling path, package, absolute path). That graph *is* the
   product-packaging constraint, and nobody has drawn it.
5. **Name the minimum sellable units.** Given the graph, which subsets are
   coherent products? Which modules can never be sold apart, and what would have
   to change for them to be?

Be honest about the gap between the README claim and the code. This ecosystem's
READMEs have a documented history of overstating what the code does. Verify
before repeating any claim, and say plainly where a module's marketing exceeds
its implementation.

### C3 — commercial blockers the coupling graph cannot show

One row per repo, each cell cited or marked "not checked":

- **License and provenance.** Is there a LICENSE file? Is there third-party
  code in the tree, and is its license known? `ecology/corpus` is 360 files of
  harvested source from other systems: whose license, and can it ship?
- **Secrets and PII.** One grep sweep per repo for keys, tokens, credentials,
  and patient-like records, including inside tracked logs, `.db`, and `.jsonl`
  files. Report counts, never the values.
- **Regulatory and clinical claims.** OBSERVE targets pediatric sepsis and ships
  `fda_510k_checklist.py`. Every README sentence that reads as a clinical,
  safety, or regulatory claim gets the same verify-before-repeating treatment,
  and the finding is worse when it is wrong. List each such sentence with the
  test that backs it, or "none."
- **Version and API surface.** Does the module have a version number, a
  changelog, and a stated public API? A module without those cannot be sold
  separately whatever the graph says.

---

## PART D — what you may change

Fix **only** these, each as its own commit:

1. Anything in Part B1 you **confirmed** by the mutation protocol: fix the test
   to assert the thing its name claims, and make sure the recorded mutation now
   fails it.
2. Any bypass in B2 where making the skip *visible* (a logged warning plus a
   field in the result) requires no behavior change. Follow the shape of
   observe-perceive commit 9ea72f9 exactly: warning names the remedy, result
   carries an `<x>_enforced` field, an opt-in strict flag refuses. Do not change
   defaults; an opt-in strict flag is fine, flipping a default is not.

Everything else is a finding, not a task. If you find a pre-existing bug the
audit doesn't cover, report it as a follow-up rather than fixing it.

**Branch and push rules.** In every repo you touch, work on the branch
`audit/governance-stack-2026-09` (existing where it exists, see traps; created
from `main` otherwise). Commit locally as you go. Push only that
branch, with `git push -u origin audit/governance-stack-2026-09`, never `main`
or `master`. Open no pull requests. List every push, by repo and commit, in the
final summary. Run `ruff check .` before every push (see traps).

---

## Repo-specific traps (learned the hard way, 2026-09-07)

- **Run `ruff check .` before pushing to any repo with a CI gate.** ghost_tools,
  ATS, observe-perceive, ecology, CCC, HERALD, innovation_os all have hard ruff
  gates. A push with two unused imports turned one red.
- **ecology**: install `requirements-dev.txt`, not `requirements.txt`
  (pytest-asyncio lives there). `corpus/` is excluded from pytest and ruff by
  config; keep it that way.
- **Branches.** `audit/governance-stack-2026-09` already exists on
  observe-perceive, ecology, HERALD, TIE, ATS and OBSERVE, and `audit/go` on
  fortress-kernel, Conservation_Kernel and sentinel_os. As of 2026-09-08 every
  one of them equals `main`: empty placeholders, not prior work. Check out the
  existing branch where there is one rather than creating a second, and
  confirm it still equals main before committing onto it.
- **observe-perceive**: needs siblings CCC, AUGUR, fortress-kernel, GEMS,
  Governance_Gateway checked out alongside, plus `pip install` of
  Conservation_Kernel and fortress-kernel (the latter declares numpy). Its CI
  workflow shows the working arrangement. sentinel_os and GSA-815 are *not*
  checked out in CI; find out what the suite does about that, because the
  `pytest.ini` line that appears to wire them in is inert (B2).
- **innovation_os**: local `main` tracks the stale `origin/master`. Any
  ahead/behind readout is wrong unless measured against `origin/main`.
- **Archived and read-only on GitHub, pushes will 403**: `Gemini_History`,
  `TAKEOUT`, `synapsis`, and since 2026-09-08 nine stack repos: `ATS`,
  `ecology`, `TOUCHSTONE`, `OBSERVE`, `GEMS`, `innovation_os`, `Triad-42`,
  `TIE`, `HERALD`. Audit the nine read-only (Parts A to C still apply);
  Part D commits go only to the nine remaining writable repos.
- `git rev-list --not --remotes` compares against remote *branches only*. Always
  add `--tags`, or every commit hanging off a pushed tag reads as unpushed.
- Running the same artifact twice through the chain is a **replay**, and the
  chain correctly refuses replays. Use distinct artifact ids in tests.

---

## Deliverables

1. **Part A**: new-since-baseline ghost_buster findings per repo (capped,
   with file pointers); blackhole voids at confidence ≥ 0.4.
2. **Part B**: three tables: confirmed vacuous checks (with the mutation that
   proved each), controls-with-bypasses (prod and test enforcing/total ratios,
   plus refused-vs-absent distinguishability), and tests that mutate tracked
   files.
3. **Part C**: the per-repo independence table, the coupling graph with
   file:line-and-mechanism-labelled edges, the named minimum sellable units,
   and the C3 blockers table. **This is the headline deliverable.**
4. **Part D**: commits for confirmed B1 fixes and visible-bypass changes only,
   on the audit branch, with the push list.
5. A written summary that leads with Part C, states what you verified by
   execution versus what you inferred by reading, corrects any inventory in
   this prompt that disk proved wrong, and names what you did not get to.

Publish the Part C result as an artifact. It is a decision document about
product packaging and it will be read more than once.
