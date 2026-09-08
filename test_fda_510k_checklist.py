"""The 510(k) readiness checklist may not claim more than the repository holds.

This file is exported to reviewers by compliance_exporters.py. An audit on
2026-09-07 found eleven items marked "ready, tests pass" with no test behind
them and two citing a document that does not exist here. These tests make
that impossible to repeat: every file an item cites must exist, and every
item marked ready must name at least one test that exists.
"""

import os
import re

import pytest

from fda_510k_checklist import CHECKLIST

HERE = os.path.dirname(os.path.abspath(__file__))
READY = "✅"


def _items():
    for section, items in CHECKLIST.items():
        for key, item in items.items():
            yield f"{section} {key}", item


@pytest.mark.parametrize("label,item", list(_items()))
def test_every_cited_file_exists(label, item):
    cited = re.findall(r"[\w\-]+\.(?:py|md|txt|json|ini)", item.get("evidence", ""))
    missing = [f for f in cited if not os.path.exists(os.path.join(HERE, f))]
    assert not missing, f"{label} cites files that do not exist here: {missing}"


@pytest.mark.parametrize("label,item", [(lbl, itm) for lbl, itm in _items() if itm["status"] == READY])
def test_every_ready_item_names_a_real_test(label, item):
    refs = item.get("tests") or []
    assert refs, f"{label} is marked ready but names no test"
    for ref in refs:
        path, _, func = ref.partition("::")
        full = os.path.join(HERE, path)
        assert os.path.exists(full), f"{label}: {path} does not exist"
        if func:
            source = open(full, encoding="utf-8").read()
            assert re.search(rf"^\s*def {re.escape(func)}\(", source, re.M), \
                f"{label}: {func} is not defined in {path}"


def test_no_ready_item_relies_on_a_test_that_skips_itself():
    """A test that calls skipTest on the outcome it exists to check reports
    green whatever happens. Ready items must not rest on one."""
    skippers = {"test_deterioration_simulator.py", "test_adversarial_sensor_faults.py"}
    for label, item in _items():
        if item["status"] != READY:
            continue
        used = {ref.partition("::")[0] for ref in item.get("tests", [])}
        assert not (used & skippers), f"{label} rests on a self-skipping test: {used & skippers}"
