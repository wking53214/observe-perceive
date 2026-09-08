# Commercial Red Team: the governance stack as a company

Written as a hostile investor, against the evidence from the 2026-09-08 technical audit of all 18 repositories. Rules I held myself to: no customer numbers are invented; where I have no evidence the answer is UNKNOWN; "code exists" is never counted as "customer validated". Evidence citations are to the audit report (`docs/audit/AUDIT_REPORT_2026-09-08.md`) or to files in the repositories.

**The two questions.** If I were investing my own money, why should I believe this becomes a large company? Today: I should not yet. There is no evidence a single customer exists. What would make me walk away? Twelve more months of architecture without a paid design partner. That is the whole memo; the rest is the working.

---

## Part 16: Standalone products (Hypothesis A)

The naming convention in the prompt: a "Cat" is one component, a "Voltron" is the composed system. I evaluate each Cat on its own commercial merits, not on whether it has a repository.

| Component | Customer | Painful problem | Existing alternative | Measurable outcome | Buyer / budget | Deployment | Recurring? | Expansion | Threat | **Classification** |
|---|---|---|---|---|---|---|---|---|---|---|
| **Conservation Kernel** (52 tests, no deps, the hub) | Teams running automated or AI-driven transformations who must prove what was done | "Show me evidence this action was verified before it ran" | Application logs; OpenTelemetry traces; hand-written audit tables; signed commits | Auditable receipt per transformation, automatically | Platform / security engineering; budget owner is the head of platform or CISO | Library, pip install, minutes | Recurring only if embedded in a running control path | Into the spine, into ledgers | Any tracing vendor adds "signed receipts" | **INFRASTRUCTURE** (a library, not a product; monetizes only as part of something) |
| **Governance Spine** (observe-perceive PERCEIVE gates + orchestrator + Gateway admission) | Enterprises letting AI agents or automation take actions | An agent executes something nobody authorized, and afterwards nobody can prove what was checked | Policy engines (OPA, Cedar), agent-framework guardrails, human-in-the-loop queues, SOAR playbooks | Unauthorized actions blocked; every execution carries a decision record | Platform / AI enablement team; CISO or CTO budget | Python library, single process, duck-typed input, dict output; no service, no API, no UI | Yes, if in the control path | Add stages (screen, containment, recurrence) | Hyperscaler agent platforms adding policy hooks; ServiceNow, Microsoft Purview | **PRODUCT CANDIDATE**, currently a library |
| **Governance_Gateway** (45 tests) | Same as spine | Unadmitted or unsealed artifacts entering a governed flow | Schema validation, signing, admission controllers (Kubernetes analogy) | Rejected-at-the-door count | Same | Small library | With spine | Into spine | Commodity | **FEATURE** of the spine |
| **fortress-kernel** (48 tests, numpy) | Control engineers, anyone bounding an automated actuator | An automated controller drives a protected state out of bounds | Rate limiters, PID clamps, hand-written guards | Bounded slew and target; audit chain of interventions | Engineering lead | Library | Only in a control loop | Into spine | Trivial to rebuild per use | **FEATURE** (a well-made guard, not a market) |
| **AUGUR** (34 tests, seeded simulation, HMAC audit log) | Teams wanting a pre-execution "what if" screen | Acting without simulating | Digital-twin tools, scenario simulators, notebooks | Refusals before execution | Engineering | Library | With spine | Into spine | Simulation is a crowded category | **FEATURE** |
| **CCC** (135 tests, dependency-free) | Anyone tracking whether a claim or finding recurs | "Have we seen this before, and does the story still hold together?" | Dedup logic, incident databases, ticket search | Recurrence detected; continuity breaks flagged | Engineering or ops | Library | Yes, as a memory | Into spine, into HERALD | Vector search plus rules replicates it | **FEATURE**, possibly a component of an incident product |
| **HERALD** (364 tests) | Teams extracting checkable claims from documents | Natural language to candidate claims with confidence gating and source binding | LLM extraction pipelines; NLP libraries; document AI | Claims with provenance instead of paragraphs | Data or knowledge team | Library | With a corpus | Into TIE, CCC | Every LLM vendor's extraction | **FEATURE** unless paired with a domain |
| **TIE** (13 tests) | Analysts turning source material into traceable intelligence | Untraceable summaries | Same as HERALD | Provenance-preserving outputs | Analyst team | Library | With a corpus | With HERALD | Same | **FEATURE**; suite too thin to claim more |
| **sentinel_os + GSA-815** (664 and 121 pass with Postgres absent; twins, custody, sealed demographic channel, BISG estimator) | Regulated operators who must prove custody and origin of artifacts and decisions | Reconstructing what happened is slow and disputed | Database audit tables, immutable logs, SIEM | Forensic reconstruction time; custody proof | Compliance or risk; budget owner is a CCO or CRO | Postgres and Redis service; no dependency manifest today; submodule coupling | Yes, as system of record | Into spine as the ledger | Immutable ledger products, GRC platforms | **PRODUCT CANDIDATE** in regulated industries, least evidenced |
| **OBSERVE engine in the spine** (pediatric sepsis, Kalman trajectory, deterioration simulator, 510(k) checklist) | Hospitals | Late recognition of pediatric deterioration | Epic Sepsis Model, Bayesian Health, Prenosis (FDA-cleared ImmunoScore), bedside early-warning scores | Earlier escalation, fewer misses | CMIO / CNO; hospital capital budget | Regulated software as a medical device; EHR integration; validation study | Yes | More conditions | Incumbents with EHR distribution | **NOT COMMERCIALLY RELEVANT as-is**: five known missed detections are recorded as test skips (report, B2); no clinical validation |
| **ghost_tools** (100 tests, no deps; found real defects in this very audit) | Engineering leaders whose codebase is increasingly AI-written | Tests and checks that pass without doing their job; code that is present and wrong; code that is absent and shaped | Linters (ruff), mutation testing (mutmut, Stryker), SonarQube, CodeRabbit-style AI review | Confirmed vacuous checks found and fixed; structural debt trend | VP Engineering; tooling budget | CLI, dependency-free, minutes | Recurring as CI gate | Into an assurance service | Crowded; mutation testing is decades old | **PRODUCT CANDIDATE**, small ACV, fast cycle |
| **Triad-42** (105 tests, "advisory cognitive review harness") | Teams wanting structured multi-lens review of a decision | Unstructured review | Review checklists, LLM critics | Review records | Engineering | Library | Weak | Into spine | Trivial | **FEATURE** |
| **innovation_os** (285 tests) | Product or R&D orgs tracking ideas to implementation with traceability | Idea pipelines without provenance | Jira, Productboard, Notion, Aha | UNKNOWN | Product leadership | App-like, large | Yes if adopted | Standalone | Incumbents with distribution | **PRODUCT** in a crowded category; no evidence of differentiation |
| **ATS** (35 tests) | Hiring teams | Applicant tracking | Greenhouse, Lever, Workday | UNKNOWN | HR | Unknown | Yes | None | Overwhelming | **NOT COMMERCIALLY RELEVANT** |
| **ecology** (personal memory over harvested corpus) | The founder | Personal knowledge retrieval | Every RAG tool | UNKNOWN | None | Personal paths hard-coded | No | None | Everything | **NOT COMMERCIALLY RELEVANT**; corpus cannot ship (provenance) |
| **TOUCHSTONE** | None | "A specimen corpus. Not a system." | | | | | | | | **NOT COMMERCIALLY RELEVANT**; possibly test data for ghost_tools |
| **GEMS** (10 tests; transport untested) | UNKNOWN | UNKNOWN | | | | | | | | **CONSULTING ENABLER** at most; README calls it a "reconstruction baseline" |

