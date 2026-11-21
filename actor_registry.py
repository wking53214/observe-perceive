"""actor_registry.py -- who may act, granted, delegated and revoked by people who can prove they may.

The rest of this package can prove that a record is consistent. It cannot prove that the person behind a ruling was
entitled to make it: the escalation desk takes a fixed map of responder keys, and the kernel takes a fixed set of
trusted humans. This module is the lifecycle those fixed maps lack.

WHAT IT RECORDS

Three kinds of event, each signed by the actor who makes it, each hash-chained to the one before:

  grant      an admin gives `actor_id` a `role`, bound to a key the deployer holds (`key_id`), optionally until a time.
  delegate   an actor who holds a role by direct grant lends it to another actor, always until a time no later than
             their own grant ends. A delegated role cannot be delegated again, and `registry:admin` cannot be delegated.
  revoke     an admin, or the actor who delegated it, takes a role back. Effective from the moment it is recorded.

A delegated role ends when the delegator stops holding the role, so revoking a delegator revokes what they lent.

WHAT MAKES IT TRUSTWORTHY

* Every event is signed by the acting actor over the chain head it was made against, so a signed event cannot be
  replayed later or into another registry. Use `sign_registry_action` on the actor's side.
* The roots (the actors who hold `registry:admin` without a grant) are named at construction. There is no default
  root and no default key. Changing the roots is a redeploy, made in review, not an event.
* The log carries key identifiers, never key material. Keys are passed in by the deployer.
* On start the whole log is replayed: every hash, every signature and every authority is checked again. A registry
  that cannot prove its own history does not start (`RegistryError`).

WHAT IT DOES NOT DO

It does not prove that a key is held by the person it names; that is the deployer's key handling. With HMAC keys the
registry holds the same secret as the actor and could sign in their name; an asymmetric signer closes that and is
accepted wherever `conservation_kernel.signing` accepts one. It guards one writer per process; two processes writing
one log are not coordinated. Key rotation is not modelled: an actor keeps the key their first grant bound.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional

__all__ = ["ADMIN", "ActorRegistry", "RegistryAuthority", "RegistryError", "registry_action_digest", "sign_registry_action"]

#: The role that may grant and revoke. Held by the roots without a grant; may be granted, never delegated.
ADMIN = "registry:admin"

_KINDS = ("grant", "delegate", "revoke")


class RegistryError(ValueError):
    """An action was refused, or the registry's history could not be proved. Nothing was recorded."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse(at: Any, what: str) -> Optional[datetime]:
    if at is None:
        return None
    try:
        parsed = at if isinstance(at, datetime) else datetime.fromisoformat(str(at))
    except ValueError as exc:
        raise RegistryError(f"{what} is not an ISO time: {at!r}") from exc
    if parsed.tzinfo is None:
        raise RegistryError(f"{what} has no time zone: {at!r}")
    return parsed


def registry_action_digest(head: str, kind: str, by: str, body: Mapping[str, Any]) -> str:
    """What the acting actor signs: this action, by this actor, made against this chain head."""
    return _digest({"purpose": "actor-registry", "head": head, "kind": kind, "by": by, "body": dict(body)})


def sign_registry_action(signer, head: str, kind: str, by: str, body: Mapping[str, Any]) -> dict:
    """Run on the acting actor's side. `head` is `ActorRegistry.head` when the action is made."""
    from conservation_kernel.signing import signature_block
    return signature_block(signer, registry_action_digest(head, kind, by, body))


@dataclass(frozen=True)
class _Holding:
    actor_id: str
    role: str
    key_id: str
    granted_at: datetime
    expires_at: Optional[datetime]
    delegated_by: Optional[str]
    revoked_at: Optional[datetime] = None


class RegistryAuthority:
    """`authority(actor_id) -> bool` backed by a registry and a role. Identifiable, so a production check can tell it
    from any other callable and ask the registry about every responder."""

    def __init__(self, registry: "ActorRegistry", role: str):
        self.registry = registry
        self.role = role

    def __call__(self, actor_id: str) -> bool:
        return self.registry.holds(actor_id, self.role)


