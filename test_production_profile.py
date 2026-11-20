"""The production profile proves its wiring at construction, or the orchestrator does not start.

Needs CNS (the roster is CNS's). In CI's `cns` job a missing CNS is an error; elsewhere these skip.
"""
import itertools
from datetime import datetime, timedelta, timezone

import pytest

from conftest import importorskip_cns, make_test_desk

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.decision_guard")

import cns  # noqa: E402
from conservation_kernel import ConservationKernel, HmacSigner  # noqa: E402
from execution_guard import ExecutionLedger  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from governance_record import ReceiptLog  # noqa: E402
from escalation_runner import SweepRunner  # noqa: E402
from human_escalation import EscalationDesk, EscalationLog, NotificationPolicy, Tier  # noqa: E402
from clinical_settings import ClinicalSettings  # noqa: E402
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


_LOGS = itertools.count()


def desk(tmp_path, **override):
    """A valid desk. Each gets its own log file: one writer per log is enforced."""
    parts = dict(policy=NotificationPolicy(tiers=(Tier("charge", lambda payload: None, ("nurse-1",), 120.0, 5.0),)),
                 log=EscalationLog(tmp_path / f"escalations-{next(_LOGS)}.jsonl", signer=DEPLOYER),
                 responder_keys={"nurse-1": RESPONDER})
    parts.update(override)
    return EscalationDesk(**parts)


_RUNNERS = []


@pytest.fixture(autouse=True)
def _stop_runners():
    yield
    while _RUNNERS:
        _RUNNERS.pop().stop(timeout=2)


def runner_for(escalations, **override):
    """A started sweep runner bound to `escalations`, with intervals long enough that it never fires during a test."""
    parts = dict(sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200, alarm_timeout_s=5,
                 max_tickets_per_sweep=10, alarm=lambda payload: None)
    parts.update(override)
    runner = SweepRunner(escalations, **parts).start()
    _RUNNERS.append(runner)
    return runner


def wiring(tmp_path, **override):
    """A correct production wiring; `override` replaces one part."""
    admission = RootAdmission()
    escalations = override.get("escalations", desk(tmp_path))
    parts = dict(
        perceive=perceive(), conservation_kernel=ConservationKernel(root_guard=admission),
        observe_engine=ObserveClinicalEngine(ledger_max_entries=1000, ledger_archive_path=tmp_path / "observe.archive.jsonl"),
        clinical_settings=ClinicalSettings(), root_admission=admission, profile="production",
        receipts=ReceiptLog(tmp_path / "receipts.jsonl", signer=DEPLOYER),
        execution_ledger=ExecutionLedger(tmp_path / "ledger.jsonl", signer=DEPLOYER),
        escalations=escalations, source_signers=[SOURCE],
        sweep_runner=runner_for(escalations) if hasattr(escalations, "max_dispatch_seconds") else None)
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
                                    ObserveClinicalEngine(), profile="strict", escalations=make_test_desk())
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
    with pytest.raises(ValueError, match="must be an EscalationDesk"):         # refused at construction, before the profile
        build(tmp_path, escalations=lambda alert: None)
    problems = production_problems(                                          # and by the profile's own check
        conservation_kernel=None, root_admission=None, observe_engine=None, receipts=None,
        execution_ledger=None, alert_human=None, source_signers=None, guard_executor=False,
        escalations=lambda alert: None)
    assert any("not an EscalationDesk" in p for p in problems)


def test_a_desk_with_a_bad_policy_an_unkeyed_responder_or_a_weak_log_is_refused(tmp_path):
    no_deadline = NotificationPolicy(tiers=(Tier("charge", lambda payload: None, ("nurse-1",), 0, 5.0),))
    assert "ack_within_s" in refused(tmp_path, escalations=desk(tmp_path, policy=no_deadline))
    assert "no registered key" in refused(tmp_path, escalations=desk(tmp_path, responder_keys={}))
    assert "in memory" in refused(tmp_path, escalations=desk(tmp_path, log=EscalationLog(None, signer=DEPLOYER)))
    assert "unsigned" in refused(tmp_path, escalations=desk(tmp_path, log=EscalationLog(tmp_path / "u.jsonl")))


