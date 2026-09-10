"""cassette.py -- what a domain must supply, and nothing about any domain.

This module deliberately imports NOTHING clinical. That is not a style
preference; it is the test. If a pediatric name ever appears here, the
core has stopped being industry-agnostic and the seam has leaked.

WHY THIS EXISTS
---------------
The machinery in this system -- confidence-weighted fusion, abstention
handling, dwell and hysteresis, regime classification, the audit ledger,
the scheduler -- knows nothing about children, and never did. What made
it look clinical was that the domain rules sat in the same file with no
interface between them.

Measured 2026-09-10 on observe_consolidated.py (1,407 lines): outside the
domain's own functions, the engine touched a vital sign in exactly two
places. Everything else flowed through an identity, a timestamp, and a
context dict. The seam was already there; it just had no name.

WHAT A CASSETTE IS
------------------
A cassette is one industry's answer to seven questions. Give the core a
cassette and it will observe, fuse, escalate and audit that domain
without a line of domain code in it.

  subject_id(obs)              what this observation is ABOUT. A string
                               key, not a field name -- a clinic keys on
                               a patient, a lender on an account, a plant
                               on an asset, a court on a case. The core
                               never assumes which.
  validate(obs)                domain faults. A non-empty list means the
                               reading is a data fault, not a state, and
                               must never be scored as normal.
  context_bounds()             the numeric trust boundary: which context
                               keys are numbers, and what range is
                               physically possible in this domain. What
                               counts as impossible is domain knowledge.
  select_engines(obs, entropy) which engines are worth running. The core
                               supplies recent entropy; the cassette
                               decides.
  engines()                    name -> callable(obs) -> RiskOutput.
  hard_rule_fired(outputs)     is this a rule so unambiguous that waiting
                               for a second confirming reading is itself
                               the harm? The core cannot know. Only the
                               domain can say what is never noise.
  channels(obs)                named numeric series for trajectory
                               tracking. A cassette declares its own
                               channels; the tracker is generic over
                               however many there are.

WHAT A CASSETTE IS NOT
----------------------
It is not a place for policy. Dwell thresholds, regime boundaries and
fusion weights stay in the core, because those are statements about how
to reason under uncertainty, not about a domain. A cassette that starts
carrying thresholds is a cassette turning back into an engine.
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple, runtime_checkable


@runtime_checkable
class Observation(Protocol):
    """One reading about one subject at one moment.

    The core requires only a timestamp and a context dict. Identity is
    reached through the cassette rather than a field, so a domain may
    call its subject whatever it calls it.
    """
    timestamp: Any
    context: Dict[str, Any]


@runtime_checkable
class Cassette(Protocol):
    """One industry, expressed as the seven things the core cannot know."""

    name: str
    version: str

    def subject_id(self, obs: Any) -> str: ...
    def validate(self, obs: Any) -> List[str]: ...
    def context_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]: ...
    def select_engines(self, obs: Any, recent_entropy: float) -> List[str]: ...
    def engines(self) -> Mapping[str, Callable[[Any], Any]]: ...
    def hard_rule_fired(self, outputs: List[Any]) -> bool: ...
    def channels(self, obs: Any) -> Mapping[str, float]: ...


#: Every member the core will reach for, in one place, so a conformance
#: failure names the missing piece instead of raising an AttributeError
#: somewhere deep in an evaluation.
REQUIRED = (
    "name", "version", "subject_id", "validate", "context_bounds",
    "select_engines", "engines", "hard_rule_fired", "channels",
)

_CALLABLE = REQUIRED[2:]


def conformance_failures(candidate: Any) -> List[str]:
    """Every reason `candidate` is not a usable cassette, or an empty list.

    Returned rather than raised so a caller can report all of them at
    once. A partially-conforming cassette that fails on its fourth
    evaluation is worse than one rejected at load.
    """
    problems: List[str] = []
    for member in REQUIRED:
        if not hasattr(candidate, member):
            problems.append(f"missing {member!r}")
            continue
        if member in _CALLABLE and not callable(getattr(candidate, member)):
            problems.append(f"{member!r} is not callable")
    for member in ("name", "version"):
        value = getattr(candidate, member, None)
        if value is not None and not isinstance(value, str):
            problems.append(f"{member!r} must be a str, got {type(value).__name__}")
    return problems


def require_cassette(candidate: Any) -> None:
    """Raise unless `candidate` can serve as a cassette."""
    problems = conformance_failures(candidate)
    if problems:
        raise TypeError(
            f"{candidate!r} is not a usable cassette: " + "; ".join(problems)
        )
