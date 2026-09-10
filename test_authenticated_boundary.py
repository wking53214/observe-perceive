"""test_authenticated_boundary.py -- the seven residual risks of the closure
report, closed or narrowed in 1.3.0.

R1/R4 signed ledgers, receipts and snapshots; R2 the orchestrator guards the
executor itself; R6 a shared ledger file across processes; R7 the receipt
probe before execution; R3 PERCEIVE's ledger persisted; R5 the strict
profile; R8 attested event time.
"""
from __future__ import annotations

import datetime as dt
import json

import pytest

from conftest import ChainArtifact, run_chain
from conservation_kernel import ConservationKernel, HmacSigner
from execution_guard import ExecutionLedger, ExecutionRefusal, LedgerAuthenticityError, LedgerIntegrityError
from governance_chain import verify_result
from governance_orchestrator import GovernanceOrchestrator
from governance_record import ReceiptLog, RecordAuthenticityError
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import AuditLedgerIntegrityError, PerceiveGovernanceKernel, PolicyManifest
from sentinel_perceive_adapter import SentinelPerceiveAdapter

KEY = HmacSigner(b"deployment-key-0123456789abcdef", key_id="deploy-1")
OTHER = HmacSigner(b"someone-elses-key-0123456789ab", key_id="deploy-1")
SOURCE = HmacSigner(b"feed-icu-3-source-key-0123456789", key_id="feed-icu-3")


def _orch(perceive, kernel, **kw):
    return GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), **kw)


# ---------------------------------------------------------------- R1: signed ledger and receipts