def test_a_correct_wiring_has_a_running_sweep_runner_bound_to_its_own_desk(tmp_path):
    parts = wiring(tmp_path)
    assert parts["sweep_runner"].running and parts["sweep_runner"].desk is parts["escalations"]


def test_no_sweep_runner_is_refused_because_nothing_would_follow_up_an_unanswered_page(tmp_path):
    assert "no sweep runner" in refused(tmp_path, sweep_runner=None)


def test_something_that_is_not_a_sweep_runner_is_refused(tmp_path):
    assert "not a SweepRunner" in refused(tmp_path, sweep_runner=object())


def test_a_runner_for_a_different_desk_is_refused(tmp_path):
    other = desk(tmp_path)
    assert "different desk" in refused(tmp_path, sweep_runner=runner_for(other))


def test_a_runner_that_is_not_running_is_refused(tmp_path):
    escalations = desk(tmp_path)
    stopped = runner_for(escalations)
    stopped.stop(timeout=2)
    assert "not running" in refused(tmp_path, escalations=escalations, sweep_runner=stopped)


def test_a_runner_whose_timings_cannot_work_is_refused_with_the_reason(tmp_path):
    escalations = desk(tmp_path)
    never_started = SweepRunner(escalations, sweep_interval_s=60, watch_interval_s=10, max_silence_s=61, alarm_timeout_s=5,
                                max_tickets_per_sweep=1, alarm=lambda payload: None)   # 61 <= 60 + the desk's 5s dispatch
    msg = refused(tmp_path, escalations=escalations, sweep_runner=never_started)
    assert "sweep runner:" in msg and "max_silence_s" in msg


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
    old = ".".join(map(str, (REQUIRED_CNS[0] - 1, 0, 0)))
    monkeypatch.setattr(cns, "__version__", old)
    assert f"the installed CNS is {old}" in refused(tmp_path)
    monkeypatch.setattr(cns, "__version__", "not-a-version")
    assert "does not state a readable version" in refused(tmp_path)


def test_the_required_cns_is_no_newer_than_the_one_this_repository_pins():
    import re

    pin = open("pyproject.toml", encoding="utf-8").read()
    match = re.search(r"CNS\.git@v(\d+)\.(\d+)\.(\d+)", pin)
    assert match, "pyproject.toml pins no CNS tag"
    assert REQUIRED_CNS <= tuple(int(part) for part in match.groups())


# ---- the other profiles are unchanged ---------------------------------------------------------------------------

def test_advisory_and_strict_still_start_without_the_production_checks():
    assert GovernanceOrchestrator(perceive(), ConservationKernel(allow_unguarded_roots=True)).profile == "advisory"
    with pytest.raises(ValueError):
        GovernanceOrchestrator(perceive(), profile="nonsense")


def test_a_runner_with_no_desk_is_reported_not_raised(tmp_path):
    broken = SweepRunner(None, sweep_interval_s=1, watch_interval_s=1, max_silence_s=100, alarm_timeout_s=1,
                         max_tickets_per_sweep=1, alarm=lambda payload: None)
    msg = refused(tmp_path, sweep_runner=broken)
    assert "no escalation desk to sweep" in msg and "not running" in msg


# ---- decision D11: settings attested, ledger bounded, no raw patient ids in logs -------------------------------------------

def test_no_attested_clinical_settings_is_refused_because_silent_defaults_are_not_approval(tmp_path):
    assert "no clinical settings were attested" in refused(tmp_path, clinical_settings=None)


def test_something_that_is_not_clinical_settings_is_refused(tmp_path):
    assert "not a ClinicalSettings" in refused(tmp_path, clinical_settings={"critical_o2_below": 90})


def test_an_engine_running_other_settings_than_the_attested_ones_is_refused(tmp_path):
    from pediatric_cassette import PediatricCassette
    tuned = ClinicalSettings(critical_o2_below=90.0)
    engine = ObserveClinicalEngine(cassette=PediatricCassette(settings=tuned), ledger_max_entries=100,
                                   ledger_archive_path=tmp_path / "o.jsonl")
    problem = refused(tmp_path, observe_engine=engine, clinical_settings=ClinicalSettings())
    assert "different clinical settings from the ones attested" in problem
    build(tmp_path, observe_engine=engine, clinical_settings=tuned)                    # attested and in force: starts