Net: two product candidates with a plausible market (Governance Spine, ghost_tools), one regulated-industry candidate with the least evidence (sentinel_os ledger), one piece of infrastructure everything depends on (Conservation Kernel), and thirteen features, demos, or personal projects.

---

## Part 17: Customer problem validation

Only the candidates above. Vague outcomes are rejected as instructed.

**Governance Spine**
CUSTOMER: an enterprise platform team that has moved AI agents from "suggest" to "act" (ticket closure, infra changes, financial adjustments, customer communications).
→ PAIN: an agent performs an action no policy authorized, and after the incident nobody can produce the record of what was checked.
→ CURRENT WORKAROUND: hand-written allowlists in the agent framework; human approval queues; logging everything and hoping.
→ COST OF WORKAROUND: human approval throughput caps automation value; incident investigation is manual; every new agent re-implements its own checks. Assumption to validate: what a customer actually spends here today.
→ PRODUCT: a gate in front of execution that admits, screens, permits, verifies and records, and refuses anything it cannot record.
→ MEASURABLE IMPROVEMENT: unauthorized actions blocked (count), decision records produced automatically (100 percent of executions), investigation time per incident (before and after).
→ ECONOMIC BUYER: CISO for the "prevents unauthorized actions" framing; head of platform for the "lets us automate more" framing.
Can the architecture demonstrate this today? Partly. The chain executes end to end in tests; refusals and records exist. But the policy gates default to advisory (audit, B2: "PolicyEnforcementConfig advisory pattern"), the scope check reached 1 in 5 production callers until fixed, and the output is an untyped dict. A demonstration is possible; a deployment is not.

**ghost_tools plus the audit method**
CUSTOMER: VP Engineering at a company where a large share of new code is AI-written.
→ PAIN: green CI that means nothing; tests that compute and never assert; checks that pass without doing their job. This audit found two such tests by mutation and five clinical misses filed as skips in a repo with 475 passing tests.
→ CURRENT WORKAROUND: code review; coverage percentages; occasionally mutation testing.
→ COST: unknown per customer; the general form is "defects that reach production because the safety net was decorative". Assumption to validate.
→ PRODUCT: a scanner plus a proof protocol that reports confirmed vacuous checks with the mutation that proved each, and structural debt against a baseline.
→ MEASURABLE IMPROVEMENT: number of confirmed-vacuous checks per thousand tests, trend over time; time to confirm.
→ ECONOMIC BUYER: VP Engineering; tooling budget.
Demonstrable today: yes. It ran across 18 repositories in this audit and produced confirmed, fixed findings.

**sentinel_os ledger (custody and origin)**
CUSTOMER: a regulated operator (lending, healthcare operations, insurance) that must prove the origin and custody of decisions and artifacts.
→ PAIN: forensic reconstruction after a dispute or examination is slow and contested.
→ WORKAROUND: database audit tables, log archives, manual reconstruction.
→ COST: examiner findings, dispute losses, staff hours. Assumption to validate.
→ PRODUCT: a Postgres-backed custody ledger with sealed channels for sensitive attributes (the "sealed demographic channel" and BISG estimator in the tests suggest fair-lending or health-equity use).
→ MEASURABLE IMPROVEMENT: reconstruction time; custody gaps found.
→ ECONOMIC BUYER: Chief Compliance Officer.
Demonstrable today: no. 228 tests skip and 39 fail without Postgres, there is no dependency manifest, and I could not run the ledger path.

**Conservation Kernel**
Same customer as the spine; the outcome is "receipt per verified transformation". Demonstrable today: yes, standalone, in 52 tests. But a receipt library is not a purchase; it is a component of the spine's purchase.

**OBSERVE pediatric sepsis**
CUSTOMER: children's hospital. PAIN: late recognition. This is a real and expensive pain with real buyers. The current engine records three missed or late scenario detections and two sensor-fault gaps as skipped tests. Until a validation study exists, the honest outcome statement is "not yet demonstrable". It should be treated as the reference application that proves the spine works in a hard domain, not as a product.

---

## Part 18: Wedge product

Ranked by pain × ability to pay × differentiation × feasibility × expansion × defensibility. Scores 1 to 5 per factor are my judgement, evidence noted.

| Rank | Candidate | Pain | Pay | Diff | Feas | Expand | Defend | Notes |
|---|---|---|---|---|---|---|---|---|
| 1 | **Governed action gate** (Spine: Gateway + PERCEIVE + Conservation, optional AUGUR/fortress) | 4 | 4 | 3 | 2 | 5 | 2 | Pain is rising with agents; feasibility is the weak factor: library, no service, advisory defaults, dict outputs |
| 2 | **AI-code assurance** (ghost_tools + mutation protocol) | 3 | 2 | 2 | 5 | 3 | 1 | Works today; small contracts; crowded |
| 3 | **Custody ledger** (sentinel_os + GSA-815) | 4 | 4 | 3 | 1 | 4 | 3 | Regulated buyers pay; cannot be run today from a clone |
| 4 | **Claims with provenance** (HERALD + TIE + CCC) | 2 | 2 | 2 | 3 | 3 | 1 | A feature of a document product |
| 5 | **Pediatric deterioration engine** (OBSERVE) | 5 | 5 | 2 | 1 | 3 | 3 | Regulatory path, incumbents with EHR distribution, five known misses |