def test_a_signed_ledger_records_the_key_and_reopens_with_it(perceive, kernel, tmp_path):
    ledger = ExecutionLedger(tmp_path / "e.jsonl", signer=KEY)
    result, ran = run_chain(_orch(perceive, kernel, execution_ledger=ledger), "s1", vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    lines = [json.loads(x) for x in (tmp_path / "e.jsonl").read_text().splitlines()]
    assert [e["signature"]["key_id"] for e in lines] == ["deploy-1", "deploy-1"]
    assert result["handoff"]["execution_ledger_signed"] is True
    assert ExecutionLedger(tmp_path / "e.jsonl", signer=KEY).consumed("exec-s1")


def test_a_consistent_forgery_in_the_ledger_is_refused_with_the_key(perceive, kernel, tmp_path):
    """The attack integrity alone allowed: rewrite an entry and recompute the
    chain with the public hash function. The entry hashes recompute; the
    signature does not."""
    from execution_guard import _entry_hash
    path = tmp_path / "e.jsonl"
    run_chain(_orch(perceive, kernel, execution_ledger=ExecutionLedger(path, signer=KEY)), "s2", vitals=False)
    lines = [json.loads(x) for x in path.read_text().splitlines()]
    forged, consumed = lines
    signature = forged.pop("signature")
    forged.pop("hash")
    forged["producer"] = "someone-else"
    forged["hash"] = _entry_hash("", {k: v for k, v in forged.items() if k != "previous"})
    forged["signature"] = signature
    consumed_sig = consumed.pop("signature")
    consumed.pop("hash")
    consumed["previous"] = forged["hash"]
    consumed["hash"] = _entry_hash(forged["hash"], {k: v for k, v in consumed.items() if k != "previous"})
    consumed["signature"] = consumed_sig
    path.write_text("\n".join(json.dumps(e, sort_keys=True) for e in (forged, consumed)) + "\n")
    assert ExecutionLedger(path).verify_chain()                      # integrity alone: accepted
    with pytest.raises(LedgerAuthenticityError, match="does not verify"):
        ExecutionLedger(path, signer=KEY)                            # with the key: refused


def test_an_unsigned_ledger_and_a_wrong_key_are_refused(perceive, kernel, tmp_path):
    run_chain(_orch(perceive, kernel, execution_ledger=ExecutionLedger(tmp_path / "u.jsonl")), "s3", vitals=False)
    with pytest.raises(LedgerAuthenticityError, match="no signature"):
        ExecutionLedger(tmp_path / "u.jsonl", signer=KEY)
    run_chain(_orch(perceive, kernel, execution_ledger=ExecutionLedger(tmp_path / "w.jsonl", signer=OTHER)), "s3b", vitals=False)
    with pytest.raises(LedgerAuthenticityError, match="does not verify"):
        ExecutionLedger(tmp_path / "w.jsonl", signer=KEY)


def test_signed_receipts_refuse_a_forged_receipt_and_accept_their_own(perceive, kernel, tmp_path):
    path = tmp_path / "r.jsonl"
    result, _ = run_chain(_orch(perceive, kernel, receipts=ReceiptLog(path, signer=KEY)), "s4", vitals=False)
    assert result["receipt"]["signature"]["key_id"] == "deploy-1" and result["handoff"]["receipts_signed"] is True
    assert len(ReceiptLog(path, signer=KEY).entries) == 1
    entry = json.loads(path.read_text())
    entry["receipt"]["signature"]["value"] = "00" * 32
    path.write_text(json.dumps(entry, sort_keys=True) + "\n")
    with pytest.raises(RecordAuthenticityError):
        ReceiptLog(path, signer=KEY)
    ReceiptLog(path)                                                 # without a key, integrity still holds


# ---------------------------------------------------------------- R6: one ledger file, two processes

def test_two_ledger_objects_on_one_file_see_one_ledger(perceive, tmp_path):
    path = tmp_path / "shared.jsonl"
    a = ExecutionLedger(path)
    b = ExecutionLedger(path)                                        # "another process"
    captured = {}
    def capture(ctx):
        captured["ctx"] = ctx
        return {"status": "done"}
    result, _ = run_chain(_orch(perceive, ConservationKernel(), execution_ledger=a, guard_executor=False), "p1", func=capture, vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert b.issued("exec-p1") is not None and b.consumed("exec-p1") is not None      # b re-read the file
    with pytest.raises(ExecutionRefusal, match="already consumed"):
        b.consume(captured["ctx"], consumer="process-b")
    # The same artifact governed by the second process: its own kernel has
    # never seen it, so only the shared ledger can say it is a replay.
    second, ran = run_chain(_orch(perceive, ConservationKernel(), execution_ledger=b), "p1", vitals=False,
                            context={"execution_id": "exec-p1-b"})
    assert second["status"] == "REJECTED" and second["refused_by"] == "execution_ledger" and ran == []
    assert "already issued an execution" in second["reason"]
    assert a.issued_for_artifact("p1") == "exec-p1"


def test_a_file_edited_between_two_refreshes_is_refused(perceive, kernel, tmp_path):
    path = tmp_path / "shared.jsonl"
    a = ExecutionLedger(path)
    run_chain(_orch(perceive, kernel, execution_ledger=a), "p2", vitals=False)
    b = ExecutionLedger(path)
    run_chain(_orch(perceive, ConservationKernel(), execution_ledger=b, one_per_artifact=False) if False else
              _orch(perceive, ConservationKernel(), execution_ledger=ExecutionLedger(path, one_per_artifact=False)), "p3", vitals=False)
    lines = path.read_text().splitlines()
    lines[-1] = lines[-1].replace('"consumer": "orchestrator"', '"consumer": "intruder"')
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(LedgerIntegrityError):
        a.entries                                                    # a refreshes across the edit and refuses it


def test_per_artifact_rule_can_be_turned_off(perceive, tmp_path):
    path = tmp_path / "loose.jsonl"
    run_chain(_orch(perceive, ConservationKernel(), execution_ledger=ExecutionLedger(path, one_per_artifact=False)), "p4", vitals=False)
    again, ran = run_chain(_orch(perceive, ConservationKernel(), execution_ledger=ExecutionLedger(path, one_per_artifact=False)), "p4",
                           vitals=False, context={"execution_id": "exec-p4-b"})
    assert again["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1


# ---------------------------------------------------------------- R7: receipt probe

def test_an_unreachable_receipt_store_refuses_before_anything_is_issued(perceive, kernel, tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x")
    log = ReceiptLog.__new__(ReceiptLog)
    log.path = blocker / "receipts.jsonl"
    log.signer = None
    log._entries = []
    ledger = ExecutionLedger()
    result, ran = run_chain(_orch(perceive, kernel, receipts=log, execution_ledger=ledger), "r1", vitals=False)
    assert result["status"] == "REJECTED" and result["refused_by"] == "receipt_store" and ran == []
    assert ledger.issued("exec-r1") is None                          # refused before issuance
    assert result["receipt"] is None and "receipt_error" in result   # and the refusal itself could not be receipted, which the result says


def test_a_reachable_store_passes_the_probe_and_receipts_the_result(perceive, kernel, tmp_path):
    result, ran = run_chain(_orch(perceive, kernel, receipts=ReceiptLog(tmp_path / "ok.jsonl")), "r2", vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1 and result["receipt"]["sequence"] == 0


# ---------------------------------------------------------------- R3: PERCEIVE ledger persisted

def _perceive_at(path):
    p = PerceiveGovernanceKernel(ledger_path=path)
    p.register_manifest(PolicyManifest(manifest_id="chain-manifest", version="1.0.0",
                                       created_at=dt.datetime.now(dt.timezone.utc), policies={}))
    return p


def test_a_decision_is_found_in_perceives_ledger_after_a_restart(kernel, tmp_path):
    path = tmp_path / "perceive.jsonl"
    result, _ = run_chain(_orch(_perceive_at(path), kernel), "pl1", vitals=True)
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]
    restarted = _perceive_at(path)                                   # a new process reopening the file
    assert len(restarted.audit_ledger.entries) == 1 and restarted.verify_audit_integrity()
    v = verify_result(result, kernel=kernel, perceive=restarted)
    names = {c["name"]: c for c in v.as_dict()["checks"]}
    assert names["decision.in_perceive_ledger"]["ok"] and names["decision.matches_perceive_ledger"]["ok"]
    assert names["perceive_ledger.integrity"]["ok"] and v.valid


def test_a_tampered_perceive_ledger_refuses_to_open(kernel, tmp_path):
    path = tmp_path / "perceive.jsonl"
    run_chain(_orch(_perceive_at(path), kernel), "pl2", vitals=False)
    path.write_text(path.read_text().replace('"approved": true', '"approved": false'))
    with pytest.raises(AuditLedgerIntegrityError):
        _perceive_at(path)


# ---------------------------------------------------------------- R8: attested event time

class _Attested(ChainArtifact):
    def __init__(self, artifact_id, when, signer=SOURCE, attest_id=None):
        super().__init__(artifact_id)
        self.metadata.occurred_at = when
        self.metadata.event_time_attestation = SentinelPerceiveAdapter.attest_event_time(
            signer, attest_id or artifact_id, when.isoformat())


WHEN = dt.datetime(2026, 9, 8, 12, 0, tzinfo=dt.timezone.utc)


def test_an_attested_event_time_is_recorded_committed_and_named(perceive, kernel):
    result, ran = run_chain(_orch(perceive, kernel, source_signers=[SOURCE]), "a1", artifact=_Attested("a1", WHEN), vitals=False)
    req = result["governance_request"]
    assert result["status"] == "APPROVED_AND_EXECUTED" and req.event_time_attested is True and req.event_time_key_id == "feed-icu-3"
    assert result["temporal_anomalies"] == []
    # Committed: flipping the finding breaks the request commitment.
    req.event_time_attested = False
    assert "request.state_commitment" in verify_result(result, kernel=kernel, perceive=perceive).as_dict()["failed"]


@pytest.mark.parametrize("case, artifact, signers, problem", [
    ("wrong key", lambda: _Attested("a2", WHEN, signer=HmacSigner(b"wrong-key-0123456789abcdefgh", key_id="feed-icu-3")), [SOURCE], "does not verify"),
    ("moved to another artifact", lambda: _Attested("a3", WHEN, attest_id="a3-other"), [SOURCE], "does not verify"),
    ("unregistered key", lambda: _Attested("a4", WHEN), [], "unregistered key"),
])
def test_a_bad_attestation_is_an_anomaly_in_the_advisory_profile(perceive, kernel, case, artifact, signers, problem):
    result, ran = run_chain(_orch(perceive, kernel, source_signers=signers), "x", artifact=artifact(), vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1
    assert result["governance_request"].event_time_attested is False
    assert any(problem in a for a in result["temporal_anomalies"]), (case, result["temporal_anomalies"])


def test_a_claimed_event_time_is_not_attested_and_not_an_anomaly_by_itself(orchestrator):
    from test_failure_semantics import _Timed
    result, _ = run_chain(orchestrator, "a5", artifact=_Timed("a5", WHEN), vitals=False)
    assert result["governance_request"].event_time_attested is False and result["temporal_anomalies"] == []


# ---------------------------------------------------------------- R5: strict profile

def test_strict_refuses_what_advisory_records(perceive, kernel, tmp_path):
    strict = _orch(perceive, kernel, profile="strict", source_signers=[SOURCE])
    assert strict.require_declared_scope and strict.require_vitals and strict.require_attested_event_time and strict.enforce_advisory_violations
    unattested, ran = run_chain(strict, "st1", vitals=True, context={"gateway_scope": "EXECUTE"})
    assert unattested["status"] == "REJECTED" and unattested["refused_by"] == "source_attestation" and ran == []
    assert unattested["handoff"]["profile"] == "strict"
    bare_scope, ran = run_chain(strict, "st3", artifact=_Attested("st3", WHEN), vitals=True, context={"gateway_scope": "EXECUTE"})
    assert bare_scope["status"] == "REJECTED" and "sealed" in bare_scope["reason"] and ran == []


def test_strict_executes_when_everything_is_in_place(perceive, kernel):
    from gateway_admission_adapter import GatewayAdmissionAdapter
    gw = pytest.importorskip("governance_gateway.models")
    strict = _orch(perceive, kernel, profile="strict", source_signers=[SOURCE])
    sealed = GatewayAdmissionAdapter.seal(artifact_id="st4", payload={"content": "escalate P001"}, provenance={"source": "SENTINEL"},
                                          epistemic_status=gw.EpistemicStatus.INFERENCE, authority_actor="CHARGE_NURSE",
                                          authority_grant="unit-escalation", execute=True)
    sealed_a = GatewayAdmissionAdapter.seal(artifact_id="st4a", payload={"content": "escalate P001"}, provenance={"source": "SENTINEL"},
                                            epistemic_status=gw.EpistemicStatus.INFERENCE, authority_actor="CHARGE_NURSE",
                                            authority_grant="unit-escalation", execute=True)
    # A different patient: PERCEIVE's escalation cooldown would otherwise
    # advise against a second escalation of P001, and strict enforces it.
    no_vitals, ran = run_chain(strict, "st4a", artifact=_Attested("st4a", WHEN), vitals=False,
                               context={"gateway_scope": "EXECUTE", "gateway_admission": sealed_a, "patient_id": "P002"})
    assert no_vitals["status"] == "REJECTED" and "vitals" in no_vitals["reason"] and ran == []
    result, ran = run_chain(strict, "st4", artifact=_Attested("st4", WHEN), vitals=True,
                            context={"gateway_scope": "EXECUTE", "gateway_admission": sealed})
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1, (result["status"], result.get("refused_by"), result.get("reason"), result.get("temporal_anomalies"))
    assert result["scope_sealed"] is True and result["governance_request"].event_time_attested is True
    assert result["execution_authorization"]["guard"] is not None


def test_strict_enforces_violations_perceive_only_advised(perceive, kernel, monkeypatch):
    strict = _orch(perceive, kernel, profile="strict", source_signers=[SOURCE])
    original = strict.sentinel_adapter.evaluate_through_perceive
    def advising(p, request):
        decision = original(p, request)
        decision.advisory_violations = ["escalation_rate_policy: 4 escalations this hour (limit 3)"]
        return decision
    monkeypatch.setattr(strict.sentinel_adapter, "evaluate_through_perceive", advising)
    result, ran = run_chain(strict, "st5", artifact=_Attested("st5", WHEN), vitals=True, context={"gateway_scope": "EXECUTE"})
    assert result["status"] == "REJECTED" and result["refused_by"] == "perceive" and ran == []
    assert "escalation_rate_policy" in result["reason"] and result["advisory_violations"]
    advisory = _orch(perceive, kernel)
    monkeypatch.setattr(advisory.sentinel_adapter, "evaluate_through_perceive", advising)
    result, ran = run_chain(advisory, "st6", vitals=False)
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1      # the advisory profile records and proceeds


def test_an_unknown_profile_is_refused_at_construction(perceive, kernel):
    with pytest.raises(ValueError):
        _orch(perceive, kernel, profile="paranoid")
