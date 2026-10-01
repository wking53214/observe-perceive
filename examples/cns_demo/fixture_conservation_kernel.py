"""Demo fixture for conservation_kernel: a real OMEGA gate with real inputs.

Scenario. An AI assistant is asked to release a short status summary of a
regulated record. Preconditions (ALPHA) judge the request; the WORK writes the
summary; output checks (OMEGA) judge the result. This fixture supplies the
output check on the TRANSFORMATION from the source record to the summary: does
the summary conserve what the record claims, or does it restate an estimate as
established fact?

POSITION is 'OMEGA', for these reasons, all taken from the library itself:

* ``conservation_kernel.cns_connector`` states the mapping outright:
  "Transformation verification is the OMEGA end. ``verify`` takes the produced
  output artifact as an argument: it judges a result that already exists, and
  a rejection means the result never enters the ledger ... The verifier cannot
  run before the output exists, so there is no precondition form of it."
  ``CnsGate.position`` returns ``GatePosition.OMEGA`` and
  ``cns_chain(...).complete()`` is False by design (README section 8).
* ``docs/GATEWAY.md``: "A transformer's output is an untrusted proposal, never
  an accepted artifact. It becomes an artifact only after the kernel accepts
  it." The summary the work writes is that proposal, so the kernel can only
  judge it after the work.
* Its candidate is a ``TransformationCandidate`` (source artifact, produced
  artifact, transformation record). The produced artifact is the summary, which
  does not exist until the work has run, so the gate could not sit at the ALPHA
  end even if the demo wanted it there.

The library has a second gate, ``CnsRootAdmissionGate``, which IS at the ALPHA
end. It is not used here: it judges an asserted root artifact before it is
registered as ancestry, not the request and not the summary. In this demo the
admission of the source record is governance_gateway's job.

The candidate is therefore what the demo's work function RETURNS: the summary,
as a ``TransformationCandidate`` pairing the source record with the produced
summary artifact and the record of the transformation. The summary text is
``candidate.output.content``. The gate then judges it, and a refusal means the
summary does not leave.

What the gate checks. The kernel's own ``IndependentVerifier``, unchanged,
against an ``EvidenceRegistry`` that holds the witnesses (evidence and the QA
approval) the source record cites. Every claim in the record is a proposition
with an explicit epistemic status, origin, authority, uncertainty, temporal
scope, evidence and provenance. The verifier recomputes what changed between
source and summary and refuses any change that is undeclared, mismatched or
unauthorized: for example an ESTIMATED claim becoming FACT without independent
verification and a matching human authorization, or uncertainty disappearing.

What PASS does not mean, stated by the library and kept here. The kernel
verifies the typed envelope, not prose. A summary whose text was rewritten
passes with status ``PASS_WITH_DECLARED_TRANSFORMATION`` and the reason keeps
``unverifiable: semantic_content_equivalence``: whether the new wording says
the same thing is explicitly not verified. The self-check shows the sharpest
consequence (a summary that overstates in prose while leaving the envelope
untouched passes) as a limit, not a defect of this fixture. In the demo the
refused summary carries the overstatement in its envelope as well as its text,
which is what the kernel can see.

REFUSED_OUTCOME is 'TERMINAL_BREACH'. The refused candidate is the same
summary with one change: the shelf life, which the record holds as an
ESTIMATED claim ("Estimated shelf life is 24 months", extrapolated from three
months of accelerated data), is restated as "Shelf life is 24 months", with
its status promoted to FACT and its uncertainty cleared, and the change is not
declared. The kernel rejects it, and the connector maps every rejection to
``TERMINAL_BREACH`` and never produces ``RETRY``: the kernel "models no retry
or repair" and refuses silent repair, so no delta mends it. This was verified
by running it (see ``__main__``), not assumed.

Subject binding. Every verdict is bound: ``subject`` is the label ``SUBJECT``
and ``subject_digest`` is the library's ``transformation_digest`` of the
candidate (ids and kernel digests of the source and the summary, and the
digest of the transformation record), so a verdict cannot be moved onto a
different summary.

Import safety. Nothing here imports ``cns`` or ``conservation_kernel`` when
this module loads; both are imported inside the functions that need them.
Without ``cns`` installed, ``make_gate()`` raises
``conservation_kernel.cns_connector.CnsNotInstalled`` (an ``ImportError``).
The candidate functions need only conservation_kernel, not cns, because
``TransformationCandidate`` and the artifact types load without it.

State. ``make_gate()`` builds a new ``EvidenceRegistry``, a new
``IndependentVerifier`` and a new ``CnsGate`` on every call, and nothing at
module level holds a gate, a registry or an artifact. Each candidate function
builds a new source artifact, a new summary artifact, a new record and a new
candidate on every call, with pinned timestamps so the digests are the same on
every run. ``CnsGate.check`` writes to no ledger and no registry, so judging a
candidate does not commit it and a later candidate does not see an earlier one.

Run as a script for the self-check:  python fixture_conservation_kernel.py
"""

