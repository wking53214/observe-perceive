"""human_escalation.py -- telling a person, with evidence of each step.

"A person was told" used to mean the `alert_human` callable returned. This module separates what is known:

  raised -> dispatched -> delivered -> acknowledged      (and overdue -> next tier -> ... -> exhausted)

  dispatched    a channel callable returned without raising. Says nothing about a person.
  delivered     the channel itself returned a delivery receipt id. The channel's claim, not a reading.
  acknowledged  a named responder signed an acknowledgement bound to this ticket. The only way to this state.
  exhausted     every tier was tried and none was dispatched or acknowledged in time. Loud, open, needs a person.

Nothing is invented: no acknowledgement is ever created by this module (only checked), and no time limit has a
default. The policy numbers belong to the hospital.

The direction of every failure is toward telling someone. A log that cannot be written never stops a page (the page is
sent, the ticket is marked unlogged, and the failure is counted and logged at CRITICAL). A channel that raises or hangs
past its timeout is skipped at once. A ticket stranded by a crash, between tiers or before its first dispatch, is picked
up again by the next `sweep()`. The price is that a page may be sent twice; that is chosen over a page not sent.
See docs/race/HUMAN_ESCALATION_AND_RECEIPT_MODEL.md.
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
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

try:  # POSIX only; the single-writer guard is skipped elsewhere and the docs say so
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

__all__ = ["AcknowledgementError", "AlertOutcome", "EscalationDesk", "EscalationLog", "EscalationLogError",
           "NotificationPolicy", "Tier", "acknowledgement_digest", "sign_acknowledgement"]

logger = logging.getLogger(__name__)

RAISED, DISPATCHED, DELIVERED, ACKNOWLEDGED, OVERDUE, EXHAUSTED = (
    "raised", "dispatched", "delivered", "acknowledged", "overdue", "exhausted")


class EscalationLogError(ValueError):
    """The escalation log does not verify (broken chain, bad hash, bad signature, or another writer holds it)."""


class AcknowledgementError(ValueError):
    """An acknowledgement was refused. Nothing was recorded."""


class AlertOutcome(str):
    """The `human_alert` string for a desk-backed alert, carrying the ticket it belongs to."""
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


def _positive_finite(value: Any) -> bool:
    return (not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value > 0)


# ---- the policy: every number is the hospital's -----------------------------------------------------------------

@dataclass(frozen=True)
class Tier:
    """One rung. `channel(payload)` reaches people; `responders` are the ids who may acknowledge for this rung;
    `ack_within_s` is how long to wait for an acknowledgement before the next rung; `dispatch_timeout_s` is how long
    the channel call itself may take before it counts as failed (a hung pager must not hold up a clinical verdict).
    No default for any of them."""
    name: str
    channel: Callable[[Dict[str, Any]], Any]
    responders: Tuple[str, ...]
    ack_within_s: float
    dispatch_timeout_s: float


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
            if not _positive_finite(tier.ack_within_s):
                out.append(f"{where} has no usable ack_within_s (needs a positive finite number; there is no default)")
            if not _positive_finite(tier.dispatch_timeout_s):
                out.append(f"{where} has no usable dispatch_timeout_s (needs a positive finite number; there is no "
                           "default)")
        return out


# ---- the log ----------------------------------------------------------------------------------------------------

def _event_hash(entry: Mapping[str, Any]) -> str:
    return _digest({k: entry[k] for k in ("seq", "previous", "ticket_id", "event", "at", "data")})


class EscalationLog:
    """Append-only, hash-chained events; signed when a signer is given. In memory when there is no path.

    One writer at a time: a second process opening the same path is refused (POSIX advisory lock on `<path>.lock`),
    because two writers would reuse ticket ids and break the chain. Call `close()` to release it.
    A torn final line (a crash mid-write) is moved to `<path>.torn` and the log resumes from the last whole event;
    a bad line anywhere else is an error."""

    def __init__(self, path: Optional[str | Path] = None, *, signer=None):
        self.path = None if path is None else Path(path)
        self.signer = signer
        self._entries: List[Dict[str, Any]] = []
        self._guard = None
        #: Set when the lock file could not even be created (an unwritable directory). `probe()` then raises it, so
        #: the production profile reports it as a problem instead of the process dying in a constructor.
        self.lock_error: Optional[OSError] = None
        if self.path is not None:
            self._take_writer_lock()
            try:
                if self.path.exists():
                    self._load()
            except BaseException:
                self.close()
                raise

    def _take_writer_lock(self) -> None:
        if fcntl is None:  # pragma: no cover
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(str(self.path) + ".lock", "a")  # noqa: SIM115 - held for the life of the log
        except OSError as exc:
            self.lock_error = exc
            return
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise EscalationLogError(f"{self.path}: another writer holds this log; two writers would reuse "
                                     "ticket ids and break the chain") from None
        self._guard = handle

    def close(self) -> None:
        if self._guard is not None:
            self._guard.close()
            self._guard = None

    def _load(self) -> None:
        raw = self.path.read_bytes()
        lines = raw.split(b"\n")
        torn: Optional[bytes] = None
        if lines and lines[-1] == b"":
            lines.pop()
        elif lines:
            torn = lines.pop()                                              # no newline after it: written in full?
            try:
                json.loads(torn.decode("utf-8"))
                lines.append(torn)                                          # whole, only the newline was lost
                torn = None
                with open(self.path, "ab") as fh:
                    fh.write(b"\n")
            except ValueError:
                pass
        previous = ""
        for line_no, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line.decode("utf-8"))
            except ValueError:
                raise EscalationLogError(f"{self.path}:{line_no}: line is not valid JSON") from None
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
        if torn is not None:
            keep = b"".join(line + b"\n" for line in lines)
            Path(str(self.path) + ".torn").write_bytes(torn)
            self.path.write_bytes(keep)
            logger.critical("[Escalation] %s ended in a torn line (a crash mid-write); it was moved to %s.torn and "
                            "the log resumes from the last whole event", self.path, self.path)

    def probe(self) -> None:
        """Prove the log can take an append now. Raises whatever the OS raises."""
        if self.path is None:
            return
        if self.lock_error is not None:
            raise self.lock_error
        self.path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.path, flags, 0o600), "a", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())

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
    """What a responder signs: this ticket (by id and by the hash of its opening) and this responder."""
    return _digest({"purpose": "escalation-acknowledgement", "ticket_id": ticket_id,
                    "opened_hash": opened_hash, "actor_id": actor_id})


def sign_acknowledgement(signer, ticket_id: str, opened_hash: str, actor_id: str) -> dict:
    """Run on the responder's side: sign an acknowledgement. Only the holder of the key can produce one.
    `ticket_id` and `opened_hash` are in the payload every channel receives."""
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
    reached: Set[str] = field(default_factory=set)
    history: List[str] = field(default_factory=list)
    logged: bool = True
    busy: bool = False


def _opened_hash(ticket_id: str, kind: str, request_id, artifact_id, context, at: str) -> str:
    return _digest({"ticket_id": ticket_id, "kind": kind, "request_id": request_id, "artifact_id": artifact_id,
                    "context": context, "at": at})


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
        self._next_id = 0
        self._last_clean_sweep: Optional[datetime] = None
        #: Events that could not be written. Non-zero means the log under-reports; page-sending was not affected.
        self.log_failures = 0
        try:
            self._replay()
        except BaseException:
            self.log.close()                                                # a desk that cannot start frees its log
            raise

    # -- what the production profile asks --
    def problems(self) -> List[str]:
        out = self.policy.problems()
        for tier in self.policy.tiers:
            for responder in tier.responders:
                key = self.responder_keys.get(responder)
                if key is None:
                    out.append(f"responder {responder!r} (tier {tier.name!r}) has no registered key, so they "
                               "could never acknowledge")
                elif not callable(getattr(key, "verify", None)):
                    out.append(f"responder {responder!r} has a key that cannot verify a signature")
        if self.log.path is None:
            out.append("the escalation log is in memory: tickets would not survive a restart")
        else:
            try:
                self.log.probe()
            except Exception as exc:  # noqa: BLE001
                out.append(f"the escalation log cannot be written here: {type(exc).__name__}: {exc}")
        if self.log.signer is None:
            out.append("the escalation log is unsigned: it shows integrity, not who wrote it")
        return out

    # -- reading --
    def state(self, ticket_id: str) -> str:
        with self._lock:
            return self._tickets[ticket_id].state

    def ticket(self, ticket_id: str) -> Dict[str, Any]:
        with self._lock:
            t = self._tickets[ticket_id]
            return {"ticket_id": t.ticket_id, "opened_hash": t.opened_hash, "kind": t.kind,
                    "request_id": t.request_id, "artifact_id": t.artifact_id, "state": t.state,
                    "tier": self._tier_name(t.tier_index), "acknowledged_by": t.acknowledged_by,
                    "history": list(t.history), "logged": t.logged}

    def open_tickets(self) -> List[str]:
        """Every ticket not yet acknowledged, exhausted ones included: those need a person's recovery."""
        with self._lock:
            return [i for i, t in self._tickets.items() if t.state != ACKNOWLEDGED]

    def seconds_since_sweep(self) -> Optional[float]:
        """Seconds since the last sweep that finished without an error; None if there has been none. A watchdog
        should alarm on this: it only advances when every ticket was handled."""
        with self._lock:
            return None if self._last_clean_sweep is None else (self._clock() - self._last_clean_sweep).total_seconds()

    def _tier_name(self, index: int) -> Optional[str]:
        return self.policy.tiers[index].name if 0 <= index < len(self.policy.tiers) else None

    # -- logging that never stops a page --
    def _log(self, t: _Ticket, event: str, at: str, data: Mapping[str, Any]) -> None:
        try:
            self.log.append(t.ticket_id, event, at, data)
        except Exception as exc:  # noqa: BLE001 - the page matters more than its record
            t.logged = False
            self.log_failures += 1
            logger.critical("[Escalation] %s: the %r event could not be logged (%s: %s); the page is not held up",
                            t.ticket_id, event, type(exc).__name__, exc)

    # -- raising and dispatching --
    def raise_and_dispatch(self, *, kind: str, request_id: Optional[str] = None, artifact_id: Optional[str] = None,
                           context: Optional[Mapping[str, Any]] = None) -> Tuple[str, str]:
        """Open a ticket and dispatch it. Returns (ticket_id, state). A failing channel or log never raises."""
        with self._lock:
            ticket_id = f"esc-{self._next_id:06d}"
            self._next_id += 1
            ctx = json.loads(_canonical(context or {}))
            at = self._clock().isoformat()
            opened = _opened_hash(ticket_id, kind, request_id, artifact_id, ctx, at)
            t = _Ticket(ticket_id, opened, kind, request_id, artifact_id, ctx, history=["raised"], busy=True)
            self._tickets[ticket_id] = t
            self._log(t, "opened", at, {"kind": kind, "request_id": request_id, "artifact_id": artifact_id,
                                        "context": ctx, "opened_hash": opened})
        try:
            self._dispatch_from(t, 0)
        finally:
            with self._lock:
                t.busy = False
        return ticket_id, t.state

    def _payload(self, t: _Ticket, tier: Tier) -> Dict[str, Any]:
        payload = dict(t.context)
        payload.update({"ticket_id": t.ticket_id, "opened_hash": t.opened_hash, "kind": t.kind, "tier": tier.name,
                        "request_id": t.request_id, "artifact_id": t.artifact_id})
        return payload

    @staticmethod
    def _call(tier: Tier, payload: Dict[str, Any]) -> Any:
        """Run the channel on its own thread so a hung pager costs `dispatch_timeout_s`, not the whole verdict."""
        box: Dict[str, Any] = {}

        def run() -> None:
            try:
                box["answer"] = tier.channel(payload)
            except BaseException as exc:  # noqa: BLE001
                box["error"] = exc

        worker = threading.Thread(target=run, name=f"escalation-{tier.name}", daemon=True)
        worker.start()
        worker.join(tier.dispatch_timeout_s)
        if worker.is_alive():
            raise TimeoutError(f"channel did not return within {tier.dispatch_timeout_s}s")
        if "error" in box:
            raise box["error"]
        return box.get("answer")

    def _dispatch_from(self, t: _Ticket, start: int) -> None:
        """Try tiers from `start` until one dispatches. Runs WITHOUT the desk lock held, so a slow channel blocks
        nobody else. A raising or timed-out channel moves on at once."""
        for index in range(start, len(self.policy.tiers)):
            tier = self.policy.tiers[index]
            at = self._clock()
            try:
                answer = self._call(tier, self._payload(t, tier))
            except Exception as exc:  # noqa: BLE001 - a channel failure must never touch the clinical outcome
                with self._lock:
                    self._log(t, "dispatch", at.isoformat(), {"tier": tier.name, "outcome": "failed",
                                                              "error": f"{type(exc).__name__}: {exc}"})
                    t.history.append(f"{tier.name}:failed")
                logger.critical("[Escalation] %s: tier %r could not be dispatched (%s: %s)",
                                t.ticket_id, tier.name, type(exc).__name__, exc)
                continue
            receipt = answer.get("delivery_receipt") if isinstance(answer, Mapping) else None
            delivered = isinstance(receipt, str) and bool(receipt.strip())
            data = {"tier": tier.name, "outcome": DELIVERED if delivered else DISPATCHED}
            if delivered:
                data["delivery_receipt"] = receipt
            with self._lock:
                self._log(t, "dispatch", at.isoformat(), data)
                t.reached.add(tier.name)
                if t.state != ACKNOWLEDGED:
                    t.tier_index, t.dispatched_at, t.state = index, at, data["outcome"]
                t.history.append(f"{tier.name}:{data['outcome']}")
            return
        with self._lock:
            if t.state in (ACKNOWLEDGED, EXHAUSTED):
                return
            self._log(t, "exhausted", self._clock().isoformat(),
                      {"reason": "no tier could be dispatched" if not t.reached else "no further tier"})
            t.state = EXHAUSTED
            t.history.append("exhausted")
        logger.critical("[Escalation] %s EXHAUSTED: %s", t.ticket_id,
                        "nobody could be reached through any tier" if not t.reached else "no tier left to try")

    # -- acknowledging --
    def acknowledge(self, ticket_id: str, actor_id: str, signature: Mapping[str, Any], note: str = "") -> str:
        """Record a responder's signed acknowledgement. Refuses (and records nothing) unless every check holds."""
        with self._lock:
            t = self._tickets.get(ticket_id)
            if t is None:
                raise AcknowledgementError(f"no such ticket {ticket_id!r}")
            if t.state == ACKNOWLEDGED:
                raise AcknowledgementError(f"{ticket_id} is already acknowledged by {t.acknowledged_by}")
            if not t.reached:
                raise AcknowledgementError(f"{ticket_id} was never dispatched; there is nothing to acknowledge")
            allowed = {r for tier in self.policy.tiers if tier.name in t.reached for r in tier.responders}
            if actor_id not in allowed:
                raise AcknowledgementError(f"{actor_id!r} is not a responder on any tier that reached this ticket")
            signer = self.responder_keys.get(actor_id)
            if signer is None:
                raise AcknowledgementError(f"{actor_id!r} has no registered key")
            from conservation_kernel.signing import check_signature
            problem = check_signature(signer, acknowledgement_digest(ticket_id, t.opened_hash, actor_id), signature)
            if problem:
                raise AcknowledgementError(f"signature refused: {problem}")
            self._log(t, "acknowledged", self._clock().isoformat(),
                      {"actor_id": actor_id, "note": note, "signature": dict(signature)})
            t.state, t.acknowledged_by = ACKNOWLEDGED, actor_id
            t.history.append(f"acknowledged:{actor_id}")
            return t.state

    # -- sweeping --
    def sweep(self) -> List[Tuple[str, str]]:
        """Move every ticket that needs it. Call from a scheduler (the desk has no timer of its own).

        Handled: a dispatched or delivered ticket past its deadline (to the next tier); a ticket stranded in
        `overdue` or `raised` by a crash (picked up again at once); a ticket nobody could ever be reached for (tried
        again, since a channel may have come back). One ticket failing never skips the others.
        Returns [(ticket_id, new_state)] for tickets that changed."""
        changed: List[Tuple[str, str]] = []
        clean = True
        with self._lock:
            now = self._clock()
            work: List[Tuple[_Ticket, int]] = []
            for t in self._tickets.values():
                if t.busy or t.state == ACKNOWLEDGED:
                    continue
                if t.state in (DISPATCHED, DELIVERED) and t.dispatched_at is not None:
                    tier = self.policy.tiers[t.tier_index]
                    if now < t.dispatched_at + timedelta(seconds=tier.ack_within_s):
                        continue
                    start = t.tier_index + 1
                    t.state = OVERDUE
                    t.history.append(f"{tier.name}:overdue")
                    self._log(t, "overdue", now.isoformat(), {"tier": tier.name})
                elif t.state == OVERDUE:
                    start = t.tier_index + 1
                elif t.state == RAISED or (t.state == EXHAUSTED and not t.reached):
                    start = 0
                else:
                    continue
                t.busy = True
                work.append((t, start))
        for t, start in work:
            try:
                before = t.state
                self._dispatch_from(t, start)
                if t.state != before or t.state in (DISPATCHED, DELIVERED):
                    changed.append((t.ticket_id, t.state))
            except Exception as exc:  # noqa: BLE001 - one ticket must not skip the rest
                clean = False
                logger.critical("[Escalation] %s: sweep failed for this ticket (%s: %s)", t.ticket_id,
                                type(exc).__name__, exc)
            finally:
                with self._lock:
                    t.busy = False
        if clean:
            with self._lock:
                self._last_clean_sweep = self._clock()
        return changed

    # -- rebuilding from the log --
    def _replay(self) -> None:
        names = [x.name for x in self.policy.tiers]
        for e in self.log.entries:
            tid, data = e["ticket_id"], e["data"]
            if e["event"] == "opened":
                expect = _opened_hash(tid, data["kind"], data.get("request_id"), data.get("artifact_id"),
                                      data.get("context") or {}, e["at"])
                if data.get("opened_hash") != expect:
                    raise EscalationLogError(f"event {e['seq']}: the opening of {tid} does not match its hash")
                self._tickets[tid] = _Ticket(tid, expect, data["kind"], data.get("request_id"),
                                             data.get("artifact_id"), data.get("context") or {}, history=["raised"])
                if tid.startswith("esc-") and tid[4:].isdigit():
                    self._next_id = max(self._next_id, int(tid[4:]) + 1)
                continue
            t = self._tickets.get(tid)
            if t is None:
                raise EscalationLogError(f"event {e['seq']} refers to unknown ticket {tid!r}")
            if e["event"] == "dispatch":
                if data["tier"] not in names:
                    raise EscalationLogError(f"event {e['seq']} names tier {data['tier']!r}, which the current "
                                             "policy does not have; resolve open tickets before renaming a tier")
                t.history.append(f"{data['tier']}:{data['outcome']}")
                if data["outcome"] != "failed":
                    t.reached.add(data["tier"])
                    if t.state != ACKNOWLEDGED:
                        t.tier_index = names.index(data["tier"])
                        t.dispatched_at = datetime.fromisoformat(e["at"])
                        t.state = data["outcome"]
            elif e["event"] == "overdue":
                if t.state != ACKNOWLEDGED:
                    t.state = OVERDUE
                t.history.append(f"{data['tier']}:overdue")
            elif e["event"] == "exhausted":
                if t.state != ACKNOWLEDGED:
                    t.state = EXHAUSTED
                t.history.append("exhausted")
            elif e["event"] == "acknowledged":
                signer = self.responder_keys.get(data["actor_id"])
                if signer is None:
                    raise EscalationLogError(f"event {e['seq']}: acknowledged by {data['actor_id']!r}, who has no key")
                from conservation_kernel.signing import check_signature
                if check_signature(signer, acknowledgement_digest(tid, t.opened_hash, data["actor_id"]),
                                   data.get("signature")):
                    raise EscalationLogError(f"event {e['seq']}: the acknowledgement of {tid} does not verify")
                t.state, t.acknowledged_by = ACKNOWLEDGED, data["actor_id"]
                t.history.append(f"acknowledged:{data['actor_id']}")
