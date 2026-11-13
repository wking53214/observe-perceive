# Governed execution profile: `production`

This document is also the CNS production integration specification. The three documents the brief lists for this area overlap, so they are consolidated into this one and `CNS_ADMISSION_AND_IDENTITY_AUDIT.md`: this file says what the profile requires and how it fails; the audit says what is and is not bound.

Evidence classes: EXECUTED, INSPECTED, PROPOSED.

## Why a new profile

The strict profile makes advisory checks into refusals, but it asks only that the Conservation Kernel *has* a root guard. Any callable satisfies that. EXECUTED on `main` before this change: strict plus `ConservationKernel(root_guard=lambda a: True)` and no CNS anywhere returned `APPROVED_AND_EXECUTED`. "Strict" and "the CNS roster is the gate" were not the same statement.

## What `profile="production"` requires

It is strict, plus a proof at construction. If any item fails the orchestrator does not start, and every failure is reported at once (`ProductionProfileError.problems`).

| Requirement | How it is checked | Refused example |
|---|---|---|
| CNS importable, at least v4.0.0 (the floor, which is also what this repository pins) | imports the roster's dependencies and reads `cns.__version__` | CNS missing, v2.3.0, an unreadable version |
| `root_admission` is a real `RootAdmission` | `isinstance` | a lookalike callable with `admit` and `close` |
| The kernel's root guard **is that object** | identity (`kernel.ledger._root_guard is root_admission`) | a kernel guarded by `lambda a: True`; a second `RootAdmission` the kernel does not hold |
| The roster refuses | a canary request that must be refused is put to the roster; nothing is registered; the guard must be closed afterwards | a roster that admits the canary, crashes on it, or leaves the guard open |
| Executor guard on | strict check, and the profile's own | `guard_executor=False` |
| Receipts exist and are signed | `receipts.signer` | no log, unsigned log |
| Execution ledger is durable and signed | `path` and `signer` | in-memory ledger, unsigned ledger |
| A person is told and followed up | an `EscalationDesk`: valid policy, a key that can verify for every responder, a signed log at a path that can be written; no bare `alert_human` beside it | no desk, a bare callable, a bad policy, an unkeyed responder, an in-memory, unsigned or unwritable log |
| OBSERVE configured | not `None` | none |
| Event-time source keys registered | `source_signers` non-empty | none |

Failure behavior: fail closed at construction (`ProductionProfileError`, a `ValueError`). There is no downgrade and no warning mode. A deployment that wants advisory or strict says so by name, and the record names the profile on every result (`handoff.profile`).

## What it does not prove

* **Same-process only.** The approval proof is process-local (`DecisionGuard`), so this stops a root registering without the roster saying yes to that document. It does not stop hostile code in the same process, and it means nothing to another process. A caller who invokes the raw action function directly bypasses everything. Both are stated in the README and unchanged.
* **The private attribute.** The identity check reads the kernel's `ledger._root_guard`, because the kernel exposes only `has_root_guard`. If a kernel version renames it, the profile refuses (it cannot prove the wiring). A public accessor on the kernel would be better; that is a change to another repository.
* **The canary tests the roster's refusal path, not its approval path.** It cannot prove a roster approves the right documents.
* **Not clinical.** Nothing here decides a clinical rule, a privacy rule or an authorization. CNS is used only as the roster contract.

## Requirement the profile cannot prove (stated, not hidden)

The profile checks that a notification policy exists and is well formed. It cannot check that a scheduler calls `EscalationDesk.sweep()`, that a real person holds each responder key, or that the numbers in the policy are clinically sensible. Those are operator duties and a hospital decision. See `docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md`.

## The two pipeline paths

`GovernanceOrchestrator` remains the only path that gates a consequential action. `cns_governed_decision.GovernedPipeline` stays a separate, optional check that nothing calls. Reason: integrating it would put a second set of gates beside the orchestrator's without adding a control the roster does not already provide, and CNS must remain a pass-through contract layer. It stays a caller-chosen extra for after-the-fact gating of a result.

## Verification

`test_production_profile.py`: 20 tests, including the strict-accepts-a-lambda contrast, a second `RootAdmission`, a lookalike, a roster that approves the canary, one that crashes, one that leaves the guard open, version refusals, each missing part, and a correct wiring that executes end to end with `audit_chain_valid=True`. Six mutants of the profile are each caught. Without CNS the file skips; with `OBSERVE_PERCEIVE_REQUIRE_CNS=1` a missing CNS is an error.
