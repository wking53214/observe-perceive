"""governance_record.py -- the record that leaves the process.

Measured 2026-09-08, before this module existed: the only record of a
governed action was the dict `orchestrate_request` returned. It held live
dataclasses; it was verifiable in memory and nowhere else; a process exit
lost every explanation; and any timestamp in it could be rewritten after
the fact without `verify_result` noticing, because timestamps are outside
the commitments by design (a canonical scenario must replay to the same
commitments).

This module gives the record a durable, verifiable form:

  to_record(result)        a plain, JSON-serializable dict: dataclasses
                           become tagged mappings, enums their values,
                           datetimes ISO-8601 strings. Deterministic.
  from_record(record)      rebuilds the six contract dataclasses (and a
                           minimal stand-in for the OBSERVE verdict) so that
                           `governance_chain.verify_result` runs on it with
                           no access to the process that made it.
  verify_record(record)    exactly that.
  record_hash(record)      sha256 over the canonical record: everything,
                           timestamps included. This is where "rewritten
                           after the fact" becomes detectable -- not in the
                           commitments, which say WHAT was decided, but in
                           the receipt, which says what the record LOOKED
                           LIKE when it was written.
  ReceiptLog(path)         append-only, hash-chained JSONL of receipts, one
                           per result: {record_hash, previous, hash, ...}
                           and the record itself. Refuses to load a file
                           whose chain does not recompute. `matches(result)`
                           says whether a result in hand is byte-for-byte
                           the one that was receipted.

The orchestrator writes a receipt for every result when constructed with a
ReceiptLog, on every path including refusals and failures: an explanation
that exists only for successes explains nothing.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional

from governance_chain import ChainVerification, verify_result
from governance_contracts import (
    CONTRACT_VERSION, ConservationDecision, ExecutionApproval, ExecutionContext,
    GovernanceApproval, GovernanceDecision, GovernanceRequest, GovernanceRequestType, OutcomeContext,
)

RECORD_VERSION = "1.0.0"
_TYPE = "__type__"


class RecordIntegrityError(Exception):
    """A receipt file's chain, or a receipt's record hash, does not recompute."""


# ---------------------------------------------------------------- serialization

def _plain(value: Any) -> Any:
    """Plain data from anything the orchestrator puts in a result."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out = {_TYPE: type(value).__name__}
        for f in dataclasses.fields(value):
            out[f.name] = _plain(getattr(value, f.name))
        return out
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_plain(v) for v in value]
    if hasattr(value, "as_dict") and callable(value.as_dict):
        return _plain(value.as_dict())
    if hasattr(value, "to_dict") and callable(value.to_dict):
        return _plain(value.to_dict())
    return {"__repr__": repr(value), _TYPE: type(value).__name__}


def to_record(result: Dict[str, Any]) -> Dict[str, Any]:
    """The result as plain data, with the record and contract versions."""
    record = {k: _plain(v) for k, v in result.items() if k != "receipt"}
    record["record_version"] = RECORD_VERSION
    record.setdefault("handoff", {}).setdefault("contract_version", CONTRACT_VERSION)
    return record


