"""Without a CNS that has DecisionGuard, the examples stop before doing anything and name the install command.

demo_why.py and vertical_slice.py build the kernel with the real root guard (root_admission.RootAdmission), so
they need CNS. This file hides the module the guard needs, so it checks the same thing whether or not CNS is
installed, and runs in both CI jobs.
"""
import sys

import pytest

import demo_why
import vertical_slice
from cns_connector import INSTALL_HINT, CnsNotInstalled
from root_admission import require_root_admission


@pytest.fixture
def no_decision_guard(monkeypatch):
    monkeypatch.setitem(sys.modules, "cns_composition.decision_guard", None)


def test_the_check_refuses_and_names_the_install_command(no_decision_guard):
    with pytest.raises(CnsNotInstalled, match="pip install"):
        require_root_admission()


def test_the_demo_without_cns_exits_2_and_says_how_to_install(no_decision_guard, capsys):
    assert demo_why.main(["--quiet"]) == 2
    assert INSTALL_HINT in capsys.readouterr().err


def test_the_slice_without_cns_exits_2_before_writing_anything(no_decision_guard, capsys, tmp_path):
    workdir = tmp_path / "slice"
    assert vertical_slice.main([str(workdir)]) == 2
    assert INSTALL_HINT in capsys.readouterr().err
    assert not workdir.exists()
