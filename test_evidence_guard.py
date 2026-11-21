"""observe-perceive builds its Conservation Kernels with an evidence guard that accepts nothing.

The kernel refuses to start without an evidence guard (or an explicit allow_unguarded_evidence), because without
one any caller could add a record that declares itself independent verification and have it counted. This package
adds no evidence to its kernels, so the guard it passes, `root_admission.refuse_all_evidence`, says no to every
record. A deployer who wants evidence accepted passes their own guard; these tests show both sides of that.
"""

import pytest
from conservation_kernel import Actor, ConservationKernel, EvidenceKind, EvidenceRecord
from conservation_kernel.errors import InvalidEvent, UnguardedKernelError

from root_admission import refuse_all_evidence


def _record(evidence_id="ev-1"):
    return EvidenceRecord(
        evidence_id=evidence_id,
        subject_id="s-1",
        kind=EvidenceKind.INDEPENDENT_VERIFICATION,
        provided_by=Actor.external("someone"),
        independent=True,
        detail={"note": "declares itself independent verification"},
        created_at="2026-11-21T00:00:00Z",
    )


def test_the_guard_says_no_to_every_record():
    assert refuse_all_evidence(_record()) is False


def test_a_kernel_built_with_it_refuses_evidence_that_declares_itself_independent():
    kernel = ConservationKernel(allow_unguarded_roots=True, evidence_guard=refuse_all_evidence)
    with pytest.raises(InvalidEvent, match="evidence guard"):
        kernel._registry.add_evidence(_record())


def test_a_kernel_with_no_evidence_guard_and_no_opt_out_is_refused_at_build_time():
    with pytest.raises(UnguardedKernelError):
        ConservationKernel(allow_unguarded_roots=True)


def test_a_deployers_own_guard_is_honoured_instead():
    kernel = ConservationKernel(allow_unguarded_roots=True, evidence_guard=lambda record: True)
    kernel._registry.add_evidence(_record("ev-2"))
