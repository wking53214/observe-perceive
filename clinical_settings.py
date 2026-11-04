"""clinical_settings.py -- the values a clinician may tune, in one validated place.

WHY THIS EXISTS

Clinical thresholds were constants in source, so changing one meant changing code. Most of them are not
engineering decisions and some are still marked provisional pending clinician sign-off. A value a clinician
owns should be a value a clinician can change, without anyone needing to settle it today.

HOW IT BEHAVES

* Every default is the value the system already used (or, for the population bound and the hard-rule
  corroboration, the value named in its docstring). A system built with no settings behaves, and
  fingerprints, exactly as before, apart from those two documented defaults.
* Nothing is tuned silently. A non-default setting is recorded in the cassette identity that goes into every
  decision's fingerprint (`PediatricCassette.identity_extras`), by name and by a digest of the full set, so
  any verdict can be traced to the settings that produced it. A change that makes the system less likely to
  alert is also listed under `weakenings()` and logged at WARNING when a cassette is built.
* An unknown key raises. A misspelt setting that quietly did nothing would be worse than none.
* A value that would break an assumption elsewhere raises with the reason: for example a norm table that
  would make the neutral placeholder used for a faulted channel a clinical finding.

This module changes no clinical judgement. It does not say who may change a setting or how the change is
approved: that is the hospital's release process (see the roadmap), and a digest in every record is what
lets that process be audited.

Population. This system is pediatric. `max_age_months` bounds the supported population; a reading above it
is assessed on the pediatric norms anyway (a deteriorating adult must not be scored less severely for
being out of scope) but is floored at WARNING and carries a named finding, so a person looks. Set
`outside_population` to "assess_anyway" to turn the finding off, or raise `max_age_months` (None removes
the bound). Adult norms are a future cassette, not a setting.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Mapping, Optional, Tuple

from observe_consolidated import AGE_GROUP_UPPER_MONTHS, PEDIATRIC_NORMS

__all__ = ["ClinicalSettings", "SettingsError"]

OUTSIDE_POPULATION_ACTIONS = ("alert", "assess_anyway")
_NORM_KEYS = ("hr_high", "hr_low", "rr_high", "o2_low", "temp_high")
# What a faulted channel is read as (pediatric_cassette._VITALS_NEUTRAL). A norm table must keep these clear
# of every threshold, or masking a channel would itself produce a finding.
_NEUTRAL = {"heart_rate": 110.0, "oxygen_saturation": 98.0, "respiratory_rate": 25.0, "temperature": 37.0}


class SettingsError(ValueError):
    """A setting is unknown, malformed, or would break an assumption the engine relies on."""


def _num(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise SettingsError(f"{name} must be a finite number, got {value!r}")
    return float(value)


def _default_norms() -> Dict[str, Dict[str, float]]:
    return copy.deepcopy(PEDIATRIC_NORMS)


@dataclass(frozen=True)
class ClinicalSettings:
    # --- population ---
    max_age_months: Optional[float] = 216.0      # 18 years; None = no upper bound
    outside_population: str = "alert"            # "alert" | "assess_anyway"
    require_age: bool = False                    # True: a reading with no age is a finding, not "generic norms"
    # --- age groups and norms ---
    age_group_upper_months: Tuple[float, float, float] = AGE_GROUP_UPPER_MONTHS
    norms: Dict[str, Dict[str, float]] = field(default_factory=_default_norms)
    # --- heuristic hard-rule triggers ---
    critical_o2_below: float = 88.0
    hypothermia_below: float = 35.0
    # --- hard-rule confirmation (see observe_consolidated.HARD_RULE_CONFIRM_WINDOW) ---
    hard_rule_confirm_window: int = 3
    hard_rule_corroboration: bool = True
    hard_rule_corroboration_margin: float = 0.05
    # --- annotation only: never changes a regime ---
    probe_off_spo2_at_or_below: Optional[float] = 0.0

    def __post_init__(self) -> None:
        norms = copy.deepcopy(self.norms)
        object.__setattr__(self, "norms", norms)
        object.__setattr__(self, "age_group_upper_months", tuple(self.age_group_upper_months))
        self._validate()

    # -- validation ------------------------------------------------------
    def _validate(self) -> None:
        if self.max_age_months is not None and not 0 < _num(self.max_age_months, "max_age_months") <= 1500:
            raise SettingsError("max_age_months must be in (0, 1500] or None")
        if self.outside_population not in OUTSIDE_POPULATION_ACTIONS:
            raise SettingsError(f"outside_population must be one of {OUTSIDE_POPULATION_ACTIONS}")
        if not isinstance(self.require_age, bool):
            raise SettingsError("require_age must be true or false")
        b = [_num(x, "age_group_upper_months") for x in self.age_group_upper_months]
        if len(b) != 3 or b[0] <= 0 or not b[0] < b[1] < b[2]:
            raise SettingsError("age_group_upper_months must be three increasing positive numbers")
        for group, row in self.norms.items():
            if group not in PEDIATRIC_NORMS:
                raise SettingsError(f"unknown age group {group!r} in norms; known: {sorted(PEDIATRIC_NORMS)}")
            if not isinstance(row, Mapping) or set(row) != set(_NORM_KEYS):
                raise SettingsError(f"norms[{group!r}] needs exactly {list(_NORM_KEYS)}")
            v = {k: _num(row[k], f"norms[{group!r}][{k!r}]") for k in _NORM_KEYS}
            if not 0 < v["hr_low"] < _NEUTRAL["heart_rate"] < v["hr_high"] <= 350:
                raise SettingsError(f"norms[{group!r}]: hr_low < {_NEUTRAL['heart_rate']:g} < hr_high is required "
                                    "(110 is the placeholder for a faulted heart-rate channel)")
            if not _NEUTRAL["respiratory_rate"] < v["rr_high"] <= 150:
                raise SettingsError(f"norms[{group!r}]: rr_high must exceed {_NEUTRAL['respiratory_rate']:g} "
                                    "(the faulted-channel placeholder)")
            if not 0 < v["o2_low"] < _NEUTRAL["oxygen_saturation"]:
                raise SettingsError(f"norms[{group!r}]: o2_low must be in (0, {_NEUTRAL['oxygen_saturation']:g})")
            if not _NEUTRAL["temperature"] < v["temp_high"] <= 45:
                raise SettingsError(f"norms[{group!r}]: temp_high must exceed {_NEUTRAL['temperature']:g}")
        if not 0 < _num(self.critical_o2_below, "critical_o2_below") < _NEUTRAL["oxygen_saturation"]:
            raise SettingsError(f"critical_o2_below must be in (0, {_NEUTRAL['oxygen_saturation']:g})")
        if not 20 < _num(self.hypothermia_below, "hypothermia_below") < _NEUTRAL["temperature"]:
            raise SettingsError(f"hypothermia_below must be in (20, {_NEUTRAL['temperature']:g})")
        if isinstance(self.hard_rule_confirm_window, bool) or not isinstance(self.hard_rule_confirm_window, int) \
                or not 1 <= self.hard_rule_confirm_window <= 20:
            raise SettingsError("hard_rule_confirm_window must be a whole number of readings from 1 to 20")
        if not isinstance(self.hard_rule_corroboration, bool):
            raise SettingsError("hard_rule_corroboration must be true or false")
        if not 0 <= _num(self.hard_rule_corroboration_margin, "hard_rule_corroboration_margin") <= 1:
            raise SettingsError("hard_rule_corroboration_margin must be in [0, 1]")
        if self.probe_off_spo2_at_or_below is not None \
                and not 0 <= _num(self.probe_off_spo2_at_or_below, "probe_off_spo2_at_or_below") < 100:
            raise SettingsError("probe_off_spo2_at_or_below must be in [0, 100) or None")

    # -- construction ------------------------------------------------------
    @classmethod
    def from_mapping(cls, overrides: Optional[Mapping[str, Any]] = None) -> "ClinicalSettings":
        """Defaults with `overrides` applied. `norms` may name just the groups and keys it changes."""
        overrides = dict(overrides or {})
        known = {f.name for f in fields(cls)}
        unknown = sorted(set(overrides) - known)
        if unknown:
            raise SettingsError(f"unknown setting(s) {unknown}; known: {sorted(known)}")
        if "norms" in overrides:
            merged = _default_norms()
            for group, row in dict(overrides["norms"]).items():
                if group not in merged:
                    raise SettingsError(f"unknown age group {group!r} in norms; known: {sorted(merged)}")
                bad = sorted(set(row) - set(_NORM_KEYS))
                if bad:
                    raise SettingsError(f"unknown norm key(s) {bad} for {group!r}; known: {list(_NORM_KEYS)}")
                merged[group].update(row)
            overrides["norms"] = merged
        return cls(**overrides)

    @classmethod
    def from_json(cls, text: str) -> "ClinicalSettings":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise SettingsError(f"settings are not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SettingsError("settings JSON must be an object")
        return cls.from_mapping(data)

    # -- what the record needs ---------------------------------------------
    def as_dict(self) -> Dict[str, Any]:
        out = {f.name: getattr(self, f.name) for f in fields(self)}
        out["age_group_upper_months"] = list(self.age_group_upper_months)
        out["norms"] = copy.deepcopy(self.norms)
        return out

    def digest(self) -> str:
        """SHA-256 of the full, canonical settings: any change to any value changes it."""
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()

    def non_default(self) -> Dict[str, str]:
        """Dotted setting name to its value as text, for each value that is not the default. Empty at defaults."""
        base, mine = ClinicalSettings().as_dict(), self.as_dict()
        changed: Dict[str, str] = {}
        for key in sorted(mine):
            if key == "norms":
                for group in sorted(mine["norms"]):
                    for k in _NORM_KEYS:
                        if mine["norms"][group][k] != base["norms"][group][k]:
                            changed[f"norms.{group}.{k}"] = f"{mine['norms'][group][k]:g}"
            elif mine[key] != base[key]:
                changed[key] = str(mine[key])
        return changed

    # direction in which a value makes the system LESS likely to alert
    _WEAKER_WHEN_HIGHER = ("norms.*.hr_high", "norms.*.rr_high", "norms.*.temp_high", "hard_rule_confirm_window",
                           "hard_rule_corroboration_margin")
    _WEAKER_WHEN_LOWER = ("norms.*.hr_low", "norms.*.o2_low", "critical_o2_below", "hypothermia_below")

    def weakenings(self) -> List[str]:
        """Names of the non-default settings that make an alert less likely than the default does."""
        base, mine = ClinicalSettings().as_dict(), self.as_dict()
        weak: List[str] = []

        def check(name: str, new: float, old: float, higher_is_weaker: bool) -> None:
            if (new > old) if higher_is_weaker else (new < old):
                weak.append(name)

        for group in sorted(mine["norms"]):
            for k in _NORM_KEYS:
                pattern = f"norms.*.{k}"
                if pattern in self._WEAKER_WHEN_HIGHER or pattern in self._WEAKER_WHEN_LOWER:
                    check(f"norms.{group}.{k}", mine["norms"][group][k], base["norms"][group][k],
                          pattern in self._WEAKER_WHEN_HIGHER)
        for key in ("hard_rule_confirm_window", "hard_rule_corroboration_margin"):
            check(key, mine[key], base[key], True)
        for key in ("critical_o2_below", "hypothermia_below"):
            check(key, mine[key], base[key], False)
        if self.hard_rule_corroboration is False:
            weak.append("hard_rule_corroboration")
        if self.outside_population == "assess_anyway":
            weak.append("outside_population")
        return sorted(weak)

    def engine_params_are_default(self) -> bool:
        """True when none of the values the risk engines read differ from the engines' own defaults."""
        base = ClinicalSettings()
        return (self.norms == base.norms and self.age_group_upper_months == base.age_group_upper_months
                and self.critical_o2_below == base.critical_o2_below
                and self.hypothermia_below == base.hypothermia_below)