Detail for the top three:

**1. Governed action gate.** ICP: a company with 50 to 5,000 engineers already running at least one agent that mutates production systems. Buyer: head of platform engineering with CISO sign-off. Problem: agents act outside authorization and leave no defensible record. Alternatives: agent-framework guardrails (per-framework, unverifiable), OPA-style policy engines (decide but do not record verification or refuse on missing evidence), human queues (do not scale). Why insufficient: none produces a per-execution record that distinguishes "checked and allowed" from "never checked"; this stack's recent fixes are exactly that distinction (`scope_enforced`, `observe_enforced`, `stage_error`). Product: a gate service with a typed request and decision, a receipt per execution, refusals by default in strict mode. MVP: wrap `orchestrate_request` in an HTTP service with a typed schema, flip strict flags on by configuration, one adapter for one agent framework. Proof required: one design partner's real agent, one month, count of refusals and records; one incident reconstructed from receipts alone. Sales cycle: three to six months, security review included. Expansion: more agents, more stages, the ledger. Platform reason: every automated action in the enterprise could pass through it.

**2. AI-code assurance.** ICP: engineering org with heavy AI-assisted coding and a CI gate they no longer trust. Buyer: VP Engineering. Alternatives: SonarQube, mutation tools, AI reviewers. Why insufficient: mutation tools are expensive to run and report noise; AI reviewers do not prove anything. Product: scanner plus proof protocol plus baseline delta, as a CI job. MVP: exists (ghost_buster, blackhole, the B1 protocol from the prompt). Proof: three teams, confirmed findings per thousand tests. Sales cycle: weeks. Expansion: assurance reports for auditors. Platform reason: weak; it becomes a feature of a code-quality platform.

**3. Custody ledger.** ICP: mid-size lender or healthcare operator under active examination. Buyer: CCO. Alternatives: audit tables, GRC platforms. Why insufficient: GRC records policy, not custody of individual decisions. Product: ledger service with sealed sensitive-attribute channels. MVP: does not exist as deployable; needs a manifest, a container, and a runnable Postgres fixture. Proof: reconstruct one real dispute from the ledger. Sales cycle: nine to eighteen months. Expansion: into the gate. Platform reason: system of record.

**Recommended wedge: the governed action gate.** Not because it is the biggest code, but because it is the only candidate where pain, budget and timing coincide and where the architecture's one real idea (a record that cannot say "allowed" when it means "unchecked") is the differentiator. Its feasibility score is the risk, and Part 34's experiment is designed to test exactly that.

---

## Part 19: Voltron commerciality test

| Composition | 1. New capability | 2. Why not one component | 3. Problem | 4. Who pays | 5. Competes with | 6. Newly possible | 7. Cuts cost | 8. Cuts risk | 9. New revenue | 10. Switching cost | 11. Defensible | 12. Worth paying more | **Class** |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Spine + Conservation (the pair that cannot be split) | Permission plus verification plus a receipt in one path | Conservation alone verifies but does not decide; PERCEIVE decides but does not verify | Unauthorized or unverifiable action | Platform / CISO | Policy engines, guardrails | An execution record that is honest about what was skipped | Modestly (one gate instead of per-agent checks) | Yes | Yes (the wedge) | Moderate once in the control path | Weak today | Yes, modestly | **COMMERCIALLY USEFUL** |
| Gate + AUGUR + fortress (screen and contain) | Refuse before acting on a simulated trajectory; bound the actuator | Each is a guard; together they cover intent and magnitude | Bad actions with bad magnitude | Same | Digital twins plus rate limiters | Pre-execution refusal on simulated instability | No | Yes for actuator-like agents | Add-on | Low | Weak | Only for physical or financial actuators | **TECHNICALLY INTERESTING** |
| Gate + CCC (recurrence memory) | The gate remembers refusals and recurrences | CCC alone has nothing to remember | Repeat incidents | Ops | Incident tooling | "We refused this shape before" | Slightly | Yes | Add-on | Grows with history | Plausible knowledge moat | Later | **COMMERCIALLY USEFUL** later |
| Gate + ledger (sentinel_os/GSA-815) | Custody of every governed execution | Ledger alone records what it is told; gate alone forgets | Forensics and examination | CCO | GRC, immutable logs | Examiner-grade reconstruction | Yes (investigation hours) | Yes | Yes | High (system of record) | Plausible | Yes | **POTENTIAL COMMERCIAL MOAT**, unproven, ledger not runnable today |
| HERALD + TIE + CCC (claims pipeline) | Traceable claims with recurrence | Each is a step | Untraceable analysis | Analyst teams | Document AI | Provenance on claims | Unclear | Unclear | Unclear | Low | Weak | No | **TECHNICALLY INTERESTING** |
| **Full Voltron** (all eight named stages) | The full chain diagram | Two stages are names, not code (GSA-815, sentinel_os never imported); OBSERVE skips without vitals | No single customer has all these problems at once | Nobody yet | Nobody sells this shape | Nothing a customer asked for | No | Unclear | No | Would be high if it existed | No | No | **MARKETING METAPHOR** today |

Conclusion: the Voltron thesis is real for exactly two compositions (gate plus Conservation now, gate plus ledger later). The full eight-stage chain is a diagram whose wiring the audit disproved. Sell the pair, earn the ledger, retire the diagram.

---

## Part 20: Business model test

