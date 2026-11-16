"""pediatric_cassette.py -- the sepsis/deterioration domain, as a cassette.

Everything here is an answer to a question the core is not entitled to
have an opinion about: what a subject is, what a fault is, what is
physically possible, which engines are worth running, and what is so
unambiguous that waiting for a second reading is itself the harm.

Nothing here is new. Every rule is the rule observe_consolidated.py
already applied; this module gives it a name and a boundary so a second
industry can supply its own answers without touching the engine.
"""
from __future__ import annotations

import logging
import math
from dataclasses import replace
from functools import partial
from typing import Any, Callable, Dict, List, Mapping, Optional, Set, Tuple

from cassette import ChannelModel
from clinical_settings import (
    DEFAULT_HARD_RULE_CONFIRM_WINDOW,
    DEFAULT_HARD_RULE_CORROBORATION,
    DEFAULT_HARD_RULE_CORROBORATION_MARGIN,
    MAX_HARD_RULE_CONFIRM_WINDOW,
    MIN_HARD_RULE_CORROBORATION_MARGIN,
    ClinicalSettings,
)
from kalman_trajectory import _PEDIATRIC_CHANNEL_MODEL
from logic_stamp import logic_digest

from observe_consolidated import (
    RiskAdapters,
    _NUMERIC_CONTEXT_LIST_BOUNDS,
    _NUMERIC_CONTEXT_SCALAR_BOUNDS,
    _has_physiological_telemetry,
    ObserveClinicalEngine,
    RiskOutput,
    validate_vitals,
)

#: Engines that assess this domain. Same table the engine has always used.
_ENGINES = dict(ObserveClinicalEngine.ENGINE_MAP)

#: Risk at or above which the heuristic's verdict is a hard rule rather
#: than a vote. Lifted verbatim from the escalation path so there is one
#: definition, reachable by a test -- it previously lived as a literal
#: inside `evaluate` where nothing could observe it.
HARD_RULE_RISK = 0.5

#: The engine whose hard rules carry that weight in this domain.
HARD_RULE_ENGINE = "heuristic"

#: How a hard rule is confirmed (see PediatricCassette): the defaults and the sanity limits live with the other settings
#: in clinical_settings and are imported above, so there is one definition. A lone hard-rule reading may be a sensor
#: artefact, so it is judged as the fusion judges it; the same rule firing again within the window of the patient's
#: readings, or any other abnormal finding beside it, is not an artefact and holds the regime at WARNING. All three are
#: the clinician's to change; the defaults are a starting point, not a clinical finding.
logger = logging.getLogger("PEDIATRIC_CASSETTE")
#: The name of the finding a reading outside the supported population carries.
POPULATION_FINDING = "OUTSIDE_SUPPORTED_POPULATION"
_UNSET = object()
_LOGIC_DIGEST: Optional[str] = None
#: Heuristic scores are sums of decimals (0.2 + 0.2 + 0.15 is 0.55000000000000004), so the margin comparison is made
#: with this slack and a score exactly at the margin counts on every platform.
_SCORE_TOLERANCE = 1e-9

#: The heuristic's finding names (the text before the colon in its rules), grouped by channel, so corroboration can tell
#: heart rate from the others. A rule not named here counts as another channel, the direction that escalates.
_HEART_RATE_FINDINGS = frozenset({"TACHYCARDIA", "BRADYCARDIA"})
_SPO2_FINDINGS = frozenset({"CRITICAL_O2", "WARNING_O2"})

#: What a faulted channel is read as so the OTHER channels can still be
#: assessed. Each value is inside every PEDIATRIC_NORMS window and clear of
#: every heuristic, bayesian, behavioral and select_engines trigger, so a
#: masked channel is a finding in no engine; the fault itself is reported
#: by the core. A placeholder, never an estimate of the child.
_VITALS_NEUTRAL: Dict[str, float] = {
    "heart_rate": 110.0,
    "oxygen_saturation": 98.0,
    "respiratory_rate": 25.0,
    "temperature": 37.0,
}

