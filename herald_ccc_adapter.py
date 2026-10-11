"""Recording HERALD's admitted claims into CCC, only when HERALD's proof checks.

HERALD turns a document into claims and issues an export that authenticates
which claims were admitted. CCC records findings, but its intake takes any
object with a ``verified`` flag at face value. CCC's own docstring says so:
a forged ``verified=True`` is only stopped by a sealed, hash-checked claim,
which is HERALD's discipline, not CCC's intake.

This adapter is where the two meet. It records a claim only after the export
passes a verifier, and it records admitted claims only. Refused claims are
not recorded, because CCC refuses unverified findings outright.

The verifier is passed in, not imported. HERALD's export MAC is keyed to the
process that issued it, so the verifier has to be HERALD's own
``verify_export`` called from that same process. Without a verifier the
adapter refuses to record anything (fail closed).

HERALD is not imported here. The export is read as plain data, the same way
observe-perceive reads every other stack package.
"""

from dataclasses import dataclass
from typing import Any, Callable, List, Mapping, Optional, Tuple

from orchestrator_ccc_adapter import Actor, CCCSystem, make_ccc_system


class HeraldExportNotVerified(Exception):
    """The export did not pass HERALD's verifier, or no verifier was given."""


#: Machine actor recorded on every HERALD-originated finding.
DEFAULT_ACTOR_ID = "herald-intake"

#: The method string CCC stores on each record. It names what produced the
#: finding so a recurrence query can tell HERALD's output from other sources.
METHOD = "HERALD extraction, OMEGA confidence gate, export MAC verified"


@dataclass(frozen=True)
class HeraldFinding:
    """The CCC finding shape, built here rather than imported.

    Same structural contract as the orchestrator and Ecology shapes:
    ``conclusion``, ``method``, ``source_material``, ``confidence``,
    ``verified`` and ``evidence``.
    """
    conclusion: str
    method: str
    source_material: Tuple[str, ...]
    confidence: Optional[float]
    verified: bool
    evidence: Tuple[Tuple[str, str], ...] = ()


def _verified_export(export: Mapping[str, Any], verify: Optional[Callable]) -> None:
    if verify is None:
        raise HeraldExportNotVerified(
            "no verifier supplied; pass HERALD's verify_export from the process that issued the export"
        )
    try:
        verify(export)
    except Exception as exc:  # any failure to verify is a refusal
        raise HeraldExportNotVerified(f"HERALD export did not verify: {exc}") from exc


def finding_from_claim(entry: Mapping[str, Any]) -> HeraldFinding:
    """Turn one admitted HERALD claim entry into a CCC finding.

    The conclusion names the kind of claim, its standing, its reading and its
    authority, plus the source it came from. Those words repeat across runs,
    so occurrences of the same kind cluster. The source id makes each document
    distinguishable, which keeps one run from looking like another's duplicate.
    The claim's value is left out of the conclusion so the excerpt, not the
    value, carries the content.
    """
    conclusion = (
        f"HERALD admitted a {entry['kind']} claim from {entry['standing']} source "
        f"{entry['source_id']}, read as {entry['reading']} with {entry['authority']} authority."
    )
    return HeraldFinding(
        conclusion=conclusion,
        method=METHOD,
        source_material=(str(entry["source_id"]),),
        confidence=float(entry["confidence"]),
        verified=True,
        evidence=((str(entry["source_id"]), str(entry["raw"])),),
    )


def findings_from_export(
    export: Mapping[str, Any],
    *,
    verify: Optional[Callable[[Mapping[str, Any]], None]] = None,
) -> List[HeraldFinding]:
    """Findings for every admitted claim, once the export has verified.

    Raises HeraldExportNotVerified before reading any claim if the export does
    not verify. Refused entries never become findings.
    """
    _verified_export(export, verify)
    return [finding_from_claim(entry) for entry in export["admitted"]]


class HeraldCCCAdapter:
    """Records verified HERALD exports into CCC as discoveries."""

    def __init__(self, ccc_system=None, actor_id: str = DEFAULT_ACTOR_ID):
        """
        Args:
            ccc_system: A CCCSystem. Built by make_ccc_system() if omitted.
            actor_id: Recorded as the machine actor on every finding.
        """
        if CCCSystem is None:
            raise ImportError(
                "CCC not found. Clone it beside this repo (../CCC) or install it."
            )
        self.ccc = ccc_system if ccc_system is not None else make_ccc_system()
        self.actor = Actor.model(actor_id)

    def record_export(
        self,
        export: Mapping[str, Any],
        *,
        verify: Optional[Callable[[Mapping[str, Any]], None]] = None,
        allow_private_source: bool = False,
    ) -> list:
        """Record each admitted claim of a verified export. Returns CCC's records.

        Returns an empty list when the export admitted nothing. Raises
        HeraldExportNotVerified, and records nothing, if the export does not
        verify. CCC's own refusals (private source, duplicate policy) propagate.
        """
        findings = findings_from_export(export, verify=verify)
        return [
            self.ccc.record_external_finding(
                finding,
                actor=self.actor,
                allow_private_source=allow_private_source,
            )
            for finding in findings
        ]
