"""HERALD's admitted claims reach CCC only when HERALD's own proof checks.

The adapter's job is to refuse. These tests pin each refusal, and then pin
the one path that records: a verified export with admitted claims. Refused
claims never become findings, because CCC would refuse them as unverified.

The unit tests use a fake CCC so they can count what would have been
recorded. The last two tests run against the real CCC and CCCb, so the
contract is checked against the actual intake, not only against the fake.
"""

import os
import sys

import pytest

# CCC and CCCb resolve as sibling checkouts, the same way the adapter does.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CCC"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "CCCb"))

# No skip guard on purpose: these tests are the contract for recording into CCC.
# If CCC or CCCb is missing, this module fails to import instead of being skipped
# silently (a silent skip here would hide that nothing is being checked).
from ccc import ActorType  # noqa: E402

from herald_ccc_adapter import (  # noqa: E402
    HeraldCCCAdapter,
    HeraldExportNotVerified,
    finding_from_claim,
    findings_from_export,
)


ADMITTED = {
    "claim_id": "clm-1",
    "kind": "date",
    "value": "2026-11-01",
    "raw": "2026-11-01",
    "span": [48, 58],
    "segment": None,
    "bundle_id": "s1",
    "confidence": 0.88,
    "reasons": [],
    "opacity_flags": ["DEIXIS"],
    "reading": "EXTRACTED",
    "standing": "record",
    "derivation_method": "herald:iso_date",
    "extractor": "iso_date",
    "source_id": "inv-1",
    "source_hash": "a" * 64,
    "content_hash": "b" * 64,
    "authority": "ADVISORY",
}

REFUSED = {
    "claim_id": "clm-2",
    "kind": "amount",
    "raw": "1250",
    "confidence": 0.40,
    "reason": "below threshold",
    "verdict": "REFUSED_PENDING_HUMAN",
}


def export_with(admitted=(ADMITTED,), refused=(REFUSED,), source_id="inv-1"):
    return {
        "document": {"source_id": source_id, "standing": "record"},
        "herald": {"version": "test"},
        "admitted": [dict(entry, source_id=source_id) for entry in admitted],
        "refused": [dict(entry) for entry in refused],
        "export_mac": "not-checked-here",
        "export_mac_version": "1",
    }


def accepting(export):
    return None


def rejecting(export):
    raise ValueError("MAC mismatch")


class FakeCCC:
    """Stands in for CCCSystem and records what the adapter would have sent."""

    def __init__(self, private_source_markers=()):
        self.recorded = []
        self.private_source_markers = tuple(private_source_markers)

    def record_external_finding(self, finding, *, actor, allow_private_source=False):
        self.recorded.append((finding, actor, allow_private_source))
        return f"record-{len(self.recorded)}"


# -- Refusals: nothing is recorded ------------------------------------------

def test_no_verifier_refuses_and_records_nothing():
    fake = FakeCCC()
    adapter = HeraldCCCAdapter(ccc_system=fake)
    with pytest.raises(HeraldExportNotVerified, match="no verifier"):
        adapter.record_export(export_with())
    assert fake.recorded == []


def test_failed_verification_refuses_and_records_nothing():
    fake = FakeCCC()
    adapter = HeraldCCCAdapter(ccc_system=fake)
    with pytest.raises(HeraldExportNotVerified, match="did not verify"):
        adapter.record_export(export_with(), verify=rejecting)
    assert fake.recorded == []


def test_verifier_receives_the_export_unchanged():
    seen = []
    export = export_with()
    findings_from_export(export, verify=lambda e: seen.append(e))
    assert seen == [export]


def test_unverified_export_is_not_read_at_all():
    # A malformed export that also fails verification must be refused on the
    # verification, not crash on its contents.
    with pytest.raises(HeraldExportNotVerified):
        findings_from_export({"admitted": "not a list"}, verify=rejecting)


# -- What a verified export becomes ----------------------------------------

def test_only_admitted_claims_become_findings():
    findings = findings_from_export(export_with(), verify=accepting)
    assert len(findings) == 1
    assert "amount" not in findings[0].conclusion


def test_empty_admission_records_nothing_and_is_not_an_error():
    fake = FakeCCC()
    adapter = HeraldCCCAdapter(ccc_system=fake)
    assert adapter.record_export(export_with(admitted=()), verify=accepting) == []
    assert fake.recorded == []


