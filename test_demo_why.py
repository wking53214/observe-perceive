"""
The demo is a test: it must run the real chain, verify a clean record, and
catch every corruption it offers. If the enforcement boundary regresses, the
demo's exit status is the first thing to go red.
"""
import pytest

import demo_why


@pytest.mark.parametrize("corruption", sorted(demo_why.CORRUPTIONS))
def test_demo_detects_every_offered_corruption(corruption):
    assert demo_why.main(["--corrupt", corruption, "--quiet"]) == 0


def test_demo_clean_record_verifies_completely():
    result, orchestrator, _ = demo_why.run_scenario(quiet=True)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]
    assert result["chain_verification"]["complete"]
