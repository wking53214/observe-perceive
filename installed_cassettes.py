"""installed_cassettes.py -- every domain this tree ships, in one list.

WHY A LIST AND NOT A CONVENTION
-------------------------------
A core coupled to its only domain passes every test that domain can
write. Agnosticism is therefore not checkable with one cassette; it needs
at least two, running against the SAME core in the SAME test invocation.
This registry is what makes that structural instead of hopeful: the
cross-cassette suite parametrises over it, so a new cassette is covered
the moment it is added here, and a tree that drops back to one cassette
fails a test that says why.

WHAT A SCENARIO KIT IS FOR
--------------------------
Conformance -- does the object have the members -- is cheap and shallow.
It cannot tell you the cassette actually works, only that it type-checks.
So each entry also supplies three readings from its own domain:

  nominal   nothing wrong; must come back stable
  adverse   the domain's own "never noise" condition; must escalate
  faulted   an impossible reading; must be a data fault, never read as stable

Those three are the smallest set that exercises the whole path, and they
are written in each domain's own units by whoever knows them.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, List

from industrial_cassette import AssetReading, IndustrialCassette
from observe_consolidated import VitalsSnapshot
from pediatric_cassette import PediatricCassette

_T = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@dataclass(frozen=True)
class InstalledCassette:
    cassette: Any
    nominal: Callable[[], Any]
    adverse: Callable[[], Any]
    faulted: Callable[[], Any]

    @property
    def name(self) -> str:
        return self.cassette.name


INSTALLED: List[InstalledCassette] = [
    InstalledCassette(
        cassette=PediatricCassette(),
        nominal=lambda: VitalsSnapshot("SUBJ-1", _T, 100, 97.0, 24, 37.0,
                                       {"age_months": 24}),
        # A lone CRITICAL_O2 with tachycardia: this domain's hard rule.
        adverse=lambda: VitalsSnapshot("SUBJ-1", _T, 155, 85.0, 30, 37.0,
                                       {"age_months": 24, "force_heavy": True}),
        faulted=lambda: VitalsSnapshot("SUBJ-1", _T, float("nan"), 97.0, 24, 37.0,
                                       {"age_months": 24}),
    ),
    InstalledCassette(
        cassette=IndustrialCassette(),
        nominal=lambda: AssetReading("SUBJ-1", _T, 2.1, 55.0, 3.2),
        # Vibration in the ISO 10816 unacceptable zone, oil starvation.
        adverse=lambda: AssetReading("SUBJ-1", _T, 9.4, 98.0, 0.6),
        faulted=lambda: AssetReading("SUBJ-1", _T, float("nan"), 55.0, 3.2),
    ),
]

#: Below this the tree can no longer demonstrate what it claims.
MINIMUM_DOMAINS = 2


def verify_registry(entries: List[InstalledCassette]) -> None:
    """Raise unless the registry can still support the agnosticism claim.

    A function rather than an inline assertion so the rule itself can be
    tested against a deliberately broken registry. A guard that has never
    once been shown to fire is indistinguishable from a comment.
    """
    if len(entries) < MINIMUM_DOMAINS:
        raise AssertionError(
            f"only {len(entries)} cassette(s) installed. At least "
            f"{MINIMUM_DOMAINS} unrelated domains must run against this core in "
            "one test invocation, or 'industry-agnostic' is an untested claim."
        )
    names = [e.name for e in entries]
    if len(set(names)) != len(names):
        raise AssertionError(f"domain names must be distinct, got {names}")
    types = {type(e.nominal()) for e in entries}
    if len(types) != len(entries):
        raise AssertionError(
            "each domain must bring its own observation type; sharing one "
            "would let a field-level coupling survive undetected"
        )
