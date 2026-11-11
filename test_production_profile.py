"""The production profile proves its wiring at construction, or the orchestrator does not start.

Needs CNS (the roster is CNS's). In CI's `cns` job a missing CNS is an error; elsewhere these skip.
"""
from datetime import datetime, timedelta, timezone

import pytest

from conftest import importorskip_cns

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.decision_guard")

import cns  # noqa: E402
from conservation_kernel import ConservationKernel, HmacSigner  # noqa: E402
from execution_guard import ExecutionLedger  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from governance_record import ReceiptLog  # noqa: E402
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier  # noqa: E402
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot  # noqa: E402
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest  # noqa: E402
from production_profile import ProductionProfileError, REQUIRED_CNS, production_problems  # noqa: E402
from root_admission import RootAdmission  # noqa: E402
from sentinel_perceive_adapter import SentinelPerceiveAdapter  # noqa: E402

DEPLOYER = HmacSigner(b"production-profile-deployer-key-0123456789", key_id="deployer")
SOURCE = HmacSigner(b"production-profile-source-key-0123456789ab", key_id="feed")


def perceive():
    k = PerceiveGovernanceKernel()
    k.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=datetime.now(timezone.utc), policies={}))
    return k


RESPONDER = HmacSigner(b"production-profile-responder-key-0123456789", key_id="nurse-1")


def desk(tmp_path, **override):
    parts = dict(policy=NotificationPolicy(tiers=(Tier("charge", lambda payload: None, ("nurse-1",), 120.0),)),
                 log=EscalationLog(tmp_path / "escalations.jsonl", signer=DEPLOYER), responder_keys={"nurse-1": RESPONDER})
    parts.update(override)
    return EscalationDesk(**parts)


def wiring(tmp_path, **override):
    """A correct production wiring; `override` replaces one part."""
    admission = RootAdmission()
    parts = dict(
        perceive=perceive(), conservation_kernel=ConservationKernel(root_guard=admission),
        observe_engine=ObserveClinicalEngine(), root_admission=admission, profile="production",
        receipts=ReceiptLog(tmp_path / "receipts.jsonl", signer=DEPLOYER),
        execution_ledger=ExecutionLedger(tmp_path / "ledger.jsonl", signer=DEPLOYER),
        escalations=desk(tmp_path), source_signers=[SOURCE])
    parts.update(override)
    return parts


def build(tmp_path, **override):
    return GovernanceOrchestrator(**wiring(tmp_path, **override))


def refused(tmp_path, **override):
    with pytest.raises(ProductionProfileError) as info:
        build(tmp_path, **override)
    return " | ".join(info.value.problems)


# ---- a correct wiring starts and acts ---------------------------------------------------------------------------

def test_a_correct_wiring_starts_and_is_named_production(tmp_path):
    o = build(tmp_path)
    assert o.profile == "production" and o.require_declared_scope and o.guard_executor


def test_a_correct_wiring_executes_a_governed_action_end_to_end(tmp_path):
    importorskip_cns("governance_gateway")
    from gateway_admission_adapter import GatewayAdmissionAdapter, GatewayEpistemicStatus

    class S:
        def __init__(self, v):
            self.value = v

    class Meta:
        def __init__(self, aid, t):
            self.origin_status, self.authority_status, self.epistemic_status = S("SENTINEL"), S("SYSTEM"), S("INFERRED")
            self.parent_artifact_ids, self.occurred_at = ["feed"], t
            self.event_time_attestation = SentinelPerceiveAdapter.attest_event_time(SOURCE, aid, t.isoformat())

    class Art:
        def __init__(self, aid, content, t):
            self.artifact_id, self.content, self.metadata = aid, content, Meta(aid, t)

    t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)
    effects = []
    o = build(tmp_path)
    gw = GatewayAdmissionAdapter()
    sealed = gw.seal(artifact_id="prod-1", payload={"content": "escalate P001"}, provenance={"source": "SENTINEL"},
                     epistemic_status=GatewayEpistemicStatus.INFERENCE, authority_actor="CHARGE_NURSE",
                     authority_grant="unit-escalation", execute=True)
    result = gw.govern_admitted(
        o, sealed, Art("prod-1", "escalate P001", t), operation_type="escalate",
        operation_func=lambda ctx: effects.append(ctx.execution_id) or {"status": "done"},
        context={"patient_id": "P001"},
        vitals_snapshot=VitalsSnapshot("P001", t + timedelta(minutes=1), 142, 91, 28, 38.9))
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(effects) == 1
    assert result["audit_chain_valid"] is True and result["handoff"]["profile"] == "production"