from __future__ import annotations

import types
from typing import Any

POSITION = "OMEGA"

REFUSED_OUTCOME = "TERMINAL_BREACH"

DESCRIPTION = (
    "Checks that the released status summary conserves the claims of the "
    "source batch release record, and refuses a summary that restates an "
    "estimate as established fact without verification or authorization."
)

#: What the verdict's ``subject`` is set to: the label for the judged transformation.
SUBJECT = "source-to-summary"

#: Where the library's own refusal reason starts, for the demo's log.
REFUSED_REASON_PREFIX = "REJECT: "

#: The violation codes the refused candidate trips, as the kernel names them.
REFUSED_CODES = (
    "UNDECLARED_CHANGE",
    "UNCERTAINTY_COLLAPSE",
    "NO_INDEPENDENT_VERIFICATION",
    "MISSING_EPISTEMIC_AUTHORIZATION",
)

_EXPORTED_AT = "2026-09-14T09:30:00+00:00"
_SUMMARY_AT = "2026-09-14T10:00:00+00:00"
_REVIEWER = "qa-reviewer-117"
_ASSISTANT_ID = "status-summary-assistant"
_SOURCE_ID = "batch-release-L2291"
_SOURCE_REF = "source:LIMS-export-EXP-2026-09-14-0042"

_SOURCE_CONTENT = (
    "Batch release record L-2291, Compound X 10 mg tablets: status released, "
    "reviewed by QA-117 on 2026-09-14. Assay 99.2%, dissolution 96.0%, "
    "endotoxin 0.12 EU/mL. Estimated shelf life 24 months, extrapolated from "
    "3 months of accelerated stability data."
)

# The same first sentence the dit fixture uses, so the demo tells one story.
_RELEASE_SENTENCE = (
    "Lot L-2291 of Compound X 10 mg tablets was released on 2026-09-14 "
    "after QA-117 review, because assay measured 99.2%, dissolution 96.0% "
    "and endotoxin 0.12 EU/mL."
)
_OK_SUMMARY = _RELEASE_SENTENCE + " Estimated shelf life is 24 months."
_REFUSED_SUMMARY = _RELEASE_SENTENCE + " Shelf life is 24 months."
_NO_APPROVAL_SUMMARY = (
    "Lot L-2291 of Compound X 10 mg tablets was released on 2026-09-14, "
    "because assay measured 99.2%, dissolution 96.0% and endotoxin "
    "0.12 EU/mL. Estimated shelf life is 24 months."
)