def record_hash(record: Dict[str, Any]) -> str:
    body = {k: v for k, v in record.items() if k != "receipt"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- reconstruction

def _dt(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return value
    return value


def _fields(cls, data: Dict[str, Any]) -> Dict[str, Any]:
    names = {f.name for f in dataclasses.fields(cls)}
    return {k: v for k, v in data.items() if k in names}


class RecordedVerdict:
    """Enough of an OBSERVE verdict to verify the outcome that recorded it."""

    class _Regime:
        def __init__(self, value):
            self.value = value

    def __init__(self, data: Dict[str, Any]):
        self.regime = RecordedVerdict._Regime(data.get("regime"))
        self.audit_hash = data.get("audit_hash")
        self.risk_score = data.get("risk_score")
        self.confidence = data.get("confidence")
        self.escalation_required = data.get("escalation_required")
        self.recorded = dict(data)


def _request(d: Dict[str, Any]) -> GovernanceRequest:
    kw = _fields(GovernanceRequest, d)
    kw["request_type"] = GovernanceRequestType(kw["request_type"])
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return GovernanceRequest(**kw)


def _decision(d: Dict[str, Any]) -> GovernanceDecision:
    kw = _fields(GovernanceDecision, d)
    kw["approval"] = GovernanceApproval(kw["approval"])
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return GovernanceDecision(**kw)


def _conservation(d: Dict[str, Any]) -> ConservationDecision:
    kw = _fields(ConservationDecision, d)
    kw["approval"] = GovernanceApproval(kw["approval"])
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return ConservationDecision(**kw)


def _approval(d: Dict[str, Any]) -> ExecutionApproval:
    kw = _fields(ExecutionApproval, d)
    kw["approval"] = GovernanceApproval(kw["approval"])
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return ExecutionApproval(**kw)


def _context(d: Dict[str, Any]) -> ExecutionContext:
    kw = _fields(ExecutionContext, d)
    kw["approval"] = _approval(kw["approval"])
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return ExecutionContext(**kw)


def _outcome(d: Dict[str, Any]) -> OutcomeContext:
    kw = _fields(OutcomeContext, d)
    kw["timestamp"] = _dt(kw.get("timestamp"))
    return OutcomeContext(**kw)


_BUILDERS = {
    "governance_request": _request,
    "governance_decision": _decision,
    "conservation_decision": _conservation,
    "execution_approval": _approval,
    "execution_context": _context,
    "outcome_context": _outcome,
}


def from_record(record: Dict[str, Any]) -> Dict[str, Any]:
    """A result the verifier accepts, rebuilt from the record alone."""
    result: Dict[str, Any] = dict(record)
    for key, build in _BUILDERS.items():
        value = record.get(key)
        result[key] = build(value) if isinstance(value, dict) else None
    verdict = record.get("observe_verdict")
    result["observe_verdict"] = RecordedVerdict(verdict) if isinstance(verdict, dict) else None
    return result


def verify_record(record: Dict[str, Any], kernel=None, perceive=None) -> ChainVerification:
    return verify_result(from_record(record), kernel=kernel, perceive=perceive)


# ---------------------------------------------------------------- receipts

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _receipt_hash(previous: str, record_digest: str, written_at: str) -> str:
    payload = json.dumps({"previous": previous, "record_hash": record_digest, "written_at": written_at},
                         sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class RecordAuthenticityError(RecordIntegrityError):
    """A receipt is unsigned, signed by another key, or does not verify, and
    the log was opened with a signer. Nothing is loaded."""


class ReceiptLog:
    """Append-only, hash-chained receipts, one per orchestrated result.

    With a `signer` (conservation_kernel.signing.Signer) every receipt is
    signed over its hash and the file refuses to load unless every receipt
    verifies under that key.
    """

    def __init__(self, path: str | Path, *, signer=None):
        self.path = Path(path)
        self.signer = signer
        self._entries: List[Dict[str, Any]] = []
        if self.path.exists():
            self._load()

    def _load(self) -> None:
        previous = ""
        for line_no, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            entry = json.loads(line)
            receipt = entry["receipt"]
            if receipt["previous"] != previous:
                raise RecordIntegrityError(f"{self.path}:{line_no}: previous-hash link broken")
            if record_hash(entry["record"]) != receipt["record_hash"]:
                raise RecordIntegrityError(f"{self.path}:{line_no}: record hash does not recompute")
            if _receipt_hash(previous, receipt["record_hash"], receipt["written_at"]) != receipt["hash"]:
                raise RecordIntegrityError(f"{self.path}:{line_no}: receipt hash does not recompute")
            if self.signer is not None:
                from conservation_kernel.signing import check_signature
                problem = check_signature(self.signer, receipt["hash"], receipt.get("signature"))
                if problem:
                    raise RecordAuthenticityError(f"{self.path}:{line_no}: {problem}")
            self._entries.append(entry)
            previous = receipt["hash"]

    def probe(self) -> None:
        """Prove the store can take an append now, before an action depends on it.

        Opens the file for append and syncs it. Raises whatever the OS
        raises (missing directory, permissions, read-only mount), so the
        orchestrator can refuse before executing rather than discover after.
        A full disk can still fail the later write; that residual is
        recorded as `receipt_error` on the result.
        """
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.flush()
            os.fsync(fh.fileno())

    def append(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Write a receipt for `result` and return it."""
        record = to_record(result)
        digest = record_hash(record)
        previous = self._entries[-1]["receipt"]["hash"] if self._entries else ""
        written_at = _now()
        receipt = {
            "sequence": len(self._entries),
            "record_hash": digest,
            "previous": previous,
            "written_at": written_at,
            "hash": _receipt_hash(previous, digest, written_at),
            "status": result.get("status"),
            "request_id": getattr(result.get("governance_request"), "request_id", None),
            "execution_id": getattr(result.get("execution_context"), "execution_id", None),
        }
        if self.signer is not None:
            from conservation_kernel.signing import signature_block
            receipt["signature"] = signature_block(self.signer, receipt["hash"])
        entry = {"receipt": receipt, "record": record}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        self._entries.append(entry)
        return receipt

    @property
    def entries(self) -> List[Dict[str, Any]]:
        return list(self._entries)

    def records(self) -> List[Dict[str, Any]]:
        return [e["record"] for e in self._entries]

    def find(self, execution_id: Optional[str] = None, request_id: Optional[str] = None) -> List[Dict[str, Any]]:
        out = []
        for e in self._entries:
            r = e["receipt"]
            if execution_id is not None and r.get("execution_id") != execution_id:
                continue
            if request_id is not None and r.get("request_id") != request_id:
                continue
            out.append(e)
        return out

    def matches(self, result: Dict[str, Any]) -> bool:
        """Is this result, as it stands now, the one that was receipted?"""
        receipt = result.get("receipt")
        if not receipt:
            return False
        return record_hash(to_record(result)) == receipt.get("record_hash")

    def verify_all(self, kernel=None, perceive=None) -> List[Dict[str, Any]]:
        """Re-verify every receipted record from the file alone."""
        out = []
        for e in self._entries:
            v = verify_record(e["record"], kernel=kernel, perceive=perceive)
            out.append({"sequence": e["receipt"]["sequence"], "status": e["record"].get("status"),
                        "valid": v.valid, "complete": v.complete, "failed": v.as_dict()["failed"]})
        return out
