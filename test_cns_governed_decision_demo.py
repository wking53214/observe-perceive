"""Tests for examples/cns_governed_decision_demo.py.

Two halves.

The independence half never skips. It checks, in fresh interpreters, that
importing the demo needs neither CNS nor any library, that running it without
CNS stops with exit status 2 and says what to install, and that the new files
carry no CNS import at module level, no em or en dash, and a requirements file
and README section that say what they should.

The connected half runs the real demo against the real libraries and asserts
every scenario's verdicts, its not-evaluated list, how often each gate was
asked, and how often the work ran. It skips cleanly, with a reason, when CNS or
any of the five libraries (or a version of one that has no CNS connector) is
not installed:

    pip install -r requirements-demo.txt
    PYTHONPATH=. python -m pytest test_cns_governed_decision_demo.py
"""

from __future__ import annotations

import ast
import importlib.util
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
DEMO = HERE / "examples" / "cns_governed_decision_demo.py"
FIXTURE_DIR = HERE / "examples" / "cns_demo"
REQUIREMENTS = HERE / "requirements-demo.txt"
README = HERE / "README.md"
RECORDED = HERE / "docs" / "CNS_GOVERNED_DECISION_DEMO.md"

LIBRARY_MODULES = ("governance_gateway", "ccc", "augur", "dit", "conservation_kernel")
CNS_MODULES = ("cns", "cns.gate")
BRANCH = "claude/lean-technical-review-9n6xwk"
CNS_PIN = "3b465dbcc1a6a4ab6f1040f93d44483196abd737"

EM_DASH, EN_DASH = chr(0x2014), chr(0x2013)

GATE_NAMES = ["governance_gateway", "ccc.transition", "augur_screen", "dit", "conservation"]
ALPHA_GATES, OMEGA_GATES = GATE_NAMES[:3], GATE_NAMES[3:]


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _block(*modules: str) -> str:
    """Code that makes importing each named module fail, like an absent package."""
    return "import sys\n" + "".join(f"sys.modules[{m!r}] = None\n" for m in modules)