class ActorRegistry:
    """The signed, replayable record of who holds which role, and since and until when."""

    def __init__(self, *, roots: Mapping[str, str], keys: Mapping[str, Any], path: Optional[str | Path] = None,
                 clock: Callable[[], datetime] = _now):
        if not roots:
            raise RegistryError("no roots: someone must hold registry:admin without a grant, and none is assumed")
        missing = sorted(key_id for key_id in roots.values() if key_id not in keys)
        if missing:
            raise RegistryError(f"no key was given for root key id(s) {missing}")
        self.roots = dict(roots)
        self.keys = dict(keys)
        self.path = Path(path) if path is not None else None
        self._clock = clock
        self._lock = threading.RLock()
        self._events: List[Dict[str, Any]] = []
        self._holdings: List[_Holding] = []
        self._bound_keys: Dict[str, str] = dict(self.roots)
        if self.path is not None and self.path.exists():
            self._replay()

    # -- reading ---------------------------------------------------------------------------------------------------

    @property
    def head(self) -> str:
        """The hash of the last event, or '' for an empty registry. Every action is signed against it."""
        with self._lock:
            return self._events[-1]["hash"] if self._events else ""

    @property
    def events(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [dict(e) for e in self._events]

    def why_not(self, actor_id: str, role: str, at: Any = None) -> Optional[str]:
        """Why `actor_id` does not hold `role` at `at` (default now); None if they do."""
        when = _parse(at, "at") or self._clock()
        with self._lock:
            if role == ADMIN and actor_id in self.roots:
                return None
            reasons = []
            for h in self._holdings:
                if h.actor_id != actor_id or h.role != role or h.granted_at > when:
                    continue
                problem = self._holding_problem(h, when)
                if problem is None:
                    return None
                reasons.append(problem)
            return "; ".join(reasons) if reasons else f"{actor_id!r} was never given {role!r}"

    def holds(self, actor_id: str, role: str, at: Any = None) -> bool:
        return self.why_not(actor_id, role, at) is None

    def authority(self, role: str) -> "RegistryAuthority":
        """A check another component can call: does this actor hold `role` right now?"""
        return RegistryAuthority(self, role)

    def key_id_of(self, actor_id: str) -> Optional[str]:
        with self._lock:
            return self._bound_keys.get(actor_id)

    def responder_keys(self, role: str) -> Dict[str, Any]:
        """{actor_id: key} for everyone who holds `role` now. A snapshot: pair it with `authority(role)`."""
        with self._lock:
            actors = {h.actor_id for h in self._holdings if h.role == role}
            if role == ADMIN:
                actors |= set(self.roots)
            return {a: self.keys[self._bound_keys[a]] for a in sorted(actors) if self.holds(a, role)}

    def _holding_problem(self, h: _Holding, when: datetime) -> Optional[str]:
        if h.revoked_at is not None and h.revoked_at <= when:
            return f"{h.role!r} was revoked from {h.actor_id!r} at {h.revoked_at.isoformat()}"
        if h.expires_at is not None and h.expires_at <= when:
            return f"{h.actor_id!r}'s {h.role!r} expired at {h.expires_at.isoformat()}"
        if h.delegated_by is not None and not self._holds_directly(h.delegated_by, h.role, when):
            return f"{h.actor_id!r}'s {h.role!r} was lent by {h.delegated_by!r}, who no longer holds it"
        return None

    def _holds_directly(self, actor_id: str, role: str, when: datetime) -> bool:
        if role == ADMIN and actor_id in self.roots:
            return True
        return any(h.actor_id == actor_id and h.role == role and h.delegated_by is None and h.granted_at <= when
                   and self._holding_problem(h, when) is None for h in self._holdings)

    def _direct_expiry(self, actor_id: str, role: str, when: datetime) -> Optional[datetime]:
        """The latest end among this actor's live direct grants of `role`; None means one has no end."""
        ends = [h.expires_at for h in self._holdings if h.actor_id == actor_id and h.role == role
                and h.delegated_by is None and self._holding_problem(h, when) is None]
        return None if any(e is None for e in ends) else max(ends)

    # -- acting ----------------------------------------------------------------------------------------------------

    def grant(self, actor_id: str, role: str, key_id: str, *, by: str, signature: Mapping[str, Any],
              expires_at: Any = None) -> Dict[str, Any]:
        body = {"actor_id": actor_id, "role": role, "key_id": key_id,
                "expires_at": None if expires_at is None else _parse(expires_at, "expires_at").isoformat()}
        return self._act("grant", by, body, signature)

    def delegate(self, actor_id: str, role: str, key_id: str, *, by: str, signature: Mapping[str, Any],
                 expires_at: Any) -> Dict[str, Any]:
        if expires_at is None:
            raise RegistryError("a delegation must end: give expires_at")
        body = {"actor_id": actor_id, "role": role, "key_id": key_id,
                "expires_at": _parse(expires_at, "expires_at").isoformat()}
        return self._act("delegate", by, body, signature)

    def revoke(self, actor_id: str, role: str, *, by: str, signature: Mapping[str, Any], reason: str) -> Dict[str, Any]:
        if not str(reason or "").strip():
            raise RegistryError("a revocation must say why")
        return self._act("revoke", by, {"actor_id": actor_id, "role": role, "reason": reason}, signature)

    def _act(self, kind: str, by: str, body: Dict[str, Any], signature: Mapping[str, Any]) -> Dict[str, Any]:
        with self._lock:
            at = self._clock()
            self._check(kind, by, body, signature, self.head, at)
            event = {"kind": kind, "by": by, "at": at.isoformat(), "body": body, "signature": dict(signature),
                     "previous": self.head}
            event["hash"] = _digest({k: v for k, v in event.items() if k != "hash"})
            if self.path is not None:
                self._write(event)
            self._apply(event)
            return dict(event)

    def _check(self, kind: str, by: str, body: Mapping[str, Any], signature: Mapping[str, Any], head: str,
               at: datetime) -> None:
        """Every reason this action may not be recorded. Raises RegistryError; changes nothing."""
        if kind not in _KINDS:
            raise RegistryError(f"unknown action {kind!r}")
        key_id = self._bound_keys.get(by)
        if key_id is None or key_id not in self.keys:
            raise RegistryError(f"{by!r} has no registered key")
        from conservation_kernel.signing import check_signature
        problem = check_signature(self.keys[key_id], registry_action_digest(head, kind, by, body), signature)
        if problem:
            raise RegistryError(f"signature by {by!r} refused: {problem}")
        actor_id, role = body.get("actor_id"), body.get("role")
        if not actor_id or not role:
            raise RegistryError("an action names an actor and a role")
        if kind == "revoke":
            lent = any(h.actor_id == actor_id and h.role == role and h.delegated_by == by and h.revoked_at is None
                       for h in self._holdings)
            if not lent and not self._holds_directly(by, ADMIN, at):
                raise RegistryError(f"{by!r} may not revoke: not an admin, and did not lend {role!r} to {actor_id!r}")
            if not any(h.actor_id == actor_id and h.role == role and h.revoked_at is None for h in self._holdings):
                raise RegistryError(f"{actor_id!r} has no unrevoked {role!r} to revoke")
            return
        if actor_id == by:
            raise RegistryError(f"{by!r} may not {kind} a role to themselves")
        new_key = body.get("key_id")
        if new_key not in self.keys:
            raise RegistryError(f"no key was given for key id {new_key!r}")
        bound = self._bound_keys.get(actor_id)
        if bound is not None and bound != new_key:
            raise RegistryError(f"{actor_id!r} is bound to key {bound!r}, not {new_key!r} (rotation is not modelled)")
        if any(other != actor_id and k == new_key for other, k in self._bound_keys.items()):
            raise RegistryError(f"key {new_key!r} is already bound to another actor")
        expires = _parse(body.get("expires_at"), "expires_at")
        if expires is not None and expires <= at:
            raise RegistryError("expires_at is not in the future")
        if kind == "grant":
            if not self._holds_directly(by, ADMIN, at):
                raise RegistryError(f"{by!r} does not hold {ADMIN!r} and may not grant")
            return
        if role == ADMIN:
            raise RegistryError(f"{ADMIN!r} cannot be delegated")
        if not self._holds_directly(by, role, at):
            raise RegistryError(f"{by!r} does not hold {role!r} by direct grant and may not delegate it")
        ceiling = self._direct_expiry(by, role, at)
        if ceiling is not None and expires > ceiling:
            raise RegistryError(f"a delegation may not outlast the delegator's own grant (ends {ceiling.isoformat()})")

    def _apply(self, event: Dict[str, Any]) -> None:
        body, at = event["body"], _parse(event["at"], "at")
        if event["kind"] == "revoke":
            self._holdings = [
                _Holding(**{**h.__dict__, "revoked_at": at})
                if h.actor_id == body["actor_id"] and h.role == body["role"] and h.revoked_at is None
                and (h.delegated_by == event["by"] or self._holds_directly(event["by"], ADMIN, at)) else h
                for h in self._holdings]
        else:
            self._bound_keys.setdefault(body["actor_id"], body["key_id"])
            self._holdings.append(_Holding(
                actor_id=body["actor_id"], role=body["role"], key_id=body["key_id"], granted_at=at,
                expires_at=_parse(body.get("expires_at"), "expires_at"),
                delegated_by=event["by"] if event["kind"] == "delegate" else None))
        self._events.append(event)

    # -- the durable log --------------------------------------------------------------------------------------------

    def _write(self, event: Dict[str, Any]) -> None:
        line = _canonical(event) + "\n"
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_NOFOLLOW", 0)
        with os.fdopen(os.open(self.path, flags, 0o600), "a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def _replay(self) -> None:
        """Re-prove every event: its hash, its link, its signature and the authority of the actor who made it."""
        with self.path.open(encoding="utf-8") as fh:
            for line_no, line in enumerate(fh, 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise RegistryError(f"registry line {line_no} is not JSON") from exc
                if event.get("previous") != self.head:
                    raise RegistryError(f"registry line {line_no} does not follow the line before it")
                if event.get("hash") != _digest({k: v for k, v in event.items() if k != "hash"}):
                    raise RegistryError(f"registry line {line_no} was altered")
                try:
                    self._check(event["kind"], event["by"], event["body"], event["signature"], event["previous"],
                                _parse(event["at"], "at"))
                except (KeyError, RegistryError) as exc:
                    raise RegistryError(f"registry line {line_no} cannot be proved: {exc}") from exc
                self._apply(event)