def _registry() -> Any:
    """A new evidence registry holding the witnesses the source record cites.

    Evidence for the release, the independent QC results and the stability
    data, and the QA reviewer's human authorization of the release decision.
    ``trusted_humans`` is set, so only the named QA reviewer can mint an
    authorization: a machine that builds ``Actor.human(...)`` itself cannot.
    The registry does not hold anything that would authorize the shelf life to
    become a fact, which is what makes the refused candidate refusable.
    """
    from conservation_kernel import (
        Actor,
        AuthorityStatus,
        AuthorizationEvent,
        EvidenceKind,
        EvidenceRecord,
        EvidenceRegistry,
        TransitionKind,
    )

    registry = EvidenceRegistry(trusted_humans={_REVIEWER})
    registry.add_evidence(EvidenceRecord(
        evidence_id="ev-lims-release",
        subject_id="p-release",
        kind=EvidenceKind.SOURCE_OBSERVATION,
        provided_by=Actor.external("lims"),
        detail={"export_id": "EXP-2026-09-14-0042"},
        created_at=_EXPORTED_AT,
    ))
    registry.add_evidence(EvidenceRecord(
        evidence_id="ev-qc-results",
        subject_id="p-tests",
        kind=EvidenceKind.INDEPENDENT_VERIFICATION,
        provided_by=Actor.external("qc-laboratory"),
        independent=True,
        detail={"tests": "assay, dissolution, endotoxin"},
        created_at=_EXPORTED_AT,
    ))
    registry.add_evidence(EvidenceRecord(
        evidence_id="ev-stability-data",
        subject_id="p-shelf-life",
        kind=EvidenceKind.SOURCE_OBSERVATION,
        provided_by=Actor.external("stability-laboratory"),
        detail={"study": "accelerated, 3 months"},
        created_at=_EXPORTED_AT,
    ))
    registry.add_authorization(AuthorizationEvent(
        authorization_id="auth-release-qa117",
        authorized_by=Actor.human(_REVIEWER, "QA reviewer"),
        subject_id="p-decision",
        transition_kind=TransitionKind.AUTHORITY_ESCALATION,
        from_value=AuthorityStatus.NONE.value,
        to_value=AuthorityStatus.HUMAN_AUTHORIZED.value,
        reason="QA reviewer approved release of lot L-2291",
        created_at=_EXPORTED_AT,
    ))
    return registry


def _source() -> Any:
    """A new source artifact: the regulated record, four claims with envelopes."""
    from conservation_kernel import (
        Actor,
        Artifact,
        AuthorityStatus,
        EpistemicStatus,
        OriginStatus,
        Proposition,
        TemporalMetadata,
        TemporalScope,
        Uncertainty,
        UncertaintyState,
    )

    propositions = (
        Proposition(
            "p-release",
            "Lot L-2291 of Compound X 10 mg tablets was released on 2026-09-14.",
            EpistemicStatus.FACT,
            OriginStatus.HUMAN_ORIGINATED,
            temporal=TemporalMetadata(
                TemporalScope.HISTORICAL,
                occurred_at="2026-09-14T09:00:00Z",
                observed_at="2026-09-14T09:30:00Z",
            ),
            evidence_refs=("ev-lims-release",),
            source_refs=(_SOURCE_REF,),
        ),
        Proposition(
            "p-tests",
            "Assay measured 99.2%, dissolution 96.0% and endotoxin 0.12 EU/mL.",
            EpistemicStatus.OBSERVATION,
            OriginStatus.EXTERNAL_ORIGINATED,
            temporal=TemporalMetadata(
                TemporalScope.HISTORICAL,
                occurred_at="2026-09-13T16:00:00Z",
                observed_at="2026-09-13T16:05:00Z",
            ),
            evidence_refs=("ev-qc-results",),
            source_refs=(_SOURCE_REF,),
        ),
        Proposition(
            "p-decision",
            "QA-117 approved the release of lot L-2291.",
            EpistemicStatus.DECISION,
            OriginStatus.HUMAN_ORIGINATED,
            authority=AuthorityStatus.HUMAN_AUTHORIZED,
            authorization_refs=("auth-release-qa117",),
            evidence_refs=("ev-lims-release",),
            source_refs=(_SOURCE_REF,),
        ),
        Proposition(
            "p-shelf-life",
            "Estimated shelf life is 24 months.",
            EpistemicStatus.ESTIMATED,
            OriginStatus.MACHINE_ORIGINATED,
            uncertainty=Uncertainty(
                UncertaintyState.UNCERTAIN,
                "extrapolated from 3 months of accelerated stability data",
            ),
            evidence_refs=("ev-stability-data",),
            source_refs=("source:stability-study-SS-2291",),
            derivation_method="linear extrapolation of 3 month accelerated stability data",
        ),
    )
    return Artifact(
        artifact_id=_SOURCE_ID,
        content=_SOURCE_CONTENT,
        propositions=propositions,
        producer=Actor.human(_REVIEWER, "QA reviewer"),
        created_at=_EXPORTED_AT,
    )


def _overstate_shelf_life(propositions: Any) -> Any:
    """The propositions with the shelf life restated as an established fact."""
    from dataclasses import replace

    from conservation_kernel import EpistemicStatus, Uncertainty

    return tuple(
        replace(
            p,
            text="Shelf life is 24 months.",
            epistemic_status=EpistemicStatus.FACT,
            uncertainty=Uncertainty(),
        )
        if p.proposition_id == "p-shelf-life"
        else p
        for p in propositions
    )


