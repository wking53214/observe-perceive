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
  context_bounds()             the numeric trust boundary for scalar
                               context keys: which are numbers, and what
                               range is physically possible in this
                               domain. What counts as impossible is
                               domain knowledge, which is exactly why the
                               core cannot hold this table.
  context_list_bounds()        the same, for keys holding a LIST of
                               numbers, whose bad elements are filtered
                               rather than the whole key dropped. Kept
                               separate rather than inferred from the
                               value's type, because "a list arrived
                               where a scalar was declared" is itself a
                               fault worth dropping.
  select_engines(obs, entropy) which engines are worth running. The core
                               supplies recent entropy; the cassette
                               decides.
  engines()                    name -> callable(obs) -> RiskOutput.
  hard_rule_fired(outputs)     is this a rule so unambiguous that waiting
                               for a second confirming reading is itself
                               the harm? The core cannot know. Only the
                               domain can say what is never noise.
  channels(obs)                named numeric series for trajectory
                               tracking, as {name: value}. A cassette
                               declares its own; the tracker is generic
                               over however many there are.
  labels()                     what this domain calls things in the
                               record: {"record_kind": ..., 
                               "safety_bypass": ...}. The EVENTS are the
                               core's -- an assessment happened, a bypass
                               fired -- but the words go into an
                               append-only audit ledger, and a governed
                               domain does not get its vocabulary changed
                               underneath it by a refactor. Missing keys
                               fall back to neutral defaults.
  channel_model()              {name: ChannelModel} -- the noise and
                               adverse-trend description for each of
                               those series. Sensor noise and "which
                               direction is bad" are facts about a
                               domain's instruments, not about how to
                               reason.

WHAT A CASSETTE IS NOT
----------------------
It is not a place for policy. Dwell thresholds, regime boundaries and
fusion weights stay in the core, because those are statements about how
to reason under uncertainty, not about a domain. A cassette that starts
carrying thresholds is a cassette turning back into an engine.

WHAT A CASSETTE MAY SUPPLY
--------------------------
One more answer is optional. A cassette without it is still a cassette.

  mask_faults(obs, faults)     given a reading validate() faulted and the
                               very list it returned, the reading to assess
                               in its place: each faulted channel set to a
                               value that is a finding in no engine of this
                               domain, and every context key that carries
                               that channel's history removed, so the
                               channels that still work are still scored.
                               None when nothing can be assessed: a fault
                               that names no known channel, or every
                               channel faulted. Must not mutate obs, must
                               keep its identity and timestamp, and must be
                               deterministic.

Which value is inert, which keys carry a channel's past, and how a fault
names its channel are facts about a domain's instruments, so they live in
the cassette. What to do with a partial view (a floor, a penalty, a frozen
policy, deduplicated paging) is reasoning under uncertainty, so it stays
in the core. A cassette that does not supply mask_faults gets the plain
fault path: nothing scored, a WARNING record.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Mapping, Optional, Protocol, Tuple, runtime_checkable


@dataclass(frozen=True)
class ChannelModel:
    """How one numeric series behaves, and what a bad trend looks like in it.

    Plain numbers, no units, no domain. A tracker uses these to separate
    signal from jitter; it never needs to know whether the series is a
    heart rate or a bearing temperature.

    process_noise_position / process_noise_velocity
        how much the true value and its rate are expected to move on
        their own between readings. Larger = trust the trend less.
    measurement_noise
        how noisy the sensor is. Larger = trust each reading less.
    adverse_direction
        -1 if falling is the dangerous direction, +1 if rising is, 0 if
        the series has no dangerous direction and only surprise matters.
    adverse_rate
        magnitude of change per hour that counts as a dangerous trend.
    weight
        how much such a trend contributes to risk, 0.0-1.0.
    """
    process_noise_position: float
    process_noise_velocity: float
    measurement_noise: float
    adverse_direction: int = 0
    adverse_rate: float = 0.0
    weight: float = 0.0


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
    def context_list_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]: ...
    def select_engines(self, obs: Any, recent_entropy: float) -> List[str]: ...
    def engines(self) -> Mapping[str, Callable[[Any], Any]]: ...
    def hard_rule_fired(self, outputs: List[Any]) -> bool: ...
    def channels(self, obs: Any) -> Mapping[str, float]: ...
    def channel_model(self) -> Mapping[str, "ChannelModel"]: ...
    def labels(self) -> Mapping[str, str]: ...


@runtime_checkable
class PartiallyAssessable(Protocol):
    """The optional answer: a faulted reading with its faulted channels made
    inert, or None when nothing in it can be assessed. Kept out of Cassette
    so a domain without it still satisfies that protocol."""

    def mask_faults(self, obs: Any, faults: List[str]) -> Optional[Any]: ...


#: Every member the core will reach for, in one place, so a conformance
#: failure names the missing piece instead of raising an AttributeError
#: somewhere deep in an evaluation.
REQUIRED = (
    "name", "version", "subject_id", "validate", "context_bounds",
    "context_list_bounds", "select_engines", "engines", "hard_rule_fired",
    "channels", "channel_model", "labels",
)

_CALLABLE = REQUIRED[2:]

#: Members a cassette MAY supply. Absence is an answer ("this domain cannot
#: assess a reading partially"), not a conformance failure. Presence without
#: callability is a failure: a capability declared wrongly is rejected at
#: load rather than quietly skipped on some later evaluation.
OPTIONAL = ("mask_faults",)


#: Used when a cassette omits a label key. Neutral on purpose: a domain
#: that has not said what it calls something should not inherit another
#: domain's word for it.
DEFAULT_LABELS = {"record_kind": "assessment", "safety_bypass": "SAFETY_BYPASS"}


def label(cassette: Any, key: str) -> str:
    """The domain's word for `key`, or the neutral default."""
    try:
        supplied = cassette.labels()
    except Exception:
        return DEFAULT_LABELS[key]
    return supplied.get(key, DEFAULT_LABELS[key]) if supplied else DEFAULT_LABELS[key]


def optional_member(cassette: Any, name: str) -> Optional[Callable[..., Any]]:
    """The cassette's optional member `name`, or None when it supplies none.

    Unlike label(), a call through the returned member is not guarded: a
    mask is substantive, and an exception from it must surface rather than
    turn into a plausible verdict.
    """
    member = getattr(cassette, name, None)
    return member if callable(member) else None


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
    for member in OPTIONAL:
        if hasattr(candidate, member) and not callable(getattr(candidate, member)):
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
