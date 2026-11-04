# CNS admission and identity binding: audit

Evidence classes: EXECUTED (run on `main` before the production profile), INSPECTED, PROPOSED. A CNS digest is tamper-evidence for what it was computed over. It is not proof that an event occurred, that a clinical action was authorized, or that an execution succeeded.

## Admission

| Question | Finding |
|---|---|
| Can a governed path run without CNS root admission? | Advisory and strict: yes (EXECUTED: strict plus a bare lambda guard executed one action). `production`: no, it refuses to start |
| Is the configured `RootAdmission` the one the kernel uses? | Now proven by identity in the production profile. Before, nothing checked it |
| Does the roster refuse? | The production profile puts a canary request to it at startup |

## Identity binding (EXECUTED on `main`; not changed by the production profile)

| Identity | State | Evidence |
|---|---|---|
| Request vs document | **One string.** `request_id` equals `artifact_id` | `sentinel_perceive_adapter.py`; probe: equal |
| Subject (patient) | **Unbound.** It is a claim in `context["patient_id"]`. With context patient P002, vitals patient P001 and artifact text P777, the action executed | probe |
| Assessment to decision | OBSERVE's verdict fingerprint is not in the PERCEIVE request; in the orchestrator OBSERVE runs *after* the action | `governance_orchestrator.py` Phase 6 |
| Decision to execution | Bound: commitments recomputed by the chain verifier; `audit_chain_valid` true on the end-to-end test | `governance_chain.verify_result` |
| Notification | **No identity exists.** No notification, delivery or acknowledgement record | n/a |
| Escalation episode | Each reading has its own audit hash; since PR #71 an escalation names its kind and previous regime. There is no episode id | `observe_consolidated.py` |

## Native digest vs CNS binding digest

OBSERVE's `decision_fingerprint`, PERCEIVE's `state_commitment`, and the execution ledger's entry hashes are native. CNS gate results carry a `subject_digest` computed by `cns.gate.subject_digest`. They are kept distinct and neither replaces the other. The CNS OMEGA gates for OBSERVE and PERCEIVE still accept hand-built verdicts with a non-empty audit hash (EXECUTED); they bind a verdict to a subject but do not recompute or look up the evidence. That is a gap in the optional CNS layer, not in the production profile, and it is recorded here so the profile is not read as closing it.

## Open items (not fixed by the production profile)

1. Separate request id from artifact id.
2. Bind the subject across vitals, request and artifact, and refuse a mismatch.
3. Bind the OBSERVE assessment fingerprint into the PERCEIVE request, and run OBSERVE before the action.
4. Make the CNS OMEGA gates recompute or look up evidence.
5. Give notifications and acknowledgements identities (the human escalation and receipt model).
