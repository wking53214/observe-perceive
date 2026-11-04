"""Stage 1A step 4: after the action, an OBSERVE crash resets that patient's tracked state.

evaluate() moves a patient's escalation state before it records, so a crash part way can leave the patient
marked "already escalated" for a reading no ledger holds. The error to prefer is a repeat page, never silence.
"""
from conftest import run_chain
from governance_orchestrator import GovernanceOrchestrator
from observe_consolidated import ObserveClinicalEngine


class CrashingObserve(ObserveClinicalEngine):
    def __init__(self):
        super().__init__()
        self.resets = []

    def evaluate(self, vitals):
        raise RuntimeError("OBSERVE crashed")

    def reset_tracked_state(self, vitals):
        self.resets.append(vitals.patient_id)


def test_an_observe_crash_after_the_action_resets_the_patients_tracked_state(perceive, kernel):
    engine = CrashingObserve()
    result, ran = run_chain(GovernanceOrchestrator(perceive, kernel, engine), "reset-1")
    assert result["status"] == "APPROVED_AND_EXECUTED" and len(ran) == 1     # the action stands
    assert result["observe_error"].startswith("RuntimeError")
    assert engine.resets == ["P001"]


def test_a_failing_reset_is_recorded_beside_the_crash(perceive, kernel):
    class ResetBreaks(CrashingObserve):
        def reset_tracked_state(self, vitals):
            raise RuntimeError("reset failed")

    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, ResetBreaks()), "reset-2")
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert "tracked state reset failed" in result["observe_error"]


def test_an_engine_without_a_reset_still_records_the_crash(perceive, kernel):
    class Bare:
        def evaluate(self, vitals):
            raise RuntimeError("OBSERVE crashed")

    result, _ = run_chain(GovernanceOrchestrator(perceive, kernel, Bare()), "reset-3")
    assert result["status"] == "APPROVED_AND_EXECUTED"
    assert result["observe_error"].startswith("RuntimeError")


def test_engine_reset_drops_the_state_so_the_next_reading_escalates():
    from datetime import datetime, timedelta, timezone
    from observe_consolidated import VitalsSnapshot
    t0 = datetime(2026, 11, 4, 8, 0, tzinfo=timezone.utc)

    def crit(i):
        return VitalsSnapshot("P1", t0 + timedelta(minutes=i), 168, 83.0, 46, 39.5, {"age_months": 12})

    engine = ObserveClinicalEngine()
    assert engine.evaluate(crit(0)).escalation_required
    assert not engine.evaluate(crit(1)).escalation_required     # inside the cooldown: a repeat is held
    engine.reset_tracked_state(crit(2))
    assert engine.evaluate(crit(2)).escalation_required         # after a reset it is judged fresh
