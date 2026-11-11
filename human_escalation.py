"""human_escalation.py -- telling a person, with evidence of each step.

"A person was told" used to mean the `alert_human` callable returned. This module separates what is known:

  raised -> dispatched -> delivered -> acknowledged      (and overdue -> next tier -> ... -> exhausted)

  dispatched    a channel callable returned without raising. Says nothing about a person.
  delivered     the channel itself returned a delivery receipt id. The channel's claim, not a reading.
  acknowledged  a named responder signed an acknowledgement bound to this ticket. The only way to this state.
  exhausted     every tier was tried and none was dispatched or acknowledged in time. Loud, open, needs a person.

Nothing is invented: no acknowledgement is ever created by this module (only checked), and no time limit has a default.
The policy numbers belong to the hospital. See docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

__all__ = ["AcknowledgementError", "AlertOutcome", "EscalationDesk", "EscalationLog", "EscalationLogError",
           "NotificationPolicy", "Tier", "acknowledgement_digest", "sign_acknowledgement"]

logger = logging.getLogger(__name__)

RAISED, DISPATCHED, DELIVERED, ACKNOWLEDGED, OVERDUE, EXHAUSTED = (
    "raised", "dispatched", "delivered", "acknowledged", "overdue", "exhausted")


class EscalationLogError(ValueError):
    """The escalation log does not verify (broken chain, bad hash, or a signature that does not check)."""


class AcknowledgementError(ValueError):
    """An acknowledgement was refused. Nothing was recorded."""


class AlertOutcome(str):
    """The legacy `human_alert` string ("delivered", "failed", "no_channel"), carrying the ticket it belongs to."""
    ticket_id: Optional[str] = None

    def __new__(cls, text: str, ticket_id: Optional[str] = None):
        obj = super().__new__(cls, text)
        obj.ticket_id = ticket_id
        return obj


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---- the policy: every number is the hospital's -----------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    """One rung. `channel(payload)` reaches people; `responders` are the ids who may acknowledge for this rung;
    `ack_within_s` is how long to wait for an acknowledgement before the next rung. No default for any of them."""
    name: str
    channel: Callable[[Dict[str, Any]], Any]
    responders: Tuple[str, ...]
    ack_within_s: float


@dataclass(frozen=True)
class NotificationPolicy:
    tiers: Tuple[Tier, ...]

    def problems(self) -> List[str]:
        out: List[str] = []
        if not self.tiers:
            return ["the notification policy has no tiers: nobody would be told"]
        seen = set()
        for i, tier in enumerate(self.tiers):
            where = f"tier {i}"
            if not isinstance(tier.name, str) or not tier.name.strip():
                out.append(f"{where} has no name")
            elif tier.name in seen:
                out.append(f"{where}: the name {tier.name!r} is used twice")
            else:
                seen.add(tier.name)
            if not callable(tier.channel):
                out.append(f"{where} has no callable channel")
            if not tier.responders or not all(isinstance(r, str) and r.strip() for r in tier.responders):
                out.append(f"{where} names no responder who may acknowledge")
            secs = tier.ack_within_s
            if isinstance(secs, bool) or not isinstance(secs, (int, float)) or not math.isfinite(secs) or secs <= 0:
                out.append(f"{where} has no usable ack_within_s (needs a positive finite number; there is no default)")
        return out


# ---- the log ----------------------------------------------------------------------------------------------------

def _event_hash(entry: Mapping[str, Any]) -> str:
    return _digest({k: entry[k] for k in ("seq", "previous", "ticket_id", "event", "at", "data")})


class EscalationLog:
    """Append-only, hash-chained events; signed when a signer is given. In memory when there is no path."""

    def __init__(self, path: Optional[str | Path] = None, *, signer=None):
        self.path = None if path is None else Path(path)
        self.signer = signer
        self._entries: List[Dict[str, Any]] = []
        if self.path is not None and self.path.exists():
            self._load()

    def _load(self) -> None:
        previous = ""
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            if entry.get("previous") != previous or entry.get("seq") != len(self._entries):
                raise EscalationLogError(f"{self.path}:{line_no}: chain link broken")
            if _event_hash(entry) != entry.get("hash"):
                raise EscalationLogError(f"{self.path}:{line_no}: event hash does not recompute")
            if self.signer is not None:
                from conservation_kernel.signing import check_signature
                problem = check_signature(self.signer, entry["hash"], entry.get("signature"))
                if problem:
                    raise EscalationLogError(f"{self.path}:{line_no}: {problem}")
            self._entries.append(entry)
            previous = entry["hash"]

    def append(self, ticket_id: str, event: str, at: str, data: Mapping[str, Any]) -> Dict[str, Any]:
        previous = self._entries[-1]["hash"] if self._entries else ""
        entry: Dict[str, Any] = {"seq": len(self._entries), "previous": previous, "ticket_id": ticket_id,
                                 "event": event, "at": at, "data": json.loads(_canonical(data))}
        entry["hash"] = _event_hash(entry)
        if self.signer is not None:
            from conservation_kernel.signing import signature_block
            entry["signature"] = signature_block(self.signer, entry["hash"])
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
            with os.fdopen(os.open(self.path, flags, 0o600), "a", encoding="utf-8") as fh:
                fh.write(_canonical(entry) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        self._entries.append(entry)
        return entry

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)


# ---- acknowledgement --------------------------------------------------------------------------------------------

def acknowledgement_digest(ticket_id: str, opened_hash: str, actor_id: str) -> str:
    """What a responder signs: this ticket (by id and by the hash of its opening event) and this responder."""
    return _digest({"purpose": "escalation-acknowledgement", "ticket_id": ticket_id,
                    "opened_hash": opened_hash, "actor_id": actor_id})


def sign_acknowledgement(signer, ticket_id: str, opened_hash: str, actor_id: str) -> dict:
    """Run on the responder's side: sign an acknowledgement. Only the holder of the key can produce one."""
    from conservation_kernel.signing import signature_block
    return signature_block(signer, acknowledgement_digest(ticket_id, opened_hash, actor_id))


