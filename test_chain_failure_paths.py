"""
Failure paths and contracts (Phases 12 and 13 of the repair mission).

Every failure must produce an explicit, inspectable state. No silent
fallback, no lost record. And every result, on every path, must carry the
handoff record and the request it decided.
"""
import pytest

from conftest import ChainArtifact, run_chain
from conservation_kernel.errors import LedgerError
from governance_contracts import CONTRACT_VERSION
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine


# --- missing / malformed source ---------------------------------------------------

def test_missing_source_is_an_explicit_invalid_request(orchestrator):
    result = orchestrator.orchestrate_request(None, "escalate", lambda ctx: None)
    assert result["status"] == "INVALID_REQUEST" and "no artifact" in result["reason"]
    assert result["handoff"]["contract_version"] == CONTRACT_VERSION


def test_malformed_source_names_what_is_missing(orchestrator):
    class Half:
        artifact_id = "half"
    result = orchestrator.orchestrate_request(Half(), "escalate", lambda ctx: None)
    assert result["status"] == "INVALID_REQUEST"
    assert "content" in result["reason"] and "metadata" in result["reason"]


# --- conflicting evidence / corrupted artifact ------------------------------------------

def test_content_altered_after_hashing_is_refused_before_execution(orchestrator, monkeypatch):
    adapter = orchestrator.sentinel_adapter
    original = adapter.sentinel_artifact_to_governance_request

    def tampering(artifact, operation_type, context=None, **kwargs):
        request = original(artifact, operation_type, context, **kwargs)
        request.artifact_content = request.artifact_content + " [altered in flight]"
        return request
    monkeypatch.setattr(adapter, "sentinel_artifact_to_governance_request", tampering)
    result, ran = run_chain(orchestrator, "tamper-1")
    assert result["status"] == "REJECTED" and not ran
    assert result["refused_by"] == "conservation" and "hash mismatch" in result["reason"]


# --- unknown epistemic state is preserved, not collapsed ---------------------------------

@pytest.mark.parametrize("status", ["UNKNOWN", "INFERRED", "EXPLICIT", "CONFLICTED"])
def test_epistemic_status_survives_into_the_record_unchanged(orchestrator, status):
    result, _ = run_chain(orchestrator, f"epi-{status}", artifact=ChainArtifact(f"epi-{status}", epistemic=status))
    assert result["governance_request"].epistemic_status == status
    assert result["handoff"]["epistemic_status"] == status


# --- denied permission -------------------------------------------------------------------

def test_denied_permission_is_recorded_as_perceive_refusal(kernel):
    # export_data with no consent and no encryption. PERCEIVE's gates default
    # to ADVISORY (the refusal is recorded on the verdict but does not flip
    # it), so the denial path needs the export gate enforced.
    from datetime import datetime, timezone
    from perceive_consolidated import PerceiveGovernanceKernel, PolicyEnforcementConfig, PolicyManifest
    enforcing = PerceiveGovernanceKernel(enforcement=PolicyEnforcementConfig(enforce_export_controls=True))
    enforcing.register_manifest(PolicyManifest(manifest_id="m", version="1.0.0", created_at=datetime.now(timezone.utc), policies={}))
    orchestrator = GovernanceOrchestrator(enforcing, kernel, ObserveClinicalEngine())
    result, ran = run_chain(
        orchestrator, "export-1", operation="export",
        context={"export_type": "full_record", "has_consent": False, "will_encrypt": False},
    )
    assert result["status"] == "REJECTED" and not ran
    assert result["refused_by"] == "perceive" and result["stage_refused"] is True


def test_advisory_denial_is_visible_on_an_approved_record(orchestrator):
    """With the default advisory gates the same request is APPROVED, and the
    record must say the gate objected: an approval despite violations is a
    different thing from a clean one."""
    result, ran = run_chain(
        orchestrator, "export-adv", operation="export",
        context={"export_type": "full_record", "has_consent": False, "will_encrypt": False},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED" and ran
    assert result["advisory_violations"], "the export gate's objection was dropped from the record"


# --- prediction failure ------------------------------------------------------------------

def test_a_crashing_screen_refuses_and_says_it_crashed(perceive, kernel, monkeypatch):
    if pytest.importorskip("augur_screen_adapter").Augur is None:
        pytest.skip("AUGUR checkout not available")
    orchestrator = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), simulation_screen=True)

    def crash(context):
        raise RuntimeError("simulator exploded")
    monkeypatch.setattr(orchestrator.simulation_screen, "screen_request", crash)
    result, ran = run_chain(orchestrator, "screen-crash")
    assert result["status"] == "REJECTED" and not ran
    assert result["stage_refused"] is False and "RuntimeError" in result["stage_error"]


# --- execution failure / partial execution -------------------------------------------------

def test_execution_failure_is_a_recorded_state_that_keeps_the_approval(orchestrator):
    def boom(ctx):
        raise IOError("downstream unavailable")
    result, _ = run_chain(orchestrator, "exec-fail", func=boom)
    assert result["status"] == "EXECUTION_FAILED"
    assert result["execution_status"] == "failed" and "IOError" in result["execution_error"] or "OSError" in result["execution_error"]
    assert result["execution_approval"] is not None and result["conservation_decision"] is not None
    assert result["executed_at"]["started"] and result["executed_at"]["finished"]
    assert result["audit_chain_valid"] is False
    # The links that exist still verify: the failure did not corrupt the record.
    assert result["chain_verification"]["valid"], result["chain_verification"]["failed"]
    assert result["chain_verification"]["complete"] is False


def test_execution_failure_can_raise_on_request(perceive, kernel):
    strict = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), raise_on_execution_error=True)
    with pytest.raises(IOError):
        run_chain(strict, "exec-raise", func=lambda ctx: (_ for _ in ()).throw(IOError("x")))