| Model | Gross margin | Scalability | Sales complexity | Implementation burden | Recurring | Concentration risk | Defensibility | Investor appeal | **Type** |
|---|---|---|---|---|---|---|---|---|---|
| A Standalone module licensing | High | Low (small ACVs, features not products) | Low | Low | Weak | Low | Low | Low | Scalable software revenue, small |
| B Platform subscription (the gate as a service) | High | High if productized | Medium to high | Medium today, low once a service exists | Strong | Medium early | Medium | High, if proven | Scalable software revenue, **the target** |
| C Enterprise annual license (gate + ledger, self-hosted) | High | Medium | High | High (Postgres, integration) | Strong | High early | Medium to high | Medium | Scalable software revenue with services drag |
| D Usage-based (per governed execution) | High | High | Medium | Low | Strong, variable | Medium | Medium | High with volume | Scalable software revenue, later |
| E Implementation services | Low to medium | Low | Medium | Is the product | Weak | High | None | Low | Services revenue |
| F Governance / assurance services (the audit method as a service, ghost_tools inside) | Medium | Low to medium | Low | Low | Medium (annual) | Medium | Low | Low alone; useful as entry | Services revenue that can seed software |
| G Data preparation / governance services | Low | Low | Medium | High | Weak | High | None | Low | Services revenue |
| H Data marketplace economics | Unknown | Unknown | Very high | Very high | Speculative | Unknown | Unknown | Very low today | Potential future revenue |
| I Revenue share | Unknown | Depends on partner | High | Low | Variable | Very high | Low | Low | Potential future revenue |
| J Combination | | | | | | | | | F to seed B, D to price B at scale, C for regulated |

Explicit split. Scalable software revenue: B, C, D, A. Services revenue: E, F, G. Potential future revenue: H, I. None of these is current revenue; there is no evidence of any revenue.

---

## Part 21: Consulting trap test

Could a skilled consulting team reproduce the customer outcome without licensing the software? For the gate: largely yes, today. The outcome is "a decision record per action and refusals on missing evidence"; a good team can build that in OPA plus a database in weeks per customer. What they cannot cheaply reproduce is the accumulated set of failure modes the stack has already encoded (silent skip, crash filed as verdict, undeclared scope executed). That knowledge is in commit messages and tests, not in a product. For ghost_tools: yes, with mutation tooling and patience. For the ledger: yes, with more effort.

What can become productized: the gate as a service with configuration-driven policies (most of it), adapters as configuration (partly; today each adapter is hand-written and the input contract is duck-typed), the assurance method as a CI job (all of it). What stays custom: policy content per customer, integration with each agent framework, the ledger's schema for each regulated domain.

Verdict: consulting is a viable entry and a real trap. The first three customers will be services-heavy by necessity. The flywheel only turns if each engagement's adapters and policies become configuration shipped to the next customer. The evidence today is against that: twelve hand-written adapters, five copy-pasted test fixtures (ghost_buster flagged them), no typed public API.

---

## Part 22: Data business test

Data problem: enterprises cannot trust the provenance of AI-processed or AI-generated data enough to use it operationally or sell it. Why existing platforms fail: lineage tools track tables and jobs, not decisions and evidence; they record that a transformation ran, not that it was verified. What this layer adds: receipts and custody per transformation (Conservation, sentinel_os). Who owns the data: the customer, always. Who pays for preparation: the customer, as services (Model G), which is low margin. Can data be monetized: only by the customer; a governance vendor taking a cut is Model H and requires transaction infrastructure this stack does not have. Would customers permit monetization: UNKNOWN; in regulated domains the default is no. Obstacles requiring professional review: privacy law for any demographic inference (the BISG estimator and sealed demographic channel in sentinel_os are exactly the kind of thing counsel must review), data-sale restrictions, contractual limits on derived data. Transaction infrastructure: none exists. Revenue model: none demonstrable.

CURRENTLY DEMONSTRABLE: receipts per verified transformation (Conservation, 52 tests). PLAUSIBLE FUTURE: "operationally trustworthy data" as a governed-pipeline outcome, sold as software to the data owner. SPECULATIVE: any marketplace or transaction revenue. Recommendation: do not raise on the data business; do not mention marketplaces to investors; keep the receipt primitive because it is what makes the future story possible.

---

## Part 23: Competitive substitution test

| Substitute | Does better | Does worse | Distribution | Switching cost | Why choose this stack |
|---|---|---|---|---|---|
| Hyperscaler agent platforms (AWS Bedrock Agents guardrails, Azure AI, Google Vertex) | Integrated, funded, default | Vendor-specific; guardrails are content filters and allowlists, not verification records | Overwhelming | High once adopted | Cross-vendor gate with honest records; only matters to customers who refuse lock-in |
| AI governance platforms (IBM watsonx.governance, Credo AI, Holistic AI) | Policy libraries, reporting, compliance mapping, sales teams | Govern models and documents, not individual executions | Enterprise sales in place | Medium | Execution-level enforcement rather than documentation |
| Policy engines (OPA, Cedar, Oso) | Mature, fast, general | Decide; do not verify evidence, simulate, or produce receipts by default | Open source, everywhere | Low | Adds verification and record to a decision |
| Observability and LLM observability (Datadog, LangSmith, Arize, Langfuse) | Tracing, dashboards, adoption | Observe after the fact; never refuse | Strong | Medium | A gate refuses; a trace does not |
| Security platforms and SOAR | Incident workflows, integrations | Playbook-level, not per-action verification | Strong | High | Finer grain, earlier in the path |
| Data lineage (Collibra, Alation, OpenLineage) | Table and job lineage | No decision or evidence semantics | Strong in data teams | High | Decision-level provenance |
| Workflow governance and GRC (ServiceNow, Archer) | Controls libraries, audit workflows, enterprise trust | Slow, human-centric, no execution path | Dominant | Very high | Automated evidence feeding into their records, not replacing them |
| Model-risk management (SR 11-7 tooling) | Regulator vocabulary | Model validation, not action governance | Banks | High | Complementary |
| SIEM (Splunk, Sentinel) | Scale, correlation | After the fact | Dominant | High | Complementary source of events |
| Provenance systems (C2PA, Sigstore, in-toto) | Standards, signing | Content and supply chain, not decisions | Growing | Medium | Could adopt their signing rather than compete |
| Consulting firms | Custom fit, trust | Non-repeatable, expensive | Strong | Low | Product economics, if productized |
| In-house engineering | Perfect fit, no vendor | Rebuilds the same failure modes | Free | None | Only if the encoded failure modes are worth more than a rebuild |

Uniqueness claim allowed by the evidence: a decision record that distinguishes checked-and-allowed from never-checked, with refusal on missing evidence as an opt-in. That is a design stance, not a moat, and a hyperscaler could adopt it in a quarter.

---

## Part 24: Build vs buy vs ignore