# ---- the desk ---------------------------------------------------------------------------------------------------

@dataclass
class _Ticket:
    ticket_id: str
    opened_hash: str
    kind: str
    request_id: Optional[str]
    artifact_id: Optional[str]
    context: Dict[str, Any]
    state: str = RAISED
    tier_index: int = -1
    dispatched_at: Optional[datetime] = None
    acknowledged_by: Optional[str] = None
    history: List[str] = field(default_factory=list)


class EscalationDesk:
    """Opens tickets, dispatches them tier by tier, checks acknowledgements, and escalates what goes unanswered."""

    def __init__(self, policy: NotificationPolicy, log: EscalationLog, *, responder_keys: Mapping[str, Any],
                 clock: Callable[[], datetime] = _now):
        self.policy = policy
        self.log = log
        self.responder_keys = dict(responder_keys)
        self._clock = clock
        self._lock = threading.RLock()
        self._tickets: Dict[str, _Ticket] = {}
        self._last_sweep: Optional[datetime] = None
        self._replay()

    # -- what the production profile asks --
    def problems(self) -> List[str]:
        out = self.policy.problems()
        for tier in self.policy.tiers:
            for responder in tier.responders:
                if responder not in self.responder_keys:
                    out.append(f"responder {responder!r} (tier {tier.name!r}) has no registered key, so they "
                               "could never acknowledge")
        if self.log.path is None:
            out.append("the escalation log is in memory: tickets would not survive a restart")
        if self.log.signer is None:
            out.append("the escalation log is unsigned: it shows integrity, not who wrote it")
        return out

    # -- reading --
    def state(self, ticket_id: str) -> str:
        return self._tickets[ticket_id].state

    def ticket(self, ticket_id: str) -> Dict[str, Any]:
        t = self._tickets[ticket_id]
        return {"ticket_id": t.ticket_id, "opened_hash": t.opened_hash, "kind": t.kind, "request_id": t.request_id,
                "artifact_id": t.artifact_id, "state": t.state, "tier": self._tier_name(t.tier_index),
                "acknowledged_by": t.acknowledged_by, "history": list(t.history)}

    def open_tickets(self) -> List[str]:
        """Every ticket not yet acknowledged, exhausted ones included: those need a person's recovery."""
        return [i for i, t in self._tickets.items() if t.state != ACKNOWLEDGED]

    def seconds_since_sweep(self) -> Optional[float]:
        """None if `sweep` has never run on this desk. A watchdog should alarm on this."""
        return None if self._last_sweep is None else (self._clock() - self._last_sweep).total_seconds()

    def _tier_name(self, index: int) -> Optional[str]:
        return self.policy.tiers[index].name if 0 <= index < len(self.policy.tiers) else None

    # -- raising and dispatching --
    def raise_and_dispatch(self, *, kind: str, request_id: Optional[str] = None, artifact_id: Optional[str] = None,
                           context: Optional[Mapping[str, Any]] = None) -> Tuple[str, str]:
        """Open a ticket and dispatch it. Returns (ticket_id, state). Never raises for a channel failure."""
        with self._lock:
            ticket_id = f"esc-{len(self.log):06d}"
            ctx = dict(context or {})
            opened = self.log.append(ticket_id, "opened", self._clock().isoformat(),
                                     {"kind": kind, "request_id": request_id, "artifact_id": artifact_id,
                                      "context": ctx})
            t = _Ticket(ticket_id, opened["hash"], kind, request_id, artifact_id, ctx, history=["raised"])
            self._tickets[ticket_id] = t
            self._dispatch_from(t, 0)
            return ticket_id, t.state

    def _payload(self, t: _Ticket, tier: Tier) -> Dict[str, Any]:
        payload = dict(t.context)
        payload.update({"ticket_id": t.ticket_id, "kind": t.kind, "tier": tier.name,
                        "request_id": t.request_id, "artifact_id": t.artifact_id})
        return payload

    def _dispatch_from(self, t: _Ticket, start: int) -> None:
        """Try tiers from `start` until one dispatches; a raising channel moves on at once, with no waiting."""
        for index in range(start, len(self.policy.tiers)):
            tier = self.policy.tiers[index]
            at = self._clock()
            try:
                answer = tier.channel(self._payload(t, tier))
            except Exception as exc:  # noqa: BLE001 - a channel failure must never touch the clinical outcome
                self.log.append(t.ticket_id, "dispatch", at.isoformat(),
                                {"tier": tier.name, "outcome": "failed",
                                 "error": f"{type(exc).__name__}: {exc}"})
                logger.critical("[Escalation] %s: tier %r could not be dispatched (%s: %s)",
                                t.ticket_id, tier.name, type(exc).__name__, exc)
                t.history.append(f"{tier.name}:failed")
                continue
            receipt = answer.get("delivery_receipt") if isinstance(answer, Mapping) else None
            delivered = isinstance(receipt, str) and bool(receipt.strip())
            data = {"tier": tier.name, "outcome": DELIVERED if delivered else DISPATCHED}
            if delivered:
                data["delivery_receipt"] = receipt
            self.log.append(t.ticket_id, "dispatch", at.isoformat(), data)
            t.tier_index, t.dispatched_at, t.state = index, at, data["outcome"]
            t.history.append(f"{tier.name}:{data['outcome']}")
            return
        self.log.append(t.ticket_id, "exhausted", self._clock().isoformat(),
                        {"reason": "no tier could be dispatched" if t.dispatched_at is None
                         else "no further tier after the last deadline"})
        t.state = EXHAUSTED
        t.history.append("exhausted")
        logger.critical("[Escalation] %s EXHAUSTED: nobody could be reached through any tier", t.ticket_id)

    # -- acknowledging --
    def acknowledge(self, ticket_id: str, actor_id: str, signature: Mapping[str, Any], note: str = "") -> str:
        """Record a responder's signed acknowledgement. Refuses (and records nothing) unless every check holds."""
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                raise AcknowledgementError(f"no such ticket {ticket_id!r}")
            if t.state == ACKNOWLEDGED:
                raise AcknowledgementError(f"{ticket_id} is already acknowledged by {t.acknowledged_by}")
            if t.dispatched_at is None:
                raise AcknowledgementError(f"{ticket_id} was never dispatched; there is nothing to acknowledge")
            reached = {r for tier in self.policy.tiers[: t.tier_index + 1] for r in tier.responders}
            if actor_id not in reached:
                raise AcknowledgementError(f"{actor_id!r} is not a responder on any tier this ticket has reached")
            signer = self.responder_keys.get(actor_id)
            if signer is None:
                raise AcknowledgementError(f"{actor_id!r} has no registered key")
            from conservation_kernel.signing import check_signature
            problem = check_signature(signer, acknowledgement_digest(ticket_id, t.opened_hash, actor_id), signature)
            if problem:
                raise AcknowledgementError(f"signature refused: {problem}")
            self.log.append(ticket_id, "acknowledged", self._clock().isoformat(),
                            {"actor_id": actor_id, "note": note, "signature": dict(signature)})
            t.state, t.acknowledged_by = ACKNOWLEDGED, actor_id
            t.history.append(f"acknowledged:{actor_id}")
            return t.state

    # -- sweeping --
    def sweep(self) -> List[Tuple[str, str]]:
        """Escalate every unacknowledged ticket whose deadline has passed. Call from a scheduler.
        Returns [(ticket_id, new_state)] for tickets that changed."""
        with self._lock:
            now = self._clock()
            self._last_sweep = now
            changed: List[Tuple[str, str]] = []
            for t in self._tickets.values():
                if t.state not in (DISPATCHED, DELIVERED) or t.dispatched_at is None:
                    continue
                tier = self.policy.tiers[t.tier_index]
                if now < t.dispatched_at + timedelta(seconds=tier.ack_within_s):
                    continue
                self.log.append(t.ticket_id, "overdue", now.isoformat(), {"tier": tier.name})
                t.state = OVERDUE
                t.history.append(f"{tier.name}:overdue")
                self._dispatch_from(t, t.tier_index + 1)
                changed.append((t.ticket_id, t.state))
            return changed

    # -- rebuilding from the log --
    def _replay(self) -> None:
        for e in self.log.entries:
            tid, data = e["ticket_id"], e["data"]
            if e["event"] == "opened":
                self._tickets[tid] = _Ticket(tid, e["hash"], data["kind"], data.get("request_id"),
                                             data.get("artifact_id"), data.get("context") or {}, history=["raised"])
                continue
            t = self._tickets.get(tid)
            if t is None:
                raise EscalationLogError(f"event {e['seq']} refers to unknown ticket {tid!r}")
            if e["event"] == "dispatch":
                names = [x.name for x in self.policy.tiers]
                if data["tier"] not in names:
                    raise EscalationLogError(f"event {e['seq']} names tier {data['tier']!r}, which the current "
                                             "policy does not have; resolve open tickets before renaming a tier")
                t.history.append(f"{data['tier']}:{data['outcome']}")
                if data["outcome"] != "failed":
                    t.tier_index = names.index(data["tier"])
                    t.dispatched_at = datetime.fromisoformat(e["at"])
                    t.state = data["outcome"]
            elif e["event"] == "overdue":
                t.state = OVERDUE
                t.history.append(f"{data['tier']}:overdue")
            elif e["event"] == "exhausted":
                t.state = EXHAUSTED
                t.history.append("exhausted")
            elif e["event"] == "acknowledged":
                t.state, t.acknowledged_by = ACKNOWLEDGED, data["actor_id"]
                t.history.append(f"acknowledged:{data['actor_id']}")
