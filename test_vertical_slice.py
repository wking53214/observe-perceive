"""test_vertical_slice.py -- the product-level proof, as a test.

One governed action through the real code, explained backwards from its
effect after a restart, then replayed three ways and refused three ways.
"""
from __future__ import annotations

import json

import pytest

from vertical_slice import Paths, explain, run_action, run_slice

# The slice is real implementations only: it seals at the Gateway, so it
# needs the Gateway. Importing vertical_slice first lets the adapter resolve
# a sibling checkout before this check runs.
pytest.importorskip("governance_gateway.models", reason="Governance_Gateway checkout not available")


def test_one_governed_action_is_explained_after_a_restart_and_cannot_be_replayed(tmp_path):
    out = run_slice(tmp_path / "slice")
    assert out["status"] == "APPROVED_AND_EXECUTED"
    assert out["explained"], out["steps"]
    assert [s["step"] for s in out["steps"]] == [
        "ACTION", "EXECUTION", "AUTHORIZATION", "GOVERNANCE DECISION", "INTERPRETATION",
        "EVIDENCE", "SOURCE", "RECORD", "OBSERVATION",
    ]
    attacks = out["attacks"]
    assert attacks["effects_written_by_attacks"] == 0
    assert attacks["replay_issued_context"].startswith("refused")
    assert attacks["forged_context"].startswith("refused")
    assert attacks["replay_whole_request"].startswith("REJECTED")
    assert attacks["attack_receipted"] is True


def test_the_effect_carries_enough_to_find_its_explanation(tmp_path):
    action = run_action(tmp_path / "slice")
    paths = action["paths"]
    effect = json.loads(paths.effects.read_text().splitlines()[0])
    assert set(effect) >= {"execution_id", "artifact_id", "action", "at"}
    explained = explain(tmp_path / "slice", effect)
    assert explained["explained"]
    assert explained["verification"]["valid"] and explained["verification"]["complete"]


def test_a_tampered_effect_finds_no_explanation(tmp_path):
    run_action(tmp_path / "slice")
    explained = explain(tmp_path / "slice", {"execution_id": "exec-9999", "action": "page on-call"})
    assert explained["explained"] is False and explained["steps"][0]["ok"] is False


def test_the_files_left_behind_are_the_whole_story(tmp_path):
    action = run_action(tmp_path / "slice")
    paths = action["paths"]
    for p in (paths.executions, paths.receipts, paths.kernel, paths.effects):
        assert p.exists(), p
    ledger_kinds = [json.loads(line)["kind"] for line in paths.executions.read_text().splitlines()]
    assert ledger_kinds == ["issued", "consumed"]
    receipt = json.loads(paths.receipts.read_text().splitlines()[0])
    assert receipt["record"]["scope_sealed"] is True and receipt["record"]["gateway_admission"]["integrity"] == action["sealed_integrity"]
    assert receipt["record"]["temporal_anomalies"] == []


def test_ccc_records_the_orchestration_when_the_pack_is_present(tmp_path):
    pytest.importorskip("orchestrator_ccc_adapter")
    action = run_action(tmp_path / "slice")
    if action["ccc_recorded"] is None:
        pytest.skip("CCC not resolvable beside this checkout")
    assert Paths(tmp_path / "slice").ccc.exists()
