"""heartbeat.py -- proving the escalation process is alive from outside it: silence is the alarm.

The sweep runner's watchdog notices a stopped sweep, but it lives in the same process. If the process dies, hangs, or
is paused, the watchdog goes with it and nobody is told. Nothing inside a process can prove it is still running, so
this module turns the question around: the process keeps proving it is alive, and a separate process treats the
absence of that proof as the alarm (a dead man's switch).

  HeartbeatWriter   given to `SweepRunner(heartbeat=...)`. After each watchdog pass it writes one small signed file
                    with the runner's status and the time. The file is replaced in one step, so a reader never sees
                    half of it.
  check_heartbeat   run by something else: cron, a systemd timer, a Kubernetes liveness probe, another host. It fails
                    when the file is missing, unreadable, unsigned or signed with another key, older than `max_age_s`,
                    dated in the future, or says the runner is not healthy (not running, a stale sweep, an alarm that
                    could not be delivered, a stuck delivery).
  python -m heartbeat check PATH --max-age SECONDS --key-env VAR --key-id ID
                    the same check for a scheduler: exit 0 healthy, 2 alarm (the reason is printed), 3 misconfigured.
                    The key is read from the named environment variable; there is no default key.

WHAT IT CANNOT DO

It cannot prove that anything runs the check, or that the checker's own alarm reaches a person. Those belong to the
scheduler and the deployer's paging, outside this package. The signature stops anyone without the key forging a
healthy beat; the time inside the signed file stops an old healthy beat being replayed after `max_age_s`. With an
HMAC key the checker holds the same secret as the writer; use a key for nothing else.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Mapping, Optional

__all__ = ["HeartbeatVerdict", "HeartbeatWriter", "beat_digest", "check_heartbeat", "main"]

_FIELDS = ("running", "alarmed", "undelivered", "delivery_stuck", "sweeps", "sweep_errors",
           "seconds_since_clean_sweep")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def beat_digest(beat: Mapping[str, Any]) -> str:
    body = {k: v for k, v in beat.items() if k != "signature"}
    payload = json.dumps({"purpose": "sweep-heartbeat", "beat": body}, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class HeartbeatWriter:
    """Writes the runner's signed status to `path`, replacing the last one. Never raises into the runner."""

    def __init__(self, path: str | Path, signer: Any, *, label: str, clock: Callable[[], datetime] = _now):
        if signer is None:
            raise ValueError("a heartbeat needs a signer: an unsigned beat can be forged by anyone who can write the file")
        if not str(label or "").strip():
            raise ValueError("a heartbeat needs a label naming what it is the heartbeat of")
        self.path = Path(path)
        self.signer = signer
        self.label = label
        self._clock = clock
        self.seq = 0
        self.failures = 0
        self.last_error: Optional[str] = None

    def __call__(self, status: Mapping[str, Any]) -> bool:
        """Write one beat. Returns False (and counts it) if it could not be written; the checker will then alarm."""
        try:
            from conservation_kernel.signing import signature_block
            self.seq += 1
            beat: Dict[str, Any] = {"label": self.label, "seq": self.seq, "at": self._clock().isoformat()}
            beat.update({k: status.get(k) for k in _FIELDS})
            beat["signature"] = signature_block(self.signer, beat_digest(beat))
            fd, tmp = tempfile.mkstemp(dir=str(self.path.parent), prefix=f".{self.path.name}.")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(beat, fh, sort_keys=True)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return True
        except Exception as exc:  # noqa: BLE001 - a heartbeat that cannot be written is silence, which the checker sees
            self.failures += 1
            self.last_error = f"{type(exc).__name__}: {exc}"
            return False


@dataclass(frozen=True)
class HeartbeatVerdict:
    healthy: bool
    reason: str
    beat: Optional[Dict[str, Any]] = None


def check_heartbeat(path: str | Path, signer: Any, *, max_age_s: float, label: Optional[str] = None,
                    now: Optional[datetime] = None) -> HeartbeatVerdict:
    """Is the process behind this heartbeat alive and healthy? Run it from somewhere else."""
    if isinstance(max_age_s, bool) or not isinstance(max_age_s, (int, float)) or not math.isfinite(max_age_s) \
            or max_age_s <= 0:
        raise ValueError("max_age_s must be a positive finite number of seconds (there is no default)")
    try:
        beat = json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return HeartbeatVerdict(False, "no heartbeat file: the runner never started, or its beats are not reaching here")
    except (OSError, ValueError) as exc:
        return HeartbeatVerdict(False, f"the heartbeat file cannot be read: {type(exc).__name__}: {exc}")
    if not isinstance(beat, dict):
        return HeartbeatVerdict(False, "the heartbeat file is not a heartbeat")
    from conservation_kernel.signing import check_signature
    problem = check_signature(signer, beat_digest(beat), beat.get("signature"))
    if problem:
        return HeartbeatVerdict(False, f"the heartbeat's signature is refused: {problem}", beat)
    if label is not None and beat.get("label") != label:
        return HeartbeatVerdict(False, f"the heartbeat is from {beat.get('label')!r}, not {label!r}", beat)
    try:
        at = datetime.fromisoformat(str(beat.get("at")))
        if at.tzinfo is None:
            raise ValueError("no time zone")
    except ValueError as exc:
        return HeartbeatVerdict(False, f"the heartbeat's time cannot be read: {exc}", beat)
    age = ((now or _now()) - at).total_seconds()
    if age > max_age_s:
        return HeartbeatVerdict(False, f"the last heartbeat is {age:.0f}s old (limit {max_age_s:g}s): the process is "
                                       "dead, hung or paused", beat)
    if age < -max_age_s:
        return HeartbeatVerdict(False, f"the heartbeat is dated {-age:.0f}s in the future: a clock is wrong", beat)
    if beat.get("running") is not True:
        return HeartbeatVerdict(False, "the process is alive but its sweep runner is not running", beat)
    if beat.get("alarmed"):
        return HeartbeatVerdict(False, "the sweep runner reports a stale sweep (its own alarm may not have got through)",
                                beat)
    if beat.get("undelivered"):
        return HeartbeatVerdict(False, f"{beat['undelivered']} watchdog alarm(s) could not be delivered: the alarm "
                                       "channel is failing", beat)
    if beat.get("delivery_stuck"):
        return HeartbeatVerdict(False, "the watchdog's alarm delivery is stuck", beat)
    return HeartbeatVerdict(True, f"healthy: beat {beat.get('seq')} from {beat.get('label')!r}, {max(age, 0):.0f}s old",
                            beat)


def main(argv: Optional[list] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(prog="python -m heartbeat", description="Check a sweep runner's heartbeat.")
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("check")
    check.add_argument("path")
    check.add_argument("--max-age", type=float, required=True, help="seconds; there is no default")
    check.add_argument("--key-env", required=True, help="environment variable holding the heartbeat key")
    check.add_argument("--key-id", required=True)
    check.add_argument("--label")
    args = parser.parse_args(argv)
    key = os.environ.get(args.key_env, "")
    if not key.strip():
        print(f"MISCONFIGURED: environment variable {args.key_env} is empty or unset", file=sys.stderr)
        return 3
    from conservation_kernel import HmacSigner
    try:
        verdict = check_heartbeat(args.path, HmacSigner(key.encode("utf-8"), key_id=args.key_id),
                                  max_age_s=args.max_age, label=args.label)
    except ValueError as exc:
        print(f"MISCONFIGURED: {exc}", file=sys.stderr)
        return 3
    print(("OK: " if verdict.healthy else "ALARM: ") + verdict.reason)
    return 0 if verdict.healthy else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
