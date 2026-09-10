"""How a known clinical detection gap is recorded.

WHY THIS IS NOT A SKIP
----------------------
Until now each of the two clinical test modules carried its own copy of
this helper, and both called `skipTest`. A skip is invisible: pytest
reports the run as green, the count of skips is buried among the dozens
of legitimate "sibling checkout not available" skips, and nothing in the
output distinguishes a missing dependency from a child whose
deterioration this engine failed to escalate.

Measured 2026-09-10: five missed detections in a pediatric deterioration
engine were sitting behind skips, and a plain `pytest` run reported
success. The docstring on the old helper said so out loud -- "that
default hides five missed detections behind a green run" -- which made
the concealment honest but did not make it any less concealing.

An expected failure is the right category and always was. `xfail` keeps
the suite green (so CI still catches regressions, which is what CI is
for) while listing every gap by name in the run summary, and it fails
loudly with XPASS the day a gap is actually closed, which is the signal
you most want and the one a skip can never give you.

Set OBSERVE_STRICT_CLINICAL=1 to turn every gap into a hard failure --
for a deployment gate, where "known and recorded" is not good enough.
"""
from __future__ import annotations

import os

import pytest

STRICT_ENV = "OBSERVE_STRICT_CLINICAL"


def strict() -> bool:
    return bool(os.environ.get(STRICT_ENV))


def known_gap(testcase, message: str) -> None:
    """Record a known clinical detection gap and stop the test.

    Never returns. Raises the failure or the xfail signal, so a caller
    cannot accidentally continue past a recorded gap and assert something
    else about a scenario the engine has already got wrong.
    """
    if strict():
        testcase.fail(f"known clinical gap ({STRICT_ENV} set): {message}")
    pytest.xfail(message)
