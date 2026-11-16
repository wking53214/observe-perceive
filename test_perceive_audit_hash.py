"""test_perceive_audit_hash.py -- PERCEIVE's chain hash covers the request snapshot (decision D10).

Measured on `main` before this change (perceive_consolidated.py, `ImmutableAuditLedger._hash`): the chain hash covered
`audit_id`, `timestamp`, `evaluated_gates` and `final_verdict` and nothing else, so the patient id and the request context in
`request_snapshot`, the gate outputs, and the manifest version and hash could be rewritten with the chain still valid. Entries
now carry `hash_version`: 1 is the old hash (every entry written before, which load unchanged), 2 covers the rest.
"""
import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest

from perceive_consolidated import (
    AuditEntry, AuditLedgerIntegrityError, ImmutableAuditLedger, PerceiveGovernanceKernel, PolicyManifest, PolicyRequest,
)

T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


def kernel(path=None):
    k = PerceiveGovernanceKernel(ledger_path=path)
    k.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=T0, policies={"escalation_policy": {"max_daily": 5}}))
    return k


def ask(k, i=0, patient="P1"):
    return k.evaluate_request(PolicyRequest(request_id=f"R{i}", request_type="escalate_patient", subject_id=patient,
                                            actor_id="t", context={"justification": "x", "severity": "warning"},
                                            timestamp=T0 + timedelta(hours=i * 3)))


def test_new_entries_are_version_2_and_verify():
    k = kernel()
    ask(k)
    assert [e.hash_version for e in k.audit_ledger.entries] == [2] and k.audit_ledger.verify_chain_integrity()


@pytest.mark.parametrize("field,value", [
    ("request_snapshot", {"subject_id": "SOMEONE-ELSE"}),
    ("policy_outputs", [{"gate_name": "boundary_gate", "approved": True, "confidence": 1.0, "violations": []}]),
    ("manifest_version", "999"),
    ("manifest_hash", "0" * 64),
])
def test_rewriting_what_the_hash_used_to_leave_out_now_breaks_the_chain(field, value):
    k = kernel()
    ask(k)
    entry = k.audit_ledger.entries[0]
    setattr(entry, field, value)
    assert not k.audit_ledger.verify_chain_integrity()


def test_the_patient_id_in_the_snapshot_is_covered():
    k = kernel()
    ask(k, patient="PATIENT-A")
    k.audit_ledger.entries[0].request_snapshot["subject_id"] = "PATIENT-B"
    assert not k.audit_ledger.verify_chain_integrity()


def test_a_persisted_ledger_reloads_and_a_rewritten_file_is_refused(tmp_path):
    path = tmp_path / "perceive.jsonl"
    k = kernel(path)
    for i in range(3):
        ask(k, i)
    assert kernel(path).audit_ledger.verify_chain_integrity()                   # reloads, chain re-verified on load
    lines = path.read_text().splitlines()
    raw = json.loads(lines[1])
    raw["request_snapshot"]["subject_id"] = "SOMEONE-ELSE"
    lines[1] = json.dumps(raw, sort_keys=True)
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(AuditLedgerIntegrityError):
        kernel(path)


def test_a_ledger_written_before_this_change_still_loads_and_new_entries_chain_after_it(tmp_path):
    """Version-1 entries carry no `hash_version`; they must verify exactly as they did, and a version-2 entry may follow."""
    path = tmp_path / "old.jsonl"
    old = ImmutableAuditLedger(path=None)
    prev = old.chain_head
    for i in range(2):
        e = AuditEntry(audit_id=f"old{i}", timestamp=T0 + timedelta(minutes=i), request_snapshot={"subject_id": "P"},
                       evaluated_gates=["boundary_gate"], policy_outputs=[], final_verdict={"approved": True},
                       manifest_version="1", manifest_hash="h", previous_hash=prev, hash_version=1)
        e.immutable_hash = ImmutableAuditLedger._hash(e)
        prev = e.immutable_hash
        raw = dataclasses.asdict(e)
        raw.pop("hash_version")                                                 # exactly the old file shape
        raw["timestamp"] = e.timestamp.isoformat()
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(raw, sort_keys=True, default=str) + "\n")
    k = kernel(path)
    assert [e.hash_version for e in k.audit_ledger.entries] == [1, 1] and k.audit_ledger.verify_chain_integrity()
    ask(k)
    assert [e.hash_version for e in k.audit_ledger.entries] == [1, 1, 2] and k.audit_ledger.verify_chain_integrity()
    assert [e.hash_version for e in kernel(path).audit_ledger.entries] == [1, 1, 2]    # and the mixed file reloads


def test_a_version_1_entry_cannot_be_used_to_launder_a_rewrite():
    """Setting an entry back to version 1 makes its hash a version-1 hash, which no longer matches the stored hash."""
    k = kernel()
    ask(k)
    k.audit_ledger.entries[0].hash_version = 1
    assert not k.audit_ledger.verify_chain_integrity()