def _drop_decision(propositions: Any) -> Any:
    """The propositions without the QA approval."""
    return tuple(p for p in propositions if p.proposition_id != "p-decision")


def _candidate(tag: str, summary: str, edit: Any = None) -> Any:
    """The source record plus one summary of it, as a ``TransformationCandidate``.

    Everything is built new on each call. ``edit``, when given, maps the
    source's propositions to the summary's; the summary's one declared change
    is always its wording (the artifact content), as an honest summarizer would
    declare it. Whatever ``edit`` changes beyond that is therefore undeclared.
    """
    from conservation_kernel import (
        Actor,
        Artifact,
        DeclaredChange,
        Dimension,
        TransformationRecord,
        TransitionKind,
    )
    from conservation_kernel.cns_connector import TransformationCandidate

    source = _source()
    output = Artifact(
        artifact_id=f"status-summary-L2291{tag}",
        content=summary,
        propositions=source.propositions if edit is None else edit(source.propositions),
        producer=Actor.model(_ASSISTANT_ID, "AI assistant"),
        parent_artifact_ids=(source.artifact_id,),
        version=source.version + 1,
        created_at=_SUMMARY_AT,
    )
    record = TransformationRecord(
        transformation_id=f"tx-{output.artifact_id}",
        input_artifact_ids=(source.artifact_id,),
        output_artifact_id=output.artifact_id,
        transformer=output.producer,
        transformation_type="STATUS_SUMMARY",
        declared_changes=(
            DeclaredChange(
                output.artifact_id,
                Dimension.CONTENT,
                source.content_digest,
                output.content_digest,
                "short status summary written from the record",
                TransitionKind.DERIVATION,
            ),
        ),
        input_hashes=(source.artifact_digest,),
        output_hash=output.artifact_digest,
        reason="release a short status summary of the batch release record",
        created_at=_SUMMARY_AT,
    )
    return TransformationCandidate(source, output, record)


def make_gate() -> Any:
    """A new, independent ``cns.gate.Gate``: the library's own ``CnsGate``.

    Each call builds a fresh registry, verifier and ``CnsGate``; no instance is
    cached or shared. Raises ``CnsNotInstalled`` when CNS is absent (the
    connector checks at construction, not on first use).
    """
    from conservation_kernel.cns_connector import CnsGate

    return CnsGate(_registry(), subject=SUBJECT)


def ok_candidate() -> Any:
    """A summary the work returns that the kernel accepts. New objects every call.

    The wording is rewritten from the record and declared as such, every claim
    keeps its status, origin, authority, uncertainty, evidence and provenance,
    and the shelf life stays an estimate.
    """
    return _candidate("", _OK_SUMMARY)


def refused_candidate() -> Any:
    """A summary the work returns that the kernel refuses. New objects every call.

    The same summary except that the shelf life is stated as a fact: the
    claim's status is promoted ESTIMATED to FACT and its uncertainty is
    cleared, with no independent verification, no human authorization and no
    declaration of the change.
    """
    return _candidate("-overstated", _REFUSED_SUMMARY, _overstate_shelf_life)