#: Every context key an engine here reads for a channel. Dropped with the
#: channel, so trajectory, drift, adversarial and the instability axis
#: abstain on it instead of setting a faulted sensor's past against the
#: placeholder (masking O2 to 98 beside previous_o2=90 fabricated an
#: uptrend in the fork's first cut).
_CHANNEL_CONTEXT_KEYS: Dict[str, Tuple[str, ...]] = {
    "oxygen_saturation": ("previous_o2", "baseline_o2", "history_o2", "recent_o2_readings"),
    "heart_rate": ("previous_hr", "baseline_hr", "history_hr", "hr_history"),
    "respiratory_rate": ("previous_rr",),
    "temperature": ("previous_temp",),
}


#: How `select_engines` decides to run the behavioral (syndrome) engine.
#: The profile is chosen once, where the cassette is built:
#: `PediatricCassette(engine_gate=...)`. An unknown name raises; it never falls
#: back to the default.
ENGINE_GATE_WIDENED_PROVISIONAL = "widened_provisional"

#: The narrower rule OBSERVE's own engine has used: the behavioral engine runs
#: only for oxygen below 92, or heart rate above 140 or below 90. For a
#: deployment that has not had a pediatrician sign off the widened rule.
ENGINE_GATE_CONSERVATIVE_LEGACY = "conservative_legacy"

#: The gate every existing caller gets. Its behaviour, and so every fingerprint
#: made under it, is unchanged.
DEFAULT_ENGINE_GATE = ENGINE_GATE_WIDENED_PROVISIONAL

ENGINE_GATES = (ENGINE_GATE_WIDENED_PROVISIONAL, ENGINE_GATE_CONSERVATIVE_LEGACY)


