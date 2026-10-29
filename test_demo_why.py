"""
The demo is a test: it must run the real chain, verify a clean record, and
catch every corruption it offers. If the enforcement boundary regresses, the
demo's exit status is the first thing to go red.
"""
import pytest

import demo_why
from conftest import importorskip_cns

# The demo's kernel holds the real root guard, so it needs CNS with DecisionGuard. Without it the demo stops
# at the start (test_examples_need_cns.py); CI's cns job sets OBSERVE_PERCEIVE_REQUIRE_CNS=1, so there it runs.
importorskip_cns("cns_composition.decision_guard", "the demo's root guard needs the CNS release with DecisionGuard")
importorskip_cns("conservation_kernel.cns_connector", "the demo's root guard needs the kernel's CNS connector")


@pytest.mark.parametrize("corruption", sorted(demo_why.CORRUPTIONS))
def test_demo_detects_every_offered_corruption(corruption):
    assert demo_why.main(["--corrupt", corruption, "--quiet"]) == 0


def test_demo_clean_record_verifies_completely():
    result, orchestrator, _ = demo_why.run_scenario(quiet=True)
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["audit_chain_valid"], result["chain_verification"]["failed"]
    assert result["chain_verification"]["complete"]
