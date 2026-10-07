"""The recurring-problems report over governance findings recorded in CCC."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CCC"))

from governance_recurring_report import (  # noqa: E402
    LABEL_ANOMALY,
    LABEL_MANDATE,
    LABEL_PATTERN,
    LABEL_REVIEW,
    format_report,
    recurring_problems,
)


@dataclass(frozen=True)
class _Group:
    stage: object
    occurrences: tuple
    duplicates: tuple = ()
    conclusion: str = "artifact case-A: rejected, violations rate: 7 in window, limit 5"
    representative_id: str = "discovery_1"
    first_seen: str = "2026-05-14"
    last_seen: str = "2026-05-16"

    @property
    def needs_human_review(self):
        return getattr(self.stage, "value", self.stage) == "PATTERN" and len(self.occurrences) >= 3


class _Stage(SimpleNamespace):
    pass


ANOMALY, PATTERN, MANDATE = _Stage(value="ANOMALY"), _Stage(value="PATTERN"), _Stage(value="MANDATE")


class _CCC:
    def __init__(self, groups):
        self.groups = groups
        self.asked = []

    def recurring_groups(self, *, min_occurrences=1):
        self.asked.append(min_occurrences)
        return tuple(g for g in self.groups if len(g.occurrences) >= min_occurrences)


def test_labels_follow_ccc_stage_and_review_point():
    ccc = _CCC([
        _Group(MANDATE, ("a",)),
        _Group(PATTERN, ("b", "c", "d")),
        _Group(PATTERN, ("e", "f")),
        _Group(ANOMALY, ("g",)),
    ])
    labels = [p.label for p in recurring_problems(ccc)]
    assert labels == [LABEL_MANDATE, LABEL_REVIEW, LABEL_PATTERN, LABEL_ANOMALY]


def test_counts_dates_and_summary_come_straight_from_ccc():
    (problem,) = recurring_problems(_CCC([_Group(PATTERN, ("a", "b"), duplicates=("x",))]))
    assert (problem.occurrence_count, problem.duplicate_count) == (2, 1)
    assert (problem.first_seen, problem.last_seen) == ("2026-05-14", "2026-05-16")
    assert problem.summary.startswith("artifact case-A: rejected")
    assert problem.representative_id == "discovery_1"


def test_min_occurrences_is_passed_to_ccc():
    ccc = _CCC([_Group(ANOMALY, ("a",)), _Group(PATTERN, ("b", "c"))])
    assert len(recurring_problems(ccc, min_occurrences=2)) == 1
    assert ccc.asked == [2]


def test_a_ccc_without_recurring_groups_fails_clearly():
    with pytest.raises(RuntimeError, match="recurring_groups"):
        recurring_problems(object())


def test_report_text():
    text = format_report(recurring_problems(_CCC([
        _Group(PATTERN, ("b", "c", "d"), duplicates=("x",)),
        _Group(ANOMALY, ("g",), first_seen="2026-05-20", last_seen="2026-05-20"),
    ])))
    assert text.splitlines()[0] == "4 governance finding(s) form 2 problem(s); 1 need(s) a human look."
    assert "1. [needs a human look] seen 3x, 1 re-observation(s) not counted, 2026-05-14 to 2026-05-16" in text
    assert "2. [anomaly] seen 1x, 2026-05-20" in text


def test_empty_report():
    assert format_report([]) == "No recorded governance findings."


# --- end to end with real CCC, when the installed CCC has recurring_groups --

try:
    from ccc import CCCSystem
    from orchestrator_ccc_adapter import OrchestratorCCCAdapter, make_ccc_system
    _real = hasattr(CCCSystem, "recurring_groups")
except ImportError:  # pragma: no cover
    _real = False


@pytest.mark.skipif(not _real, reason="installed CCC predates recurring_groups (CCC PR #26); bump the pin")
def test_three_same_reason_refusals_report_as_one_problem_needing_review():
    from test_orchestrator_ccc_adapter import _approved, _refused

    adapter = OrchestratorCCCAdapter(make_ccc_system())
    adapter.record(_refused("case-A", "rate: 7 in window, limit 5", 0.31))
    adapter.record(_refused("case-B", "rate: 12 in window, limit 5", 0.62))
    adapter.record(_refused("case-C", "rate: 9 in window, limit 5", 0.47))
    adapter.record(_approved("case-D"))

    problems = adapter.recurring_problems()
    assert problems[0].label == LABEL_REVIEW
    assert problems[0].occurrence_count == 3
    assert [p.label for p in adapter.recurring_problems(min_occurrences=2)] == [LABEL_REVIEW]
    assert "1 need(s) a human look" in format_report(problems)
