"""clinical_notification.py -- when a clinical event must reach a person, and what the ticket says.

OBSERVE decides that an escalation happened. PERCEIVE decides whether it is approved. Neither tells anybody: until now an
approved CRITICAL, and a CRITICAL that governance BLOCKED, returned to the caller and reached no one unless the caller
looked. This module is the one rule for "someone must be told", shared by `ClinicalGovernanceSystem` and
`GovernanceOrchestrator`, and it opens a ticket on an `EscalationDesk` (human_escalation.py) so the notification has a
lifecycle: dispatched, delivered, acknowledged, followed up.

Every threshold here is the clinician's to change (`ClinicalNotificationSettings`). The defaults are chosen to err toward
telling someone about the worst events and not toward telling everyone about everything:

  * a NEW escalation at or above `min_regime` (default critical);
  * a severity increase from an already-escalated state (default on): a patient who got worse is a new event;
  * an escalation that governance BLOCKED (default on): a blocked escalation otherwise vanishes silently;
  * an escalation whose reading could not be assessed (default on): a blind spot a person should know about.
  * governance UNAVAILABLE for an escalation is always told: there is no verdict, and that has always alerted.
  * an ASSESSMENT unavailable (OBSERVE itself failed) is always told: a patient nobody assessed is not a quiet one.

Only events are notified, never readings: OBSERVE sets `escalation_required` once per new escalation or severity
increase, so a patient who stays critical does not page again on every reading. Nothing here is a clinical rule; it is
the routing of one that already fired. See docs/race/CLINICAL_ESCALATION_NOTIFICATION.md.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

__all__ = ["ClinicalNotificationSettings", "notification_reason", "notify", "ticket_context"]

logger = logging.getLogger(__name__)

#: The order of regimes, lowest to highest. "none" as `min_regime` means no regime alone triggers a notification.
_ORDER = {"stable": 0, "caution": 1, "warning": 2, "critical": 3}
MIN_REGIMES = ("caution", "warning", "critical", "none")
_MAX_LIST = 20
#: A regime this module does not know ranks above every known one: an escalation event whose severity cannot be placed
#: is told, not dropped. (OBSERVE emits only the four above today; this is for the day it emits a fifth.)
_UNKNOWN_REGIME = 99


@dataclass(frozen=True)
class ClinicalNotificationSettings:
    """What the clinician has decided must reach a person. Every field is adjustable; none is hidden."""
    min_regime: str = "critical"
    notify_on_severity_increase: bool = True
    notify_on_blocked_escalation: bool = True
    notify_on_unassessable: bool = True

    def problems(self) -> List[str]:
        out: List[str] = []
        if self.min_regime not in MIN_REGIMES:
            out.append(f"min_regime must be one of {MIN_REGIMES}, not {self.min_regime!r}")
        for name in ("notify_on_severity_increase", "notify_on_blocked_escalation", "notify_on_unassessable"):
            if not isinstance(getattr(self, name), bool):
                out.append(f"{name} must be True or False, not {getattr(self, name)!r}")
        return out

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def silent(self) -> bool:
        """True when no clinical event can notify anyone (only an unavailable governance verdict still does). A
        clinician may choose it; a deployment should never arrive at it by accident, so constructors say so loudly."""
        return (self.min_regime == "none" and not self.notify_on_severity_increase
                and not self.notify_on_blocked_escalation and not self.notify_on_unassessable)


def notification_reason(settings: ClinicalNotificationSettings, *, regime: str, escalation_required: bool,
                        escalation_kind: str = "", unassessable: bool = False,
                        governance_status: Optional[str] = None) -> Optional[str]:
    """Why a person must be told about this reading, or None.

    Only an escalation EVENT is a reason, with one exception that keeps an old promise: an escalation for which
    governance produced no verdict is told whether or not it was a new event, because a governance layer that is down
    is worth hearing about on every reading it is down for. (A capacity-driven proactive escalation is not an OBSERVE
    event and does not page; callers pass OBSERVE's own `escalation_required`.)"""
    if governance_status == "unavailable":
        return "governance_unavailable"
    if governance_status == "assessment_unavailable":
        return "assessment_unavailable"          # OBSERVE itself failed: nothing was assessed, always told
    if not escalation_required:
        return None
    if governance_status == "blocked" and settings.notify_on_blocked_escalation:
        return "blocked_escalation"
    if unassessable and settings.notify_on_unassessable:
        return "unassessable_reading"
    if escalation_kind == "severity_increase" and settings.notify_on_severity_increase:
        return "severity_increase"
    if settings.min_regime != "none" and _ORDER.get(regime, _UNKNOWN_REGIME) >= _ORDER[settings.min_regime]:
        return "regime_at_or_above_threshold"
    return None


def _limited(items: Optional[Sequence[Any]]) -> List[str]:
    out = [str(x) for x in list(items or [])[:_MAX_LIST]]
    if items is not None and len(items) > _MAX_LIST:
        out.append(f"... and {len(items) - _MAX_LIST} more")
    return out


def ticket_context(*, reason: str, patient_id: Any, regime: str, escalation_kind: str = "", previous_regime: str = "",
                   risk_score: Optional[float] = None, triggered_rules: Optional[Sequence[Any]] = None,
                   unassessable: bool = False, observe_audit_hash: str = "", governance_status: Optional[str] = None,
                   governance_violations: Optional[Sequence[Any]] = None,
                   perceive_audit_hash: Optional[str] = None, record_status: Optional[str] = None) -> Dict[str, Any]:
    """What a person needs to act, and no raw vitals: the patient, the regime and why, what governance said, and the
    audit hashes that let the full record be found. The patient identifier is needed to act and is therefore in the
    ticket log: classify that log accordingly (docs/race/CLINICAL_ESCALATION_NOTIFICATION.md)."""
    context: Dict[str, Any] = {
        "reason": reason, "patient_id": None if patient_id is None else str(patient_id), "regime": regime, "escalation_kind": escalation_kind,
        "previous_regime": previous_regime, "risk_score": risk_score, "triggered_rules": _limited(triggered_rules),
        "unassessable": bool(unassessable), "observe_audit_hash": observe_audit_hash,
    }
    if record_status is not None:
        context["record_status"] = record_status          # e.g. the orchestrator's REJECTED or EXECUTION_FAILED
    if governance_status is not None:
        context.update({"governance_status": governance_status,
                        "governance_violations": _limited(governance_violations),
                        "perceive_audit_hash": perceive_audit_hash})
    return context


def notify(desk: Any, *, context: Mapping[str, Any], request_id: Optional[str] = None,
           artifact_id: Optional[str] = None) -> Tuple[Optional[str], str]:
    """Open a ticket and dispatch it. Returns (ticket_id, status): "delivered" only when the channel returned its own
    receipt, "dispatched" when a callable merely returned, "failed" when nobody could be reached or the desk itself
    failed. Never raises: telling a person must not alter the clinical decision."""
    try:
        ticket_id, state = desk.raise_and_dispatch(kind="clinical_escalation", request_id=request_id,
                                                   artifact_id=artifact_id, context=dict(context))
    except Exception as exc:  # noqa: BLE001
        logger.critical("[ClinicalNotification] the desk failed for patient %s: %s: %s",
                        context.get("patient_id"), type(exc).__name__, exc)
        return None, "failed"
    return ticket_id, {"exhausted": "failed", "delivered": "delivered"}.get(state, "dispatched")
