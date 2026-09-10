"""execution_guard.py -- the executor's side of the authorization boundary.

Measured 2026-09-08, before this module existed: an `ExecutionContext` built
by hand with the public dataclass constructors and the public
`compute_state_commitment` was indistinguishable, on the executor's side,
from one the orchestrator had issued; and a genuinely issued context could
be handed to the executor any number of times. Both executed. The chain
recorded approvals faithfully and nothing consulted the record before acting.

This closes that boundary with two things:

  ExecutionLedger      an append-only, hash-chained record of every execution
                       context the orchestrator ISSUED and every one an
                       executor CONSUMED. In memory by default; give it a path
                       and it is a JSONL file that survives the process, so a
                       replay after restart is still a replay.

  authorize_execution  what an executor calls before acting. It re-derives the
                       approval and context commitments from the context's own
                       fields, checks the context against the ledger's issuance
                       record field by field (commitments, ids, timestamps),
                       refuses a context that was never issued or already
                       consumed, optionally checks the decision is in the
                       Conservation Kernel's ledger, and then marks the context
                       consumed. Single use is enforced at the moment of
                       authorization, not after the action.

`guarded(func, ledger)` wraps an executor so the check cannot be forgotten.

What this does and does not prove. The commitment chain is integrity
evidence, not authenticity: anyone holding the same process can recompute
it. The ledger is the trust anchor. Whoever controls the ledger the executor
consults controls what "issued" means, so the ledger must be the one the
orchestrator writes to (the same object, or the same file). Given that, a
context that was not issued cannot pass, a context altered after issuance
cannot pass, and a context cannot pass twice. Nothing here can stop a caller
who ignores the guard and calls their own function directly; that is the
executor's contract, stated in one place so it can be audited.
"""
from __future__ import annotations

import hashlib
import json
import os
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from governance_chain import approval_state, execution_context_state
from governance_contracts import GovernanceApproval, compute_state_commitment

GUARD_VERSION = "1.0.0"


class ExecutionRefusal(Exception):
    """The guard refused to authorize an execution. The message names why."""


class LedgerIntegrityError(Exception):
    """The ledger file's hash chain does not recompute. Nothing is loaded."""


class LedgerAuthenticityError(LedgerIntegrityError):
    """An entry is unsigned, signed by another key, or its signature does
    not verify, and this ledger was opened with a signer. Nothing is loaded.

    Integrity says the file was not corrupted; only a signature says this
    deployment wrote it. Measured 2026-09-08: a writer with the public hash
    function could append a consistent forgery. With a signer, it cannot.
    """


try:
    import fcntl

    def _flock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)

    def _funlock(fh):
        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
except ImportError:  # pragma: no cover - Windows
    import msvcrt

    def _flock(fh):
        msvcrt.locking(fh.fileno(), msvcrt.LK_LOCK, 1)

    def _funlock(fh):
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)


