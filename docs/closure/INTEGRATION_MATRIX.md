# INTEGRATION_MATRIX

Cross-repository contracts as they exist in code on 2026-09-08. "Import"
lists exactly the names the spine takes from the other repository; anything
else in that repository is not a contract the spine depends on. "Resolution"
is how the dependency is found at runtime. "Break test" is what fails in the
spine's suite if the other side changes that surface.

## Hard dependency

| Repo | Version | Import (from observe-perceive) | Calls made | Resolution | Break test |
|---|---|---|---|---|---|
| conservation_kernel | 0.2.0 | `ConservationKernel`, `Artifact`, `Actor`, `ActorKind`, `Proposition`, `FunctionalContract`, `DeclaredChange`, enums (`EpistemicStatus`, `Origin`, `AuthorityStatus`, `ChangeKind`), `errors.InvalidArtifact`, `errors.LedgerError` | `register_root`, `submit`, `reconstruct`, `register_manifest`, `save`, `ConservationKernel.load` | declared in pyproject (`conservation-kernel @ git+...@mission/close-the-system`), installed package | test_conservation_boundary (7), test_chain_closure (6), test_vertical_slice (5) |

Contract the spine relies on (verified by test_conservation_boundary):
`register_root` refuses an artifact whose propositions are born canonical or
authoritative without an authorizing subject (`RootAdmissionError`);
`submit` of a derived artifact with a `FunctionalContract` returns a
decision whose `verified` flag is authoritative; `reconstruct(artifact_id)`
returns the transformation chain the verifier checks membership against
(`conservation.in_kernel_ledger` requires a non-empty transformation list
and the request artifact among the roots); `save`/`load` re-verify every
artifact on restore and raise `SnapshotIntegrityError` on any alteration.

The pin must move from the branch to a tag once the kernel's PR merges.
Until then a clean clone of observe-perceive installs from the branch.

## Optional packs (extras `chain` and `fortress`)

| Repo | Version | Import | Calls made | Resolution | When absent |
|---|---|---|---|---|---|
| Governance_Gateway | 0.1.0 | `governance_gateway.gateway.GovernanceGateway`; `models.{Artifact, Authority, EpistemicStatus, GateReason, Scope}` | `GovernanceGateway().evaluate(artifact)` in `GatewayAdmissionAdapter.admit`; `seal` builds the sealed artifact; the orchestrator re-verifies `integrity`, scope and artifact id of a passed admission | sibling `../Governance_Gateway` first, installed package second | adapter reports not available; a scope claim without a seal is honoured as a claim (default) or refused (strict) |
| AUGUR | 1.0.0 | `augur.{Augur, SimulationConfig}` | `Augur(operational_seed).run_cycle(...)` in `AugurScreenAdapter.simulate` | sibling `../AUGUR` first, installed second | `simulation_screen=True` raises ImportError at construction; default is off |
| fortress-kernel | 1.0.0 | `fortress_unified.{FortressUnified, FortressConfig, Payload}` | `FortressUnified(config).process(payload)` | sibling `../fortress-kernel` first, installed second | `fortress_controller=` raises ImportError at construction; default is off |
| GEMS | 0.1.0 | `gems.{Authority, HandoffValidator, HumanAuthorityGuard, Origin}`, `gems.governance.constitution.ConstitutionalViolation` | handoff validation in `GemsGovernanceAdapter` (not on the orchestrator path) | sibling first, installed second | adapter tests skip |
| CCC | 0.1.0 | `ccc.{Actor, CCCSystem, EpistemicStatus}` | `CCCSystem.record_external_finding(...)` in `OrchestratorCCCAdapter.record`; the vertical slice calls it after each orchestration | sibling `../CCC` first, installed second | recording is skipped and reported (`ccc_recorded=None` in the slice) |

## Frozen repositories the spine names but does not import

| Repo | Status | Relationship |
|---|---|---|
| sentinel_os | FROZEN | The "sentinel artifact" the spine accepts is duck-typed (`artifact_id`, `content`, `metadata.{origin_status, authority_status, epistemic_status, parent_artifact_ids}`, optional `metadata.occurred_at`). sentinel_os is never imported. Its Postgres custody ledger is a candidate source of `event_time` and origin evidence, not a dependency. |
| GSA-815 | FROZEN | "GSA-815" is the producer label on the `ExecutionContext`. The executor is a caller-supplied callable `func(execution_context)`; the repository has no executor of that shape. |

## Contract versions carried in every record

| Field | Value | Where |
|---|---|---|
| `handoff.contract_version` | 1.1.0 | governance_contracts.CONTRACT_VERSION; on every result and receipt |
| `record_version` | 1 | governance_record.RECORD_VERSION; on every receipt record |
| commitment scheme | `compute_state_commitment(parent, state)` over `governance_chain.{request_state, approval_state, execution_context_state, outcome_state}` | governance_chain |
| execution ledger entry | `{kind: issued|consumed|refused, execution_id, request_id, artifact_id, artifact_hash, approval_state_commitment, context_state_commitment, approval_timestamp, context_timestamp, producer, lineage, consumer, outcome, previous, hash, recorded_at}` | execution_guard.issuance_record |
| receipt | `{sequence, record_hash, previous, written_at, hash, status, request_id, execution_id}` beside the full `record` | governance_record.ReceiptLog |

## Second-order dependencies observed

- The kernel's `Artifact` gained a required `propositions` argument in
  0.2.0; the spine's adapter and the kernel-only attack script were both
  updated. Any other caller constructing `Artifact` directly will break;
  none exists in the active repositories.
- CCC's `CCCSystem.load(path, semantic_index=, semantic_threshold=)` is
  used only by CCC's own tests and the vertical slice through
  `OrchestratorCCCAdapter(ccc_system=...)`.
- ghost_tools is not imported by any repository; it is run against them.

## Release readiness, measured 2026-09-08

| Repo | Branch | Clean-clone install | Tests | ruff |
|---|---|---|---|---|
| observe-perceive | claude/prompt-red-blue-team-sj9a31 | kernel only: imports ok | 494 passed, 63 skipped (kernel only); 589 passed, 5 skipped (all packs) | clean |
| conservation_kernel | mission/close-the-system | ok | 67 passed | clean |
| CCC | mission/close-the-system | ok | 142 passed, 1 xfailed | clean |
| ghost_tools | mission/close-the-system | ok | 167 passed | clean |
| Governance_Gateway | main | ok (PR #3) | CI green | clean |
