"""One operational chain, end to end: the registry, the execution ledger, the reconciler, the escalation desk and the
external heartbeat, working together on a real production-profile orchestrator with every record on disk.

Each part has its own tests. This one proves the parts work as one chain, in the order a bad day would use them:

  1. a governed action runs; its effect lands in an independent effect log; reconciliation is clean
  2. someone calls the action directly; reconciliation finds the bypass; a ticket is raised and written to the signed log
  3. the runner's process dies; a separate process checking the heartbeat sees healthy, then silence, and its alarm
     lands on an on-call desk that is not the one that died
  4. everything restarts from disk; the open ticket is still there and the bypass is not paged a second time
  5. the responder acknowledges; their role is revoked; the same key is refused on the next ticket

Needs CNS (production does); in CI's `cns` job a missing CNS is an error, elsewhere this skips.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

import pytest

from conftest import importorskip_cns

importorskip_cns("cns.gate")
importorskip_cns("cns_composition.decision_guard")
importorskip_cns("governance_gateway")

from actor_registry import ActorRegistry, sign_registry_action  # noqa: E402
from clinical_settings import ClinicalSettings  # noqa: E402
from conservation_kernel import ConservationKernel, HmacSigner  # noqa: E402
from escalation_runner import SweepRunner  # noqa: E402
from execution_guard import ExecutionLedger  # noqa: E402
from gateway_admission_adapter import GatewayAdmissionAdapter, GatewayEpistemicStatus  # noqa: E402
from governance_orchestrator import GovernanceOrchestrator  # noqa: E402
from governance_record import ReceiptLog  # noqa: E402
from heartbeat import HeartbeatWriter  # noqa: E402
from human_escalation import (AcknowledgementError, EscalationDesk, EscalationLog, NotificationPolicy,  # noqa: E402
                              Tier, sign_acknowledgement)
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot  # noqa: E402
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest  # noqa: E402
from reconciler import escalate, reconcile  # noqa: E402
from root_admission import RootAdmission, refuse_all_evidence  # noqa: E402
from sentinel_perceive_adapter import SentinelPerceiveAdapter  # noqa: E402

DEPLOYER = HmacSigner(b"operational-chain-deployer-key-0123456789ab", key_id="deployer")
SOURCE = HmacSigner(b"operational-chain-source-feed-key-0123456789", key_id="feed")
REGISTRAR = HmacSigner(b"operational-chain-registrar-key-0123456789a", key_id="registrar")
NURSE = HmacSigner(b"operational-chain-nurse-1-key-0123456789abcd", key_id="nurse-1")
HEARTBEAT_SECRET = "operational-chain-heartbeat-key-0123456789"
HEARTBEAT = HmacSigner(HEARTBEAT_SECRET.encode(), key_id="heartbeat")
POLICY = dict(clock_skew_s=5, abandoned_after_s=3600)
HERE = os.path.dirname(os.path.abspath(__file__))


def _wall_clock():
    """The real time, even when a test run has moved this process's clock (CI's future-clock run). The heartbeat's
    checker is a separate process on the real clock, as it is when deployed, so the writer must be on it too."""
    return datetime.fromtimestamp(time.clock_gettime(time.CLOCK_REALTIME), tz=timezone.utc)


def _registry(tmp_path):
    return ActorRegistry(roots={"registrar": "registrar"}, keys={"registrar": REGISTRAR, "nurse-1": NURSE},
                         path=tmp_path / "registry.jsonl")


def _registrar(reg, kind, **body):
    full = {"actor_id": body["actor_id"], "role": body["role"]}
    if kind == "revoke":
        full["reason"] = body["reason"]
    else:
        full.update(key_id=body["key_id"], expires_at=None)
    signature = sign_registry_action(REGISTRAR, reg.head, kind, "registrar", full)
    return getattr(reg, kind)(by="registrar", signature=signature, **body)


def _desk(tmp_path, reg, pager, name="escalations"):
    """The ward's desk: paged responders are whoever the registry says holds `responder`, checked live."""
    return EscalationDesk(NotificationPolicy((Tier("charge", pager.append, ("nurse-1",), 120.0, 5.0),)),
                          EscalationLog(tmp_path / f"{name}.jsonl", signer=DEPLOYER),
                          responder_keys=reg.responder_keys("responder"), authority=reg.authority("responder"))