def test_finding_is_verified_and_carries_confidence_and_excerpt():
    finding = finding_from_claim(ADMITTED)
    assert finding.verified is True
    assert finding.confidence == 0.88
    assert finding.source_material == ("inv-1",)
    # Evidence is span offsets, never the matched text (privacy: see module docs).
    assert finding.evidence == (("inv-1", "span 48-58"),)
    assert finding.method.startswith("HERALD extraction")


def test_conclusion_names_the_case_but_not_the_value():
    conclusion = finding_from_claim(ADMITTED).conclusion
    assert "date claim" in conclusion
    assert "inv-1" in conclusion
    assert "2026-11-01" not in conclusion


def test_two_documents_give_distinguishable_conclusions():
    # The orchestrator adapter's lesson: an identical conclusion for every
    # run reads as a duplicate. The source id has to tell occurrences apart.
    first = finding_from_claim(ADMITTED).conclusion
    second = finding_from_claim(dict(ADMITTED, source_id="inv-2")).conclusion
    assert first != second


def test_evidence_never_carries_the_matched_text():
    finding = finding_from_claim(ADMITTED)
    assert "2026-11-01" not in repr(finding.evidence)


@pytest.mark.parametrize("reading", ["HUMAN_CONFIRMED", "WEIRD", "", None])
def test_only_machine_readings_become_findings(reading):
    # A person's sign-off is not a machine discovery, and an unknown reading
    # fails closed. Only EXTRACTED and INFERRED are recorded.
    export = export_with(admitted=(dict(ADMITTED, reading=reading),))
    assert findings_from_export(export, verify=accepting) == []


@pytest.mark.parametrize("reading", ["EXTRACTED", "INFERRED"])
def test_machine_readings_are_recorded(reading):
    export = export_with(admitted=(dict(ADMITTED, reading=reading),))
    assert len(findings_from_export(export, verify=accepting)) == 1


def test_private_source_anywhere_in_batch_records_nothing():
    # Regression: one private source must not leave earlier findings recorded.
    fake = FakeCCC(private_source_markers=("Resume_OS",))
    adapter = HeraldCCCAdapter(ccc_system=fake)
    export = export_with(admitted=(ADMITTED, dict(ADMITTED, source_id="Resume_OS-x")))
    export["admitted"][1]["source_id"] = "Resume_OS-x"
    with pytest.raises(ValueError, match="nothing was recorded"):
        adapter.record_export(export, verify=accepting)
    assert fake.recorded == []


def test_private_source_is_allowed_only_when_the_caller_says_so():
    fake = FakeCCC(private_source_markers=("Resume_OS",))
    adapter = HeraldCCCAdapter(ccc_system=fake)
    export = export_with(source_id="Resume_OS-invoice")
    adapter.record_export(export, verify=accepting, allow_private_source=True)
    assert len(fake.recorded) == 1
    assert fake.recorded[0][2] is True


def test_each_admitted_claim_is_recorded_as_a_model_actor():
    fake = FakeCCC()
    adapter = HeraldCCCAdapter(ccc_system=fake)
    other = dict(ADMITTED, claim_id="clm-3", kind="amount", raw="1250.00", confidence=0.91)
    records = adapter.record_export(export_with(admitted=(ADMITTED, other)), verify=accepting)
    assert len(records) == 2
    for _finding, actor, allow_private in fake.recorded:
        assert actor is adapter.actor
        assert actor.kind == ActorType.MODEL
        assert allow_private is False


def test_missing_admitted_field_fails_closed():
    broken = dict(ADMITTED)
    del broken["confidence"]
    with pytest.raises(KeyError):
        finding_from_claim(broken)


# -- The real CCC intake ----------------------------------------------------

def test_real_ccc_records_a_verified_export():
    from orchestrator_ccc_adapter import make_ccc_system

    adapter = HeraldCCCAdapter(ccc_system=make_ccc_system())
    records = adapter.record_export(export_with(), verify=accepting)
    assert len(records) == 1
    assert records[0] is not None


def test_real_ccc_refuses_a_private_source():
    from orchestrator_ccc_adapter import make_ccc_system

    adapter = HeraldCCCAdapter(ccc_system=make_ccc_system())
    private = export_with(source_id="Resume_OS-invoice")
    with pytest.raises(ValueError, match="known-private"):
        adapter.record_export(private, verify=accepting)