# ---- the wiring the strict profile accepts, and production refuses -----------------------------------------------

def test_strict_accepts_a_bare_lambda_guard_and_production_refuses_it(tmp_path):
    strict = GovernanceOrchestrator(perceive(), ConservationKernel(root_guard=lambda a: True),
                                    ObserveClinicalEngine(), profile="strict")
    assert strict.profile == "strict"
    msg = refused(tmp_path, conservation_kernel=ConservationKernel(root_guard=lambda a: True))
    assert "not the RootAdmission given to the orchestrator" in msg


def test_a_second_rootadmission_the_kernel_does_not_hold_is_refused(tmp_path):
    assert "not the RootAdmission given to the orchestrator" in refused(tmp_path, root_admission=RootAdmission())


def test_a_lookalike_callable_in_place_of_the_roster_is_refused(tmp_path):
    class Lookalike:
        def __call__(self, artifact):
            return False

        def admit(self, kernel, ruling, root):
            raise RuntimeError("never reached")

        def close(self):
            pass

    look = Lookalike()
    msg = refused(tmp_path, conservation_kernel=ConservationKernel(root_guard=look), root_admission=look)
    assert "not a RootAdmission" in msg


def test_no_rootadmission_and_no_root_guard_are_refused(tmp_path):
    assert "no RootAdmission was given" in refused(tmp_path, root_admission=None)
    # An unguarded kernel is refused earlier still, by the strict check the production profile includes.
    with pytest.raises(ValueError, match="root guard"):
        build(tmp_path, conservation_kernel=ConservationKernel(allow_unguarded_roots=True))


def test_a_roster_that_approves_the_canary_is_refused_and_leaves_nothing_registered(tmp_path):
    class Approves(RootAdmission):
        def admit(self, kernel, ruling, root):
            self._mine.guard = lambda artifact: True           # opens for anything
            return None

    admission = Approves()
    kernel = ConservationKernel(root_guard=admission)
    before = len(kernel.ledger.snapshot()["artifacts"]) if hasattr(kernel.ledger, "snapshot") else None
    msg = refused(tmp_path, conservation_kernel=kernel, root_admission=admission)
    assert "ADMITTED the startup canary" in msg
    assert admission(object()) is False                           # the profile closed what the roster opened
    if before is not None:
        assert len(kernel.ledger.snapshot()["artifacts"]) == before


def test_a_guard_that_stays_open_after_a_refused_canary_is_refused(tmp_path):
    from root_admission import RootNotAdmitted

    class LeavesOpen(RootAdmission):
        def admit(self, kernel, ruling, root):
            self._mine.guard = lambda artifact: True           # opens, then reports a refusal
            raise RootNotAdmitted("refused, but the guard was left open")

        def close(self):                                       # and cannot be closed
            pass

    admission = LeavesOpen()
    msg = refused(tmp_path, conservation_kernel=ConservationKernel(root_guard=admission), root_admission=admission)
    assert "stayed open after the startup canary" in msg


def test_a_roster_that_crashes_on_the_canary_is_refused(tmp_path):
    class Crashes(RootAdmission):
        def admit(self, kernel, ruling, root):
            raise RuntimeError("roster is down")

    admission = Crashes()
    msg = refused(tmp_path, conservation_kernel=ConservationKernel(root_guard=admission), root_admission=admission)
    assert "crashed on the startup canary" in msg


# ---- audit, alert, observe, sources, executor --------------------------------------------------------------------