def _python(code: str, *, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = {"PYTHONPATH": str(HERE), "PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [sys.executable, "-c", textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(cwd or HERE),
        timeout=300,
    )


def _run_script(*, block: tuple[str, ...] = (), cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """The demo run as a script (so its ``__main__`` block runs), with modules blocked."""
    code = (
        _block(*block)
        + "import runpy\n"
        + f"runpy.run_path({str(DEMO)!r}, run_name='__main__')\n"
    )
    return _python(code, cwd=cwd)


def _top_level_imports(path: Path) -> set[str]:
    """Top-level module names imported by statements that run when the file loads.

    Walks the module body, into if/try/with blocks, but not into function or
    class bodies, which do not run at import time.
    """
    names: set[str] = set()

    def visit(statements: list[ast.stmt]) -> None:
        for node in statements:
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level == 0 and node.module:
                    names.add(node.module.split(".")[0])
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            else:
                for field in ("body", "orelse", "finalbody"):
                    visit(getattr(node, field, []) or [])
                for handler in getattr(node, "handlers", []) or []:
                    visit(handler.body)

    visit(ast.parse(path.read_text(encoding="utf-8")).body)
    return names


def _readme_section() -> str:
    text = README.read_text(encoding="utf-8")
    start = text.index("## Governed decision across libraries (optional)")
    nxt = re.search(r"^## ", text[start + 3 :], flags=re.MULTILINE)
    return text[start : start + 3 + nxt.start()] if nxt else text[start:]


# ---------------------------------------------------------------------------
# Independence half: never skips.
# ---------------------------------------------------------------------------


def test_importing_the_demo_needs_neither_cns_nor_any_library():
    done = _python(
        _block(*CNS_MODULES, *LIBRARY_MODULES)
        + f"""
import importlib.util
spec = importlib.util.spec_from_file_location("demo_under_test", {str(DEMO)!r})
module = importlib.util.module_from_spec(spec)
sys.modules["demo_under_test"] = module
spec.loader.exec_module(module)
assert callable(module.main) and callable(module.run_demo)
assert [s.key for s in module.SCENARIOS][:2] == ["S1", "S2"]
print("ok")
"""
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_importing_the_demo_loads_no_cns_and_no_library_even_when_they_are_installed():
    done = _python(
        f"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("demo_under_test", {str(DEMO)!r})
module = importlib.util.module_from_spec(spec)
sys.modules["demo_under_test"] = module
spec.loader.exec_module(module)
loaded = sorted(
    name for name in sys.modules
    if sys.modules[name] is not None
    and name.split(".")[0] in {set(("cns",) + LIBRARY_MODULES)!r}
)
assert loaded == [], loaded
print("ok")
"""
    )
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok"


def test_running_the_demo_without_cns_exits_2_and_says_what_to_install(tmp_path):
    done = _run_script(block=CNS_MODULES, cwd=tmp_path)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "The demo cannot run" in done.stderr
    assert "pip install -r requirements-demo.txt" in done.stderr
    assert "cns" in done.stderr.lower()
    assert "Traceback" not in done.stderr
    assert done.stdout == ""
    assert list(tmp_path.iterdir()) == []


def test_no_new_file_imports_cns_or_a_library_at_module_level():
    files = [DEMO, *sorted(FIXTURE_DIR.glob("*.py")), Path(__file__)]
    assert len(files) >= 8, files
    forbidden = set(CNS_MODULES[:1]) | set(LIBRARY_MODULES)
    for path in files:
        assert _top_level_imports(path).isdisjoint(forbidden), (
            path.name,
            sorted(_top_level_imports(path) & forbidden),
        )


def test_the_demo_is_a_runnable_script_and_a_package_next_to_it():
    assert DEMO.is_file() and (FIXTURE_DIR / "__init__.py").is_file()
    assert {p.name for p in FIXTURE_DIR.glob("fixture_*.py")} == {
        "fixture_augur.py",
        "fixture_ccc.py",
        "fixture_conservation_kernel.py",
        "fixture_dit.py",
        "fixture_governance_gateway.py",
    }
    tree = ast.parse(DEMO.read_text(encoding="utf-8"))
    assert any(
        isinstance(n, ast.If) and "__main__" in ast.unparse(n.test) for n in tree.body
    )


def test_the_new_files_contain_no_em_or_en_dash():
    section = _readme_section()
    texts = {
        "demo": DEMO.read_text(encoding="utf-8"),
        "this test": Path(__file__).read_text(encoding="utf-8"),
        "requirements-demo.txt": REQUIREMENTS.read_text(encoding="utf-8"),
        "recorded output": RECORDED.read_text(encoding="utf-8"),
        "README section": section,
    }
    for path in sorted(FIXTURE_DIR.glob("*.py")):
        texts[path.name] = path.read_text(encoding="utf-8")
    for name, text in texts.items():
        assert EM_DASH not in text and EN_DASH not in text, name


def test_requirements_demo_names_cns_and_each_library_at_its_branch():
    lines = [
        line.strip()
        for line in REQUIREMENTS.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    by_name = {re.split(r"[\[ @]", line, maxsplit=1)[0]: line for line in lines}
    assert set(by_name) == {
        "cns",
        "governance-gateway",
        "cognitive-continuity-constitution",
        "augur",
        "dit",
        "conservation-kernel",
    }
    assert by_name["cns"] == f"cns @ git+https://github.com/wking53214/cns.git@{CNS_PIN}"
    repos = {
        "governance-gateway": "governance_gateway",
        "cognitive-continuity-constitution": "ccc",
        "augur": "augur",
        "dit": "dit",
        "conservation-kernel": "conservation_kernel",
    }
    for dist, repo in repos.items():
        # Each library is pinned to an exact 40-hex commit, never a branch name,
        # so a later push cannot change what the demo installs.
        assert re.fullmatch(
            re.escape(f"{dist}[cns] @ git+https://github.com/wking53214/{repo}.git@")
            + r"[0-9a-f]{40}",
            by_name[dist],
        ), by_name[dist]
        assert BRANCH not in by_name[dist]
    text = REQUIREMENTS.read_text(encoding="utf-8")
    assert "exact commits" in text and "BRANCH REFS" not in text


def test_the_readme_section_explains_the_layer_and_lists_rules_r1_to_r7():
    section = _readme_section()
    assert section.startswith("## Governed decision across libraries (optional)")
    for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7"):
        assert re.search(rf"^\| {rule} ", section, flags=re.MULTILINE), rule
    for needle in (
        "examples/cns_governed_decision_demo.py",
        "requirements-demo.txt",
        "GovernanceOrchestrator",
        "docs/CNS_GOVERNED_DECISION_DEMO.md",
        "does not replace",
        "tamper-evidence",
    ):
        assert needle in section, needle


def _recorded_output() -> str:
    text = RECORDED.read_text(encoding="utf-8")
    blocks = re.findall(r"```text\n(.*?)\n```", text, flags=re.DOTALL)
    assert len(blocks) == 2, "expected the command block and the output block"
    return blocks[1] + "\n"


def test_the_recorded_run_is_the_demo_output_with_every_scenario_as_expected():
    out = _recorded_output()
    headings = re.findall(r"^--- (S\w+): ", out, flags=re.MULTILINE)
    assert headings == ["S1", "S2", "S3", "S3b", "S4", "S5", "S6", "S6b", "S7", "S7b", "S8"]
    assert out.rstrip().endswith("ALL SCENARIOS AS EXPECTED")
    assert "MISMATCH" not in out
    assert "expected: APPROVED with 1 work call(s): ok" in out
    assert out.count("work executed: NO (work calls: 0)") == 5
    command = re.findall(r"```text\n(.*?)\n```", RECORDED.read_text(encoding="utf-8"), flags=re.DOTALL)[0]
    assert "cns_governed_decision_demo.py" in command


# ---------------------------------------------------------------------------
# Connected half: needs CNS and the five libraries, skips cleanly without them.
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def demo():
    """The demo module, loaded from its file. Skips unless everything it needs is here."""
    pytest.importorskip("cns.gate", reason="CNS is not installed (pip install -r requirements-demo.txt)")
    for library in LIBRARY_MODULES:
        pytest.importorskip(library, reason=f"{library} is not installed")
        pytest.importorskip(
            f"{library}.cns_connector",
            reason=f"the installed {library} has no CNS connector",
        )
    spec = importlib.util.spec_from_file_location("cns_governed_decision_demo_under_test", DEMO)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their annotations through sys.modules
    try:
        spec.loader.exec_module(module)
        yield module
    finally:
        sys.modules.pop(spec.name, None)


@pytest.fixture(scope="module")
def run(demo):
    return demo.run_demo()


@pytest.fixture(scope="module")
def reports(run):
    return {r.scenario.key: r for r in run.reports}


def triples(report):
    return [(v.gate, v.position.name, v.outcome.name) for v in report.decision.verdicts]


def asked(report):
    return dict(report.checks)


def not_evaluated(report):
    return dict(report.decision.not_evaluated)


def verdict(report, gate):
    (v,) = [v for v in report.decision.verdicts if v.gate == gate]
    return v


ALL_PASS = [
    ("governance_gateway", "ALPHA", "PASS"),
    ("ccc.transition", "ALPHA", "PASS"),
    ("augur_screen", "ALPHA", "PASS"),
    ("dit", "OMEGA", "PASS"),
    ("conservation", "OMEGA", "PASS"),
]


def test_the_pipeline_scenarios_are_s1_to_s7_with_three_variants(run):
    assert [r.scenario.key for r in run.reports] == [
        "S1", "S2", "S3", "S3b", "S4", "S5", "S6", "S6b", "S7", "S7b",
    ]


def test_every_gate_is_the_librarys_own_and_sits_in_the_slot_for_its_declared_end(demo):
    from cns.gate import Gate, GateChain

    gates, missing = demo.load_gates()
    assert missing == {}
    assert [g.name for g in gates.values()] == GATE_NAMES
    for lib in demo.LIBRARIES:
        gate = gates[lib.key]
        assert isinstance(gate, Gate)
        assert gate.position.name == lib.fixture.POSITION
        assert type(gate._gate).__module__ == f"{lib.key}.cns_connector"
    chain = GateChain(
        alpha=tuple(gates[k] for k in ("governance_gateway", "ccc", "augur")),
        omega=tuple(gates[k] for k in ("dit", "conservation_kernel")),
    )
    assert chain.misplaced() == () and chain.complete()
    pipeline = demo.build_pipeline(gates)
    assert pipeline.names == tuple(GATE_NAMES)


def test_s1_everything_passes_and_the_work_runs_once(reports):
    r = reports["S1"]
    d = r.decision
    assert triples(r) == ALL_PASS
    assert (d.outcome, d.executed, r.work_calls) == ("pass", True, 1)
    assert d.not_evaluated == () and d.work_error is None and d.result_withheld is False
    assert d.result.text.startswith("Lot L-2291 of Compound X 10 mg tablets was released")
    assert asked(r) == {name: 1 for name in GATE_NAMES}
    assert verdict(r, "augur_screen").reason.startswith(
        "no objection: the simulation ended in regime STABLE"
    )


def test_s2_an_omega_text_gate_refuses_retryably_and_the_work_ran(reports):
    r = reports["S2"]
    d = r.decision
    assert triples(r) == [*ALL_PASS[:3], ("dit", "OMEGA", "RETRY"), ("conservation", "OMEGA", "PASS")]
    assert (d.outcome, d.executed, r.work_calls) == ("retry", True, 1)
    assert "hedging" in verdict(r, "dit").reason and "'may'" in verdict(r, "dit").reason
    assert d.result is None and d.result_withheld is True
    assert d.not_evaluated == ()


def test_s3_an_omega_terminal_refusal_overrides_every_passing_gate(reports):
    r = reports["S3"]
    d = r.decision
    assert triples(r) == [*ALL_PASS[:4], ("conservation", "OMEGA", "TERMINAL_BREACH")]
    assert (d.outcome, d.executed, r.work_calls) == ("terminal_breach", True, 1)
    reason = verdict(r, "conservation").reason
    assert reason.startswith("REJECT: ")
    for code in ("UNDECLARED_CHANGE", "UNCERTAINTY_COLLAPSE", "NO_INDEPENDENT_VERIFICATION"):
        assert code in reason
    assert d.result is None and d.result_withheld is True


def test_s3b_a_retry_and_a_terminal_breach_together_resolve_to_terminal(reports):
    r = reports["S3b"]
    assert triples(r) == [
        *ALL_PASS[:3],
        ("dit", "OMEGA", "RETRY"),
        ("conservation", "OMEGA", "TERMINAL_BREACH"),
    ]
    assert r.decision.outcome == "terminal_breach"
    assert asked(r)["conservation"] == 1  # a RETRY does not stop the omega end


def test_s4_an_alpha_refusal_means_the_work_is_never_called(reports):
    r = reports["S4"]
    d = r.decision
    assert r.work_calls == 0, "the work function must never run when an ALPHA gate refuses"
    assert (d.outcome, d.executed) == ("terminal_breach", False)
    assert triples(r) == [*ALL_PASS[:2], ("augur_screen", "ALPHA", "TERMINAL_BREACH")]
    assert verdict(r, "augur_screen").reason.startswith(
        "refused: the simulation predicts regime CRITICAL at its last step"
    )
    assert set(not_evaluated(r)) == {"dit", "conservation"}
    assert all("alpha gate did not pass" in why for why in not_evaluated(r).values())
    assert asked(r) == {
        "governance_gateway": 1, "ccc.transition": 1, "augur_screen": 1, "dit": 0, "conservation": 0,
    }
    assert d.result is None and d.result_withheld is False


def test_s5_the_first_terminal_alpha_verdict_stops_the_second_gate(reports):
    r = reports["S5"]
    d = r.decision
    assert r.work_calls == 0 and (d.outcome, d.executed) == ("terminal_breach", False)
    assert triples(r) == [("governance_gateway", "ALPHA", "TERMINAL_BREACH")]
    assert verdict(r, "governance_gateway").reason == "INTEGRITY_FAILURE"
    skipped = not_evaluated(r)
    assert list(skipped) == ["ccc.transition", "augur_screen", "dit", "conservation"]
    assert skipped["ccc.transition"] == "short-circuit: governance_gateway returned TERMINAL_BREACH"
    assert skipped["augur_screen"] == "short-circuit: governance_gateway returned TERMINAL_BREACH"
    assert asked(r) == {
        "governance_gateway": 1, "ccc.transition": 0, "augur_screen": 0, "dit": 0, "conservation": 0,
    }


def test_s5_the_gate_that_was_skipped_really_would_have_refused(reports):
    ((key, v),) = reports["S5"].comparisons
    assert key == "ccc"
    assert (v.gate, v.position.name, v.outcome.name) == ("ccc.transition", "ALPHA", "TERMINAL_BREACH")
    assert v.reason.startswith("CCC-RATIFICATION-001")


def test_s6_a_required_alpha_library_that_is_missing_fails_closed(reports):
    r = reports["S6"]
    d = r.decision
    assert reports["S6"].scenario.withhold == ("ccc",)
    assert r.work_calls == 0 and (d.outcome, d.executed) == ("terminal_breach", False)
    assert triples(r) == [("governance_gateway", "ALPHA", "PASS"), ("ccc.transition", "ALPHA", "TERMINAL_BREACH")]
    synthetic = verdict(r, "ccc.transition")
    assert synthetic.subject == "synthetic:gate_unavailable"
    assert "gate unavailable" in synthetic.reason and "required" in synthetic.reason
    assert asked(r)["ccc.transition"] == 0
    assert "short-circuit" in not_evaluated(r)["augur_screen"]


def test_s6b_a_required_omega_library_that_is_missing_stops_the_work_before_it_starts(reports):
    r = reports["S6b"]
    d = r.decision
    assert reports["S6b"].scenario.withhold == ("conservation_kernel",)
    assert r.work_calls == 0 and (d.outcome, d.executed) == ("terminal_breach", False)
    assert triples(r)[:3] == ALL_PASS[:3]
    assert triples(r)[3] == ("conservation", "OMEGA", "TERMINAL_BREACH")
    assert verdict(r, "conservation").subject == "synthetic:gate_unavailable"
    assert "the work was not started" in verdict(r, "conservation").reason
    assert not_evaluated(r) == {
        "dit": "not evaluated: the work did not run, a required output gate is unavailable"
    }
    assert asked(r)["dit"] == 0


def test_s7_a_real_alpha_gate_that_raises_fails_closed_without_a_crash(reports):
    r = reports["S7"]
    d = r.decision
    assert r.work_calls == 0 and (d.outcome, d.executed) == ("terminal_breach", False)
    assert triples(r) == [("governance_gateway", "ALPHA", "PASS"), ("ccc.transition", "ALPHA", "TERMINAL_BREACH")]
    synthetic = verdict(r, "ccc.transition")
    assert synthetic.subject == "synthetic:gate_error"
    assert "raised TypeError" in synthetic.reason
    assert asked(r)["ccc.transition"] == 1, "the real gate was asked, and it was the real gate that raised"
    assert asked(r)["augur_screen"] == 0
    assert set(not_evaluated(r)) == {"augur_screen", "dit", "conservation"}


def test_s7b_a_real_omega_gate_that_raises_withholds_the_result(reports):
    r = reports["S7b"]
    d = r.decision
    assert r.work_calls == 1 and (d.outcome, d.executed) == ("terminal_breach", True)
    assert triples(r)[:4] == ALL_PASS[:4]
    assert triples(r)[4] == ("conservation", "OMEGA", "TERMINAL_BREACH")
    assert verdict(r, "conservation").subject == "synthetic:gate_error"
    assert "raised TypeError" in verdict(r, "conservation").reason
    assert d.result is None and d.result_withheld is True


def test_one_pipeline_serves_every_scenario_except_the_two_with_an_empty_slot(demo, monkeypatch):
    built = []
    real = demo.build_pipeline

    def spy(gates, withhold=()):
        built.append(withhold)
        return real(gates, withhold)

    monkeypatch.setattr(demo, "build_pipeline", spy)
    result = demo.run_demo()
    assert built == [(), ("ccc",), ("conservation_kernel",)]
    assert len(result.reports) == 10 and result.all_as_expected


def test_every_verdict_in_every_scenario_is_bound_to_what_it_judged(run):
    from cns.gate import unbound

    for r in run.reports:
        assert unbound(r.decision.verdicts) == (), r.scenario.key


def test_only_the_combining_layer_issues_synthetic_verdicts_and_only_where_a_gate_failed(run):
    synthetic = {
        r.scenario.key: [v.gate for v in r.decision.verdicts if v.subject.startswith("synthetic:")]
        for r in run.reports
    }
    assert synthetic == {
        "S1": [], "S2": [], "S3": [], "S3b": [], "S4": [], "S5": [],
        "S6": ["ccc.transition"], "S6b": ["conservation"],
        "S7": ["ccc.transition"], "S7b": ["conservation"],
    }


def test_the_work_ran_exactly_as_often_as_the_scenarios_say(run):
    assert {r.scenario.key: r.work_calls for r in run.reports} == {
        "S1": 1, "S2": 1, "S3": 1, "S3b": 1, "S4": 0, "S5": 0, "S6": 0, "S6b": 0, "S7": 0, "S7b": 1,
    }
    assert {r.scenario.key: r.decision.outcome for r in run.reports} == {
        "S1": "pass", "S2": "retry", "S3": "terminal_breach", "S3b": "terminal_breach",
        "S4": "terminal_breach", "S5": "terminal_breach", "S6": "terminal_breach",
        "S6b": "terminal_breach", "S7": "terminal_breach", "S7b": "terminal_breach",
    }


def test_no_omega_gate_is_asked_unless_every_alpha_gate_passed_and_the_work_ran(run):
    for r in run.reports:
        omega_asked = sum(asked(r)[name] for name in OMEGA_GATES)
        if r.work_calls == 0:
            assert omega_asked == 0, r.scenario.key


def test_s8_an_edited_text_no_longer_binds_even_with_its_digest_recomputed(run):
    t = run.tamper
    assert "dissolution 96.0%" in t.original_text and "dissolution 99.9%" in t.edited_text
    assert t.binds_original is True
    assert t.binds_edited is False
    assert t.record_digest_ok is True
    assert t.edited_record_digest_ok is False


def test_s8_a_hand_built_verdict_with_the_new_digest_does_bind_so_this_is_tamper_evidence_only(run):
    assert run.tamper.forged_binds is True


def test_the_demo_as_a_whole_ends_as_expected(run):
    assert run.all_as_expected is True
    assert all(r.as_expected for r in run.reports)


def test_the_rendered_output_shows_every_scenario_and_the_final_decisions(demo, run):
    out = demo.render(run)
    assert out.rstrip().endswith("ALL SCENARIOS AS EXPECTED")
    headings = re.findall(r"^--- (S\w+): ", out, flags=re.MULTILINE)
    assert headings == ["S1", "S2", "S3", "S3b", "S4", "S5", "S6", "S6b", "S7", "S7b", "S8"]
    finals = re.findall(r"^  final decision: (\w+)$", out, flags=re.MULTILINE)
    assert finals == [
        "APPROVED", "RETRY", "TERMINAL_BREACH", "TERMINAL_BREACH", "TERMINAL_BREACH",
        "TERMINAL_BREACH", "TERMINAL_BREACH", "TERMINAL_BREACH", "TERMINAL_BREACH", "TERMINAL_BREACH",
    ]
    assert re.findall(r"^  work executed: (\w+)", out, flags=re.MULTILINE) == [
        "yes", "yes", "yes", "yes", "NO", "NO", "NO", "NO", "NO", "yes",
    ]
    assert "[issued by the combining layer, not by a gate]" in out
    assert EM_DASH not in out and EN_DASH not in out


def test_the_recorded_output_has_the_same_outcomes_as_a_live_run(demo, run):
    live = demo.render(run)
    recorded = _recorded_output()

    def outcomes(text):
        return (
            re.findall(r"^--- (S\w+): ", text, flags=re.MULTILINE),
            re.findall(r"^  final decision: (\w+)$", text, flags=re.MULTILINE),
            re.findall(r"^  work executed: (\w+) \(work calls: (\d+)\)", text, flags=re.MULTILINE),
            re.findall(r"^  gate check\(\) calls: (.*)$", text, flags=re.MULTILINE),
        )

    assert outcomes(recorded) == outcomes(live)


def test_a_scenario_that_misses_its_expectation_makes_the_demo_exit_1(demo, monkeypatch, capsys):
    import dataclasses

    wrong = tuple(
        dataclasses.replace(s, expect_work_calls=5) if s.key == "S1" else s for s in demo.SCENARIOS
    )
    monkeypatch.setattr(demo, "SCENARIOS", wrong)
    assert demo.main() == 1
    out = capsys.readouterr().out
    assert "expected: APPROVED with 5 work call(s): MISMATCH" in out
    assert out.rstrip().endswith("SOME SCENARIOS DID NOT END AS EXPECTED")


def test_the_stub_work_counts_its_calls(demo):
    work = demo.StubWork()
    assert work.calls == 0
    first = work(demo.Plan())
    work(demo.Plan(wording="hedged"))
    assert work.calls == 2
    assert "may be" not in first.text


def test_a_missing_library_is_reported_and_exits_2_when_cns_is_present(tmp_path, demo):
    done = _run_script(block=("ccc",), cwd=tmp_path)
    assert done.returncode == 2, (done.stdout, done.stderr)
    assert "The demo cannot run" in done.stderr and "ccc" in done.stderr
    assert "Traceback" not in done.stderr
    assert done.stdout == ""


def test_run_as_a_script_it_exits_0_prints_the_same_thing_twice_and_leaves_no_files(tmp_path, demo):
    first = _run_script(cwd=tmp_path)
    second = _run_script(cwd=tmp_path)
    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert first.stdout == second.stdout
    assert first.stdout.rstrip().endswith("ALL SCENARIOS AS EXPECTED")
    assert first.stderr == "" and second.stderr == ""
    assert list(tmp_path.iterdir()) == [], "the demo must not write into its working directory"