class PediatricCassette:
    """Pediatric deterioration: the original domain, behind the seam."""

    name = "pediatric_deterioration"
    # 1.1.0: supplies mask_faults. The hard-rule confirmation below is branch logic attested by the source revision, as
    # evaluate's other branches are; a setting that is not at its default is recorded in `identity_extras`, and any lift
    # it causes in the decision's `safety_bypass` note.
    # The version stays 1.1.0 across the default changes of decisions D1 and D2 (window 1, heart rate alone does not corroborate),
    # as it did across the hard-rule floor and the population floor, so recorded fingerprints keep their meaning. The cost is
    # known and listed in the register: an entry with empty `identity_extras` was made under the defaults of ITS source
    # revision, and nothing in the entry says which. Closing that is the parameter-set change (decision D9), which puts these
    # defaults into the version stamp once, on purpose.
    version = "1.1.0"

    def __init__(self, engine_gate: str = DEFAULT_ENGINE_GATE, *,
                 settings: Optional[ClinicalSettings] = None,
                 hard_rule_confirm_window: Any = _UNSET,
                 hard_rule_corroboration: Any = _UNSET,
                 hard_rule_corroboration_margin: Any = _UNSET) -> None:
        if engine_gate not in ENGINE_GATES:
            raise ValueError(
                f"unknown engine_gate {engine_gate!r}; expected one of {sorted(ENGINE_GATES)}"
            )
        if settings is not None and not isinstance(settings, ClinicalSettings):
            raise TypeError("settings must be a ClinicalSettings (ClinicalSettings.from_mapping builds one)")
        given = {name: value for name, value in (("hard_rule_confirm_window", hard_rule_confirm_window),
                                                 ("hard_rule_corroboration", hard_rule_corroboration),
                                                 ("hard_rule_corroboration_margin", hard_rule_corroboration_margin))
                 if value is not _UNSET}
        if settings is not None and given:
            raise ValueError(f"{sorted(given)} were passed beside `settings`; the hard-rule values are settings, "
                             "so give them there or as arguments, not both")
        self.engine_gate = engine_gate
        if hard_rule_confirm_window is _UNSET:
            hard_rule_confirm_window = DEFAULT_HARD_RULE_CONFIRM_WINDOW if settings is None \
                else settings.hard_rule_confirm_window
        if hard_rule_corroboration is _UNSET:
            hard_rule_corroboration = DEFAULT_HARD_RULE_CORROBORATION if settings is None \
                else settings.hard_rule_corroboration
        if hard_rule_corroboration_margin is _UNSET:
            hard_rule_corroboration_margin = DEFAULT_HARD_RULE_CORROBORATION_MARGIN if settings is None \
                else settings.hard_rule_corroboration_margin
        if isinstance(hard_rule_confirm_window, bool) or not isinstance(hard_rule_confirm_window, int) \
                or not 1 <= hard_rule_confirm_window <= MAX_HARD_RULE_CONFIRM_WINDOW:
            raise ValueError(f"hard_rule_confirm_window must be a whole number of readings from 1 to "
                             f"{MAX_HARD_RULE_CONFIRM_WINDOW}, not {hard_rule_confirm_window!r}")
        if not isinstance(hard_rule_corroboration, bool):
            raise ValueError(f"hard_rule_corroboration must be True or False, not {hard_rule_corroboration!r}")
        if isinstance(hard_rule_corroboration_margin, bool) or not isinstance(hard_rule_corroboration_margin, (int, float)) \
                or not MIN_HARD_RULE_CORROBORATION_MARGIN <= hard_rule_corroboration_margin <= 1:   # NaN fails too
            raise ValueError(f"hard_rule_corroboration_margin must be a number from "
                             f"{MIN_HARD_RULE_CORROBORATION_MARGIN} to 1 (to hold every hard rule at once, use "
                             f"hard_rule_confirm_window=1), not {hard_rule_corroboration_margin!r}")
        self._hard_rule_confirm_window = hard_rule_confirm_window
        self._hard_rule_corroboration = hard_rule_corroboration
        self._hard_rule_corroboration_margin = float(hard_rule_corroboration_margin)
        # One object holds every value in force: the settings the caller gave, or the defaults with the hard-rule
        # arguments applied. The record, the engines and the cassette all read this, so they cannot disagree.
        if settings is None:
            settings = ClinicalSettings(hard_rule_confirm_window=self._hard_rule_confirm_window,
                                        hard_rule_corroboration=self._hard_rule_corroboration,
                                        hard_rule_corroboration_margin=self._hard_rule_corroboration_margin)
        self.settings = settings
        if settings.engine_params_are_default():
            self._engines = _ENGINES
        else:
            self._engines = {
                **_ENGINES,
                "heuristic": partial(RiskAdapters.heuristic, norms=settings.norms,
                                     age_bounds=settings.age_group_upper_months,
                                     critical_o2_below=settings.critical_o2_below,
                                     hypothermia_below=settings.hypothermia_below),
                "bayesian": partial(RiskAdapters.bayesian, norms=settings.norms,
                                    age_bounds=settings.age_group_upper_months),
            }
        weaker = settings.weakenings()
        if weaker:
            logger.warning(f"clinical settings make an alert LESS likely than the defaults: {weaker} "
                           f"(settings digest {settings.digest()[:12]})")
        object.__setattr__(self, "_settled", True)

    #: Settings a verdict's recorded identity speaks for. Once the cassette is built they cannot be reassigned: a
    #: setting changed afterwards would leave the identity recording one thing and the engine doing another.
    _SETTINGS = frozenset({"engine_gate", "settings", "_engines", "hard_rule_confirm_window", "hard_rule_corroborated",
                           "_hard_rule_confirm_window", "_hard_rule_corroboration", "_hard_rule_corroboration_margin"})

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._SETTINGS and self.__dict__.get("_settled"):
            raise AttributeError(f"{name} is fixed once the cassette is built; build a new PediatricCassette")
        object.__setattr__(self, name, value)

    @property
    def identity_extras(self) -> Dict[str, str]:
        """What the core records under this cassette's identity beyond name and
        version. Empty for the default gate, so default entries and fingerprints
        are exactly what they were; the gate's name for any other. Likewise each hard-rule setting that is not at its
        default, recorded by name, so a verdict made under a longer window or without corroboration says so."""
        extras: Dict[str, str] = {}
        if self.engine_gate != DEFAULT_ENGINE_GATE:
            extras["engine_gate"] = self.engine_gate
        if self._hard_rule_confirm_window != DEFAULT_HARD_RULE_CONFIRM_WINDOW:
            extras["hard_rule_confirm_window"] = str(self._hard_rule_confirm_window)
        if self._hard_rule_corroboration != DEFAULT_HARD_RULE_CORROBORATION:
            extras["hard_rule_corroboration"] = str(self._hard_rule_corroboration).lower()
        if self._hard_rule_corroboration_margin != DEFAULT_HARD_RULE_CORROBORATION_MARGIN:
            extras["hard_rule_corroboration_margin"] = repr(self._hard_rule_corroboration_margin)
        # Any other setting that is not at its default: by name, with the digest of the whole set and the names of the
        # ones that make an alert less likely, so a verdict is traceable to the values that produced it. The hard-rule
        # values keep the names above, so a cassette tuned only there records exactly what it always did.
        others = {k: v for k, v in self.settings.non_default().items() if not k.startswith("hard_rule_")}
        if others:
            extras["settings_digest"] = self.settings.digest()
            for key, value in others.items():
                extras[f"setting.{key}"] = value
            weaker = self.settings.weakenings()
            if weaker:
                extras["settings_weaker"] = ",".join(weaker)
        return extras

    # -- identity -----------------------------------------------------
    def subject_id(self, obs: Any) -> str:
        return obs.patient_id

    # -- trust boundary -----------------------------------------------
    def validate(self, obs: Any) -> List[str]:
        return validate_vitals(obs)

    def regime_floor(self, obs: Any) -> Optional[Tuple[str, str]]:
        """A floor on the reported regime, with its reason, or None.

        Pediatric is the supported population; adult norms are a future cassette. A reading above `max_age_months` is
        assessed on every channel exactly as before (and tracked as before, so a CRITICAL is still held), but is
        reported no lower than WARNING with a named finding, so a person looks: a deteriorating adult must not be
        scored less severely for being out of scope, and a healthy one must not be reassured by child norms. It is a
        floor on the report, not a data fault: nothing about the reading is untrustworthy."""
        s = self.settings
        context = obs.context if isinstance(getattr(obs, "context", None), dict) else {}
        age = context.get("age_months")
        known = (isinstance(age, (int, float)) and not isinstance(age, bool) and math.isfinite(age)
                 and 0 <= age <= 1500)
        if not known:
            if s.require_age:
                return ("warning", f"{POPULATION_FINDING}: this deployment requires the patient's age and none "
                                   "usable was given")
            return None
        if s.outside_population == "alert" and s.max_age_months is not None and age > s.max_age_months:
            return ("warning", f"{POPULATION_FINDING}: age {age:g} months is above the supported pediatric "
                               f"population (max {s.max_age_months:g}); assessed on pediatric norms, "
                               "interpret with caution")
        return None

    def logic_digest(self) -> str:
        """What this domain's rules DO, as a digest (logic_stamp): the cassette class, its settings class, the engine-selection
        gate and the constants those read, and the settings DEFAULTS (a changed default is a changed behaviour). Recorded, with
        the core's, in every decision's provenance, so a record says which behaviour made it. Decision D9."""
        global _LOGIC_DIGEST
        if _LOGIC_DIGEST is not None:                       # the source cannot change within a process: compute it once
            return _LOGIC_DIGEST
        import sys
        import clinical_settings as _settings_module
        from clinical_settings import ClinicalSettings as _Settings
        # Whole modules, for the reason given at ENGINE_LOGIC_DIGEST: this module and the settings module, so every constant and
        # helper either reads is covered, not only the class.
        _LOGIC_DIGEST = logic_digest(
            sys.modules[__name__], _settings_module,
            constants={"HARD_RULE_RISK": HARD_RULE_RISK, "HARD_RULE_ENGINE": HARD_RULE_ENGINE,
                       "neutral": _VITALS_NEUTRAL, "channel_context_keys": _CHANNEL_CONTEXT_KEYS,
                       "default_engine_gate": DEFAULT_ENGINE_GATE, "engine_gates": list(ENGINE_GATES),
                       "heart_rate_findings": sorted(_HEART_RATE_FINDINGS), "spo2_findings": sorted(_SPO2_FINDINGS),
                       "score_tolerance": _SCORE_TOLERANCE, "settings_defaults": _Settings().as_dict()})
        return _LOGIC_DIGEST

    def advisory_notes(self, obs: Any) -> List[str]:
        """Notes that sit beside the verdict and change no regime.

        An SpO2 at or below `probe_off_spo2_at_or_below` (default 0) is far more often a probe that is off than a
        patient with no oxygen, but it may be both: the note says to check the probe AND the patient, and the reading
        is assessed exactly as before (a zero is still a CRITICAL_O2 hard rule). `None` switches the note off."""
        limit = self.settings.probe_off_spo2_at_or_below
        o2 = getattr(obs, "oxygen_saturation", None)
        if limit is not None and isinstance(o2, (int, float)) and not isinstance(o2, bool) \
                and math.isfinite(o2) and o2 <= limit:
            return [f"POSSIBLE_SENSOR_DISCONNECT: SpO2 reads {o2:g}%; check the probe and check the patient. "
                    "The reading is not downgraded."]
        return []

    def mask_faults(self, obs: Any, faults: List[str]) -> Optional[Any]:
        """The reading to assess in place of a faulted one, or None.

        validate_vitals names the channel before the first '=' of every
        fault; that grammar is this domain's, which is why the parse is
        here and not in the core. None when a fault names no channel we
        know (we cannot say which channel is poisoned) or when every
        channel is faulted (nothing is left to assess).
        """
        faulted: Set[str] = set()
        for fault in faults:
            channel = fault.split("=", 1)[0]
            if channel not in _VITALS_NEUTRAL:
                return None
            faulted.add(channel)
        if not faulted or faulted == set(_VITALS_NEUTRAL):
            return None
        dropped = {key for channel in faulted for key in _CHANNEL_CONTEXT_KEYS[channel]}
        context = obs.context if isinstance(obs.context, dict) else {}
        kept = {k: v for k, v in context.items() if k not in dropped}
        return replace(obs, context=kept, **{c: _VITALS_NEUTRAL[c] for c in faulted})

    def context_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return _NUMERIC_CONTEXT_SCALAR_BOUNDS

    def context_list_bounds(self) -> Mapping[str, Optional[Tuple[float, float]]]:
        return _NUMERIC_CONTEXT_LIST_BOUNDS

    # -- what to run ---------------------------------------------------
    def select_engines(self, obs: Any, recent_entropy: float) -> List[str]:
        """Deterministic engine selection based on entropy + explicit triggers."""
        engines = ["heuristic"]

        if recent_entropy > 0.6 or obs.context.get("force_heavy"):
            engines += ["bayesian", "trajectory", "drift"]

        if "previous_o2" in obs.context or "previous_hr" in obs.context:
            if "trajectory" not in engines:
                engines.append("trajectory")

        if obs.context.get("recent_o2_readings"):
            engines.append("adversarial")

        # Behavioral/syndrome analysis must run whenever ANY syndrome could
        # fire, not only on O2/HR. Septic shock keys on RR>35 + fever;
        # respiratory distress on RR>45. Thresholds match the lowest syndrome
        # cutoffs. PROVISIONAL -- pending pediatrician sign-off. The widened
        # clause (RR and temperature) applies only under the widened_provisional
        # gate; conservative_legacy keeps the O2/HR rule alone.
        if (obs.oxygen_saturation < 92.0 or obs.heart_rate > 140 or obs.heart_rate < 90
                or (self.engine_gate == ENGINE_GATE_WIDENED_PROVISIONAL
                    and (obs.respiratory_rate > 35 or obs.temperature > 38.5))):
            engines.append("behavioral")

        if (recent_entropy > 0.6 or obs.context.get("force_heavy")
                or _has_physiological_telemetry(obs)):
            engines.append("physiological_reserve")

        seen = set()
        ordered = []
        for e in engines:
            if e not in seen:
                seen.add(e)
                ordered.append(e)
        return ordered

    def engines(self) -> Mapping[str, Callable[[Any], RiskOutput]]:
        return self._engines

    # -- what is never noise -------------------------------------------
    def hard_rule_fired(self, outputs: List[RiskOutput]) -> bool:
        """A CRITICAL_O2 reading or equivalent: escalate now, do not wait.

        The core cannot know which readings are never noise. This is the
        domain saying so, and -- unlike the literal it replaces -- it is
        a callable a test can put a value into.
        """
        for out in outputs:
            if out.engine_name == HARD_RULE_ENGINE:
                return out.risk_score >= HARD_RULE_RISK
        return False

    # -- how a hard rule is confirmed (clinician settings) -------------
    def hard_rule_confirm_window(self) -> int:
        """How many of a patient's readings, the current one included, make a repeat of the hard rule. 1 means
        every hard rule holds the floor at once, with no wait to see whether it was an artefact."""
        return self._hard_rule_confirm_window

    def hard_rule_corroborated(self, outputs: List[RiskOutput]) -> bool:
        """Is the hard rule accompanied by another abnormal finding in the same assessment?

        The heuristic's score is the sum of its findings, and a hard rule is a score of at least HARD_RULE_RISK (a
        CRITICAL_O2 finding alone is worth exactly that), so a score above it by at least the margin means the
        heuristic found something else abnormal as well. That is evidence from a different channel for respiratory
        rate and temperature, which a pulse-oximeter artefact does not move, and weaker evidence for heart rate, which
        usually shares the probe with SpO2. Raise the margin to ask for more than one extra finding.

        By default a heart-rate finding alone does not corroborate (`ClinicalSettings.heart_rate_corroborates`): if the only
        other findings are heart rate (and SpO2 itself) the hard rule is not corroborated. A heuristic output whose rule names
        cannot be told apart (no recognised findings) is judged on its score alone, the direction that escalates."""
        if not self._hard_rule_corroboration:
            return False
        for out in outputs:
            if out.engine_name == HARD_RULE_ENGINE and not out.abstained:
                if out.risk_score + _SCORE_TOLERANCE < HARD_RULE_RISK + self._hard_rule_corroboration_margin:
                    return False
                if self.settings.heart_rate_corroborates:
                    return True
                names = {str(r).split(":", 1)[0].strip() for r in (out.triggered_rules or [])}
                other_channel = names - _HEART_RATE_FINDINGS - _SPO2_FINDINGS - {"BASE_RISK"}
                heart_rate_only = bool(names & _HEART_RATE_FINDINGS) and not other_channel
                return not heart_rate_only
        return False

    def labels(self) -> Mapping[str, str]:
        """The exact strings this domain's ledger already contains.

        Not modernised. The audit ledger is append-only and hash-chained,
        so renaming a record kind would leave historical entries
        unmatchable by any query written against the new one. A refactor
        does not get to rewrite a governed vocabulary.
        """
        return {"record_kind": "clinical_assessment",
                "safety_bypass": "CLINICAL_SAFETY_BYPASS"}

    def channel_model(self) -> Mapping[str, ChannelModel]:
        """Reused, not restated: the same table the tracker has always
        used, so the numbers cannot drift apart from the clinical ones."""
        return _PEDIATRIC_CHANNEL_MODEL

    # -- trajectory ------------------------------------------------------
    def channels(self, obs: Any) -> Dict[str, float]:
        """The numeric series worth tracking over time in this domain."""
        return {
            "heart_rate": obs.heart_rate,
            "oxygen_saturation": obs.oxygen_saturation,
            "respiratory_rate": obs.respiratory_rate,
            "temperature": obs.temperature,
        }