@pytest.mark.parametrize("override,expected", [
    ({"receipts": None}, "no receipt log"),
    ({"escalations": None}, "no escalation desk"),
    ({"observe_engine": None}, "no OBSERVE engine"),
    ({"source_signers": None}, "no event-time source keys"),
    ({"source_signers": []}, "no event-time source keys"),
])
def test_each_missing_part_is_named(tmp_path, override, expected):
    assert expected in refused(tmp_path, **override)


def test_a_bare_alert_callable_is_not_a_notification_policy(tmp_path):
    msg = refused(tmp_path, escalations=None, alert_human=lambda alert: None)
    assert "no escalation desk" in msg and "beside the production escalation desk" in msg


def test_a_desk_and_a_legacy_callable_together_are_refused_before_the_profile_runs(tmp_path):
    with pytest.raises(ValueError, match="not both"):
        build(tmp_path, alert_human=lambda alert: None)


def test_something_that_is_not_a_desk_is_refused(tmp_path):
    assert "not an EscalationDesk" in refused(tmp_path, escalations=lambda alert: None)


def test_a_desk_with_a_bad_policy_an_unkeyed_responder_or_a_weak_log_is_refused(tmp_path):
    no_deadline = NotificationPolicy(tiers=(Tier("charge", lambda payload: None, ("nurse-1",), 0),))
    assert "ack_within_s" in refused(tmp_path, escalations=desk(tmp_path, policy=no_deadline))
    assert "no registered key" in refused(tmp_path, escalations=desk(tmp_path, responder_keys={}))
    assert "in memory" in refused(tmp_path, escalations=desk(tmp_path, log=EscalationLog(None, signer=DEPLOYER)))
    assert "unsigned" in refused(tmp_path, escalations=desk(tmp_path, log=EscalationLog(tmp_path / "u.jsonl")))


def test_an_unguarded_executor_is_refused_by_strict_and_by_the_profile_itself(tmp_path):
    with pytest.raises(ValueError, match="guard_executor"):                  # refused earlier, by the strict check
        build(tmp_path, guard_executor=False)
    problems = production_problems(                                          # and by the profile's own check
        conservation_kernel=None, root_admission=None, observe_engine=None, receipts=None,
        execution_ledger=None, alert_human=None, source_signers=None, guard_executor=False)
    assert any("guard_executor is off" in p for p in problems)


def test_an_in_memory_or_unsigned_ledger_and_unsigned_receipts_are_refused(tmp_path):
    assert "in memory" in refused(tmp_path, execution_ledger=ExecutionLedger())
    assert "execution ledger is unsigned" in refused(tmp_path, execution_ledger=ExecutionLedger(tmp_path / "l2.jsonl"))
    assert "receipt log is unsigned" in refused(tmp_path, receipts=ReceiptLog(tmp_path / "r2.jsonl"))


def test_every_problem_is_reported_together(tmp_path):
    problems = production_problems(conservation_kernel=None, root_admission=None, observe_engine=None, receipts=None,
                                   execution_ledger=None, alert_human=None, source_signers=None, guard_executor=False)
    assert len(problems) >= 7


# ---- CNS version -------------------------------------------------------------------------------------------------

def test_an_older_cns_is_refused_and_a_readable_version_is_required(tmp_path, monkeypatch):
    old = ".".join(map(str, (REQUIRED_CNS[0], REQUIRED_CNS[1] - 1, 0)))
    monkeypatch.setattr(cns, "__version__", old)
    assert f"the installed CNS is {old}" in refused(tmp_path)
    monkeypatch.setattr(cns, "__version__", "not-a-version")
    assert "does not state a readable version" in refused(tmp_path)


def test_the_required_cns_is_the_one_this_repository_pins():
    pin = open("pyproject.toml", encoding="utf-8").read()
    assert f"CNS.git@v{'.'.join(map(str, REQUIRED_CNS))}" in pin


# ---- the other profiles are unchanged ---------------------------------------------------------------------------

def test_advisory_and_strict_still_start_without_the_production_checks():
    assert GovernanceOrchestrator(perceive(), ConservationKernel(allow_unguarded_roots=True)).profile == "advisory"
    with pytest.raises(ValueError):
        GovernanceOrchestrator(perceive(), profile="nonsense")