def test_partial_execution_is_recorded_as_partial(orchestrator):
    result, _ = run_chain(orchestrator, "exec-partial", func=lambda ctx: {"status": "partial", "done": 3, "of": 5})
    assert result["status"] == "APPROVED_AND_EXECUTED" and result["execution_status"] == "partial"
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]


# --- observation failure ---------------------------------------------------------------------

def test_observe_failure_after_execution_keeps_the_execution_record(orchestrator, monkeypatch):
    def broken(vitals):
        raise ValueError("sensor feed corrupt")
    monkeypatch.setattr(orchestrator.observe_engine, "evaluate", broken)
    result, ran = run_chain(orchestrator, "observe-fail")
    assert ran and result["status"] == "APPROVED_AND_EXECUTED"
    assert result["observe_enforced"] is False and "ValueError" in result["observe_error"]
    assert result["execution_context"] is not None and result["gsa815_result"] is not None
    assert result["audit_chain_valid"] is False and result["chain_verification"]["valid"]


def test_no_observe_engine_with_vitals_is_recorded_not_raised(perceive, kernel):
    orchestrator = GovernanceOrchestrator(perceive, kernel, None)
    result, ran = run_chain(orchestrator, "no-engine")
    assert ran and result["status"] == "APPROVED_AND_EXECUTED"
    assert "no OBSERVE engine" in result["observe_error"]


# --- storage / retrieval failure -----------------------------------------------------------------

def test_storage_failure_at_the_kernel_is_a_recorded_refusal(orchestrator, monkeypatch):
    monkeypatch.setattr(orchestrator.conservation_kernel, "register_root", lambda a: (_ for _ in ()).throw(LedgerError("disk full")))
    result, ran = run_chain(orchestrator, "store-fail")
    assert result["status"] == "REJECTED" and not ran and result["refused_by"] == "conservation"


def test_retrieval_failure_makes_verification_fail_loudly(orchestrator, monkeypatch):
    result, _ = run_chain(orchestrator, "retrieve-fail")
    assert result["audit_chain_valid"]
    from governance_chain import verify_result
    monkeypatch.setattr(orchestrator.conservation_kernel, "reconstruct", lambda i: (_ for _ in ()).throw(LedgerError("ledger offline")))
    later = verify_result(result, kernel=orchestrator.conservation_kernel, perceive=orchestrator.perceive).as_dict()
    assert "conservation.in_kernel_ledger" in later["failed"]


# --- duplicate / replayed event ----------------------------------------------------------------------

def test_duplicate_event_is_refused_and_the_first_record_stands(orchestrator):
    first, _ = run_chain(orchestrator, "dup-1")
    second, ran = run_chain(orchestrator, "dup-1")
    assert first["status"] == "APPROVED_AND_EXECUTED"
    assert second["status"] == "REJECTED" and not ran and second["refused_by"] == "conservation"


# --- contract: every path carries the handoff and the request ----------------------------------

def _every_path(orchestrator, perceive, kernel):
    yield orchestrator.orchestrate_request(None, "escalate", lambda c: None)                     # INVALID_REQUEST
    yield run_chain(orchestrator, "path-ok")[0]                                                  # APPROVED_AND_EXECUTED
    yield run_chain(orchestrator, "path-ok")[0]                                                  # REJECTED (replay)
    yield run_chain(orchestrator, "path-ro", context={"gateway_scope": "READ_ONLY"})[0]          # REJECTED (scope)
    yield run_chain(orchestrator, "path-fail", func=lambda c: (_ for _ in ()).throw(RuntimeError("x")))[0]  # EXECUTION_FAILED
    strict = GovernanceOrchestrator(perceive, kernel, ObserveClinicalEngine(), require_vitals=True)
    yield run_chain(strict, "path-novitals", vitals=False)[0]                                    # REJECTED (vitals)


def test_every_result_path_carries_handoff_and_request(orchestrator, perceive, kernel):
    seen = set()
    for result in _every_path(orchestrator, perceive, kernel):
        seen.add(result["status"])
        assert "handoff" in result and "governance_request" in result, result["status"]
        h = result["handoff"]
        assert h["producer"] == "observe-perceive.GovernanceOrchestrator"
        assert h["contract_version"] == CONTRACT_VERSION and h["issued_at"]
        assert set(h["strict"]) == {"require_declared_scope", "require_vitals", "raise_on_stage_error", "raise_on_execution_error",
                                    "require_attested_event_time", "enforce_advisory_violations", "guard_executor"}
        assert h["profile"] == "advisory"
        if result["governance_request"] is not None:
            assert h["authority"] == result["governance_request"].authority
            assert h["request_state_commitment"] == result["governance_request"].state_commitment
        assert {"status", "governance_decision", "audit_chain"} <= set(result)
    assert seen == {"INVALID_REQUEST", "APPROVED_AND_EXECUTED", "REJECTED", "EXECUTION_FAILED"}


# --- provenance: identity and lineage survive the whole path ----------------------------------------

def test_provenance_survives_from_artifact_to_outcome(orchestrator):
    artifact = ChainArtifact("prov-1", origin="TIE", authority="CLINICIAN", epistemic="EXPLICIT", parents=["src-a", "src-b"])
    result, _ = run_chain(orchestrator, "prov-1", artifact=artifact)
    request = result["governance_request"]
    assert (request.origin, request.authority, request.epistemic_status) == ("TIE", "CLINICIAN", "EXPLICIT")
    assert request.lineage == ["src-a", "src-b"]
    assert result["execution_context"].lineage == ["src-a", "src-b"]
    assert result["outcome_context"].lineage == ["src-a", "src-b", "prov-1"]
    assert result["outcome_context"].governance_decision_id == result["governance_decision"].decision_id
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]