def _iso(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entry_hash(previous: str, entry: Dict[str, Any]) -> str:
    payload = json.dumps({"previous": previous, "entry": entry}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def issuance_record(execution_context) -> Dict[str, Any]:
    """The fields the ledger records at issuance and compares at authorization.

    Everything a caller could alter after issuance that the commitments do
    not cover (the timestamps, the execution id before 1.1.0) is listed here
    explicitly, so "altered after issuance" is detectable without a secret.
    """
    approval = execution_context.approval
    return {
        "execution_id": execution_context.execution_id,
        "request_id": execution_context.request_id,
        "artifact_id": execution_context.artifact_id,
        "artifact_hash": execution_context.artifact_hash,
        "producer": execution_context.producer,
        "lineage": list(execution_context.lineage or []),
        "context_commitment": execution_context.state_commitment,
        "context_timestamp": _iso(getattr(execution_context, "timestamp", None)),
        "approval_commitment": approval.state_commitment,
        "approval_timestamp": _iso(getattr(approval, "timestamp", None)),
        "approval_value": getattr(approval.approval, "value", approval.approval),
        "conservation_decision_id": approval.conservation_decision_id,
        "conservation_receipt_id": approval.conservation_receipt_id,
        "conservation_audit_hash": approval.conservation_audit_hash,
    }


@dataclass
class ExecutionAuthorization:
    """What the guard hands back when it authorizes. Carry it into the outcome."""
    execution_id: str
    context_commitment: str
    ledger_entry_hash: str
    authorized_at: str
    checks: List[Dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "context_commitment": self.context_commitment,
            "ledger_entry_hash": self.ledger_entry_hash,
            "authorized_at": self.authorized_at,
            "guard_version": GUARD_VERSION,
            "checks": list(self.checks),
        }


class ExecutionLedger:
    """Append-only, hash-chained record of issued and consumed executions.

    Each entry is {kind, execution_id, ..., recorded_at, previous, hash} where
    `hash` covers the entry and the previous entry's hash. With a path, every
    entry is appended to a JSONL file as it happens and the whole chain is
    re-verified on load; a file whose chain does not recompute is refused
    outright rather than partially trusted.

    Shared file (1.3.0). The file, not this object, is the ledger: every
    write takes a lock on `<path>.lock`, re-reads whatever other processes
    appended since, then checks and appends. Two processes opening the same
    path therefore see one ledger, and a context issued in one cannot be
    issued or consumed twice in the other. Reads re-read too.

    Signed (1.3.0). With a `signer` (conservation_kernel.signing.Signer),
    every entry carries a signature over its hash and the file refuses to
    load unless every entry verifies under that key: a writer with the file
    and the public hash function can no longer append a consistent forgery.

    One issuance per artifact (1.3.0). An artifact id already issued in this
    ledger is refused a second issuance, so the same artifact governed by a
    second process against a shared file is a replay, not a second decision.
    `one_per_artifact=False` restores the per-execution-id rule alone.
    """

    def __init__(self, path: Optional[str | Path] = None, *, signer=None, one_per_artifact: bool = True):
        self.path = Path(path) if path else None
        self.signer = signer
        self.one_per_artifact = one_per_artifact
        self._entries: List[Dict[str, Any]] = []
        self._issued: Dict[str, Dict[str, Any]] = {}
        self._consumed: Dict[str, Dict[str, Any]] = {}
        self._by_artifact: Dict[str, str] = {}
        self._offset = 0          # bytes of the file already indexed
        if self.path and self.path.exists():
            self._refresh()

    # -- persistence ---------------------------------------------------------

    def _check_entry(self, line_no: int, stored: Dict[str, Any], previous: str) -> Dict[str, Any]:
        signature = stored.pop("signature", None)
        recorded_hash = stored.pop("hash", None)
        if stored.get("previous") != previous:
            raise LedgerIntegrityError(f"{self.path}:{line_no}: previous-hash link broken")
        if _entry_hash(previous, {k: v for k, v in stored.items() if k != "previous"}) != recorded_hash:
            raise LedgerIntegrityError(f"{self.path}:{line_no}: entry hash does not recompute")
        if self.signer is not None:
            from conservation_kernel.signing import check_signature
            problem = check_signature(self.signer, recorded_hash, signature)
            if problem:
                raise LedgerAuthenticityError(f"{self.path}:{line_no}: {problem}")
        stored["hash"] = recorded_hash
        if signature is not None:
            stored["signature"] = signature
        return stored

    def _refresh(self) -> None:
        """Index whatever the file holds beyond what this object has seen.

        Only complete lines are taken; a line another process is still
        writing waits for the next refresh. The chain is verified across the
        boundary, so a file edited between two refreshes is still refused.
        """
        if not self.path or not self.path.exists():
            return
        with self.path.open("rb") as fh:
            fh.seek(self._offset)
            data = fh.read()
        if not data:
            return
        complete = data.rfind(b"\n")
        if complete < 0:
            return
        chunk = data[: complete + 1]
        previous = self._entries[-1]["hash"] if self._entries else ""
        line_no = len(self._entries)
        for raw in chunk.decode("utf-8").splitlines():
            line_no += 1
            if not raw.strip():
                continue
            stored = self._check_entry(line_no, json.loads(raw), previous)
            self._index(stored)
            previous = stored["hash"]
        self._offset += len(chunk)

    def _load(self) -> None:          # kept for callers of the 1.2.0 name
        self._refresh()

    @contextmanager
    def _locked(self):
        """Exclusive lock on `<path>.lock` for the duration; a no-op in memory."""
        if not self.path:
            yield
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_name(self.path.name + ".lock")
        with lock_path.open("a+") as lock:
            _flock(lock)
            try:
                self._refresh()
                yield
            finally:
                _funlock(lock)

    def _append(self, kind: str, body: Dict[str, Any]) -> Dict[str, Any]:
        previous = self._entries[-1]["hash"] if self._entries else ""
        entry = {"kind": kind, "recorded_at": _now(), **body}
        entry_hash = _entry_hash(previous, entry)
        stored = {**entry, "previous": previous, "hash": entry_hash}
        if self.signer is not None:
            from conservation_kernel.signing import signature_block
            stored["signature"] = signature_block(self.signer, entry_hash)
        if self.path:
            line = json.dumps(stored, sort_keys=True, default=str) + "\n"
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)
                fh.flush()
                os.fsync(fh.fileno())
            self._offset += len(line.encode("utf-8"))
        self._index(stored)
        return stored

    def _index(self, stored: Dict[str, Any]) -> None:
        self._entries.append(stored)
        if stored["kind"] == "issued":
            self._issued[stored["execution_id"]] = stored
            artifact_id = stored.get("artifact_id")
            if artifact_id and artifact_id not in self._by_artifact:
                self._by_artifact[artifact_id] = stored["execution_id"]
        elif stored["kind"] == "consumed":
            self._consumed[stored["execution_id"]] = stored

    # -- API -------------------------------------------------------------------

    @property
    def entries(self) -> List[Dict[str, Any]]:
        self._refresh()
        return list(self._entries)

    def issued(self, execution_id: str) -> Optional[Dict[str, Any]]:
        self._refresh()
        return self._issued.get(execution_id)

    def consumed(self, execution_id: str) -> Optional[Dict[str, Any]]:
        self._refresh()
        return self._consumed.get(execution_id)

    def issued_for_artifact(self, artifact_id: str) -> Optional[str]:
        """The execution id first issued for this artifact in this ledger, if any."""
        self._refresh()
        return self._by_artifact.get(artifact_id)

    def issue(self, execution_context) -> Dict[str, Any]:
        """Record that the orchestrator issued this context. Refuses a second
        issuance under the same execution id: an id is an identity, and two
        approvals under one id would make the ledger unable to say which one
        an executor consumed. With `one_per_artifact`, also refuses a second
        issuance for an artifact this ledger has already issued for."""
        record = issuance_record(execution_context)
        if not record["execution_id"]:
            raise ExecutionRefusal("cannot issue an execution context with no execution_id")
        with self._locked():
            if record["execution_id"] in self._issued:
                raise ExecutionRefusal(
                    f"execution id {record['execution_id']!r} was already issued; a replayed "
                    "request must carry a new execution id"
                )
            earlier = self._by_artifact.get(record["artifact_id"] or "")
            if self.one_per_artifact and earlier is not None:
                raise ExecutionRefusal(
                    f"artifact {record['artifact_id']!r} was already issued an execution ({earlier!r}) "
                    "in this ledger; a second issuance is a replay"
                )
            return self._append("issued", record)

    def consume(self, execution_context, *, consumer: str, outcome: str = "authorized") -> Dict[str, Any]:
        record = issuance_record(execution_context)
        execution_id = record["execution_id"]
        with self._locked():
            if execution_id not in self._issued:
                raise ExecutionRefusal(f"execution id {execution_id!r} was never issued")
            if execution_id in self._consumed:
                raise ExecutionRefusal(f"execution id {execution_id!r} was already consumed")
            return self._append("consumed", {
                "execution_id": execution_id,
                "context_commitment": record["context_commitment"],
                "consumer": consumer,
                "outcome": outcome,
                "issued_entry_hash": self._issued[execution_id]["hash"],
            })

    def verify_chain(self) -> bool:
        previous = ""
        for stored in self._entries:
            body = {k: v for k, v in stored.items() if k not in ("previous", "hash", "signature")}
            if stored["previous"] != previous or _entry_hash(previous, body) != stored["hash"]:
                return False
            previous = stored["hash"]
        return True


def authorize_execution(execution_context, ledger: ExecutionLedger, *, kernel=None,
                        consumer: str = "executor") -> ExecutionAuthorization:
    """Authorize one execution of `execution_context`, or raise ExecutionRefusal.

    Order matters: structural checks first (cheap, no side effects), then the
    ledger (the trust anchor), then the kernel (optional, external), then the
    single-use mark. A refusal at any step leaves the ledger untouched except
    that a refused attempt on an issued context is recorded as such, so a
    replay attempt is itself evidence.
    """
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})
        if not ok:
            raise ExecutionRefusal(f"{name}: {detail}" if detail else name)

    if execution_context is None:
        raise ExecutionRefusal("no execution context supplied")
    approval = getattr(execution_context, "approval", None)
    check("context.has_approval", approval is not None, "execution context carries no approval")
    check(
        "approval.approved",
        getattr(approval.approval, "value", approval.approval) == GovernanceApproval.APPROVED.value,
        f"approval is {getattr(approval.approval, 'value', approval.approval)!r}",
    )
    # The approval names what it approved (1.1.0); the context must be about
    # the same artifact. The producer differs by design: the approval carries
    # the request's producer, the context the executor's.
    check(
        "approval.names_this_artifact",
        approval.artifact_id == execution_context.artifact_id
        and approval.artifact_hash == execution_context.artifact_hash,
        "the approval is for a different artifact than the context names",
    )
    check(
        "approval.state_commitment",
        approval.state_commitment == compute_state_commitment(
            approval.conservation_audit_hash,
            approval_state(approval, approval.artifact_id, approval.artifact_hash,
                           approval.producer, approval.lineage),
        ),
        "approval commitment does not recompute from the approval's own fields",
    )
    check(
        "context.state_commitment",
        execution_context.state_commitment == compute_state_commitment(
            approval.state_commitment, execution_context_state(execution_context)),
        "execution context commitment does not recompute",
    )
    check("context.execution_id", bool(execution_context.execution_id), "execution context has no execution id")

    # -- the ledger is the trust anchor ----------------------------------------
    presented = issuance_record(execution_context)
    issued = ledger.issued(presented["execution_id"])
    check("ledger.issued", issued is not None, f"execution id {presented['execution_id']!r} was never issued")
    differing = sorted(k for k in presented if issued.get(k) != presented[k])
    check(
        "ledger.matches_issuance",
        not differing,
        "context differs from what was issued on: " + ", ".join(differing) if differing else "",
    )
    already = ledger.consumed(presented["execution_id"])
    if already is not None:
        # Record the attempt before refusing: a replay is evidence.
        ledger._append("refused", {
            "execution_id": presented["execution_id"],
            "context_commitment": presented["context_commitment"],
            "consumer": consumer,
            "reason": "already consumed",
            "consumed_entry_hash": already["hash"],
        })
        check("ledger.single_use", False, f"execution id {presented['execution_id']!r} was already consumed at {already['recorded_at']}")
    checks.append({"name": "ledger.single_use", "ok": True, "detail": "first use"})

    # -- optional: the decision is in the Conservation Kernel's ledger ----------
    if kernel is not None:
        decision_artifact_id = f"decision-{approval.conservation_decision_id}"
        try:
            reconstruction = kernel.reconstruct(decision_artifact_id)
            ok = bool(reconstruction.transformation_ids_in_order) and execution_context.artifact_id in reconstruction.root_artifact_ids
            detail = (
                f"{decision_artifact_id} reconstructs with a transformation from root {execution_context.artifact_id}"
                if ok else f"{decision_artifact_id} is in the ledger but not as a decision derived from {execution_context.artifact_id}"
            )
        except Exception as e:  # the kernel's own error family for unknown ids
            ok, detail = False, f"{decision_artifact_id} not in kernel ledger: {type(e).__name__}"
        check("kernel.decision_in_ledger", ok, detail)

    consumed = ledger.consume(execution_context, consumer=consumer)
    return ExecutionAuthorization(
        execution_id=execution_context.execution_id,
        context_commitment=execution_context.state_commitment,
        ledger_entry_hash=consumed["hash"],
        authorized_at=consumed["recorded_at"],
        checks=checks,
    )


def guarded(func, ledger: ExecutionLedger, *, kernel=None, consumer: str = "executor"):
    """Wrap an executor so it runs only after `authorize_execution` passes.

    The wrapped callable has the orchestrator's executor signature,
    `func(execution_context)`, and returns whatever `func` returns, with the
    authorization attached under `_authorization` when the result is a
    mapping so it travels into the outcome record.
    """
    def run(execution_context):
        authorization = authorize_execution(execution_context, ledger, kernel=kernel, consumer=consumer)
        result = func(execution_context)
        if isinstance(result, dict):
            result = {**result, "_authorization": authorization.as_dict()}
        return result
    run.__name__ = f"guarded_{getattr(func, '__name__', 'executor')}"
    run.execution_guard = True
    return run
