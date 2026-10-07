"""Recurring problems across governance decisions, read from CCC.

orchestrator_ccc_adapter records every governed decision into CCC as a
machine finding. CCC links a repeat of the same problem to the earlier one
and runs its anomaly -> pattern ladder on the group. This report asks CCC
for those groups (`CCCSystem.recurring_groups()`) and turns them into a
short list an operator can act on: "these 14 refusals are 3 recurring
problems, and one of them needs a human look."

What it does not do:

- It does not decide what counts as the same problem. CCC does, with the
  same rule it uses to climb the ladder, so this report cannot disagree
  with CCC's own record.
- It keeps no copy of CCC's store and writes nothing to it.
- It never escalates. "needs a human look" is CCC's own third-strike point
  (a PATTERN with three or more independent occurrences); MANDATE is
  something only a human sets in CCC, and is reported as such.

Labels, from CCC's stage:
    anomaly             seen once (or only re-observed)
    pattern             seen independently twice
    needs a human look  seen independently three or more times
    mandate (human)     a human has established it in CCC
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

__all__ = ["RecurringProblem", "recurring_problems", "format_report"]

LABEL_ANOMALY = "anomaly"
LABEL_PATTERN = "pattern"
LABEL_REVIEW = "needs a human look"
LABEL_MANDATE = "mandate (human)"


@dataclass(frozen=True)
class RecurringProblem:
    label: str
    occurrence_count: int
    duplicate_count: int
    first_seen: str
    last_seen: str
    summary: str
    representative_id: str


def _stage_name(group) -> str:
    stage = getattr(group, "stage", None)
    return getattr(stage, "value", stage) or ""


def _label(group) -> str:
    stage = _stage_name(group)
    if stage == "MANDATE":
        return LABEL_MANDATE
    if getattr(group, "needs_human_review", False):
        return LABEL_REVIEW
    if stage == "PATTERN":
        return LABEL_PATTERN
    return LABEL_ANOMALY


def recurring_problems(ccc_system, *, min_occurrences: int = 1) -> List[RecurringProblem]:
    """The recurring problems CCC holds, most serious first (CCC's order).

    `ccc_system` is a CCCSystem (or anything with `recurring_groups`).
    `min_occurrences=2` leaves out one-off findings.
    """
    lister = getattr(ccc_system, "recurring_groups", None)
    if lister is None:
        raise RuntimeError(
            "this CCC has no recurring_groups(); it was added in CCC PR #26. "
            "Update the CCC pin in pyproject.toml to a commit that includes it."
        )
    return [
        RecurringProblem(
            label=_label(group),
            occurrence_count=len(group.occurrences),
            duplicate_count=len(group.duplicates),
            first_seen=group.first_seen,
            last_seen=group.last_seen,
            summary=group.conclusion,
            representative_id=group.representative_id,
        )
        for group in lister(min_occurrences=min_occurrences)
    ]


def format_report(problems: List[RecurringProblem], *, width: int = 100) -> str:
    """A plain-text summary, one block per problem."""
    if not problems:
        return "No recorded governance findings."
    findings = sum(p.occurrence_count for p in problems)
    review = sum(1 for p in problems if p.label == LABEL_REVIEW)
    lines = [
        f"{findings} governance finding(s) form {len(problems)} problem(s); "
        f"{review} need(s) a human look.",
        "",
    ]
    for number, problem in enumerate(problems, start=1):
        summary = problem.summary if len(problem.summary) <= width else problem.summary[: width - 3] + "..."
        span = problem.first_seen if problem.first_seen == problem.last_seen else f"{problem.first_seen} to {problem.last_seen}"
        repeats = f", {problem.duplicate_count} re-observation(s) not counted" if problem.duplicate_count else ""
        lines.append(f"{number}. [{problem.label}] seen {problem.occurrence_count}x{repeats}, {span}")
        lines.append(f"   {summary}")
        lines.append(f"   CCC record: {problem.representative_id}")
    return "\n".join(lines)
