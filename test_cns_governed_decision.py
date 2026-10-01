"""Tests for cns_governed_decision, the optional CNS combining layer.

Two halves.

The independence half never skips. Each test runs in a fresh interpreter in
which ``cns`` is blocked outright (``sys.modules['cns'] = None`` makes any
import of it fail), so the result does not depend on what the environment
happens to contain: the module imports, using the pipeline says what is
missing, and the packaging adds no runtime dependency.

The connected half needs ``cns.gate`` (``pip install 'observe-perceive[cns]'``)
and skips without it. Its fake gates declare a position and return what they
are told, so every rule R1 to R8 is exercised without any library behind it,
and one test drives the real DIT connector (``pip install 'dit[cns]'``).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import os
import subprocess
import sys
import textwrap
import tomllib
import types
from pathlib import Path

import pytest

import cns_governed_decision as cgd
from cns_governed_decision import (
    PLACEMENT_GATE,
    SYNTHETIC_SUBJECT_PREFIX,
    WORK_GATE,
    CnsNotInstalled,
    GovernedPipeline,
    Slot,
)

HERE = Path(__file__).resolve().parent
MODULE_PATH = HERE / "cns_governed_decision.py"
PYPROJECT = HERE / "pyproject.toml"

CNS_PIN = "3b465dbcc1a6a4ab6f1040f93d44483196abd737"
HINT = "pip install 'observe-perceive[cns]'"


# ---------------------------------------------------------------------------
# Independence half: no cns, never skips.
# ---------------------------------------------------------------------------

BLOCK = "import sys; sys.modules['cns'] = None; sys.modules['cns.gate'] = None\n"


def _run(code: str, *, block: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", (BLOCK if block else "") + textwrap.dedent(code)],
        capture_output=True,
        text=True,
        env={"PYTHONPATH": str(HERE), "PATH": os.environ.get("PATH", "")},
        cwd=str(HERE),
        timeout=60,
    )


def _ok(done: subprocess.CompletedProcess[str]) -> None:
    assert done.returncode == 0, done.stderr
    assert done.stdout.strip() == "ok", done.stdout


def test_the_module_imports_with_cns_blocked():
    _ok(
        _run(
            """
            import cns_governed_decision as m
            assert m.cns_available() is False
            for name in ("Slot", "GovernedPipeline", "GovernedDecision", "CnsNotInstalled",
                         "cns_available"):
                assert hasattr(m, name), name
            assert issubclass(m.CnsNotInstalled, ImportError)
            print("ok")
            """
        )
    )


def test_using_the_pipeline_with_cns_blocked_says_what_is_missing():
    _ok(
        _run(
            """
            import cns_governed_decision as m
            seen = []

            class Gate:
                name = "g"
                position = None
                def check(self, candidate):
                    seen.append(candidate)

            slot = m.Slot(Gate(), lambda ctx: ctx)  # a Slot is plain data: no cns needed
            for build in (lambda: m.GovernedPipeline(),
                          lambda: m.GovernedPipeline(alpha=[slot], omega=[slot])):
                try:
                    build()
                except m.CnsNotInstalled as exc:
                    assert isinstance(exc, ImportError)
                    assert "pip install 'observe-perceive[cns]'" in str(exc), str(exc)
                    assert "cns.gate" in str(exc)
                    assert isinstance(exc.__cause__, ImportError)
                else:
                    raise SystemExit("no CnsNotInstalled")
            assert seen == []
            print("ok")
            """
        )
    )


def test_loading_the_module_never_imports_cns_even_when_it_is_installed():
    _ok(
        _run(
            """
            import sys
            import cns_governed_decision
            leaked = [name for name in sys.modules if name == "cns" or name.startswith("cns.")]
            assert leaked == [], leaked
            print("ok")
            """,
            block=False,
        )
    )


def _imports(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            found.append(node.module or "")
    return found


def test_the_module_imports_only_the_standard_library_and_reaches_cns_one_way():
    source = MODULE_PATH.read_text()
    imported = _imports(ast.parse(source))
    assert imported, "expected at least importlib"
    for name in imported:
        top = name.split(".")[0]
        assert top in sys.stdlib_module_names, f"{name} is not standard library"
    assert not [n for n in imported if n.split(".")[0] == "cns"]
    # The one helper that asks for CNS, by name, on demand.
    assert source.count('importlib.import_module("cns.gate")') == 1


def test_the_layer_does_not_import_the_orchestrator_or_any_sibling_module():
    imported = _imports(ast.parse(MODULE_PATH.read_text()))
    repo_modules = {p.stem for p in HERE.glob("*.py")} - {"cns_governed_decision"}
    assert not [n for n in imported if n.split(".")[0] in repo_modules]


def test_the_packaging_adds_no_runtime_dependency():
    data = tomllib.loads(PYPROJECT.read_text())
    project = data["project"]
    deps = project["dependencies"]
    assert len(deps) == 1 and deps[0].startswith("conservation-kernel @ git+"), deps
    assert not any("cns" in d.lower() for d in deps)
    extras = project["optional-dependencies"]
    assert set(extras) == {"chain", "fortress", "test", "cns"}
    assert extras["cns"] == [f"cns @ git+https://github.com/wking53214/cns.git@{CNS_PIN}"]
    for name in ("chain", "fortress", "test"):
        assert not any("cns" in d.lower().split("@")[0] for d in extras[name]), name
    assert "cns_governed_decision" in data["tool"]["setuptools"]["py-modules"]
    assert data["build-system"]["requires"] == ["setuptools>=68"]


def test_the_new_files_contain_no_em_dash():
    # Built from code points so this file never contains either character itself.
    dashes = (chr(0x2014), chr(0x2013))
    for path in (MODULE_PATH, Path(__file__), PYPROJECT):
        text = path.read_text()
        for dash in dashes:
            assert dash not in text, f"{path.name} contains U+{ord(dash):04X}"


# ---------------------------------------------------------------------------
# Connected half: needs cns.gate.
# ---------------------------------------------------------------------------

_UNSET = object()


@pytest.fixture(scope="module")
def cg():
    return pytest.importorskip(
        "cns.gate", reason="cns not installed; run in an environment with observe-perceive[cns]"
    )


class FakeGate:
    """A gate with no library behind it: declares a position, returns what it is told."""

    def __init__(self, cg, name, position, outcome, *, trace, bound=True, raises=None,
                 returns=_UNSET, stamp=None, reason=None):
        self._cg = cg
        self.name = name
        self.position = position
        self.outcome = outcome
        self.trace = trace
        self.bound = bound
        self.raises = raises
        self.returns = returns
        self.stamp = stamp
        self.reason = reason
        self.seen: list[object] = []

    def check(self, candidate):
        self.trace.append(("check", self.name))
        self.seen.append(candidate)
        if self.raises is not None:
            raise self.raises
        if self.returns is not _UNSET:
            return self.returns
        cg = self._cg
        return cg.GateResult(
            gate=self.name,
            position=self.stamp or self.position,
            outcome=self.outcome,
            reason=self.reason or f"{self.name} says {self.outcome.value}",
            subject="fake" if self.bound else "",
            subject_digest=cg.subject_digest({"candidate": repr(candidate)}) if self.bound else "",
        )


class Work:
    def __init__(self, trace, result="RESULT", raises=None):
        self.trace = trace
        self.result = result
        self.raises = raises
        self.calls = 0
        self.contexts: list[object] = []

    def __call__(self, ctx):
        self.trace.append("work")
        self.calls += 1
        self.contexts.append(ctx)
        if self.raises is not None:
            raise self.raises
        return self.result


class Rig:
    def __init__(self, cg):
        self.cg = cg
        self.trace: list[object] = []
        self.work = Work(self.trace)
        self._outcomes = {
            "pass": cg.GateOutcome.PASS,
            "retry": cg.GateOutcome.RETRY,
            "terminal": cg.GateOutcome.TERMINAL_BREACH,
        }

    def gate(self, end, name, outcome="pass", **kw):
        position = self.cg.GatePosition.ALPHA if end == "alpha" else self.cg.GatePosition.OMEGA
        return FakeGate(self.cg, name, position, self._outcomes[outcome], trace=self.trace, **kw)

    def alpha(self, name, outcome="pass", **kw):
        return self.gate("alpha", name, outcome, **kw)

    def omega(self, name, outcome="pass", **kw):
        return self.gate("omega", name, outcome, **kw)

    def slot(self, end, gate, **kw):
        if end == "alpha":
            return Slot(gate, lambda ctx: ("alpha-candidate", ctx), **kw)
        return Slot(gate, lambda ctx, result: ("omega-candidate", result), **kw)

    def a(self, name, outcome="pass", **kw):
        return self.slot("alpha", self.alpha(name, outcome))

    def o(self, name, outcome="pass", **kw):
        return self.slot("omega", self.omega(name, outcome))

    def run(self, alpha=(), omega=(), *, work=None, ctx=None, **kw):
        pipeline = GovernedPipeline(alpha=alpha, omega=omega, **kw)
        return pipeline.run({"request": 7} if ctx is None else ctx, work or self.work)

    def checks(self):
        return [t[1] for t in self.trace if isinstance(t, tuple)]


@pytest.fixture
def rig(cg):
    return Rig(cg)


def verdicts_of(decision, name):
    return [v for v in decision.verdicts if v.gate == name]


def gates_of(decision):
    return [v.gate for v in decision.verdicts]


# R1 ORDER -------------------------------------------------------------------


def test_r1_alpha_gates_are_judged_in_order_before_the_work_then_omega(rig):
    a1, a2, o1 = rig.alpha("a1"), rig.alpha("a2"), rig.omega("o1")
    d = rig.run([rig.slot("alpha", a1), rig.slot("alpha", a2)], [rig.slot("omega", o1)])
    assert rig.trace == [("check", "a1"), ("check", "a2"), "work", ("check", "o1")]
    assert (d.outcome, d.executed, d.result) == ("pass", True, "RESULT")
    assert gates_of(d) == ["a1", "a2", "o1"]


def test_r1_each_gate_judges_what_its_candidate_builder_made(rig):
    a1, o1 = rig.alpha("a1"), rig.omega("o1")
    rig.run([rig.slot("alpha", a1)], [rig.slot("omega", o1)], ctx="CTX")
    assert a1.seen == [("alpha-candidate", "CTX")]
    assert o1.seen == [("omega-candidate", "RESULT")]
    assert rig.work.contexts == ["CTX"]


@pytest.mark.parametrize("outcome", ["terminal", "retry"])
def test_r1_work_never_runs_when_an_alpha_verdict_is_not_pass(rig, outcome):
    d = rig.run([rig.a("a1"), rig.a("a2", outcome)], [rig.o("o1")])
    assert rig.work.calls == 0
    assert d.executed is False and d.result is None
    assert d.outcome == ("terminal_breach" if outcome == "terminal" else "retry")
    assert "o1" not in rig.checks()


def test_r1_work_never_runs_when_an_alpha_gate_raised(rig):
    boom = rig.alpha("boom", raises=RuntimeError("down"))
    d = rig.run([rig.slot("alpha", boom)], [rig.o("o1")])
    assert rig.work.calls == 0 and d.executed is False
    assert d.outcome == "terminal_breach"


def test_r1_work_never_runs_when_a_required_alpha_gate_is_missing(rig):
    d = rig.run([rig.a("a1"), Slot(None, lambda ctx: ctx, name="lib_gate")], [rig.o("o1")])
    assert rig.work.calls == 0 and d.executed is False
    assert d.outcome == "terminal_breach"


def test_r1_an_alpha_end_with_no_gates_does_not_stop_the_work(rig):
    d = rig.run([], [rig.o("o1")])
    assert rig.work.calls == 1 and d.outcome == "pass"
    # Allowed, but the record says there was no precondition end.
    assert d.chain_complete is False


# R2 FAIL CLOSED --------------------------------------------------------------


@pytest.mark.parametrize("end", ["alpha", "omega"])
def test_r2_a_gate_that_raises_is_a_synthetic_terminal_breach(rig, cg, end):
    boom = rig.gate(end, "boom", raises=RuntimeError("kaput 42"))
    slots = {"alpha": [rig.slot(end, boom)] if end == "alpha" else [],
             "omega": [rig.slot(end, boom)] if end == "omega" else []}
    d = rig.run(**slots)  # must not raise
    (v,) = verdicts_of(d, "boom")
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH
    assert v.position is (cg.GatePosition.ALPHA if end == "alpha" else cg.GatePosition.OMEGA)
    assert "RuntimeError" in v.reason and "kaput 42" in v.reason
    assert v.subject.startswith(SYNTHETIC_SUBJECT_PREFIX)
    assert d.outcome == "terminal_breach"
    assert d.executed is (end == "omega")


def test_r2_a_required_slot_with_no_gate_is_gate_unavailable(rig, cg):
    d = rig.run([Slot(None, lambda ctx: ctx, name="lib_gate")])
    (v,) = d.verdicts
    assert (v.gate, v.position, v.outcome) == (
        "lib_gate", cg.GatePosition.ALPHA, cg.GateOutcome.TERMINAL_BREACH)
    assert "gate unavailable" in v.reason
    assert d.outcome == "terminal_breach" and rig.work.calls == 0


def test_r2_a_non_required_slot_with_no_gate_is_recorded_and_does_not_block(rig):
    d = rig.run(
        [Slot(None, lambda ctx: ctx, required=False, name="maybe_a"), rig.a("a1")],
        [Slot(None, lambda ctx, r: r, required=False, name="maybe_o"), rig.o("o1")],
    )
    assert d.outcome == "pass" and rig.work.calls == 1
    skipped = dict(d.not_evaluated)
    assert "gate unavailable" in skipped["maybe_a"] and "gate unavailable" in skipped["maybe_o"]
    assert gates_of(d) == ["a1", "o1"]


def test_r2_a_required_omega_gate_that_is_missing_is_refused_before_the_work_runs(rig, cg):
    d = rig.run([rig.a("a1")], [rig.o("o1"), Slot(None, lambda c, r: r, name="lib_out")])
    (v,) = verdicts_of(d, "lib_out")
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH and v.position is cg.GatePosition.OMEGA
    assert "gate unavailable" in v.reason
    assert d.outcome == "terminal_breach"
    assert rig.work.calls == 0 and d.executed is False
    assert "o1" in dict(d.not_evaluated)


def test_r2_a_candidate_builder_that_raises_fails_closed(rig, cg):
    gate = rig.alpha("g1")

    def broken(ctx):
        raise KeyError("missing field")

    d = rig.run([Slot(gate, broken)])
    (v,) = d.verdicts
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH
    assert "KeyError" in v.reason and "missing field" in v.reason
    assert gate.seen == [] and rig.work.calls == 0


@pytest.mark.parametrize(
    "bad",
    [None, "pass", True, {"outcome": "pass"}],
    ids=["none", "string", "bool", "mapping"],
)
def test_r2_a_gate_that_returns_something_else_fails_closed(rig, cg, bad):
    d = rig.run([rig.slot("alpha", rig.alpha("g1", returns=bad))])
    (v,) = d.verdicts
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH
    assert "not a well-formed" in v.reason and rig.work.calls == 0


def test_r2_a_verdict_whose_outcome_is_a_bare_string_is_not_read_as_pass(rig, cg):
    # resolve() compares by identity, so this would otherwise add up to PASS.
    forged = cg.GateResult("g1", cg.GatePosition.ALPHA, "terminal_breach", "x", "s", "d")
    assert cg.resolve([forged]) is cg.GateOutcome.PASS  # the hole this closes
    d = rig.run([rig.slot("alpha", rig.alpha("g1", returns=forged))])
    assert d.outcome == "terminal_breach" and rig.work.calls == 0


def test_r2_a_gate_without_a_check_method_fails_closed(rig, cg):
    class NoCheck:
        name = "nocheck"
        position = cg.GatePosition.ALPHA

    d = rig.run([Slot(NoCheck(), lambda ctx: ctx)])
    assert d.outcome == "terminal_breach" and "AttributeError" in d.verdicts[0].reason


def test_r2_an_exception_with_a_broken_str_still_fails_closed(rig):
    class Evil(Exception):
        def __str__(self):
            raise RuntimeError("no str for you")

    d = rig.run([rig.slot("alpha", rig.alpha("g1", raises=Evil()))])
    assert d.outcome == "terminal_breach" and "Evil" in d.verdicts[0].reason


def test_r2_an_exception_message_with_a_lone_surrogate_still_fails_closed(rig):
    # CNS digests are over UTF-8: a stray surrogate must not turn a refusal into a crash.
    stray = chr(0xDCFF)
    d = rig.run([rig.slot("alpha", rig.alpha("g1", raises=ValueError(f"bad {stray} name")))])
    assert d.outcome == "terminal_breach" and rig.work.calls == 0
    assert "\\udcff" in d.verdicts[0].reason
    data = d.as_dict()
    assert json.loads(json.dumps(data)) == data
    assert data["digest"] == d.digest


def test_r2_a_work_error_and_a_gate_reason_with_a_lone_surrogate_still_record(rig, cg):
    stray = chr(0xDCFF)
    loose = rig.alpha("g1", reason=f"reason {stray}")
    d = rig.run([rig.slot("alpha", loose)], [rig.o("o1")],
                work=Work(rig.trace, raises=OSError(f"disk {stray}")))
    assert d.outcome == "terminal_breach" and "\\udcff" in d.work_error
    data = d.as_dict()
    assert "\\udcff" in data["verdicts"][0]["reason"]
    assert json.loads(json.dumps(data)) == data
    body = {k: v for k, v in data.items() if k != "digest"}
    assert cg.subject_digest(body) == data["digest"] == d.digest


def test_r2_a_base_exception_is_not_swallowed(rig):
    with pytest.raises(KeyboardInterrupt):
        rig.run([rig.slot("alpha", rig.alpha("g1", raises=KeyboardInterrupt()))])


# R3 PLACEMENT ---------------------------------------------------------------


def test_r3_an_omega_gate_in_the_alpha_slot_refuses_the_whole_run(rig, cg):
    good, late = rig.alpha("good"), rig.omega("late")
    d = rig.run([rig.slot("alpha", good), rig.slot("alpha", late)], [rig.o("o1")])
    assert rig.trace == []  # not even the well-placed gate ran, and not the work
    (v,) = d.verdicts
    assert v.gate == PLACEMENT_GATE and v.outcome is cg.GateOutcome.TERMINAL_BREACH
    assert "late (placed in the alpha slot)" in v.reason and "good" not in v.reason
    assert d.outcome == "terminal_breach" and d.executed is False
    assert {n for n, _ in d.not_evaluated} == {"good", "late", "o1"}


def test_r3_an_alpha_gate_in_the_omega_slot_refuses_the_whole_run(rig, cg):
    early = rig.alpha("early")
    d = rig.run([rig.a("a1")], [rig.slot("omega", early)])
    assert rig.trace == []
    assert d.verdicts[0].gate == PLACEMENT_GATE and "early (placed in the omega slot)" in d.verdicts[0].reason
    assert d.outcome == "terminal_breach"


def test_r3_the_declared_position_is_never_relabelled(rig, cg):
    late = rig.omega("late")
    rig.run([rig.slot("alpha", late)])
    assert late.position is cg.GatePosition.OMEGA
    assert cg.GateChain(alpha=(late,)).misplaced() == ("late",)


def test_r3_a_gate_with_no_readable_position_is_refused_not_guessed(rig, cg):
    class NoPosition:
        name = "nopos"

        def check(self, candidate):  # pragma: no cover - must never be reached
            raise AssertionError("ran a gate of unknown position")

    d = rig.run([Slot(NoPosition(), lambda ctx: ctx)])
    assert d.verdicts[0].gate == PLACEMENT_GATE
    assert "nopos (position unreadable" in d.verdicts[0].reason and rig.work.calls == 0


def test_r3_a_position_that_is_not_the_enum_is_misplaced(rig, cg):
    stringly = rig.alpha("stringly")
    stringly.position = "alpha"
    d = rig.run([rig.slot("alpha", stringly)])
    assert d.verdicts[0].gate == PLACEMENT_GATE and rig.trace == []


def test_r3_a_verdict_stamped_at_the_other_end_is_refused(rig, cg):
    liar = rig.alpha("liar", stamp=cg.GatePosition.OMEGA)
    d = rig.run([rig.slot("alpha", liar)])
    assert gates_of(d) == ["liar", "liar"]
    assert d.verdicts[0].position is cg.GatePosition.OMEGA  # kept as issued, not relabelled
    assert d.verdicts[1].subject == SYNTHETIC_SUBJECT_PREFIX + "misplaced_verdict"
    assert d.outcome == "terminal_breach" and rig.work.calls == 0


def test_r3_correct_placement_runs_normally(rig, cg):
    a1, o1 = rig.alpha("a1"), rig.omega("o1")
    assert cg.GateChain(alpha=(a1,), omega=(o1,)).misplaced() == ()
    assert rig.run([rig.slot("alpha", a1)], [rig.slot("omega", o1)]).outcome == "pass"


# R4 PRECEDENCE --------------------------------------------------------------

PRECEDENCE = [
    (["pass", "pass"], "pass"),
    (["pass", "retry"], "retry"),
    (["retry", "pass"], "retry"),
    (["retry", "terminal"], "terminal_breach"),
    (["terminal", "retry"], "terminal_breach"),
    (["pass", "pass", "terminal", "pass"], "terminal_breach"),
    (["pass", "retry", "pass", "terminal", "retry"], "terminal_breach"),
]


@pytest.mark.parametrize("outcomes,expected", PRECEDENCE)
def test_r4_alpha_verdicts_resolve_terminal_over_retry_over_pass(rig, cg, outcomes, expected):
    d = rig.run([rig.a(f"a{i}", o) for i, o in enumerate(outcomes)])
    assert d.outcome == expected
    assert d.outcome == cg.resolve(d.verdicts).value


@pytest.mark.parametrize("outcomes,expected", PRECEDENCE)
def test_r4_omega_verdicts_resolve_terminal_over_retry_over_pass(rig, cg, outcomes, expected):
    d = rig.run([rig.a("a0")], [rig.o(f"o{i}", o) for i, o in enumerate(outcomes)])
    assert d.outcome == expected
    assert d.outcome == cg.resolve(d.verdicts).value
    assert d.executed is True


def test_r4_a_retry_before_work_decides_even_if_omega_would_have_been_terminal(rig):
    d = rig.run([rig.a("a1", "retry")], [rig.o("o1", "terminal")])
    assert d.outcome == "retry" and rig.work.calls == 0


def test_r4_the_outcome_is_the_value_string(rig, cg):
    d = rig.run([rig.a("a1")])
    assert type(d.outcome) is str and d.outcome == cg.GateOutcome.PASS.value == "pass"


# R5 SHORT-CIRCUIT -----------------------------------------------------------


class Ledger:
    """A gate with a side effect: it promotes what it accepts when it is checked."""

    def __init__(self, cg, name, position, trace):
        self.name, self.position, self.trace, self._cg = name, position, trace, cg
        self.promoted: list[object] = []

    def check(self, candidate):
        self.trace.append(("check", self.name))
        self.promoted.append(candidate)
        return self._cg.GateResult(
            self.name, self.position, self._cg.GateOutcome.PASS, "promoted", "ledger",
            self._cg.subject_digest({"c": repr(candidate)}))


@pytest.mark.parametrize("end", ["alpha", "omega"])
def test_r5_no_gate_is_evaluated_after_a_terminal_breach(rig, cg, end):
    position = cg.GatePosition.ALPHA if end == "alpha" else cg.GatePosition.OMEGA
    ledger = Ledger(cg, "ledger", position, rig.trace)
    third = rig.gate(end, "third")
    slots = [rig.slot(end, rig.gate(end, "first")), rig.slot(end, rig.gate(end, "stop", "terminal")),
             rig.slot(end, ledger), rig.slot(end, third)]
    d = rig.run(**{end: slots})
    assert ledger.promoted == [] and third.seen == []
    assert rig.checks() == ["first", "stop"]
    skipped = dict(d.not_evaluated)
    assert set(skipped) == {"ledger", "third"}
    assert all("stop" in reason and "TERMINAL_BREACH" in reason for reason in skipped.values())
    assert d.outcome == "terminal_breach"


def test_r5_a_retry_does_not_short_circuit(rig):
    d = rig.run([rig.a("a1", "retry"), rig.a("a2"), rig.a("a3", "retry")])
    assert rig.checks() == ["a1", "a2", "a3"] and d.not_evaluated == ()
    assert d.outcome == "retry"


def test_r5_a_synthetic_terminal_short_circuits_too(rig):
    later = rig.alpha("later")
    d = rig.run([rig.slot("alpha", rig.alpha("boom", raises=ValueError("x"))), rig.slot("alpha", later)])
    assert later.seen == [] and "later" in dict(d.not_evaluated)


def test_r5_a_missing_required_gate_short_circuits_the_rest(rig):
    later = rig.alpha("later")
    d = rig.run([Slot(None, lambda ctx: ctx, name="gone"), rig.slot("alpha", later)])
    assert later.seen == [] and "gone" in dict(d.not_evaluated)["later"]


# R6 OMEGA -------------------------------------------------------------------


def test_r6_omega_does_not_run_when_alpha_did_not_pass(rig):
    d = rig.run([rig.a("a1", "retry")], [rig.o("o1")])
    assert rig.checks() == ["a1"]
    assert "alpha gate did not pass" in dict(d.not_evaluated)["o1"]


def test_r6_a_work_that_raises_is_a_terminal_breach_and_omega_is_not_run(rig, cg):
    work = Work(rig.trace, raises=ValueError("bad input"))
    d = rig.run([rig.a("a1")], [rig.o("o1")], work=work)
    assert d.outcome == "terminal_breach"
    assert d.work_error == "ValueError: bad input"
    assert d.executed is True and d.result is None
    assert rig.checks() == ["a1"]
    v = d.verdicts[-1]
    assert v.gate == WORK_GATE and v.position is cg.GatePosition.OMEGA
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH and "ValueError: bad input" in v.reason
    assert "work raised" in dict(d.not_evaluated)["o1"]


def test_r6_work_error_is_none_when_the_work_returns(rig):
    assert rig.run([rig.a("a1")], [rig.o("o1")]).work_error is None


def test_r6_an_omega_failure_withholds_the_result(rig, cg):
    for outcome, expected in (("terminal", "terminal_breach"), ("retry", "retry")):
        rig = Rig(cg)
        d = rig.run([rig.a("a1")], [rig.o("o1", outcome)])
        assert d.outcome == expected and d.executed is True
        assert d.result is None and d.result_withheld is True
        assert d.result_digest == cg.subject_digest({"result": "RESULT"})


def test_r6_a_passing_decision_hands_the_result_back(rig, cg):
    d = rig.run([rig.a("a1")], [rig.o("o1")])
    assert d.result == "RESULT" and d.result_withheld is False
    assert d.result_digest == cg.subject_digest({"result": "RESULT"})


def test_r6_a_result_cns_cannot_describe_has_no_digest_but_still_passes(rig):
    d = rig.run([rig.a("a1")], [rig.o("o1")], work=Work(rig.trace, result=object()))
    assert d.outcome == "pass" and d.result_digest is None


# R7 BINDING -----------------------------------------------------------------


@pytest.mark.parametrize("end", ["alpha", "omega"])
def test_r7_an_unbound_verdict_is_a_terminal_breach_even_when_it_passes(rig, cg, end):
    slots = {"alpha": [], "omega": []}
    slots[end] = [rig.slot(end, rig.gate(end, "loose", bound=False))]
    d = rig.run(**slots)
    assert d.outcome == "terminal_breach"
    assert gates_of(d) == ["loose", "loose"]
    original, synthetic = d.verdicts
    assert original.outcome is cg.GateOutcome.PASS and not original.bound()
    assert synthetic.outcome is cg.GateOutcome.TERMINAL_BREACH
    assert "unbound" in synthetic.reason and synthetic.subject.endswith("unbound")


def test_r7_an_unbound_alpha_verdict_keeps_the_work_from_starting(rig):
    rig.run([rig.slot("alpha", rig.alpha("loose", bound=False))], [rig.o("o1")])
    assert rig.work.calls == 0 and "o1" not in rig.checks()


def test_r7_binding_is_required_by_default(rig):
    assert GovernedPipeline().require_bound is True


def test_r7_without_require_bound_an_unbound_verdict_is_accepted(rig, cg):
    d = rig.run([rig.slot("alpha", rig.alpha("loose", bound=False))], require_bound=False)
    assert d.outcome == "pass" and rig.work.calls == 1
    assert cg.unbound(d.verdicts) == ("loose",)


def test_r7_a_passing_run_holds_only_bound_verdicts(rig, cg):
    d = rig.run([rig.a("a1"), rig.a("a2")], [rig.o("o1")])
    assert cg.unbound(d.verdicts) == ()


def test_r7_synthetic_verdicts_are_bound_to_their_gate_and_reason(rig, cg):
    runs = [
        rig.run([rig.slot("alpha", rig.alpha("boom", raises=RuntimeError("x")))]),
        rig.run([Slot(None, lambda ctx: ctx, name="gone")]),
        rig.run([rig.slot("alpha", rig.omega("late"))]),
        rig.run([rig.a("a1")], [rig.o("o1")], work=Work(rig.trace, raises=OSError("disk"))),
        rig.run([rig.slot("alpha", rig.alpha("loose", bound=False))]),
    ]
    seen = 0
    for d in runs:
        for v in d.verdicts:
            if not v.subject.startswith(SYNTHETIC_SUBJECT_PREFIX):
                continue
            seen += 1
            assert v.bound()
            assert v.binds(v.subject, cg.subject_digest({"gate": v.gate, "reason": v.reason}))
            assert not v.binds(v.subject, cg.subject_digest({"gate": v.gate, "reason": "other"}))
    assert seen >= 5


# R8 AUDIT -------------------------------------------------------------------


def test_r8_duplicate_gate_names_raise_at_construction(rig):
    with pytest.raises(ValueError, match="duplicate gate name 'a1'"):
        GovernedPipeline(alpha=[rig.a("a1"), rig.a("a1")])
    with pytest.raises(ValueError, match="duplicate gate name 'x'"):
        GovernedPipeline(alpha=[rig.a("x")], omega=[rig.o("x")])


def test_r8_a_slot_name_overrides_and_counts_for_duplicates(rig):
    GovernedPipeline(alpha=[Slot(rig.alpha("same"), lambda c: c, name="one")],
                     omega=[Slot(rig.omega("same"), lambda c, r: r, name="two")])
    with pytest.raises(ValueError, match="duplicate gate name 'one'"):
        GovernedPipeline(alpha=[Slot(rig.alpha("a"), lambda c: c, name="one"),
                                Slot(rig.alpha("one"), lambda c: c)])


def test_r8_two_unavailable_slots_need_distinct_names(cg):
    with pytest.raises(ValueError, match="needs a name"):
        Slot(None, lambda c: c).label()
    with pytest.raises(ValueError, match="duplicate"):
        GovernedPipeline(alpha=[Slot(None, lambda c: c, name="x"), Slot(None, lambda c: c, name="x")])


def test_r8_slots_and_names_are_validated(rig):
    with pytest.raises(TypeError):
        Slot(rig.alpha("a"), "not callable")
    with pytest.raises(ValueError):
        Slot(rig.alpha("a"), lambda c: c, name="")
    with pytest.raises(TypeError):
        GovernedPipeline(alpha=[rig.alpha("a")])
    with pytest.raises(ValueError, match="reserved"):
        GovernedPipeline(alpha=[Slot(rig.alpha("a"), lambda c: c, name=PLACEMENT_GATE)])
    with pytest.raises(TypeError):
        GovernedPipeline().run({}, "not callable")


def test_r8_names_lists_every_slot_alpha_end_first(rig):
    pipeline = GovernedPipeline(alpha=[rig.a("a1"), Slot(None, lambda c: c, name="gone")],
                                omega=[rig.o("o1")])
    assert pipeline.names == ("a1", "gone", "o1")


def test_r8_chain_complete_reports_whether_both_ends_are_populated(rig):
    assert rig.run([rig.a("a1")], [rig.o("o1")]).chain_complete is True
    assert Rig(rig.cg).run([rig.a("a1")]).chain_complete is False
    assert Rig(rig.cg).run(omega=[rig.o("o1")]).chain_complete is False
    assert Rig(rig.cg).run().chain_complete is False
    # A slot with no gate does not populate its end.
    missing = Slot(None, lambda c: c, required=False, name="gone")
    assert Rig(rig.cg).run([missing], [rig.o("o1")]).chain_complete is False


def _scenarios(rig, cg):
    """Name -> (decision, every configured slot name)."""
    def build(alpha, omega, **kw):
        return rig.run(alpha, omega, **kw), GovernedPipeline(alpha=alpha, omega=omega).names

    gone = lambda n, req=True: Slot(None, lambda *a: None, required=req, name=n)  # noqa: E731
    return {
        "all pass": build([rig.a("a1")], [rig.o("o1")]),
        "alpha terminal mid": build([rig.a("a1"), rig.a("a2", "terminal"), rig.a("a3")], [rig.o("o1")]),
        "alpha retry": build([rig.a("a4", "retry")], [rig.o("o2")]),
        "missing required alpha": build([gone("g1"), rig.a("a5")], [rig.o("o3")]),
        "missing optional alpha": build([gone("g2", False), rig.a("a6")], [rig.o("o4")]),
        "missing required omega": build([rig.a("a7")], [rig.o("o5"), gone("g3")]),
        "work raises": build([rig.a("a8")], [rig.o("o6")], work=Work(rig.trace, raises=OSError("x"))),
        "omega terminal first": build([rig.a("a9")], [rig.o("o7", "terminal"), rig.o("o8")]),
        "misplaced": build([rig.slot("alpha", rig.omega("w1"))], [rig.o("o9")]),
    }


def test_r8_every_configured_slot_is_accounted_for_in_every_outcome(rig, cg):
    for label, (d, names) in _scenarios(rig, cg).items():
        judged = {v.gate for v in d.verdicts} - {PLACEMENT_GATE, WORK_GATE}
        skipped = {n for n, _ in d.not_evaluated}
        assert judged | skipped == set(names), label
        assert not judged & skipped, label


def test_r8_as_dict_is_json_safe_and_complete(rig, cg):
    d = rig.run([rig.a("a1"), rig.a("a2", "retry")], [rig.o("o1")])
    data = d.as_dict()
    assert json.loads(json.dumps(data)) == data
    assert set(data) == {"outcome", "executed", "work_error", "chain_complete", "result_digest",
                         "result_withheld", "verdicts", "not_evaluated", "digest"}
    assert [v["gate"] for v in data["verdicts"]] == ["a1", "a2"]
    assert set(data["verdicts"][0]) == {"gate", "position", "outcome", "reason", "subject",
                                        "subject_digest"}
    assert data["verdicts"][1]["outcome"] == "retry" and data["verdicts"][0]["position"] == "alpha"
    assert data["not_evaluated"] == [["o1", dict(d.not_evaluated)["o1"]]]
    assert data["outcome"] == "retry" and data["executed"] is False


def test_r8_the_digest_is_the_cns_digest_of_the_content_and_is_stable(rig, cg):
    d1 = rig.run([rig.a("a1")], [rig.o("o1")])
    d2 = Rig(cg).run([rig.a("a1")], [rig.o("o1")])
    data = d1.as_dict()
    body = {k: v for k, v in data.items() if k != "digest"}
    assert d1.digest == data["digest"] == cg.subject_digest(body)
    assert d1.digest == d2.digest and len(d1.digest) == 64


def test_r8_the_digest_changes_when_any_part_of_the_record_changes(rig, cg):
    d = rig.run([rig.a("a1")], [rig.o("o1")])
    first = dataclasses.replace(d.verdicts[0], reason="edited")
    edits = [
        dataclasses.replace(d, verdicts=(first,) + d.verdicts[1:]),
        dataclasses.replace(d, verdicts=d.verdicts[:1]),
        dataclasses.replace(d, outcome="retry"),
        dataclasses.replace(d, executed=False),
        dataclasses.replace(d, not_evaluated=(("o1", "because"),)),
        dataclasses.replace(d, work_error="boom"),
        dataclasses.replace(d, chain_complete=False),
        dataclasses.replace(d, result_digest="0" * 64),
    ]
    digests = {e.digest for e in edits}
    assert d.digest not in digests and len(digests) == len(edits)


def test_r8_the_decision_is_frozen(rig):
    d = rig.run([rig.a("a1")])
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.outcome = "pass"
    assert isinstance(d.verdicts, tuple) and isinstance(d.not_evaluated, tuple)


# Environment ----------------------------------------------------------------


def test_cns_is_seen_as_available_here(cg):
    assert cgd.cns_available() is True


def test_a_cns_too_old_to_bind_or_unbind_is_reported_as_not_installed(cg, monkeypatch):
    old = types.SimpleNamespace(GateChain=object, GateOutcome=object, GatePosition=object,
                                GateResult=object, resolve=lambda r: None)
    monkeypatch.setitem(sys.modules, "cns.gate", old)
    with pytest.raises(CnsNotInstalled, match="subject_digest, unbound"):
        GovernedPipeline()
    assert cgd.cns_available() is False


def test_the_install_hint_is_the_one_the_independence_half_checks(cg, monkeypatch):
    monkeypatch.setitem(sys.modules, "cns.gate", None)
    with pytest.raises(CnsNotInstalled) as caught:
        GovernedPipeline()
    assert HINT in str(caught.value) and cgd.INSTALL_HINT == HINT


# The real DIT connector -------------------------------------------------------

DIT_CLEAN = "Queue depth fell 22% because the batch window was widened."
DIT_HEDGED = "Throughput may have improved."
DIT_AFFECTIVE = "Queue depth fell 22% because the window widened, and we hope it holds."


@pytest.fixture
def dit(cg):
    connector = pytest.importorskip(
        "dit.cns_connector", reason="dit[cns] not installed; the real-connector tests need it")
    import dit as dit_pkg

    class Kit:
        pass

    kit = Kit()
    kit.connector = connector
    kit.dit = dit_pkg
    kit.gates = lambda stack=None: [
        connector.CnsGate(g)
        for g in dit_pkg.build_stack(dit_pkg.default_rules(), stack or dit_pkg.FULL_STACK)
    ]
    kit.slots = lambda stack=None: [
        Slot(g, lambda ctx, result: result) for g in kit.gates(stack)
    ]
    return kit


def test_dit_a_clean_text_passes_every_real_gate(rig, cg, dit):
    d = rig.run([rig.a("authority")], dit.slots(), work=Work(rig.trace, result=DIT_CLEAN))
    assert d.outcome == "pass" and d.executed and d.result == DIT_CLEAN
    assert gates_of(d) == ["authority", "identity", "hedging", "causality", "semantic_contamination"]
    assert all(v.position is cg.GatePosition.OMEGA for v in d.verdicts[1:])
    assert cg.unbound(d.verdicts) == () and d.chain_complete is True
    assert d.as_dict()["digest"] == d.digest


def test_dit_a_hedged_text_is_a_retry_and_the_result_does_not_leave(rig, cg, dit):
    d = rig.run([rig.a("authority")], dit.slots(), work=Work(rig.trace, result=DIT_HEDGED))
    assert d.outcome == "retry" and d.result is None and d.result_withheld
    assert d.result_digest == cg.subject_digest({"result": DIT_HEDGED})
    hedging = verdicts_of(d, "hedging")[0]
    assert hedging.outcome is cg.GateOutcome.RETRY and "hedging" in hedging.reason.lower()


def test_dit_an_affective_text_is_a_terminal_breach_over_earlier_retries(rig, cg, dit):
    d = rig.run([rig.a("authority")], dit.slots(), work=Work(rig.trace, result=DIT_AFFECTIVE))
    assert d.outcome == "terminal_breach" and d.result is None
    assert d.verdicts[-1].gate == "semantic_contamination"
    assert d.verdicts[-1].outcome is cg.GateOutcome.TERMINAL_BREACH
    assert any(v.outcome is cg.GateOutcome.RETRY for v in d.verdicts)  # a retry did not decide it
    assert d.outcome == cg.resolve(d.verdicts).value


def test_dit_a_real_omega_gate_in_the_alpha_slot_is_refused_not_relabelled(rig, cg, dit):
    gate = dit.gates()[0]
    d = rig.run([Slot(gate, lambda ctx: DIT_CLEAN)])
    assert gate.position is cg.GatePosition.OMEGA
    assert d.verdicts[0].gate == PLACEMENT_GATE and "identity (placed in the alpha slot)" in d.verdicts[0].reason
    assert rig.work.calls == 0 and d.outcome == "terminal_breach"


def test_dit_a_real_gate_that_raises_on_a_bad_candidate_fails_closed(rig, cg, dit):
    gate = dit.gates()[0]
    d = rig.run([], [Slot(gate, lambda ctx, result: b"bytes, not text")])
    (v,) = d.verdicts
    assert v.outcome is cg.GateOutcome.TERMINAL_BREACH and v.position is cg.GatePosition.OMEGA
    assert "TypeError" in v.reason and "DIT gates judge text" in v.reason
    assert d.outcome == "terminal_breach" and d.result is None


def test_dit_the_layer_agrees_with_dits_own_evaluation(dit, cg):
    texts = (DIT_CLEAN, DIT_HEDGED, DIT_AFFECTIVE, "", "Nothing to report.")
    for text in texts:
        rig = Rig(cg)
        native = dit.dit.evaluate(text)
        d = rig.run([], dit.slots(dit.dit.GSA_V13_STACK), work=Work(rig.trace, result=text))
        assert (d.outcome == "pass") is native.passed, text
        assert (d.outcome == "terminal_breach") is native.terminal, text