For the governed action gate, the likely enterprise answer today is **BUILD**, for three reasons the audit supports. The gate is a few thousand lines of Python around a policy kernel; the input contract is duck-typed and the output is a dict, so there is no API surface that makes buying easier than building; and the encoded lessons are readable in commit messages a competent team could copy in an afternoon. The answer shifts to **BUY** only when (a) the gate is a service with a stable schema and adapters for the two or three agent frameworks the customer already runs, and (b) it ships with receipts a customer's auditor accepts. The answer is **IGNORE** for any company whose agents only read; the pain starts when agents act. That is a large and growing fraction, but it is not everyone.

---

## Part 25: Economic value test

All figures below are placeholders to be replaced through discovery; none is a claim.

**Gate.** Risk value = expected loss from unauthorized or unverifiable agent actions without the gate − expected loss with it. Assumptions to validate: frequency of unauthorized actions per thousand agent executions (UNKNOWN), cost per incident including investigation hours (UNKNOWN), share prevented by a gate with the customer's policies (UNKNOWN). Second equation: automation enabled = actions the customer would allow an agent to take only with a gate × value per action − gate cost. This second equation is the one that sells; the first is the one security signs.

**Assurance.** Savings = (defects reaching production because a check was vacuous × cost per defect) − scanner cost. Assumptions: base rate of vacuous checks per thousand tests (this audit measured two confirmed per roughly 2,900 tests scanned by hand, plus five clinical skips; that is a floor, not a rate), cost per escaped defect (UNKNOWN).

**Ledger.** Savings = (reconstruction hours per dispute or examination × fully loaded rate × events per year) − ledger cost, plus avoided findings. All inputs UNKNOWN.

Discovery must produce, for at least three prospects each: incident frequency, investigation hours, current spend on the workaround, and what they would pay to make it go away.

---

## Part 26: Pricing hypotheses

**Gate.** Unit: governed executions per month, with a platform fee. Structure: annual contract, platform fee plus tiered execution bands. Implementation fee: yes for the first year (adapters and policies), targeted to zero by year two. Expansion: more agents, strict mode as a higher tier, ledger add-on. Price up: regulated industry, actions with financial or safety consequence, self-hosting. Price down: read-only agents, open-source alternatives, hyperscaler bundling. Minimum sensible contract: the level at which a single security review is worth the vendor's time; a hypothesis is the mid five figures annually, unvalidated. Evidence needed: three design partners stating what they would pay and what they pay today for the workaround.

**Assurance.** Unit: repositories or seats. Structure: annual, low four to low five figures. Implementation fee: none. Expansion: assurance reports for auditors. Validation: conversion rate from a free scan to a paid gate.

**Ledger.** Unit: governed decisions stored per year plus a platform fee; enterprise license for self-hosted. Implementation fee: substantial. Validation: one CCO's budget line.

Most plausible initial motion: founder-led, design-partner sales to three platform teams that already run acting agents, starting with a free assurance scan of their agent codebase (which the stack can do today) to open the conversation about the gate (which it cannot fully deliver today).

---

## Part 27: Land and expand

| Stage | Initial buyer | Next buyer | Incremental value | Integration cost | Switching cost | Expansion trigger | Contract mechanism |
|---|---|---|---|---|---|---|---|
| Cat: assurance scan | VP Eng | Platform lead | Confirmed vacuous checks | Near zero | Near zero | "Our agent code is full of these" | Free to paid scan |
| Cat: the gate (spine + Conservation) | Platform lead | CISO | Refusals and records on one agent | One adapter, policies | Moderate | Second agent, first incident reconstructed | Executions tier |
| More Cats: AUGUR, fortress, CCC | Platform lead | Ops | Screening, containment, recurrence | Configuration if productized; code today | Moderate | An action with magnitude; a repeat incident | Add-on modules |
| Sub-Voltron: gate + ledger | CISO | CCO | Custody, examiner-grade reconstruction | High (Postgres, schema) | High | Examination or dispute | Enterprise license |
| Full Voltron | Nobody today | | None demonstrated | | | | |

Viable? The first three stages are plausible and the architecture does support buying one piece first; the audit confirmed the gate runs without fortress, AUGUR, Gateway and CCC. The stage that matters commercially (gate to ledger) is blocked today because the ledger cannot be deployed from a clone. The full Voltron stage should be removed from the pitch.

---

## Part 28: Venture scale test

What would have to be true at each stage, given a gate-first model with a platform fee plus execution tiers. Contract values are illustrative bands, not forecasts.

| ARR | Customers × contract (illustrative) | Must be true |
|---|---|---|
| $1M | 10 to 20 design partners at mid five figures | A deployable gate service; two agent-framework adapters; receipts an auditor accepted once; founder-led sales |
| $5M | 40 to 80 customers, or 15 at low six figures | Net revenue retention above 110 percent from added agents; implementation under two weeks; first two account executives; gross margin above 75 percent |
| $10M | 80 to 150 customers | A partner channel (an agent-framework vendor or a GRC vendor embedding the gate); ledger add-on live; a second geography |
| $50M | 300 to 600 customers, or 100 at mid six figures | Category recognition ("execution governance" or whatever the market calls it); hyperscalers have not bundled an equivalent; regulated-industry references |
| $100M+ | Platform status: the gate in front of most automated actions at hundreds of enterprises | Usage pricing scales with agent volume; a standards position (receipts as an accepted evidence format) |

The assumption most likely to break the model: **that enterprises will buy a cross-vendor gate rather than accept the guardrails bundled with the agent platform they already pay for.** If hyperscalers ship honest execution records, this company is a feature. The second most likely: bus factor of one (every commit is by one author with AI co-authorship).

---

## Part 29: Moat test

| Moat | Status | Evidence |
|---|---|---|
| Code | **Speculative** | The hub is 41 files; the gate a few thousand lines; much AI-generated (Co-Authored-By trailers throughout); reproducible by a small team |
| Architectural | **Plausible** | The failure modes encoded (silent skips, crash as verdict, unchecked scope) are non-obvious and the fixes are principled; but they are readable in the open repos |
| Data | **Speculative** | Receipts and refusals would accumulate; nothing accumulates today |
| Integration | **Plausible later** | A gate in the execution path is sticky; nothing is in any execution path today |
| Governance | **Plausible later** | Becoming the customer's evidence source for auditors is the real prize; no auditor has seen a receipt |
| Network | **Speculative** | No cross-customer value mechanism exists |
| Knowledge | **Plausible later** | CCC plus ledger history could become the memory of what was refused and why; not built as a product |
| Distribution | **None** | Single founder, no channel, no partner |

