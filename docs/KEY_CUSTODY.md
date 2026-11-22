# Key custody

Every key this package signs or verifies with: who holds it, who checks it, and what someone who stole it could do.
The package never creates, stores or defaults a key. Each is handed in by the deployer, as a `Signer`
(`conservation_kernel.signing`: `key_id`, `sign(payload)`, `verify(payload, signature)`), except the FORTRESS audit
key, which is read from `FORTRESS_AUDIT_KEY`.

## The one rule that decides most of this

`check_signature` only ever calls `verify` and reads `key_id`. So a slot where this package **only checks**
signatures can be given a **verify-only** object: one whose `verify` uses a public key and whose `sign` refuses. Then
the process doing the checking cannot sign in anyone's name. `test_key_custody.py` proves this for every verify-only
slot below by passing an object whose `sign` raises.

With `HmacSigner` (the only `Signer` this package's tests use) signing and verifying use the same secret, so **every
checker can also forge**. That is the HMAC residual the README and the reviews name. It is a property of the key type
the deployer picks, not of these slots. An asymmetric signer (Ed25519, an HSM or a signing service) closes it for the
verify-only slots with no change to this package.

## The map

| Key | Signs | Checks | Slot kind | If stolen |
|---|---|---|---|---|
| Execution ledger (`ExecutionLedger(signer=)`) | the orchestrator, on issue and consume | the orchestrator on reload; `reconcile()` on read, which only verifies; **the `python -m reconciler run` CLI builds an `HmacSigner`**, so a scheduled reconciler holds this secret | **sign and verify** (reading alone can use a verify-only key) | Forge `issued` and `consumed` entries. That can pair a bypass effect with a fake slip and **hide it from the reconciler**. The most valuable key here. |
| Receipts (`ReceiptLog(signer=)`) | the orchestrator, per result | the log on reload | sign and verify | Forge or rewrite receipts of what was decided. |
| Escalation log (`EscalationLog(signer=)`) | the desk, per ticket event | the desk on reload | sign and verify | Forge ticket history. Where what was already paged is read back from this log (as `test_operational_chain.py` does), a forged `opened` entry makes a real violation look already paged, so it is never paged. |
| Responder keys (`EscalationDesk(responder_keys=)`) | each responder, on their own device | the desk, at acknowledgement | **verify only** | With HMAC, whoever holds the desk's copy can acknowledge as that responder. Verify-only keys remove that. |
| Registry actor keys (`ActorRegistry(keys=)`) | each actor (admin, delegator), on their own side | the registry, on every action and on replay | **verify only** | With HMAC, the registry's holder can grant or revoke roles as any actor. A root's key is the worst case: it can grant `registry:admin`. |
| Event-time sources (`source_signers=`) | the feed or sensor that states the time | the orchestrator | **verify only** | Backdate or postdate events, which moves them across the governance chain's time windows. |
| Heartbeat (`HeartbeatWriter(signer)`, `check_heartbeat(signer)`) | the sweep runner | an external checker | verify only in `check_heartbeat`; **the CLI builds an `HmacSigner`** | Forge a healthy heartbeat for a dead process, which silences the external alarm. The CLI checker necessarily holds the secret today. |
| FORTRESS audit (`FORTRESS_AUDIT_KEY`) | fortress-kernel's audit ledger | fortress-kernel | sign and verify (HMAC, fortress-kernel's) | Forge FORTRESS audit records. `production` refuses a missing, short (< 32 bytes) or published key. |

CCC write permission and human verification are **not keys** here: `make_ccc_system` passes callables, or states that
none are given (see the CHANGELOG, Step 1).

## What a deployer should decide, per key

1. **Who holds the signing half.** For the four sign-and-verify keys, that is the process that writes the log, and
   whoever can read that process's memory or environment.
2. **Whether checkers get the signing half.** For the four verify-only slots, give them a verify-only object if the
   key type allows it.
3. **Separation.** Use a different key for each row. Sharing one key across rows turns the theft of any one into the
   theft of all, for example a heartbeat key that is also the ledger key.
4. **Rotation.** Not modelled anywhere in this package. A stolen key stays valid until the deployer replaces it and
   starts a new log, or a new registry, under the new one.

## What this document does not cover

It does not prove any of this is deployed. Whether keys sit in an HSM, an environment variable or a file, who can read
them, and how a compromise is noticed are the deployer's, outside this package.