def test_a_cassette_that_hides_its_settings_is_refused(tmp_path):
    engine = ObserveClinicalEngine(ledger_max_entries=100, ledger_archive_path=tmp_path / "o.jsonl")
    engine._cassette = type("C", (), {"name": "x", "version": "1"})()
    assert "not a PediatricCassette holding ClinicalSettings" in refused(tmp_path, observe_engine=engine)


def test_an_unbounded_or_archiveless_observe_ledger_is_refused(tmp_path):
    assert "unbounded" in refused(tmp_path, observe_engine=ObserveClinicalEngine())


def test_raw_patient_ids_in_logs_are_refused(tmp_path, monkeypatch):
    monkeypatch.setenv("OBSERVE_LOG_RAW_PATIENT_IDS", "1")
    assert "OBSERVE_LOG_RAW_PATIENT_IDS" in refused(tmp_path)


def test_the_settings_digest_is_in_every_handoff_and_absent_when_none_was_given(tmp_path):
    from conftest import ChainArtifact
    production = build(tmp_path)
    result = production.orchestrate_request(ChainArtifact("pp-1"), "escalate", lambda c: {}, vitals_snapshot=None,
                                            context={"execution_id": "pp-e1"})
    assert result["handoff"]["clinical_settings_digest"] == ClinicalSettings().digest()
    advisory = GovernanceOrchestrator(perceive(), ConservationKernel(allow_unguarded_roots=True))
    plain = advisory.orchestrate_request(ChainArtifact("pp-2"), "escalate", lambda c: {}, vitals_snapshot=None,
                                         context={"execution_id": "pp-e2"})
    assert "clinical_settings_digest" not in plain["handoff"]


def test_a_settings_subclass_that_lies_about_its_digest_is_refused(tmp_path):
    class Liar(ClinicalSettings):
        def digest(self):
            return ClinicalSettings().digest()
    engine = ObserveClinicalEngine(ledger_max_entries=100, ledger_archive_path=tmp_path / "o.jsonl")
    assert "not a ClinicalSettings" in refused(tmp_path, observe_engine=engine, clinical_settings=Liar(critical_o2_below=80.0))


def test_a_cassette_that_only_claims_the_attested_settings_is_refused(tmp_path):
    engine = ObserveClinicalEngine(ledger_max_entries=100, ledger_archive_path=tmp_path / "o.jsonl")
    engine._cassette = type("C", (), {"name": "x", "version": "1", "settings": ClinicalSettings()})()
    assert "not a PediatricCassette holding ClinicalSettings" in refused(tmp_path, observe_engine=engine)


def test_settings_that_override_their_own_digest_on_the_instance_are_not_trusted(tmp_path):
    attested = ClinicalSettings(critical_o2_below=80.0)
    object.__setattr__(attested, "digest", lambda: ClinicalSettings().digest())     # claims to be the defaults the engine runs
    engine = ObserveClinicalEngine(ledger_max_entries=100, ledger_archive_path=tmp_path / "o.jsonl")
    assert "different clinical settings from the ones attested" in refused(tmp_path, observe_engine=engine, clinical_settings=attested)


# ---- FORTRESS in the chain: its audit ledger signs with a configured key ---------------------------------------------

def _fortress_or_skip():
    try:
        import fortress_perceive_adapter  # noqa: F401
    except ImportError:  # the adapter raises a plain ImportError when fortress-kernel is absent
        pytest.skip("fortress-kernel is not installed")


def test_fortress_without_a_configured_audit_key_is_refused(tmp_path, monkeypatch):
    _fortress_or_skip()
    monkeypatch.delenv("FORTRESS_AUDIT_KEY", raising=False)
    assert "FORTRESS is in the chain but its audit ledger has no configured key" in refused(
        tmp_path, fortress_controller="energy")


def test_fortress_with_a_configured_audit_key_starts(tmp_path, monkeypatch):
    import secrets
    _fortress_or_skip()
    monkeypatch.setenv("FORTRESS_AUDIT_KEY", secrets.token_hex(32))   # generated per run, never written down
    o = build(tmp_path, fortress_controller="energy")
    assert o.fortress_adapter is not None and o.fortress_adapter.fortress.audit.ephemeral_key is False
