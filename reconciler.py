"""reconciler.py -- proving the inverse: every effect in the world is matched to an authorization, or it is reported.

The execution guard proves that an action routed through it was authorized. It cannot see an action that went around
it. This module turns that around: it takes what actually happened (an effect log the deployer supplies) and the
signed execution ledger, joins them, and reports every effect that has no authorization and every authorization that
has no effect. An effect is unauthorized until a join finds its slip.

THE JOINS (each is one SQL query in QUERIES and finds one kind of disagreement; one effect can show more than one)

  bypass             an effect carrying no execution id at all                          (effects, no slip)
  forged             an effect naming an execution id the ledger never issued           effects ANTI JOIN issued
  unconsumed_use     an effect using an issued slip the guard never consumed            effects JOIN issued ANTI JOIN consumed
  substitution       an effect on a different artifact from the one the slip approved   effects JOIN issued ON id, hash differs
  replay             one slip, more than one effect                                     GROUP BY slip HAVING COUNT > 1
  early              an effect recorded before its slip was consumed                    effects JOIN consumed ON time
  claimed_no_effect  a slip consumed with no effect in the log                          consumed ANTI JOIN effects
  abandoned          a slip issued, never consumed, older than the given age            issued ANTI JOIN consumed

Every kind except `abandoned` goes to a person (`escalate`): the joins say where the records disagree, not which
record is wrong or whether it was harmful. That judgement is a person's.

WHAT IT DEPENDS ON

The effect log must come from a source the caller of the guarded action cannot write to, such as the downstream
system's own record. If whoever bypasses the guard can also edit or omit effects, these joins find nothing. That is
the deployer's to establish; this module cannot. It refuses a ledger whose hash chain does not verify, because joins
over a forged ledger prove nothing. The policy numbers (clock skew, abandonment age) have no default.

RUNNING IT ON A SCHEDULE

  python -m reconciler run --ledger PATH --effects PATH --state PATH --clock-skew S --abandoned-after S
                           --key-env VAR --key-id ID

reads the signed ledger (checking every signature) and the effect log (one JSON object per line), prints each NEW
violation that needs a person as one JSON line, records it in the state file, and exits:

  0  nothing new needs a person
  2  something does: new violations (printed), or a ledger that does not verify, or an effect log that is missing or
     malformed. Not being able to see what happened is itself an alarm.
  3  misconfigured: an argument or the key is missing or invalid.

What was already raised is kept in `--state`, written in one step, so a second run does not page the same violation
again. A violation is marked raised when it is printed: the scheduler must treat a failure to deliver exit-2 output as
its own alarm, or that violation will not be printed again. The ledger key is read from the named environment
variable; there is no default key.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Iterable, List, Mapping, Optional, Set, Tuple

__all__ = ["KINDS", "QUERIES", "Reconciliation", "ReconciliationRefused", "Violation", "escalate", "load_raised", "main",
           "reconcile", "save_raised"]

#: kind -> who must see it. "page" goes to a person through the escalation desk; "record" is reported only.
KINDS: Dict[str, str] = {
    "bypass": "page", "forged": "page", "unconsumed_use": "page", "substitution": "page", "replay": "page",
    "early": "page", "claimed_no_effect": "page", "abandoned": "record",
}

#: The join library. Times are epoch seconds; :skew and :abandoned_before are bound at run time.
QUERIES: Dict[str, str] = {
    "bypass": """
        SELECT e.effect_id, NULL AS execution_id, e.artifact_hash, NULL AS approved_hash
        FROM effects e WHERE e.execution_id IS NULL""",
    "forged": """
        SELECT e.effect_id, e.execution_id, e.artifact_hash, NULL AS approved_hash
        FROM effects e LEFT JOIN issued i ON i.execution_id = e.execution_id
        WHERE e.execution_id IS NOT NULL AND i.execution_id IS NULL""",
    "unconsumed_use": """
        SELECT e.effect_id, e.execution_id, e.artifact_hash, i.artifact_hash AS approved_hash
        FROM effects e JOIN issued i ON i.execution_id = e.execution_id
        LEFT JOIN consumed c ON c.execution_id = e.execution_id
        WHERE c.execution_id IS NULL""",
    "substitution": """
        SELECT e.effect_id, e.execution_id, e.artifact_hash, i.artifact_hash AS approved_hash
        FROM effects e JOIN issued i ON i.execution_id = e.execution_id
        WHERE e.artifact_hash IS NOT i.artifact_hash""",
    "replay": """
        SELECT GROUP_CONCAT(e.effect_id) AS effect_id, e.execution_id, NULL AS artifact_hash,
               COUNT(*) AS approved_hash
        FROM effects e JOIN issued i ON i.execution_id = e.execution_id
        GROUP BY e.execution_id HAVING COUNT(*) > 1""",
    "early": """
        SELECT e.effect_id, e.execution_id, e.artifact_hash, NULL AS approved_hash
        FROM effects e JOIN consumed c ON c.execution_id = e.execution_id
        WHERE e.at < c.at - :skew""",
    "claimed_no_effect": """
        SELECT NULL AS effect_id, c.execution_id, NULL AS artifact_hash, NULL AS approved_hash
        FROM consumed c LEFT JOIN effects e ON e.execution_id = c.execution_id
        WHERE e.effect_id IS NULL""",
    "abandoned": """
        SELECT NULL AS effect_id, i.execution_id, NULL AS artifact_hash, i.artifact_hash AS approved_hash
        FROM issued i LEFT JOIN consumed c ON c.execution_id = i.execution_id
        LEFT JOIN effects e ON e.execution_id = i.execution_id
        WHERE c.execution_id IS NULL AND e.effect_id IS NULL AND i.at < :abandoned_before""",
}


class ReconciliationRefused(ValueError):
    """The inputs cannot support a reconciliation: a ledger that does not verify, or a malformed effect log."""


@dataclass(frozen=True)
class Violation:
    kind: str
    severity: str
    violation_id: str                 # stable across runs, so the same disagreement is raised to a person once
    execution_id: Optional[str]
    effect_id: Optional[str]
    detail: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Reconciliation:
    violations: Tuple[Violation, ...]
    effects_checked: int
    issued_checked: int
    consumed_checked: int

    @property
    def clean(self) -> bool:
        return not self.violations

    def of_kind(self, kind: str) -> List[Violation]:
        return [v for v in self.violations if v.kind == kind]


def _epoch(value: Any, what: str) -> float:
    try:
        parsed = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ReconciliationRefused(f"{what} is not an ISO time: {value!r}") from exc
    if parsed.tzinfo is None:
        raise ReconciliationRefused(f"{what} has no time zone: {value!r}")
    return parsed.timestamp()


def _non_negative(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value >= 0 or value == float("inf"):
        raise ReconciliationRefused(f"{what} must be a finite number of seconds, zero or more: {value!r}")
    return float(value)


def _violation_id(kind: str, execution_id: Any, effect_id: Any) -> str:
    payload = json.dumps({"kind": kind, "execution_id": execution_id, "effect_id": effect_id}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def reconcile(ledger, effects: Iterable[Mapping[str, Any]], *, now: Any, clock_skew_s: float,
              abandoned_after_s: float) -> Reconciliation:
    """Join the ledger's issued and consumed entries against `effects` and return every disagreement.

    Each effect is a mapping with `effect_id` (unique), `at` (ISO time with a zone), `execution_id` (None when the
    effect carries no slip) and `artifact_hash` (what was acted on; required whenever `execution_id` is given).
    """
    skew = _non_negative(clock_skew_s, "clock_skew_s")
    abandoned_before = _epoch(now, "now") - _non_negative(abandoned_after_s, "abandoned_after_s")
    entries = ledger.entries                                    # re-reads a file-backed ledger, checking signatures
    if not ledger.verify_chain():
        raise ReconciliationRefused("the execution ledger's hash chain does not verify: nothing joined to it means anything")

    db = sqlite3.connect(":memory:")
    try:
        db.executescript("""
            CREATE TABLE issued   (execution_id TEXT PRIMARY KEY, artifact_hash TEXT, at REAL NOT NULL);
            CREATE TABLE consumed (execution_id TEXT PRIMARY KEY, at REAL NOT NULL);
            CREATE TABLE effects  (effect_id TEXT PRIMARY KEY, execution_id TEXT, artifact_hash TEXT, at REAL NOT NULL);
            CREATE INDEX effects_by_slip ON effects (execution_id);
        """)
        issued = consumed = 0
        for entry in entries:
            if entry.get("kind") == "issued":
                db.execute("INSERT INTO issued VALUES (?, ?, ?)", (entry["execution_id"], entry.get("artifact_hash"),
                                                                   _epoch(entry["recorded_at"], "ledger time")))
                issued += 1
            elif entry.get("kind") == "consumed":
                db.execute("INSERT INTO consumed VALUES (?, ?)", (entry["execution_id"],
                                                                  _epoch(entry["recorded_at"], "ledger time")))
                consumed += 1
        count = 0
        for n, effect in enumerate(effects, 1):
            effect_id, execution_id = effect.get("effect_id"), effect.get("execution_id")
            if not effect_id:
                raise ReconciliationRefused(f"effect {n} has no effect_id")
            if execution_id is not None and not effect.get("artifact_hash"):
                raise ReconciliationRefused(f"effect {effect_id!r} names a slip but not what it acted on (artifact_hash)")
            try:
                db.execute("INSERT INTO effects VALUES (?, ?, ?, ?)", (str(effect_id), execution_id,
                                                                        effect.get("artifact_hash"),
                                                                        _epoch(effect.get("at"), f"effect {effect_id!r} at")))
            except sqlite3.IntegrityError as exc:
                raise ReconciliationRefused(f"effect id {effect_id!r} appears twice in the effect log") from exc
            count += 1

        found: List[Violation] = []
        for kind, sql in QUERIES.items():
            for effect_id, execution_id, artifact_hash, other in db.execute(
                    sql, {"skew": skew, "abandoned_before": abandoned_before}):
                detail: Dict[str, Any] = {}
                if kind == "replay":
                    detail = {"uses": other, "effect_ids": sorted(effect_id.split(","))}
                    effect_id = None
                elif kind in ("substitution", "unconsumed_use", "abandoned"):
                    detail = {"acted_on": artifact_hash, "approved": other}
                elif artifact_hash is not None:
                    detail = {"acted_on": artifact_hash}
                found.append(Violation(kind, KINDS[kind], _violation_id(kind, execution_id, effect_id),
                                       execution_id, effect_id, detail))
        found.sort(key=lambda v: (v.kind, v.execution_id or "", v.effect_id or ""))
        return Reconciliation(tuple(found), count, issued, consumed)
    finally:
        db.close()


def escalate(result: Reconciliation, desk, *, already_raised: Set[str]) -> List[Tuple[str, str]]:
    """Raise one ticket per paging violation not in `already_raised`, and add each to it.

    `already_raised` belongs to the caller and must outlive the process if repeated runs are not to page twice for
    the same disagreement. Returns [(violation_id, ticket_id)]. The desk never raises; a ticket it could not log is
    still dispatched (see human_escalation.py).
    """
    raised: List[Tuple[str, str]] = []
    for v in result.violations:
        if v.severity != "page" or v.violation_id in already_raised:
            continue
        ticket_id, _ = desk.raise_and_dispatch(
            kind=f"reconciliation:{v.kind}", request_id=None, artifact_id=None,
            context={"violation_id": v.violation_id, "execution_id": v.execution_id, "effect_id": v.effect_id,
                     "detail": v.detail})
        already_raised.add(v.violation_id)
        raised.append((v.violation_id, ticket_id))
    return raised


# ---- running it on a schedule ---------------------------------------------------------------------------------------

def load_raised(path) -> Set[str]:
    """The violation ids already raised, from a state file written by `save_raised`. A missing file is an empty set."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        return set()
    if not isinstance(data, dict) or not isinstance(data.get("raised"), list):
        raise ReconciliationRefused(f"{path} is not a reconciler state file")
    return {str(v) for v in data["raised"]}