def _check(condition: bool, label: str, failures: list) -> None:
    print(f"  [{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def _show(label: str, res: Any) -> None:
    print(
        f"{label}: gate={res.gate!r} position={res.position.name} "
        f"outcome={res.outcome.name} subject={res.subject!r} "
        f"digest={res.subject_digest[:16]}... bound={res.bound()}"
    )
    print(f"    reason: {res.reason}")


def _self_check() -> int:
    try:
        gate = make_gate()
    except ImportError as exc:  # CnsNotInstalled is an ImportError
        print(f"make_gate() could not run: {type(exc).__name__}: {exc}")
        print("The module imported fine; the gate itself needs cns installed.")
        return 2

    import dataclasses

    from cns.gate import (
        Gate,
        GateChain,
        GateOutcome,
        GatePosition,
        resolve,
        unbound,
    )
    from conservation_kernel import IndependentVerifier, VerificationStatus
    from conservation_kernel.cns_connector import transformation_digest

    failures: list = []
    print(f"gate name      : {gate.name}")
    print(f"POSITION       : {POSITION} (gate declares {gate.position.name})")
    print(f"REFUSED_OUTCOME: {REFUSED_OUTCOME}")
    print(f"DESCRIPTION    : {DESCRIPTION}")

    ok_in, bad_in = ok_candidate(), refused_candidate()
    print(f"ok summary     : {ok_in.output.content!r}")
    print(f"refused summary: {bad_in.output.content!r}")
    ok_res, bad_res = gate.check(ok_in), gate.check(bad_in)
    _show("ok     ", ok_res)
    _show("refused", bad_res)

    print("checks: contract")
    _check(isinstance(gate, Gate), "gate satisfies cns.gate.Gate", failures)
    _check(
        gate.position is GatePosition.OMEGA and gate.position.name == POSITION,
        "POSITION matches the gate's declared position",
        failures,
    )
    _check(
        GateChain(omega=(gate,)).misplaced() == (),
        "gate sits correctly in the omega slot",
        failures,
    )
    _check(
        GateChain(alpha=(gate,)).misplaced() == (gate.name,),
        "a chain would flag it if it were put in the alpha slot",
        failures,
    )
    _check(
        ok_res.position is GatePosition.OMEGA
        and bad_res.position is GatePosition.OMEGA,
        "both verdicts carry position OMEGA",
        failures,
    )
    _check(
        ok_res.gate == gate.name and bad_res.gate == gate.name,
        "both verdicts name the gate that issued them",
        failures,
    )

    print("checks: verdicts")
    _check(
        ok_res.outcome is GateOutcome.PASS and not ok_res.blocking(),
        "ok candidate PASSES",
        failures,
    )
    _check(
        "PASS_WITH_DECLARED_TRANSFORMATION" in ok_res.reason
        and "semantic_content_equivalence" in ok_res.reason,
        "PASS keeps what the kernel left unverified (wording equivalence)",
        failures,
    )
    _check(
        bad_res.outcome is GateOutcome[REFUSED_OUTCOME] and bad_res.blocking(),
        f"refused candidate gives {REFUSED_OUTCOME}",
        failures,
    )
    _check(
        bad_res.outcome is not GateOutcome.RETRY,
        "the kernel models no retry, so none is invented",
        failures,
    )
    _check(
        bad_res.reason.startswith(REFUSED_REASON_PREFIX)
        and all(code in bad_res.reason for code in REFUSED_CODES),
        "refusal reason names the kernel's own violation codes",
        failures,
    )
    _check(
        "p-shelf-life" in bad_res.reason,
        "refusal reason names the claim that was overstated",
        failures,
    )
    _check(
        resolve([ok_res]) is GateOutcome.PASS
        and resolve([bad_res]) is GateOutcome[REFUSED_OUTCOME],
        "cns.gate.resolve agrees with each verdict",
        failures,
    )

    print("checks: binding")
    _check(unbound([ok_res, bad_res]) == (), "both verdicts are bound", failures)
    _check(
        ok_res.subject == SUBJECT and bad_res.subject == SUBJECT,
        f"subject label is {SUBJECT!r}",
        failures,
    )
    _check(
        ok_res.subject_digest == transformation_digest(ok_in),
        "ok digest is the library's digest of the ok transformation",
        failures,
    )
    _check(
        ok_res.binds(SUBJECT, transformation_digest(ok_in))
        and bad_res.binds(SUBJECT, transformation_digest(bad_in)),
        "each verdict binds to the transformation it judged",
        failures,
    )
    _check(
        not ok_res.binds(SUBJECT, transformation_digest(bad_in))
        and not bad_res.binds(SUBJECT, transformation_digest(ok_in)),
        "neither verdict binds to the other's transformation",
        failures,
    )

    print("checks: the connector agrees with the kernel's own verifier")
    verifier = IndependentVerifier()
    native_ok = verifier.verify(ok_in.inputs, ok_in.output, ok_in.record, _registry())
    native_bad = verifier.verify(bad_in.inputs, bad_in.output, bad_in.record, _registry())
    _check(
        native_ok.status is VerificationStatus.PASS_WITH_DECLARED_TRANSFORMATION
        and not native_ok.violations,
        "the verifier accepts the ok candidate, with no violations",
        failures,
    )
    bad_codes = {v.code for v in native_bad.violations}
    _check(
        native_bad.status is VerificationStatus.REJECT
        and bad_codes == set(REFUSED_CODES),
        "the verifier rejects the refused candidate with exactly REFUSED_CODES",
        failures,
    )
    print(f"    refused violation codes: {sorted(bad_codes)}")

    print("checks: what else the gate does (extra evidence, not the demo's inputs)")
    drop = _candidate("-no-approval", _NO_APPROVAL_SUMMARY, _drop_decision)
    prose = _candidate("-prose-only", _REFUSED_SUMMARY)  # envelope untouched
    extras = (
        ("QA approval dropped", drop, GateOutcome.TERMINAL_BREACH, "PROPOSITION_DROPPED"),
        (
            "LIMIT: overstated in prose, envelope untouched",
            prose,
            GateOutcome.PASS,
            "semantic_content_equivalence",
        ),
    )
    for label, cand, expected, needle in extras:
        res = gate.check(cand)
        print(f"  {label}: {res.outcome.name}: {res.reason}")
        _check(
            res.outcome is expected and needle in res.reason,
            f"{label} gives {expected.name} and says {needle}",
            failures,
        )
        _check(res.outcome is not GateOutcome.RETRY, f"{label} is never RETRY", failures)

    print("checks: independence and freshness")
    gate2 = make_gate()
    _check(gate2 is not gate, "make_gate() returns a new object each call", failures)
    _check(
        gate2._registry is not gate._registry
        and gate2._verifier is not gate._verifier,
        "the two gates share no registry and no verifier",
        failures,
    )
    snapshot_before = gate._registry.snapshot()
    gate.check(ok_in)
    gate.check(bad_in)
    _check(
        gate._registry.snapshot() == snapshot_before,
        "judging writes nothing to the gate's registry",
        failures,
    )
    first = gate.check(bad_in)
    other = gate2.check(ok_in)
    again = gate.check(ok_in)
    _check(
        first == bad_res and other == ok_res and again == ok_res,
        "interleaved use of two gates gives the same verdicts as a first run",
        failures,
    )
    # Break gate2's registry on purpose: the evidence behind the QC results goes
    # inactive. Only gate2 may notice.
    qc = gate2._registry._evidence["ev-qc-results"]
    gate2._registry._evidence["ev-qc-results"] = dataclasses.replace(qc, active=False)
    broken = gate2.check(ok_candidate())
    _check(
        broken.outcome is GateOutcome.TERMINAL_BREACH
        and "INACTIVE_EVIDENCE" in broken.reason,
        "state changed in one gate's registry is seen by that gate (control)",
        failures,
    )
    _check(
        gate.check(ok_candidate()) == ok_res,
        "the first gate is unchanged by that",
        failures,
    )
    fresh = make_gate()
    _check(
        fresh.check(refused_candidate()) == bad_res
        and fresh.check(ok_candidate()) == ok_res,
        "a gate built after use is not changed by that use",
        failures,
    )

    one, two = ok_candidate(), ok_candidate()
    _check(
        one is not two
        and one.inputs[0] is not two.inputs[0]
        and one.output is not two.output
        and one.record is not two.record
        and one.output.propositions[0] is not two.output.propositions[0]
        and one == two
        and transformation_digest(one) == transformation_digest(two),
        "ok_candidate() returns new, equal objects each call, down to the claims",
        failures,
    )
    one, two = refused_candidate(), refused_candidate()
    _check(
        one is not two
        and one.inputs[0] is not two.inputs[0]
        and one.output is not two.output
        and one.record is not two.record
        and one.output.propositions[3] is not two.output.propositions[3]
        and one == two
        and transformation_digest(one) == transformation_digest(two),
        "refused_candidate() returns new, equal objects each call, down to the claims",
        failures,
    )
    _check(
        ok_in.inputs[0] is not bad_in.inputs[0]
        and ok_in.inputs[0] == bad_in.inputs[0],
        "the two candidates do not share their source artifact",
        failures,
    )
    held = [
        name
        for name, value in globals().items()
        if not name.startswith("__")
        and name != "annotations"  # the __future__ feature object
        and not isinstance(value, (str, tuple, types.FunctionType, types.ModuleType, type))
    ]
    _check(
        held == [],
        "the module holds no gate, registry or artifact at module level",
        failures,
    )

    print()
    if failures:
        print(f"SELF-CHECK FAILED: {len(failures)} check(s) failed")
        for label in failures:
            print(f"  - {label}")
        return 1
    print("SELF-CHECK OK")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(_self_check())
