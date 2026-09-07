"""HERALD claims entering the governance chain.

Structured around the three findings from a prior hand-run of this exact
seam (a throwaway 25-line adapter, written against the real contract and run
once in August). Each of those findings gets a regression test here, because
each was a defect that reading the code had not surfaced.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import pytest

from conservation_kernel import ConservationKernel
from governance_orchestrator import GovernanceOrchestrator
from herald_governance_adapter import HeraldGovernanceAdapter
from observe_consolidated import ObserveClinicalEngine
from perceive_consolidated import PerceiveGovernanceKernel, PolicyManifest


@dataclass
class _ClaimExport:
    """Structurally the fields of herald.ClaimExport this seam reads.
    Deliberately not imported -- the contract is structural."""
    claim_id: str
    kind: str
    value: str
    standing: str
    source_id: str
    source_hash: str = "sha256:abcd"
    content_hash: str = "sha256:efgh"
    bundle_id: Optional[str] = None
    confidence: float = 0.9
    reasons: List[str] = field(default_factory=lambda: [
        "Matched the payment table header and a dated row beneath it."
    ])
    derivation_method: Optional[str] = None
    extractor: Optional[str] = None
    authority: str = "ADVISORY"
    raw: str = ""
    reading: str = ""


def _extracted(**overrides):
    """A claim HERALD pulled out of a document: carries an extractor."""
    base = dict(
        claim_id="clm-001", kind="payment.amount", value="1200.00 on 2026-03-14",
        standing="derived", source_id="doc-77",
        derivation_method="table_row_match", extractor="herald.extract.v2",
    )
    base.update(overrides)
    return _ClaimExport(**base)


def _first_hand_record(**overrides):
    """A claim taken straight from a record, with nothing derived."""
    base = dict(
        claim_id="clm-002", kind="statement.balance", value="4300.00 on 2026-03-31",
        standing="record", source_id="bank-stmt-9",
        derivation_method=None, extractor=None,
    )
    base.update(overrides)
    return _ClaimExport(**base)


@pytest.fixture
def orchestrator():
    kernel = PerceiveGovernanceKernel()
    kernel.register_manifest(PolicyManifest(
        manifest_id="herald-seam-manifest", version="1.0.0",
        created_at=datetime.now(timezone.utc), policies={},
    ))
    return GovernanceOrchestrator(kernel, ConservationKernel(), ObserveClinicalEngine())


# ---------------------------------------------------------------------------
# Finding 1: attestation is not derivation
# ---------------------------------------------------------------------------

def test_a_human_confirmed_extraction_is_not_reported_as_attested():
    """The August seam test's actual defect. Sending a human-confirmed claim
    to "attested" reads as the natural mapping, and a provenance invariant
    written a month earlier for call ingestion refused it: something observed
    or claimed may not also carry a derivation method, because something
    observed was not derived.

    The human confirmed what the claim *says*. They did not un-extract it --
    the extractor that first read it is still named on the record."""
    claim = _extracted()
    status = HeraldGovernanceAdapter.epistemic_status_for(claim, human_confirmed=True)

    assert status == "HUMAN_CONFIRMED_DERIVED"
    assert status != "ATTESTED", (
        "a machine-extracted figure just acquired the standing of a first-hand record"
    )


def test_a_human_confirmation_with_no_derivation_may_stand_alone():
    """The other side of the same rule: when nothing derived the claim, a
    human confirmation is not carrying a hidden extractor and does not need
    the qualifier."""
    claim = _first_hand_record()
    assert HeraldGovernanceAdapter.epistemic_status_for(claim, human_confirmed=True) == "HUMAN_CONFIRMED"


def test_derivation_evidence_is_detected_from_either_field():
    """`derivation_method` and `extractor` are separate fields and either one
    alone is still evidence the claim was derived."""
    assert HeraldGovernanceAdapter._has_derivation_evidence(
        _extracted(derivation_method="x", extractor=None))
    assert HeraldGovernanceAdapter._has_derivation_evidence(
        _extracted(derivation_method=None, extractor="herald.extract.v2"))
    assert not HeraldGovernanceAdapter._has_derivation_evidence(_first_hand_record())


def test_an_extracted_claim_never_reaches_record_standing_by_itself():
    """Even if the claim declares standing=record, a derivation method on it
    means something reconstructed it. The declared standing does not win over
    the evidence."""
    contradictory = _extracted(standing="record")
    assert HeraldGovernanceAdapter.epistemic_status_for(contradictory) == "DERIVED"


def test_an_assertion_is_carried_as_asserted_not_as_established():
    """HERALD is explicit that attestation standing means a party asserts it
    and truth is not established. Passing it along does not establish it
    either."""
    claim = _extracted(standing="attestation", derivation_method=None, extractor=None)
    assert HeraldGovernanceAdapter.epistemic_status_for(claim) == "ASSERTED"


# ---------------------------------------------------------------------------
# Finding 2: valid and wrong at the same time
# ---------------------------------------------------------------------------

def test_event_time_comes_from_the_claim_not_from_when_it_was_read():
    """The original adapter stamped every event as occurring at the moment the
    document was read. Every field type was right, provenance was consistent,
    the schema passed -- and the event lied about when the payment happened."""
    claim = _extracted(value="1200.00 on 2026-03-14")
    assert HeraldGovernanceAdapter.occurred_at(claim) == "2026-03-14"

    today = datetime.now(timezone.utc).date().isoformat()
    assert HeraldGovernanceAdapter.occurred_at(claim) != today


def test_a_claim_with_no_date_reports_none_rather_than_now():
    """A missing date is reported as missing. Defaulting to ingestion time is
    exactly how finding 2 happened."""
    assert HeraldGovernanceAdapter.occurred_at(_extracted(value="1200.00")) is None


def test_a_date_in_the_raw_text_is_used_when_the_value_has_none():
    claim = _extracted(value="1200.00", raw="Payment posted 2026-03-14 per statement")
    assert HeraldGovernanceAdapter.occurred_at(claim) == "2026-03-14"


def test_ambiguous_dates_are_not_guessed_at():
    """Narrow by design: a wrong date is worse than no date."""
    assert HeraldGovernanceAdapter.occurred_at(_extracted(value="paid 3/14/26")) is None


def test_occurred_at_reaches_the_governed_context():
    context = HeraldGovernanceAdapter.claim_context(_extracted())
    assert context["occurred_at"] == "2026-03-14"


# ---------------------------------------------------------------------------
# Finding 3: a bank statement and an unverified letter looked the same
# ---------------------------------------------------------------------------

def test_a_record_and_an_assertion_are_distinguishable_downstream():
    """They arrived downstream looking identical because the adapter carried
    the claim but not what kind of source produced it."""
    statement = HeraldGovernanceAdapter.claim_to_artifact(_first_hand_record())
    letter = HeraldGovernanceAdapter.claim_to_artifact(
        _extracted(standing="attestation", derivation_method=None, extractor=None)
    )
    assert statement.metadata.epistemic_status.value != letter.metadata.epistemic_status.value
    assert statement.metadata.epistemic_status.value == "RECORD"
    assert letter.metadata.epistemic_status.value == "ASSERTED"


def test_source_identity_and_hash_travel_with_the_claim():
    """So a later reader can go back to the document rather than trusting the
    claim in isolation."""
    context = HeraldGovernanceAdapter.claim_context(_extracted())
    assert context["source_id"] == "doc-77"
    assert context["source_hash"] == "sha256:abcd"
    assert context["standing"] == "derived"


def test_the_extractor_is_named_in_the_context_not_hidden():
    context = HeraldGovernanceAdapter.claim_context(_extracted())
    assert context["extractor"] == "herald.extract.v2"
    assert context["derivation_method"] == "table_row_match"


# ---------------------------------------------------------------------------
# Authority: HERALD disclaims it
# ---------------------------------------------------------------------------

def test_the_adapter_does_not_invent_authority_herald_disclaims():
    """A herald carries a message and has no authority over what happens
    next. Anything stronger here would be this adapter inventing authority the
    producer explicitly refuses."""
    artifact = HeraldGovernanceAdapter.claim_to_artifact(_extracted())
    assert artifact.metadata.authority_status.value == "ADVISORY"


def test_an_unconfirmed_claim_asks_for_human_oversight():
    """HERALD produced it; no person has looked at it."""
    assert HeraldGovernanceAdapter.claim_context(_extracted())["requires_human_oversight"] is True
    assert HeraldGovernanceAdapter.claim_context(
        _extracted(), human_confirmed=True)["requires_human_oversight"] is False


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------

def test_a_confirmed_claim_runs_the_whole_chain(orchestrator):
    result = HeraldGovernanceAdapter.govern_claim(
        orchestrator, _extracted(), human_confirmed=True,
        context={"reviewer": "w.king"},
    )
    assert result["status"] == "APPROVED_AND_EXECUTED", result.get("reason")
    assert result["conservation_decision"].verified is True


def test_a_claim_with_no_stated_reasoning_is_refused(orchestrator):
    """The claim's own reasons are passed as PERCEIVE's justification. An
    extraction that states no reasoning should not clear a gate that exists to
    require one."""
    result = HeraldGovernanceAdapter.govern_claim(
        orchestrator, _extracted(reasons=[]), human_confirmed=True,
        context={"reviewer": "w.king"},
    )
    assert result["status"] == "REJECTED"
    assert any("justification" in v.lower()
               for v in result["governance_decision"].violations)