Real today: none. Plausible: architectural stance, integration, governance, knowledge. The path to a moat is being the evidence format an auditor accepts, which is a standards and distribution game, not a code game.

---

## Part 30: Investor due diligence, 20 questions

1. **Why does this need to exist?** Because agents that act need a gate that refuses on missing evidence and records honestly. Evidence: the stack's own history shows how often a check silently did nothing (ten fixes on 2026-09-07 alone).
2. **Why now?** Agents moved from suggesting to acting in 2025 to 2026; every framework added guardrails, none added evidence. SUPPORTED by market observation, not by customer data.
3. **Who pays?** Hypothesis: platform engineering with CISO sign-off. UNKNOWN in fact; no customer conversation is in evidence.
4. **What painful problem?** Unauthorized or unverifiable automated action. Pain is asserted, not measured.
5. **Why won't Microsoft, AWS, Google build this?** They will build guardrails; whether they build honest cross-vendor receipts is UNKNOWN. The defensible answer is cross-vendor neutrality plus auditor acceptance, neither of which exists yet.
6. **Why won't an incumbent governance platform add it?** They govern models and documents; adding execution-path enforcement is a product-line change. They could partner instead; that is the opportunity and the risk.
7. **Why isn't this a consulting business?** Today it is closer to one (Part 21). It stops being one when adapters and policies ship as configuration.
8. **What is the wedge?** The governed action gate. Chosen in Part 18.
9. **What is the moat?** None today (Part 29).
10. **What is proprietary?** Nothing patented or secret; repositories are public on GitHub. The proprietary asset is the founder's understanding of failure modes.
11. **What has been validated?** Technically: the gate runs end to end; the hub passes alone; the audit's B-probes find real defects. Commercially: nothing. Zero evidence of a customer, pilot, or letter of intent.
12. **What remains hypothetical?** Every customer, price, and outcome claim in this memo.
13. **Why is the architecture so large?** Because it grew as an exploration; 18 repositories where 4 code edges exist. Thirteen components are features or personal projects (Part 16). This is a liability in diligence.
14. **How much is production quality?** Little. No versions in six repos, no dependency manifest in two, tracked audit logs that grow on every test run, a suite that crashes pytest from the root in GSA-815, a snapshot repo (OBSERVE) that cannot import its own copy. The hub and five singletons are clean.
15. **How much was AI-generated?** Substantial; commit trailers credit Claude models across the stack. UNKNOWN as a percentage. Investors will ask about maintainability and licensing of AI-written code.
16. **Can another team understand it?** Partly. Commit messages are unusually good; the orchestrator is one 344-line function; adapters are duck-typed; no public API documented. Two weeks for a senior engineer, my estimate.
17. **What happens if the founder disappears?** The company ends. Every commit is by one account.
18. **Smallest product customers will buy?** Hypothesis: an assurance scan of their agent code; then the gate on one agent. Unvalidated.
19. **Expansion path?** Part 27; plausible through the gate, blocked at the ledger.
20. **Evidence for the revenue model?** None. No revenue, no pricing conversations in evidence.

---

## Part 31: Investor kill test

The strongest case against investing, then each argument tested.

| # | Argument | Test against evidence | **Class** |
|---|---|---|---|
| 1 | No customer has ever been observed; this is a solo research program | Nothing in 18 repos references a customer, pilot, or design partner | **SERIOUS** (fatal if still true in twelve months) |
| 2 | Bus factor of one; a solo founder with AI co-authors cannot build an enterprise vendor | Every commit by one author | **SERIOUS**, addressable by a hire, but only after funding |
| 3 | Hyperscalers will bundle equivalent guardrails and kill the category | Plausible; no evidence either way; honest records are not yet a bundled feature anywhere I know of | **SERIOUS** |
| 4 | The code is not production grade | Confirmed in the audit: missing manifests, no versions, tracked logs, crashing test root, snapshot drift | **ADDRESSABLE** (weeks of work for the wedge subset) |
| 5 | The architecture is a metaphor; the chain is not wired as drawn | Confirmed: two stages nominal, one optional in practice | **DISPROVEN as a kill**, because the wedge pair is wired and runs; **SERIOUS as a credibility problem** if the diagram stays in the pitch |
| 6 | It is a consulting business in software clothing | Twelve hand-written adapters, duck-typed contract, no API: today, yes | **ADDRESSABLE** with a service boundary and configuration-driven policies |
| 7 | The controls are decorative: gates default to advisory, and until this week scope was unchecked in 4 of 5 production paths | Confirmed, and fixed with opt-in strict modes; defaults remain permissive | **ADDRESSABLE**; the product must ship strict by default |
| 8 | The clinical application is the emotional center and it does not work | Five known misses recorded as skips; no validation study | **SERIOUS** if it stays in the pitch; **WEAK** if it is repositioned as a demonstration domain |
| 9 | Market size is unknown; "execution governance" is not a budget line | True; no analyst category, no budget line | **SERIOUS** |
| 10 | Everything is public on GitHub; there is nothing proprietary | True; the code is open; the moat would have to be distribution and auditor acceptance | **WEAK** as a kill (many companies build on open code), **SERIOUS** as a moat question |
| 11 | Thirteen of eighteen components are irrelevant and signal lack of focus | Confirmed in Part 16 | **ADDRESSABLE** by archiving them before any raise |
| 12 | The provenance of the ecology corpus creates legal exposure | 872M of harvested source with no license manifest, one file matching a private-key pattern | **ADDRESSABLE**: exclude it from the company entirely |
| 13 | No pricing, no economic equation with real inputs | Confirmed | **SERIOUS**, resolved only by discovery |
| 14 | Timing is late; guardrail startups already raised in 2024 and 2025 | Partly true for content guardrails; execution-evidence is less crowded | **WEAK** |

No argument is FATAL on the evidence. Three are SERIOUS and structural: no customer, one founder, category not yet a budget line. That is the profile of a pre-seed research project, not a Series A company.

---

## Part 32: Investor blue team

Assuming the strongest version is real.

