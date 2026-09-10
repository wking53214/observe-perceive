"""One contract, every installed domain.

This suite never names a domain. It parametrises over the registry, so a
third cassette is covered the day it is added and a core change that
breaks any domain fails here rather than in whichever repository happens
to notice first.

If you are reading this because it failed on a cassette you just added:
that is the suite doing its job. The core makes exactly these promises to
every domain, and yours is the first to have caught one being broken.
"""
from __future__ import annotations

import pytest

from cassette import REQUIRED, conformance_failures, label
from installed_cassettes import INSTALLED, InstalledCassette, verify_registry
from observe_consolidated import ObserveClinicalEngine

IDS = [e.name for e in INSTALLED]


def test_the_tree_ships_enough_domains_to_prove_the_claim():
    """A core coupled to its only domain passes every test that domain can
    write. Dropping to one cassette does not break a feature -- it silently
    removes the only evidence that the core is domain-agnostic, which is
    why this is asserted rather than left to reviewers to notice."""
    verify_registry(INSTALLED)


def test_the_rule_actually_fires_on_a_single_domain():
    """The guard above, shown failing. Otherwise it is a comment."""
    with pytest.raises(AssertionError) as ctx:
        verify_registry(INSTALLED[:1])
    assert "untested claim" in str(ctx.value)


def test_the_rule_fires_on_two_domains_that_share_a_reading_type():
    """Two cassettes over the same observation would leave a field-level
    coupling undetectable, so that counts as one domain, not two."""
    twin = InstalledCassette(cassette=INSTALLED[1].cassette,
                             nominal=INSTALLED[0].nominal,
                             adverse=INSTALLED[0].adverse,
                             faulted=INSTALLED[0].faulted)
    with pytest.raises(AssertionError) as ctx:
        verify_registry([INSTALLED[0], twin])
    assert "its own observation type" in str(ctx.value)


def test_the_rule_fires_on_duplicate_domain_names():
    with pytest.raises(AssertionError) as ctx:
        verify_registry([INSTALLED[0], INSTALLED[0]])
    assert "distinct" in str(ctx.value)


@pytest.mark.parametrize("entry", INSTALLED, ids=IDS)
class TestEveryDomainHonoursTheContract:

    def test_it_conforms(self, entry):
        assert conformance_failures(entry.cassette) == []

    def test_it_answers_every_question(self, entry):
        for member in REQUIRED:
            assert hasattr(entry.cassette, member), member

    def test_its_channels_and_model_agree(self, entry):
        """A channel declared by the model but absent from a reading would
        degrade the trajectory filter invisibly."""
        values = entry.cassette.channels(entry.nominal())
        model = entry.cassette.channel_model()
        assert set(model) == set(values), (
            f"{entry.name}: channel_model() names {sorted(model)} but "
            f"channels() supplies {sorted(values)}"
        )
        assert values, "a domain must declare at least one numeric channel"

    def test_its_labels_are_its_own(self, entry):
        """No domain may inherit another's audit vocabulary."""
        others = [o for o in INSTALLED if o.name != entry.name]
        mine = {label(entry.cassette, k) for k in ("record_kind", "safety_bypass")}
        for other in others:
            theirs = {label(other.cassette, k) for k in ("record_kind", "safety_bypass")}
            assert not (mine & theirs), f"{entry.name} shares vocabulary with {other.name}"


@pytest.mark.parametrize("entry", INSTALLED, ids=IDS)
class TestEveryDomainReachesTheRightVerdict:
    """The three promises the core makes to every domain."""

    def _engine(self, entry):
        return ObserveClinicalEngine(cassette=entry.cassette)

    def test_a_nominal_reading_is_stable(self, entry):
        verdict = self._engine(entry).evaluate(entry.nominal())
        assert verdict.regime.value == "stable", (
            f"{entry.name}: a nominal reading came back {verdict.regime.value}"
        )

    def test_an_adverse_reading_escalates_without_a_second_confirmation(self, entry):
        """Each domain's own 'never noise' condition must skip dwell. A
        deteriorating subject that waits for a confirming reading is the
        harm the bypass exists to prevent."""
        verdict = self._engine(entry).evaluate(entry.adverse())
        assert verdict.regime.value in ("warning", "critical"), (
            f"{entry.name}: adverse reading only reached {verdict.regime.value}"
        )
        bypass = label(entry.cassette, "safety_bypass")
        assert any(bypass in r for r in verdict.triggered_rules), (
            f"{entry.name}: escalated but without recording why"
        )

    def test_an_impossible_reading_is_a_fault_not_a_healthy_verdict(self, entry):
        """The failure that matters most: an untrustworthy reading scored as
        normal. It must never come back stable."""
        verdict = self._engine(entry).evaluate(entry.faulted())
        assert verdict.regime.value != "stable", (
            f"{entry.name}: an impossible reading was scored as stable"
        )
        assert any("DATA_INTEGRITY_FAULT" in r for r in verdict.triggered_rules)

    def test_subjects_are_isolated_from_one_another(self, entry):
        """Per-subject state keys on whatever the cassette calls identity."""
        engine = self._engine(entry)
        engine.evaluate(entry.adverse())
        engine.evaluate(entry.nominal())
        assert entry.cassette.subject_id(entry.nominal()) in engine._patient_entropy
