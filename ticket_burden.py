"""ticket_burden.py -- what the escalation desk's own log says about how pages were handled.

`alarm_burden.py` counts what the ENGINE would page on a recorded stream. This module reads what the DESK did: the escalation log
(human_escalation.EscalationLog, a hash-chained JSON-lines file) records every ticket opened, every dispatch and its outcome, every
overdue, exhaustion and signed acknowledgement, so the burden on people can be measured from it without any new instrumentation.

Per ticket and in aggregate it counts: tickets by kind, reason and regime; tickets per day with any, and per patient-day with a ticket;
time from opening to the first successful dispatch and to acknowledgement (nearest-rank percentiles); the share acknowledged, exhausted
(nobody could be reached) and still open; tickets that needed more than one tier; failed dispatches; repeat tickets for one patient
within an hour; and how concentrated the tickets are in a few patients.

Read the numbers with these limits in mind:
  * It reads the file as it is, read-only: it takes no writer lock and repairs nothing. The hash chain is checked from the first event;
    on the first break the figures cover only the verified part and the report says so loudly. Signatures are NOT verified here (that
    needs the responders' keys: open the log with EscalationDesk for that); the report says whether the log is signed.
  * Time to acknowledge covers only tickets that WERE acknowledged. A ticket nobody acknowledged is counted separately, so a short
    median beside a large unacknowledged share is not good news.
  * It measures that a signed acknowledgement was recorded, not that anyone acted, and not whether the page was needed.
  * The ticket context holds the patient identifier (docs/race/CLINICAL_ESCALATION_NOTIFICATION.md): this report never prints one, but the
    log it reads is identifier data and belongs in a controlled environment.

Use:  python ticket_burden.py escalation_log.jsonl --source "ward 4 pilot, 2026-10-01 to 10-07" [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from alarm_burden import _percentile
from human_escalation import _event_hash

__all__ = ["TicketReport", "read_log", "analyse", "render_text", "main"]

REPEAT_WINDOW = timedelta(hours=1)
_OK_DISPATCH = ("delivered", "dispatched")


def read_log(path: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """The verified prefix of an escalation log, and what stopped it. Read-only; nothing is repaired or written."""
    entries: List[Dict[str, Any]] = []
    problems: List[str] = []
    previous = ""
    with open(Path(path), "rb") as fh:
        for n, raw in enumerate(fh, start=1):
            if not raw.strip():
                continue
            if not raw.endswith(b"\n"):
                problems.append(f"line {n}: a half-written last line (a crash mid-write); the figures stop before it")
                break
            try:
                entry = json.loads(raw)
                ok = (isinstance(entry, dict) and entry.get("seq") == len(entries) and entry.get("previous") == previous
                      and entry.get("hash") == _event_hash(entry))
            except (ValueError, KeyError, TypeError):
                ok = False
            if not ok:
                problems.append(f"line {n}: not a valid link in the hash chain; the figures stop before it")
                break
            entries.append(entry)
            previous = entry["hash"]
    return entries, problems


@dataclass
class TicketReport:
    source: str
    chain_intact: bool
    chain_problems: List[str]
    signed: bool
    events: int = 0
    first_event: Optional[str] = None
    last_event: Optional[str] = None
    tickets: int = 0
    tickets_by_kind: Dict[str, int] = field(default_factory=dict)
    tickets_by_reason: Dict[str, int] = field(default_factory=dict)
    tickets_by_regime: Dict[str, int] = field(default_factory=dict)
    days_with_a_ticket: int = 0
    tickets_per_day_with_any: Dict[str, float] = field(default_factory=dict)
    patient_days_with_a_ticket: int = 0
    tickets_per_patient_day_with_any: float = 0.0
    acknowledged: int = 0
    exhausted: int = 0
    open_at_end: int = 0
    oldest_open_seconds: Optional[float] = None
    share_acknowledged: float = 0.0
    seconds_to_first_dispatch: Dict[str, float] = field(default_factory=dict)
    seconds_to_acknowledge: Dict[str, float] = field(default_factory=dict)
    needed_more_than_one_tier: int = 0
    overdue_events: int = 0
    failed_dispatches: int = 0
    repeat_tickets_within_1h: int = 0
    share_of_tickets_from_busiest_5pct_of_patients: float = 0.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _when(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _spread(values: List[float]) -> Dict[str, float]:
    if not values:
        return {}
    ordered = sorted(values)
    return {"median": round(_percentile(ordered, 50), 1), "p90": round(_percentile(ordered, 90), 1), "max": round(ordered[-1], 1)}


def analyse(entries: Sequence[Dict[str, Any]], *, source: str, chain_problems: Sequence[str] = ()) -> TicketReport:
    """Per-ticket and aggregate burden from verified escalation events (see `read_log`)."""
    if not source or not str(source).strip():
        raise ValueError("say where the log came from (source=...): the source is part of the number")
    report = TicketReport(source=str(source).strip(), chain_intact=not chain_problems, chain_problems=list(chain_problems),
                          signed=bool(entries) and all("signature" in e for e in entries), events=len(entries))
    if not entries:
        return report
    report.first_event, report.last_event = entries[0]["at"], entries[-1]["at"]
    as_of = max(_when(e["at"]) for e in entries)
    tickets: Dict[str, Dict[str, Any]] = {}
    for e in entries:
        t = tickets.setdefault(e["ticket_id"], {"opened": None, "context": {}, "kind": "unknown", "first_ok": None, "acked": None,
                                                "exhausted": False, "tiers": set(), "last_state": "raised", "failed": 0, "overdue": 0})
        at, data = _when(e["at"]), e.get("data") or {}
        if e["event"] == "opened":
            t.update(opened=at, context=data.get("context") or {}, kind=str(data.get("kind") or "unknown"))
        elif e["event"] == "dispatch":
            if data.get("outcome") in _OK_DISPATCH:
                t["first_ok"] = t["first_ok"] or at
                t["tiers"].add(data.get("tier"))
                t["last_state"] = "dispatched"
            else:
                t["failed"] += 1
        elif e["event"] == "overdue":
            t["overdue"] += 1
            t["last_state"] = "overdue"
        elif e["event"] == "exhausted":
            t["exhausted"] = True
            t["last_state"] = "exhausted"
        elif e["event"] == "acknowledged":
            t["acked"] = t["acked"] or at
            t["last_state"] = "acknowledged"
    opened = {tid: t for tid, t in tickets.items() if t["opened"] is not None}
    report.tickets = len(opened)
    kinds, reasons, regimes = Counter(), Counter(), Counter()
    per_day: Dict[Any, int] = defaultdict(int)
    per_patient_day: Dict[Any, int] = defaultdict(int)
    per_patient: Dict[str, int] = defaultdict(int)
    last_ticket: Dict[str, datetime] = {}
    to_dispatch: List[float] = []
    to_ack: List[float] = []
    open_ages: List[float] = []
    for tid, t in sorted(opened.items(), key=lambda kv: kv[1]["opened"]):
        ctx = t["context"]
        kinds[t["kind"]] += 1
        reasons[str(ctx.get("reason", "none"))] += 1
        regimes[str(ctx.get("regime", "none"))] += 1
        day = t["opened"].astimezone(timezone.utc).date()
        per_day[day] += 1
        patient = ctx.get("patient_id")
        if patient:
            per_patient_day[(patient, day)] += 1
            per_patient[patient] += 1
            previous = last_ticket.get(patient)
            if previous is not None and t["opened"] - previous <= REPEAT_WINDOW:
                report.repeat_tickets_within_1h += 1
            last_ticket[patient] = t["opened"]
        if t["first_ok"] is not None:
            to_dispatch.append((t["first_ok"] - t["opened"]).total_seconds())
        if t["acked"] is not None:
            report.acknowledged += 1
            to_ack.append((t["acked"] - t["opened"]).total_seconds())
        elif t["last_state"] == "exhausted":
            report.exhausted += 1
        else:
            report.open_at_end += 1
            open_ages.append((as_of - t["opened"]).total_seconds())
        report.needed_more_than_one_tier += int(len(t["tiers"]) > 1)
        report.overdue_events += t["overdue"]
        report.failed_dispatches += t["failed"]
    counts = sorted(per_day.values())
    report.tickets_by_kind, report.tickets_by_reason = dict(sorted(kinds.items())), dict(sorted(reasons.items()))
    report.tickets_by_regime = dict(sorted(regimes.items()))
    if counts:
        report.days_with_a_ticket = len(counts)
        report.tickets_per_day_with_any = {"mean": round(report.tickets / len(counts), 2), "median": _percentile(counts, 50),
                                           "p90": _percentile(counts, 90), "max": counts[-1]}
    if per_patient_day:
        report.patient_days_with_a_ticket = len(per_patient_day)
        report.tickets_per_patient_day_with_any = round(sum(per_patient_day.values()) / len(per_patient_day), 2)
        busiest = sorted(per_patient.values(), reverse=True)[:max(1, math.ceil(0.05 * len(per_patient)))]
        report.share_of_tickets_from_busiest_5pct_of_patients = round(sum(busiest) / sum(per_patient.values()), 4)
    if report.tickets:
        report.share_acknowledged = round(report.acknowledged / report.tickets, 4)
    report.seconds_to_first_dispatch, report.seconds_to_acknowledge = _spread(to_dispatch), _spread(to_ack)
    report.oldest_open_seconds = round(max(open_ages), 1) if open_ages else None
    return report


def render_text(report: TicketReport) -> str:
    r = report
    lines = ["TICKET BURDEN: what the escalation desk's log records, not a measure of whether any page was needed or acted on.",
             f"source: {r.source}; log {'signed (signatures not verified here)' if r.signed else 'NOT signed'}"]
    if not r.chain_intact:
        lines += ["!! THE LOG IS NOT INTACT: " + "; ".join(r.chain_problems), "!! Figures cover the verified part only."]
    lines += [
        f"{r.events} events ({r.first_event} to {r.last_event}); {r.tickets} tickets on {r.days_with_a_ticket} days with any",
        f"  by kind {r.tickets_by_kind}; by reason {r.tickets_by_reason}; by regime {r.tickets_by_regime}",
        f"  per day with any ticket: {r.tickets_per_day_with_any}; per patient-day with a ticket: {r.tickets_per_patient_day_with_any} "
        f"({r.patient_days_with_a_ticket} patient-days)",
        f"  acknowledged {r.acknowledged} ({r.share_acknowledged:.1%}); exhausted (nobody reached) {r.exhausted}; "
        f"open at the end {r.open_at_end} (oldest {r.oldest_open_seconds} s)",
        f"  seconds to first successful dispatch {r.seconds_to_first_dispatch}; to acknowledgement (acknowledged tickets only) "
        f"{r.seconds_to_acknowledge}",
        f"  needed more than one tier: {r.needed_more_than_one_tier}; overdue events: {r.overdue_events}; failed dispatches: "
        f"{r.failed_dispatches}; repeat tickets within 1 hour for one patient: {r.repeat_tickets_within_1h}",
        f"  share of tickets from the busiest 5% of patients: {r.share_of_tickets_from_busiest_5pct_of_patients:.1%}"]
    return "\n".join(lines)


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Report the burden recorded in an escalation desk's log.")
    parser.add_argument("log", help="escalation log (JSON lines)")
    parser.add_argument("--source", required=True, help="where the log came from (printed in the report)")
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    entries, problems = read_log(args.log)
    report = analyse(entries, source=args.source, chain_problems=problems)
    print(json.dumps(report.as_dict(), indent=2, sort_keys=True) if args.json else render_text(report))
    return 0 if report.chain_intact else 2


if __name__ == "__main__":
    sys.exit(main())
