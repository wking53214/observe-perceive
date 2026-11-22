"""PERCEIVE checks the shape of a request before its words (ported from the standalone PERCEIVE event-shape gate).

Each malformed request here was APPROVED before the check, or refused only because some gate crashed on it. Now each is
refused by boundary_gate, which every request type runs, with a reason that says what is wrong.
"""
from datetime import datetime, timedelta, timezone

import pytest

from perceive_consolidated import PerceiveGovernanceKernel, PolicyGates, PolicyManifest, PolicyRequest

NOW = datetime(2026, 11, 22, 9, 0, tzinfo=timezone.utc)
BASE = dict(request_id="r1", request_type="escalate_patient", subject_id="P001", actor_id="sentinel",
            context={"patient_id": "P001"}, timestamp=NOW)


def _kernel():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=NOW, policies={}))
    return kernel


def _request(**override):
    return PolicyRequest(**{**BASE, **override})


def test_a_well_formed_request_is_still_approved():
    assert PolicyGates.request_shape_problems(_request()) == []
    assert _kernel().evaluate_request(_request()).approved


@pytest.mark.parametrize("override,reason", [
    ({"subject_id": None}, "Missing subject_id"),                                   # approved before
    ({"subject_id": ""}, "Missing subject_id"),                                     # approved before
    ({"subject_id": "  "}, "Missing subject_id"),
    ({"subject_id": 1}, "subject_id must be text"),                                 # approved before
    ({"actor_id": 7}, "actor_id must be text"),                                     # refused only by a crash before
    ({"context": ["patient_id", "P001"]}, "context must be a mapping"),             # refused only by a crash before
    ({"context": None}, "context must be a mapping"),                               # refused only by a crash before
    ({"timestamp": datetime(2026, 11, 22, 9, 0)}, "no time zone"),                  # approved before
    ({"timestamp": "2026-11-22T09:00:00+00:00"}, "timestamp must be a datetime"),   # approved before
    ({"context": {"patient_id": "P002"}}, "different patient"),                     # approved before
    ({"artifact_hash": "abc"}, "without the artifact_id"),                          # approved before
])
def test_a_malformed_request_is_refused_and_says_why(override, reason):
    request = _request(**override)
    assert any(reason in p for p in PolicyGates.request_shape_problems(request))
    gate = PolicyGates.boundary_gate(request)
    assert not gate.approved and any(reason in d for d in gate.violation_details)
    verdict = _kernel().evaluate_request(request)
    assert not verdict.approved and any("boundary_gate" in v and reason in v for v in verdict.violations)


@pytest.mark.parametrize("request_type", sorted(PolicyGates.VALID_REQUEST_TYPES))
def test_every_request_type_runs_the_shape_check(request_type):
    verdict = _kernel().evaluate_request(_request(request_type=request_type, subject_id=""))
    assert not verdict.approved and "boundary_gate" in verdict.applied_gates


def test_no_timestamp_is_still_allowed_and_falls_back_as_documented():
    assert PolicyGates.request_shape_problems(_request(timestamp=None)) == []


def test_a_far_future_time_is_not_judged_here():
    """Recorded limit: evaluation is a pure function of the request (WS2a), so this check does not compare the time
    with the wall clock. Event-time attestation (SentinelPerceiveAdapter) is where a time's origin is checked."""
    assert PolicyGates.request_shape_problems(_request(timestamp=NOW + timedelta(days=3650))) == []


def test_the_cns_wrapper_of_boundary_gate_refuses_a_malformed_request():
    from conftest import importorskip_cns
    importorskip_cns("cns.gate")
    from cns.gate import GateOutcome
    from cns_connector import PerceiveCnsGate
    result = PerceiveCnsGate("boundary_gate", "alpha").check(_request(subject_id=None))
    assert result.outcome is GateOutcome.TERMINAL_BREACH and "Missing subject_id" in result.reason


# ---- what PERCEIVE hands back cannot change what it recorded (Step 13, read-only snapshots) ----------------------

def _kernel_with_history():
    kernel = PerceiveGovernanceKernel()
    manifest = PolicyManifest(manifest_id="m", version="1", created_at=NOW, policies={"escalation": {"max_daily": 1}})
    kernel.register_manifest(manifest)
    kernel.evaluate_request(_request())
    return kernel, manifest


def test_a_replayed_event_cannot_rewrite_the_history():
    kernel, _ = _kernel_with_history()
    events = kernel.event_store.replay()
    events[0].request_snapshot["subject_id"] = "CHANGED"
    events.clear()
    assert kernel.event_store.replay()[0].request_snapshot.get("subject_id") == "P001"


def test_a_returned_manifest_cannot_change_the_registered_one():
    kernel, _ = _kernel_with_history()
    kernel.manifest_registry.get_current_manifest().policies["escalation"]["max_daily"] = 999
    assert kernel.manifest_registry.get_current_manifest().policies["escalation"]["max_daily"] == 1


def test_the_callers_own_manifest_cannot_change_the_registered_one_after_registering():
    kernel, manifest = _kernel_with_history()
    assert manifest.manifest_hash                                # still set on the caller's object, as before
    manifest.policies["escalation"]["max_daily"] = 999
    registered = kernel.manifest_registry.get_current_manifest()
    assert registered.policies["escalation"]["max_daily"] == 1
    assert registered.manifest_hash == kernel.manifest_registry._compute_hash(registered)