**WHY THIS.** Every enterprise is about to have thousands of automated actors, and the only thing that makes that governable is a gate that refuses when it cannot record. SUPPORTED (by the failure modes this stack keeps finding in itself).
**WHY NOW.** Agents act; frameworks shipped guardrails without evidence; regulators are asking for records of automated decisions. HYPOTHESIS.
**WHY YOU.** A founder who has spent a year finding every way a control can pass without doing its job, and who fixes them with an unusually principled pattern (warn, record, opt-in strict). SUPPORTED by the commit history; unproven as a company builder.
**WHY THIS ARCHITECTURE.** Because the gate composes: screen, permit, contain, verify, execute, watch, remember are separate questions with separate owners, and a customer can buy the two that hurt today. SUPPORTED for two compositions; SPECULATIVE beyond.
**WHY CUSTOMERS.** The first incident caused by an acting agent is a board-level event; after it, "show me what was checked" becomes urgent. HYPOTHESIS.
**WHY IT EXPANDS.** Every new agent is a new execution stream through the same gate; every incident is a reason to add the ledger. HYPOTHESIS with architectural support.
**WHY IT COMPOUNDS.** Refusals, receipts and recurrences accumulate into the customer's memory of what not to do. SPECULATIVE; nothing accumulates today.
**WHY IT DEFENDS.** Being the evidence format an auditor accepts. SPECULATIVE.
**WHY IT SCALES.** A gate service prices per execution while engineering cost is flat. HYPOTHESIS; today it is a library with hand-written adapters.
**WHY LARGE.** If automated action becomes the norm, the gate in front of it is a platform. SPECULATIVE.

Nothing here is PROVEN. Two claims are SUPPORTED. That is the honest shape of the blue case.

---

## Part 33: Evidence ladder

| Claim | Level | Basis |
|---|---|---|
| Conservation Kernel verifies and issues receipts | **3 Integrated** (spine calls it end to end; 52 tests alone) | Audit C1, C2 |
| The gate admits, permits, verifies, executes and records | **4 End-to-end demonstrated** in tests; **5 Independently verified** by this audit's stage-removal probes; not customer-facing | Audit C2 |
| Refusal on missing evidence, honestly recorded | **4 to 5**, opt-in only | Commits 9ea72f9, 2b8403b, e2f0683 |
| ghost_tools finds real vacuous checks | **5 Independently verified** (this audit used it and confirmed findings by mutation) | Audit B1 |
| The custody ledger reconstructs decisions | **2 Unit tested** at best; could not be run here | Audit C1 |
| Pediatric sepsis engine detects deterioration | **2 Unit tested**, with five known misses | Audit B2 |
| Screening (AUGUR) and containment (fortress) refuse bad actions | **3 Integrated**, opt-in | Audit C2 |
| Claims with provenance (HERALD, TIE, CCC) | **2 to 3** | Isolated suites pass; no end-to-end demonstration observed |
| The full eight-stage chain | **1 Code exists** for six stages, **0 Idea** for two | Audit C2 |
| A customer has the pain | **0 Idea** | No evidence |
| A customer will pay | **0** | No evidence |
| Pricing, retention, expansion | **0** | No evidence |

Nothing is above level 5. Nothing commercial is above level 0.

---

## Part 34: The single most important experiment

**Experiment.** Put the gate in front of one real, acting agent at one design partner for thirty days, in shadow mode for week one and strict mode thereafter, and ask them to pay a nominal pilot fee before it starts.

**Target customer.** A platform team at a company of 200 to 5,000 engineers already running an agent that mutates production systems (closes tickets, changes infrastructure, adjusts records). Ideally one that has had an agent incident.

**Setup.** Two weeks of engineering first: wrap `orchestrate_request` as a service with a typed request and decision schema; ship strict mode on by configuration; write one adapter for the partner's framework; store receipts in Postgres (a minimal ledger, not sentinel_os). No other subsystem.

**Demonstration.** Week one: every execution passes through, recorded, none refused; the team reads the receipts. Weeks two to four: strict mode; refusals on missing scope, evidence, or policy; the team reconstructs at least one real event from receipts alone.

**Success criterion.** The partner pays the pilot fee before day one; at day thirty the gate is still in the path; at least one refusal was correct and would have been an incident; the partner names what they would pay for a year and which second agent they would add.

**Failure criterion.** No partner will pay anything to start; or the gate is removed from the path before day thirty; or every refusal was a false positive; or the partner cannot name a budget owner.

**Evidence collected.** Executions per day, refusals and their outcomes, receipts read, reconstruction time for one event, stated willingness to pay, named buyer, integration hours.

**Decision.** Success: raise a seed on the gate alone and archive the rest. Failure with "we would build this": pivot the assurance scan into the product and treat the gate as a feature. Failure with "we do not have this pain": stop.

This experiment tests pain (they let it into the path), capability (it stays there), and willingness to pay (the fee) at once, and it costs two weeks of engineering rather than another subsystem.

---

## Part 35: 90-day plan

**Days 1 to 30, proof and packaging.**

| Activity | Objective | Owner | Output | Success | Eng | Customer | Investor evidence |
|---|---|---|---|---|---|---|---|
| Archive thirteen non-wedge repos; move OBSERVE clinical work to a "reference application" label | Focus | Founder | A four-repo company (spine, Conservation, Gateway, ghost_tools) | Diligence sees four repos | No | No | Yes |
| Gate as a service: typed schema, strict-by-config, receipts to Postgres | Deployable wedge | Founder + first engineer | Container, API, one adapter | Runs from a clean clone in ten minutes | Yes | No | Yes |
| Production hygiene on the four repos: manifests, versions, untrack audit logs, CI from a clean clone | Credibility | Engineer | Green CI with no siblings | Audit C1 rubric passes | Yes | No | Yes |
| Rewrite the pitch: kill the eight-stage diagram; sell the pair | Honesty | Founder | One-page thesis matching Part 18 | No claim above its evidence level | No | No | Yes |
| Twenty discovery interviews with platform and security leads | Pain measurement | Founder | Incident frequency, workaround cost, named buyers | Three say "we would pilot" | No | Yes | Yes |

**Days 31 to 60, customer validation.**

| Activity | Objective | Owner | Output | Success | Eng | Customer | Investor evidence |
|---|---|---|---|---|---|---|---|
| Free assurance scan of three prospects' agent codebases (ghost_tools plus the B1 protocol) | Open the door with something that works today | Founder | Three findings reports | At least one confirmed vacuous check per prospect; one converts to a gate pilot | Light | Yes | Yes |
| Run the Part 34 experiment with the first partner | The one experiment | Founder + engineer | Thirty days of receipts and refusals | Part 34 criteria | Yes | Yes | Yes |
| Auditor conversation: show one receipt to one external auditor or examiner | Test the moat hypothesis | Founder | Written reaction | "This would satisfy control X" | No | Yes | Yes |

