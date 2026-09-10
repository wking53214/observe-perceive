"""Copy the project, apply one exact-text mutation, run one test file there.

WHY A CLINICAL ENGINE NEEDS THIS
--------------------------------
A passing suite proves the tests ran. It does not prove they can fail.
For a linter that distinction is a quality concern; for a pediatric
deterioration engine it is a safety one, because the failure mode of a
vacuous test is silence exactly where an escalation should have fired.

Every mutant here breaks a documented clinical safety property on
purpose. If the suite still passes, the property is unguarded and the
comment explaining it is the only thing holding it up.

Ported from ghost_tools, where this harness found a provably dead guard,
two vacuous fixtures, and a test that passed locally and was vacuous in
CI. The layout differs: modules and tests sit at the repository root
here, so ROOT is this file's own directory.

The working tree is never modified.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent

_IGNORE = shutil.ignore_patterns(
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".venv", "docs",
)


def run_tests_with_mutation(
    test_file: str, rel: str, old: str, new: str, timeout: int = 300,
) -> subprocess.CompletedProcess:
    """Run `test_file` in a scratch copy where the single occurrence of
    `old` in `rel` has been replaced by `new`."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = pathlib.Path(tmp) / "observe-perceive"
        shutil.copytree(ROOT, copy, ignore=_IGNORE)
        target = copy / rel
        source = target.read_text()
        assert source.count(old) == 1, (
            f"mutation site not found exactly once in {rel} "
            f"({source.count(old)} occurrences): {old!r}"
        )
        target.write_text(source.replace(old, new))
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", test_file],
            cwd=copy, capture_output=True, text=True, timeout=timeout,
        )


def assert_killed(label: str, test_file: str, result: subprocess.CompletedProcess) -> None:
    assert result.returncode != 0, (
        f"MUTANT SURVIVED: {label}\n"
        f"every test in {test_file} passed with a clinical safety property broken\n"
        + result.stdout[-2500:]
    )
    # Case-insensitive on purpose. pytest -q prints "FAILED test::name" and,
    # depending on version and plugins, may omit the lowercase "N failed"
    # count line entirely -- which made a strict lowercase check report a
    # killed mutant as a survivor. A wrong verdict about a safety property
    # is worse than no verdict.
    out = result.stdout.lower()
    assert "failed" in out or "error" in out, (
        f"{label}: the run exited {result.returncode} but printed no failure "
        "evidence -- it may have been killed rather than failing a test\n"
        + result.stdout[-2500:]
    )