def save_raised(path, raised: Set[str]) -> None:
    """Replace the state file in one step, so a crash leaves the old state or the new one, never half of either."""
    directory = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".reconciler-state.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"raised": sorted(raised)}, fh)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_effects(path) -> List[Dict[str, Any]]:
    effects = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            if not line.strip():
                continue
            try:
                effects.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ReconciliationRefused(f"effect log line {n} is not JSON") from exc
    return effects


def main(argv: Optional[list] = None, *, now: Optional[datetime] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m reconciler", description="Reconcile a ledger against effects.")
    sub = parser.add_subparsers(dest="command", required=True)
    run = sub.add_parser("run")
    run.add_argument("--ledger", required=True)
    run.add_argument("--effects", required=True)
    run.add_argument("--state", required=True)
    run.add_argument("--clock-skew", type=float, required=True, help="seconds; there is no default")
    run.add_argument("--abandoned-after", type=float, required=True, help="seconds; there is no default")
    run.add_argument("--key-env", required=True, help="environment variable holding the ledger key")
    run.add_argument("--key-id", required=True)
    args = parser.parse_args(argv)

    key = os.environ.get(args.key_env, "")
    if not key.strip():
        print(f"MISCONFIGURED: environment variable {args.key_env} is empty or unset", file=sys.stderr)
        return 3
    from conservation_kernel import HmacSigner
    try:
        signer = HmacSigner(key.encode("utf-8"), key_id=args.key_id)
        raised = load_raised(args.state)
    except (ValueError, OSError) as exc:
        print(f"MISCONFIGURED: {exc}", file=sys.stderr)
        return 3
    when = now or datetime.now().astimezone()
    try:
        from execution_guard import ExecutionLedger
        if not os.path.exists(args.ledger):
            raise ReconciliationRefused(f"no ledger at {args.ledger}")
        ledger = ExecutionLedger(args.ledger, signer=signer)
        result = reconcile(ledger, _read_effects(args.effects), now=when, clock_skew_s=args.clock_skew,
                           abandoned_after_s=args.abandoned_after)
    except FileNotFoundError:
        print(json.dumps({"alarm": "effect_log_missing", "detail": f"no effect log at {args.effects}: what happened "
                                                                    "cannot be seen"}))
        return 2
    except ReconciliationRefused as exc:
        print(json.dumps({"alarm": "refused", "detail": str(exc)}))
        return 2
    except Exception as exc:  # noqa: BLE001 - a ledger that cannot be read or verified is an alarm, not a crash
        print(json.dumps({"alarm": "ledger_unreadable", "detail": f"{type(exc).__name__}: {exc}"}))
        return 2

    new = [v for v in result.violations if v.severity == "page" and v.violation_id not in raised]
    for v in new:
        print(json.dumps({"kind": v.kind, "violation_id": v.violation_id, "execution_id": v.execution_id,
                          "effect_id": v.effect_id, "detail": v.detail}, sort_keys=True))
    if new:
        try:
            save_raised(args.state, raised | {v.violation_id for v in new})
        except OSError as exc:
            print(json.dumps({"alarm": "state_unwritable", "detail": f"{type(exc).__name__}: {exc}; the same "
                                                                     "violations will be printed again next run"}))
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
