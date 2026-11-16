"""test_log_redaction.py -- a patient identifier is a pseudonym in a log line (decision D11).

Measured on `main` (EXECUTED, docs/race/DATA_PRIVACY_AND_RESEARCH.md): a canary patient id was found in log lines from the
engine, the governance system and the orchestrator. Logs leave the clinical zone.
"""
import io
import logging
from datetime import datetime, timedelta, timezone

import pytest

from clinical_governance_system import ClinicalGovernanceSystem, build_single_hospital_system
from log_redaction import patient_tag, raw_ids_enabled
from observe_consolidated import ObserveClinicalEngine, VitalsSnapshot
from test_identity_binding import orch, run, vit

CANARY = "CANARY-PT-7731"
T0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)


@pytest.fixture
def captured():
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    yield buf
    root.removeHandler(handler)
    root.setLevel(old_level)


def test_the_tag_is_stable_distinct_and_not_the_identifier(monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)
    monkeypatch.setenv("OBSERVE_LOG_SALT", "a-salt-for-tests")
    a, b = patient_tag("P1"), patient_tag("P2")
    assert a == patient_tag("P1") and a != b and a.startswith("pt:") and len(a) == 15 and "P1" not in a
    monkeypatch.setenv("OBSERVE_LOG_SALT", "another-salt")
    assert patient_tag("P1") != a                                                   # keyed: another salt, another tag


def test_the_tag_handles_odd_values_and_never_raises(monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)
    assert patient_tag(None) == "pt:-" and patient_tag("") == "pt:-"
    assert patient_tag(5) == patient_tag("5")                                       # an int id and its text are one patient

    class Bad:
        def __str__(self):
            raise RuntimeError("no text")
    assert patient_tag(Bad()) == "pt:?"


def test_raw_ids_are_an_explicit_troubleshooting_switch(monkeypatch):
    monkeypatch.setenv("OBSERVE_LOG_RAW_PATIENT_IDS", "1")
    assert raw_ids_enabled() and patient_tag("P1") == "P1"
    monkeypatch.setenv("OBSERVE_LOG_RAW_PATIENT_IDS", "no")
    assert not raw_ids_enabled() and patient_tag("P1") != "P1"


def test_no_log_line_from_the_governance_system_carries_the_patient_id(captured, monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)
    alerts = []
    system = build_single_hospital_system(alert_human=alerts.append)
    system.process_vitals(VitalsSnapshot(CANARY, T0, 168, 83.0, 46, 39.5, {"age_months": 24}))      # CRITICAL, blocked
    system.process_vitals(VitalsSnapshot(CANARY, T0 + timedelta(minutes=1), 100, 98.0, 24, 37.0, {"age_months": None}))
    log = captured.getvalue()
    assert log and CANARY not in log
    assert patient_tag(CANARY) in log                                               # and the lines are still about this patient


def test_a_crash_and_a_failed_alert_do_not_put_the_id_in_the_log(captured, monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)

    def failing_pager(decision):
        raise RuntimeError("pager down")
    system = ClinicalGovernanceSystem(alert_human=failing_pager)
    system.observe.evaluate = lambda v: (_ for _ in ()).throw(RuntimeError("engine down"))
    system.process_vitals(VitalsSnapshot(CANARY, T0, 100, 98.0, 24, 37.0, {"age_months": 24}))
    assert CANARY not in captured.getvalue() and "ASSESSMENT UNAVAILABLE" in captured.getvalue()


def test_the_engine_fault_and_escalation_lines_use_the_tag(captured, monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)
    engine = ObserveClinicalEngine()
    engine.evaluate(VitalsSnapshot(CANARY, T0, float("nan"), 98.0, 24, 37.0, {"age_months": 24}))   # a faulted channel
    engine.evaluate(VitalsSnapshot(CANARY, T0 + timedelta(minutes=1), 168, 83.0, 46, 39.5, {}))      # no age, an escalation
    assert CANARY not in captured.getvalue() and "DATA_INTEGRITY_FAULT" in captured.getvalue()


def test_an_identity_mismatch_refusal_does_not_log_either_id(captured, monkeypatch):
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)
    run(orch(alert_human=lambda a: None), "red1", vit("VITALS-PATIENT-9"), ctx={"patient_id": "CONTEXT-PATIENT-4"})
    log = captured.getvalue()
    assert "identity mismatch" in log and "VITALS-PATIENT-9" not in log and "CONTEXT-PATIENT-4" not in log


def test_the_notification_desk_failure_line_uses_the_tag(captured, monkeypatch):
    from clinical_notification import notify
    monkeypatch.delenv("OBSERVE_LOG_RAW_PATIENT_IDS", raising=False)

    class Broken:
        def raise_and_dispatch(self, **kw):
            raise RuntimeError("desk down")
    assert notify(Broken(), context={"patient_id": CANARY})[1] == "failed"
    assert CANARY not in captured.getvalue()