def _effect_log(path):
    """The downstream system's own record of what it did. The guard cannot write here; the action itself does."""
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _check_heartbeat_elsewhere(path, max_age):
    """Run the checker the way a scheduler would: as its own process, with the key from its own environment."""
    env = {**os.environ, "OPCHAIN_HEARTBEAT_KEY": HEARTBEAT_SECRET}
    return subprocess.run([sys.executable, "-m", "heartbeat", "check", str(path), "--max-age", str(max_age),
                           "--key-env", "OPCHAIN_HEARTBEAT_KEY", "--key-id", "heartbeat", "--label", "ward-7"],
                          capture_output=True, text=True, env=env, cwd=HERE)


def _raised_from_log(desk):
    """What has already been paged, read back from the desk's durable log, so a restart does not page twice."""
    return {e["data"]["context"]["violation_id"] for e in desk.log.entries
            if e.get("event") == "opened" and "violation_id" in (e.get("data", {}).get("context") or {})}


def _ack(desk, ticket_id, signer=NURSE, actor="nurse-1"):
    opened = desk.ticket(ticket_id)["opened_hash"]
    return desk.acknowledge(ticket_id, actor, sign_acknowledgement(signer, ticket_id, opened, actor))


def test_the_registry_ledger_reconciler_desk_and_heartbeat_work_as_one_chain(tmp_path):
    effects_path = tmp_path / "downstream-effects.jsonl"

    def act(ctx=None):
        """The real action. It writes the downstream record itself, whoever called it."""
        record = {"effect_id": f"eff-{len(_effect_log(effects_path)) + 1}", "at": datetime.now(timezone.utc).isoformat(),
                  "execution_id": getattr(ctx, "execution_id", None), "artifact_hash": getattr(ctx, "artifact_hash", None)}
        with effects_path.open("a") as fh:
            fh.write(json.dumps(record) + "\n")
        return {"status": "done"}

    # ---- wiring: a production orchestrator whose desk answers to the registry and whose runner beats -------------
    reg = _registry(tmp_path)
    _registrar(reg, "grant", actor_id="nurse-1", role="responder", key_id="nurse-1")
    pages = []
    desk = _desk(tmp_path, reg, pages)
    heartbeat_path = tmp_path / "ward-7.heartbeat.json"
    runner = SweepRunner(desk, sweep_interval_s=3600, watch_interval_s=3600, max_silence_s=7200, alarm_timeout_s=5,
                         max_tickets_per_sweep=10, alarm=pages.append,
                         heartbeat=HeartbeatWriter(heartbeat_path, HEARTBEAT, label="ward-7", clock=_wall_clock)).start()
    admission = RootAdmission()
    perceive = PerceiveGovernanceKernel()
    perceive.register_manifest(PolicyManifest(manifest_id="m", version="1", created_at=datetime.now(timezone.utc),
                                              policies={}))
    ledger_path = tmp_path / "execution-ledger.jsonl"
    try:
        orchestrator = GovernanceOrchestrator(
            perceive, ConservationKernel(root_guard=admission, evidence_guard=refuse_all_evidence),
            ObserveClinicalEngine(ledger_max_entries=1000, ledger_archive_path=tmp_path / "observe.archive.jsonl"),
            clinical_settings=ClinicalSettings(), root_admission=admission, profile="production",
            receipts=ReceiptLog(tmp_path / "receipts.jsonl", signer=DEPLOYER),
            execution_ledger=ExecutionLedger(ledger_path, signer=DEPLOYER),
            escalations=desk, source_signers=[SOURCE], sweep_runner=runner)

        # ---- 1. a governed action: authorized, executed, and reconciled clean -------------------------------------
        t = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(minutes=5)

        class _S:
            def __init__(self, v):
                self.value = v

        class _Meta:
            def __init__(self, aid):
                self.origin_status, self.authority_status, self.epistemic_status = _S("SENTINEL"), _S("SYSTEM"), _S("INFERRED")
                self.parent_artifact_ids, self.occurred_at = ["feed"], t
                self.event_time_attestation = SentinelPerceiveAdapter.attest_event_time(SOURCE, aid, t.isoformat())

        class _Art:
            def __init__(self, aid, content):
                self.artifact_id, self.content, self.metadata = aid, content, _Meta(aid)

        gateway = GatewayAdmissionAdapter()
        sealed = gateway.seal(artifact_id="chain-1", payload={"content": "escalate P001"},
                              provenance={"source": "SENTINEL"}, epistemic_status=GatewayEpistemicStatus.INFERENCE,
                              authority_actor="CHARGE_NURSE", authority_grant="unit-escalation", execute=True)
        result = gateway.govern_admitted(
            orchestrator, sealed, _Art("chain-1", "escalate P001"), operation_type="escalate", operation_func=act,
            context={"patient_id": "P001"},
            vitals_snapshot=VitalsSnapshot("P001", t + timedelta(minutes=1), 142, 91, 28, 38.9))
        assert result["status"] == "APPROVED_AND_EXECUTED" and result["handoff"]["profile"] == "production"
        ledger = ExecutionLedger(ledger_path, signer=DEPLOYER)            # read back from disk, signatures checked
        assert reconcile(ledger, _effect_log(effects_path), now=datetime.now(timezone.utc), **POLICY).clean

        # ---- 2. a direct call around the guard: found, paged, and written to the signed log ----------------------
        act()
        found = reconcile(ledger, _effect_log(effects_path), now=datetime.now(timezone.utc), **POLICY)
        assert [v.kind for v in found.violations] == ["bypass"] and found.violations[0].effect_id == "eff-2"
        paged = escalate(found, desk, already_raised=_raised_from_log(desk))
        assert len(paged) == 1
        bypass_violation, bypass_ticket = paged[0]
        assert [p["kind"] for p in pages if p.get("ticket_id") == bypass_ticket] == ["reconciliation:bypass"]
        assert bypass_violation in _raised_from_log(desk)

        # ---- 3. the runner's process dies: a separate process notices, and its alarm goes somewhere else -----------
        runner.check()                                                     # one watchdog pass: one fresh beat
        healthy = _check_heartbeat_elsewhere(heartbeat_path, max_age=60)
        assert healthy.returncode == 0 and healthy.stdout.startswith("OK:"), healthy.stdout + healthy.stderr
    finally:
        runner.stop(timeout=2)                                             # the process "dies": no more beats
        desk.log.close()
    time.sleep(1.5)
    silent = _check_heartbeat_elsewhere(heartbeat_path, max_age=1)
    assert silent.returncode == 2 and "dead, hung or paused" in silent.stdout, silent.stdout + silent.stderr
    on_call_pages = []
    on_call = EscalationDesk(NotificationPolicy((Tier("on-call", on_call_pages.append, ("nurse-1",), 120.0, 5.0),)),
                             EscalationLog(tmp_path / "on-call.jsonl", signer=DEPLOYER), responder_keys={"nurse-1": NURSE})
    on_call.raise_and_dispatch(kind="heartbeat:silent", context={"checker_said": silent.stdout.strip()})
    assert on_call_pages and on_call_pages[0]["kind"] == "heartbeat:silent"
    assert [e["event"] for e in on_call.log.entries][:1] == ["opened"]
    on_call.log.close()

    # ---- 4. restart from disk: the ticket survives and the same bypass is not paged twice ------------------------
    reg = _registry(tmp_path)                                              # replayed and re-proved from its log
    assert reg.holds("nurse-1", "responder")
    pages_after_restart = []
    desk = _desk(tmp_path, reg, pages_after_restart)
    try:
        assert bypass_ticket in desk.open_tickets()
        already = _raised_from_log(desk)
        again = reconcile(ExecutionLedger(ledger_path, signer=DEPLOYER), _effect_log(effects_path),
                          now=datetime.now(timezone.utc), **POLICY)
        assert escalate(again, desk, already_raised=already) == [] and pages_after_restart == []

        # ---- 5. acknowledge, revoke, and the same key is refused on the next ticket -------------------------------
        assert _ack(desk, bypass_ticket) == "acknowledged"
        _registrar(reg, "revoke", actor_id="nurse-1", role="responder", reason="suspended pending review")
        act()                                                              # a second direct call
        second = reconcile(ExecutionLedger(ledger_path, signer=DEPLOYER), _effect_log(effects_path),
                           now=datetime.now(timezone.utc), **POLICY)
        new = escalate(second, desk, already_raised=already)
        assert len(new) == 1
        with pytest.raises(AcknowledgementError, match="does not currently hold"):
            _ack(desk, new[0][1])
        assert new[0][1] in desk.open_tickets()                            # still needs a person who may act
    finally:
        desk.log.close()

    # The revocation itself survives a restart too.
    assert not _registry(tmp_path).holds("nurse-1", "responder")
