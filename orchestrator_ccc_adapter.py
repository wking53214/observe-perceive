"""
Orchestrator → CCC Adapter

Records a completed governance orchestration into CCC (the Cognitive
Continuity Constitution), so the recurrence engine watches live governance
decisions and not only reconstructed conversation history.

Why this seam exists
--------------------
The chain in `governance_orchestrator.py` decides one request at a time and
is deliberately memoryless: PERCEIVE evaluates, FORTRESS contains,
Conservation verifies, GSA-815 executes, OBSERVE watches. None of them can
see that *this is the third time this month the same class of request was
refused for the same reason*. Noticing that is a different capability, and
CCC already implements it -- APM escalation (ANOMALY -> PATTERN -> MANDATE),
anti-probability duplicate detection, transitive recurrence clustering.

So this is not a sixth gate. It is a recorder that runs after the chain has
finished, on approvals and refusals alike, turning each completed
orchestration into a CCC discovery. A pattern recurring for the third time
raises the same REPEATED_RETURN road sign that a recurring finding from the
conversation archive does -- one engine, two source domains.

No import of CCC's internals leaks upward, and CCC does not import this:
`record_external_finding` takes anything structurally shaped like Ecology's
FindingRecord, which is exactly the same duck-typed contract discipline the
rest of these adapters use.

What the machine may and may not do here
----------------------------------------
Every discovery recorded through this path is machine-originated by
construction, so it enters as an ANOMALY and may be advanced to PATTERN by
CCC's own recurrence logic. MANDATE stays human-only -- nothing in this
adapter can reach it, and nothing should.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def _import_ccc():
    """Resolve CCC the way the other adapters resolve their cross-repo
    dependencies: sibling checkout first, installed package as fallback."""
    ccc_path = os.path.join(os.path.dirname(__file__), "..", "CCC")
    if os.path.isdir(ccc_path) and ccc_path not in sys.path:
        sys.path.insert(0, ccc_path)
    try:
        from ccc import Actor, CCCSystem, EpistemicStatus
    except ImportError:
        # ModuleNotFoundError is the ordinary case; a bare ImportError means a
        # partial or shadowing `ccc` package, which is equally "not available".
        return None, None, None
    return Actor, CCCSystem, EpistemicStatus


Actor, CCCSystem, EpistemicStatus = _import_ccc()


@dataclass(frozen=True)
class OrchestrationFinding:
    """The Ecology `FindingRecord` shape, built here rather than imported.

    CCC's intake is structural: anything carrying `.conclusion`, `.method`,
    `.source_material`, `.confidence`, `.verified`, `.evidence` and the
    optional event-date pair can be recorded. Constructing that shape locally
    keeps observe-perceive free of a hard dependency on Ecology, exactly as
    CCC stays free of a dependency on either.
    """
    conclusion: str
    method: str
    source_material: Tuple[str, ...]
    confidence: Optional[float]
    verified: bool
    evidence: Tuple[Tuple[str, str], ...] = ()
    event_start_date: Optional[str] = None
    event_end_date: Optional[str] = None


class OrchestratorCCCAdapter:
    """Records completed orchestrations as CCC discoveries."""

    def __init__(self, ccc_system=None, actor_id: str = "governance-orchestrator"):
        """
        Args:
            ccc_system: A CCCSystem. Constructed fresh if omitted; pass a
                persistent one to accumulate recurrence across runs, which is
                the only configuration in which pattern detection is
                meaningful.
            actor_id: Recorded as the machine actor on every discovery.
        """
        if CCCSystem is None:
            raise ImportError(
                "CCC not found. Clone it beside this repo (../CCC) or install it."
            )
        self.ccc = ccc_system if ccc_system is not None else CCCSystem()
        self.actor = Actor.model(actor_id)

    # ------------------------------------------------------------------
    # Building the finding
    # ------------------------------------------------------------------

    @staticmethod
    def orchestration_to_finding(result: Dict[str, Any], request_id: str = None) -> OrchestrationFinding:
        """Turn an `orchestrate_request` result into a finding CCC can record.

        Both outcomes are recorded, not just approvals. A refusal that keeps
        recurring is the more interesting signal of the two -- it is the shape
        of a governance rule fighting the same case over and over, which is
        precisely what should reach a human.
        """
        status = result.get("status", "UNKNOWN")
        decision = result.get("governance_decision")
        conservation = result.get("conservation_decision")
        fortress = result.get("fortress_result")
        proof = result.get("forensic_proof")

        gates = list(getattr(decision, "applied_gates", []) or [])
        violations = list(getattr(decision, "violations", []) or [])

        # The conclusion is the recurrence-matching surface -- it is what CCC
        # compares against prior discoveries, and getting its specificity
        # right is the whole difficulty of this seam.
        #
        # Too specific (ids, hashes, timestamps) and every run looks novel:
        # no pattern is ever detected. Too generic and every run is textually
        # identical, which CCC's anti-probability matcher correctly reads as a
        # *duplicate* -- a re-observation of the same content, deliberately
        # excluded from occurrence counts so a re-run cannot inflate itself
        # into a false pattern. Measured: an early version of this method
        # produced a 235-character exact overlap between consecutive runs and
        # every occurrence after the first was absorbed as a duplicate.
        #
        # So: name the case (which request, on which subject) so occurrences
        # are distinguishable, and keep the outcome vocabulary (gates,
        # violations, refusal reason) shared so they still cluster. That is
        # the difference between "this happened again" and "you told me twice".
        subject = OrchestratorCCCAdapter._subject_of(decision, result, request_id)
        if status == "APPROVED_AND_EXECUTED":
            conclusion = (
                f"Governance chain approved and executed a governed request "
                f"for {subject}. "
                f"Gates applied: {', '.join(gates) if gates else 'none'}."
            )
        else:
            conclusion = (
                f"Governance chain refused a governed request for {subject}. "
                f"Reason: {result.get('reason', 'unspecified')}. "
                f"Violations: {'; '.join(violations) if violations else 'none recorded'}."
            )

        # Evidence: what happened in THIS case, one excerpt per stage that
        # actually attested to something.
        #
        # The excerpts are the comparison surface -- CCC builds its matching
        # text by joining them -- so what goes in them decides whether two
        # orchestrations read as one finding re-observed or as two independent
        # occurrences of a pattern. Two rules, both learned by measurement
        # rather than assumed:
        #
        # 1. The excerpt carries the *case facts*, not the template. Which
        #    subject, what its measured values were. The gate list, the
        #    stage count, the machinery of how it was decided: that is
        #    metadata about the decision procedure and lives in `method` and
        #    `source_material`, which CCC does not compare. An earlier version
        #    put the full gate list in every excerpt and two different
        #    patients produced a 110-character identical span -- correctly
        #    read as a duplicate, so the fourth escalation in a row never
        #    counted as a recurrence at all.
        #
        # 2. Case data is *interleaved*, not appended. CCC's duplicate check
        #    looks for a long identical span; its recurrence check clusters on
        #    shared vocabulary. Threading the case-specific values through the
        #    sentence breaks the span while leaving the shared words intact,
        #    which is exactly the distinction wanted: measured on three
        #    same-reason refusals, span overlap 0, recurrence detected.
        #    Appending the case id to a fixed prefix does not achieve this --
        #    the prefix is still one long identical run.
        # 3. Excerpts are terse. Narrative scaffolding ("was approved by
        #    PERCEIVE across N gates with...") is fixed text that appears
        #    verbatim in every record, and enough of it in a row is itself a
        #    long identical span no matter where the varying values sit.
        #    Measured: a fluent one-sentence-per-stage version left a 71-char
        #    identical tail between two refusals that genuinely differed.
        #    Terse "subject: value, value" excerpts keep every shared run
        #    below the 40-character floor.
        evidence = []
        if decision is not None:
            approval_word = getattr(getattr(decision, "approval", None), "value", "unknown")
            detail = "; ".join(violations) if violations else "none"
            evidence.append((
                f"PERCEIVE/{getattr(decision, 'decision_id', 'unknown')}",
                f"{subject}: {approval_word}, violations {detail}",
            ))
        if fortress is not None:
            evidence.append((
                f"FORTRESS/{fortress.controller}",
                f"{subject}: {fortress.decision} at distortion "
                f"{fortress.distortion:.4f}, regime {fortress.regime}",
            ))
        if conservation is not None:
            receipt = getattr(conservation, "conservation_receipt_id", "none")
            evidence.append((
                f"CONSERVATION/{receipt}",
                f"{subject}: conserved {getattr(conservation, 'verified', None)}, "
                f"receipt {receipt}",
            ))
        if proof is not None:
            evidence.append((
                f"CHAIN/{proof.get('execution_id', 'unknown')}",
                f"{subject}: outcome {proof.get('outcome')}, "
                f"chain {proof.get('chain_valid')}, lineage {proof.get('lineage')}",
            ))

        # source_material names the systems that actually contributed, so the
        # discovery is traceable to its producers rather than to this adapter.
        # Not compared by CCC, so the template detail is safe here.
        sources = ["observe-perceive/governance_orchestrator"]
        if decision is not None:
            sources.append("PERCEIVE")
        if fortress is not None:
            sources.append("FORTRESS")
        if conservation is not None:
            sources.append("conservation_kernel")
        if proof is not None:
            sources.append("GSA-815")

        # Event time is the decision's own timestamp. For a live orchestration
        # event time and ingest time are the same instant -- unlike a finding
        # reconstructed from months-old conversation history, where they are
        # not. Recording it explicitly keeps the two indistinguishable-looking
        # cases honestly distinguished in CCC's store.
        event_date = None
        decision_ts = getattr(decision, "timestamp", None)
        if isinstance(decision_ts, datetime):
            event_date = decision_ts.date().isoformat()

        return OrchestrationFinding(
            conclusion=conclusion,
            method=(
                "observe_perceive.governance_orchestrator.orchestrate_request"
                f" (stages: {len(sources) - 1}; gates: {', '.join(gates) if gates else 'none'})"
            ),
            source_material=tuple(sources),
            # Confidence is the fraction of chain stages that produced an
            # attestation, not a belief about correctness. A run that only got
            # through PERCEIVE is a weaker record than one with a full
            # forensic proof, and this says so without overclaiming.
            confidence=round(len(evidence) / 4.0, 4) if evidence else None,
            # NB: `method` below carries the procedural detail (gate names,
            # stage count) that was deliberately kept out of the excerpts.
            # CCC does not compare it, so it can be as templated as it likes.
            # `verified` means the record is internally consistent and worth
            # recording, not that the request was approved. CCC refuses
            # unverified findings outright, and a refusal is a real finding.
            verified=decision is not None,
            evidence=tuple(evidence),
            event_start_date=event_date,
            event_end_date=event_date,
        )

    @staticmethod
    def _subject_of(decision, result: Dict[str, Any],
                    request_id: str = None) -> str:
        """What this orchestration was *about*, in a form that distinguishes
        occurrences without defeating clustering.

        The request id is the artifact id here, which is unique per run --
        including it verbatim is what keeps consecutive runs from collapsing
        into duplicates. It is intentionally the only high-cardinality token
        in the conclusion; hashes and timestamps stay out.
        """
        rid = request_id or getattr(decision, "request_id", None)
        if rid:
            return f"artifact {rid}"
        proof = result.get("forensic_proof") or {}
        return f"execution {proof.get('execution_id', 'unidentified')}"

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(self, result: Dict[str, Any], request_id: str = None,
               allow_private_source: bool = False):
        """Record one completed orchestration into CCC.

        Returns the CCC DiscoveryRecord, or None when the result carried no
        governance decision at all (nothing happened worth recording).
        """
        finding = self.orchestration_to_finding(result, request_id)
        if not finding.verified:
            return None
        return self.ccc.record_external_finding(
            finding,
            actor=self.actor,
            allow_private_source=allow_private_source,
        )