**Days 61 to 90, paid-pilot preparation.**

| Activity | Objective | Owner | Output | Success | Eng | Customer | Investor evidence |
|---|---|---|---|---|---|---|---|
| Second and third pilots, priced | Repeatability | Founder | Two signed pilot agreements | Both pay before start | Light | Yes | Yes |
| Adapter as configuration for the two most common frameworks | Escape the consulting trap | Engineer | Config-driven adapters | New pilot integrated in under a week | Yes | No | Yes |
| Pricing test: propose the annual number to pilot one | Validate Part 26 | Founder | Reaction and counter | A number they accept in principle | No | Yes | Yes |
| Seed memo with evidence ladder updated | Fundraise readiness | Founder | This memo, re-scored | At least three claims move up two levels | No | No | Yes |

**Do not do in these 90 days:** any work on the pediatric sepsis engine; any work on sentinel_os or GSA-815 beyond archiving or a manifest; HERALD, TIE, innovation_os, ATS, ecology, GEMS, TOUCHSTONE; any new stage; any marketplace or data-monetization design; a new chain diagram.

---

## Part 36: Investor scorecard (0 to 5)

| Dimension | Score | Evidence |
|---|---|---|
| Customer pain | 3 | Plausible and rising for acting agents; not measured with any customer |
| Willingness to pay | 0 | No evidence of any kind |
| Technical differentiation | 2 | One real design stance (honest records, refuse on missing evidence); the rest is reproducible |
| Demonstrated capability | 3 | Gate runs end to end in tests; audit verified stage behaviour; ghost_tools proved itself |
| Production readiness | 1 | Manifests missing, no versions, tracked logs, crashing test root, snapshot drift; the hub and singletons are clean |
| Competitive position | 1 | No distribution; hyperscalers and governance incumbents adjacent |
| Expansion potential | 3 | Architecture supports buying one piece then more; ledger stage blocked |
| Recurring revenue potential | 3 | A gate in the execution path recurs by nature; unproven |
| Gross-margin potential | 4 | Software; services-heavy for the first customers |
| Defensibility | 1 | Nothing real today; a plausible governance moat later |
| Market size potential | 3 | Large if "execution governance" becomes a budget line; no category today |
| Founder / technical advantage | 3 | Deep, unusual understanding of control failure modes; solo, AI-assisted, no company-building evidence |
| Sales feasibility | 2 | Founder-led design-partner sales are feasible; enterprise security review is slow; no channel |
| Voltron economic value | 1 | Two compositions are useful; the full chain is a metaphor |
| **Overall venture attractiveness** | **2** | A credible wedge with zero commercial evidence and a focus problem |

---

## Investment memo

**THE COMPANY.** What it would actually sell: a gate that sits in front of automated and AI-agent actions, admits and permits them against policy, verifies them, refuses when it cannot record what it checked, and leaves a receipt per execution that an auditor can read. Everything else in the eighteen repositories is either a component of that gate, a demonstration domain, or unrelated.

**THE WEDGE.** The governed action gate: observe-perceive's PERCEIVE gates and orchestrator plus Conservation Kernel plus Governance_Gateway admission, packaged as a service with strict mode on.

**THE CUSTOMER.** A platform engineering team at a 200-to-5,000-engineer company already running at least one agent that mutates production systems, with CISO sign-off as the economic buyer.

**THE PAIN.** An agent does something no policy authorized and afterwards nobody can prove what was checked. Asserted, not yet measured.

**THE PROOF.** The gate runs end to end in tests; removing stages behaves as documented; refusals on undeclared scope, missing vitals and crashed stages are recorded honestly; the hub passes with nothing beside it; the audit tooling found and fixed real vacuous checks in this stack. Nothing has been seen by a customer.

**THE GAP.** Four things: a service boundary with a typed schema; strict mode by default in the product; production hygiene on four repos so a clean clone runs; and one paying design partner. Nothing on this list is a new subsystem.

**THE VOLTRON.** The genuine emergent capability is narrow and real: permission plus verification plus an honest receipt in one path, so "allowed" can never mean "unchecked". A second composition, gate plus custody ledger, could become the evidence source auditors accept, but the ledger cannot be deployed from a clone today. The eight-stage chain is a diagram, not a product.

**THE MOAT.** None today. Plausible later: being the evidence format an auditor accepts, and sitting in the execution path of every automated action. Both are distribution and standards outcomes, not code outcomes.

**THE BUSINESS MODEL.** Platform subscription with execution-tier pricing for the gate; enterprise license for the ledger add-on in regulated industries; services only as entry, with an explicit plan to turn adapters and policies into configuration. No data-marketplace story.

**THE EXPANSION MODEL.** Free assurance scan opens the door; the gate on one agent; more agents through the same gate; screening, containment and recurrence as add-ons; the ledger when an examination or dispute makes custody urgent.

**THE RISKS.** One: no customer has ever been observed. Two: a single founder with AI co-authors is the whole engineering organisation. Three: hyperscalers bundle honest execution records and the category collapses into a feature. Four: the pitch keeps the eight-stage chain and the sepsis engine, and diligence finds what this audit found. Five: the first customers make it a consulting business and the adapters never become configuration.

**THE NEXT 90 DAYS.** Archive thirteen repositories; ship the gate as a service that runs from a clean clone; twenty discovery interviews; three free assurance scans; one paid thirty-day pilot in the execution path; one auditor shown one receipt.

**INVESTMENT VERDICT: PROMISING BUT TOO EARLY.**

Why not "investable after validation": that label implies the technology is compelling as it stands. It is compelling as a design stance and as an engineer's understanding of how controls fail; as software it is a library with permissive defaults, hand-written adapters, an untyped output, and no deployable service, inside a portfolio of eighteen repositories of which thirteen do not belong in the company. Why not "technically impressive, commercially weak": the wedge is real, the pain is plausible and rising, and the gap to a validating experiment is two weeks of engineering plus a customer conversation, not a rebuild. Why not "do not pursue": nothing in the kill test is fatal.

What must become true for a sophisticated investor to write a check: one paying design partner keeps the gate in the execution path for thirty days and names a budget; the company is four repositories that run from a clean clone; and the founder can show, in the partner's own numbers, that a refused action would have been an incident. Until then this is a research program with a promising thesis, and it should be funded like one.
