"""End-to-end demo: one GovernedPipeline over five libraries' real CNS gates.

What this runs. ``cns_governed_decision.GovernedPipeline`` combines gates from
separate libraries into one fail-closed decision. Here it holds the real CNS
connector gate of each of five libraries, placed by the end each one declares:

    ALPHA, judged before the work    governance_gateway, ccc, augur
    the work                         a stub that writes a release summary
    OMEGA, judged on the result      dit, conservation_kernel

The work is a stub. It is not a model: it builds a summary and its typed
transformation record from a ``Plan`` and counts how many times it ran. Every
verdict in the output comes from a library's own gate, or, where a gate raised
or was missing, from the combining layer's own fail-closed refusal, which is
marked as such. Nothing here fakes a library's refusal: a refusal appears only
when the real gate was handed a candidate it genuinely refuses.

Scenarios. S1 to S8 plus three variants (S3b, S6b, S7b) the fixtures support:

    S1   everything passes: APPROVED, the work ran once
    S2   an OMEGA text gate (dit) refuses retryably: RETRY, the work ran
    S3   an OMEGA gate (conservation_kernel) refuses terminally while every
         other gate passed: TERMINAL_BREACH
    S3b  a RETRY and a TERMINAL_BREACH together: terminal wins
    S4   an ALPHA gate (augur) refuses: the work is never called and the OMEGA
         gates are not evaluated
    S5   two ALPHA gates would refuse; the first terminal one stops the rest
    S6   a required ALPHA library is missing (a Slot with gate None): fail closed
    S6b  a required OMEGA library is missing: the work is not started
    S7   an ALPHA gate raises: fail closed, no crash, the work is not called
    S7b  an OMEGA gate raises after the work: fail closed, the result is withheld
    S8   a verdict or a decision record is edited after the fact: it no longer binds

One pipeline object serves S1 to S5, S7, S7b and S3b. S6 and S6b each use a
second pipeline of the same shape with one slot left empty (gate None), because
the point of those two is a pipeline that has a required slot with no gate.

Not shown, because the fixtures cannot drive it: a gate that returns RETRY
from an ALPHA library (ccc's RETRY path needs an artifact id that a
zero-argument candidate cannot name; augur reserves RETRY for scenarios it
cannot screen).

Limits, stated plainly. This is a verdict-combining layer. It does not replace
GovernanceOrchestrator, and it only works for libraries that ship a CNS
connector. APPROVED means every gate in this pipeline passed; a library's PASS
is "no objection", not proof that the summary is correct (the conservation
kernel checks the typed envelope, not the prose; AUGUR cannot approve). The
digests are tamper-evidence, not tamper-proofing: someone who rebuilds a
verdict with a recomputed digest is not caught by the digest alone (S8 shows
this).

Import safety. Importing this module imports no CNS and no library: the
fixtures and the combining layer load them only when a gate is built or run.
Without CNS installed, running the script prints what is missing and exits 2.
The script puts its own directory and the repository root at the END of
``sys.path`` so it runs from a checkout without installing anything.

Run:  python examples/cns_governed_decision_demo.py
Exit status: 0 if every scenario ended as expected, 1 if one did not, 2 if CNS
or a library (with its CNS connector) is not installed.
"""

from __future__ import annotations

import copy
import dataclasses
import importlib.metadata
import sys
import textwrap
from pathlib import Path
from typing import Any, Callable

_HERE = Path(__file__).resolve().parent
for _path in (_HERE, _HERE.parent):
    if str(_path) not in sys.path:
        sys.path.append(str(_path))

import cns_governed_decision as cgd  # noqa: E402
from cns_demo import (  # noqa: E402
    fixture_augur,
    fixture_ccc,
    fixture_conservation_kernel,
    fixture_dit,
    fixture_governance_gateway,
)

__all__ = [
    "CountingGate",
    "DemoRun",
    "LIBRARIES",
    "Plan",
    "Release",
    "Report",
    "SCENARIOS",
    "Scenario",
    "StubWork",
    "TamperReport",
    "build_pipeline",
    "load_gates",
    "main",
    "render",
    "run_demo",
    "run_scenario",
    "tamper_demo",
]

#: What a decision's ``outcome`` is called in the output.
FINAL_LABEL = {"pass": "APPROVED", "retry": "RETRY", "terminal_breach": "TERMINAL_BREACH"}

#: Handed to a real gate in place of its candidate, to make the gate raise.
_NOT_A_CANDIDATE = "this is text, not the object the gate judges"

_WIDTH = 100


@dataclasses.dataclass(frozen=True)
class Plan:
    """What one run is told to do. It is the pipeline's ``ctx``.

    Each field picks which of a fixture's candidates its gate is handed.
    """

    #: governance_gateway judges the source record: "ok" or "refused" (edited
    #: after it was sealed).
    source: str = "ok"
    #: ccc judges the status the assistant asks to file its summary under:
    #: "ok" (INFERENCE), "refused" (HISTORICAL_RECORD) or "malformed" (not an
    #: Attempt, so the real gate raises).
    filing: str = "ok"
    #: augur simulates the planned action's scenario: "ok" (calm) or "refused"
    #: (violent).
    scenario: str = "ok"
    #: What the stub work writes: "ok", or "hedged" (a hedge word for dit).
    wording: str = "ok"
    #: The typed record of source to summary that conservation_kernel judges:
    #: "ok", "refused" (the shelf-life estimate restated as fact) or
    #: "malformed" (not a TransformationCandidate, so the real gate raises).
    transformation: str = "ok"

    def line(self) -> str:
        return " ".join(f"{f.name}={getattr(self, f.name)}" for f in dataclasses.fields(self))


@dataclasses.dataclass(frozen=True)
class Release:
    """What the stub work returns: the summary text and its transformation record."""

    text: str
    transformation: Any


def _candidate(fixture: Any, choice: str) -> Any:
    """A fixture's candidate for ``choice``; a fresh object on every call."""
    if choice == "ok":
        return fixture.ok_candidate()
    if choice == "refused":
        return fixture.refused_candidate()
    if choice == "malformed":
        return _NOT_A_CANDIDATE
    raise ValueError(f"unknown choice {choice!r}")


class StubWork:
    """Stands in for the model or transformer that writes the summary.

    It is called with the plan, builds the summary and its transformation
    record from it, and counts how many times it ran. A pipeline calls it only
    after every ALPHA gate has passed.
    """

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, plan: Plan) -> Release:
        self.calls += 1
        if plan.transformation == "malformed":
            transformation: Any = _NOT_A_CANDIDATE
            text = fixture_conservation_kernel.ok_candidate().output.content
        else:
            transformation = _candidate(fixture_conservation_kernel, plan.transformation)
            text = transformation.output.content
        if plan.wording == "hedged":
            hedged = text.replace(" is 24 months.", " may be 24 months.")
            if hedged == text:
                raise RuntimeError("the stub work could not hedge the summary it wrote")
            text = hedged
        elif plan.wording != "ok":
            raise ValueError(f"unknown wording {plan.wording!r}")
        return Release(text=text, transformation=transformation)


class CountingGate:
    """A library's own gate, with a count of how often ``check`` was called.

    It delegates ``name``, ``position`` and ``check`` and changes no verdict.
    The count is how the demo shows that a gate was not asked.
    """

    def __init__(self, gate: Any) -> None:
        self._gate = gate
        self.checks = 0

    @property
    def name(self) -> str:
        return self._gate.name

    @property
    def position(self) -> Any:
        return self._gate.position

    def check(self, candidate: object) -> Any:
        self.checks += 1
        return self._gate.check(candidate)


@dataclasses.dataclass(frozen=True)
class Library:
    """One library's fixture and the callable that builds what its gate judges.

    ``candidate`` is ``plan -> object`` for an ALPHA fixture and
    ``(plan, release) -> object`` for an OMEGA one.
    """

    key: str
    distribution: str
    fixture: Any
    candidate: Callable[..., object]


LIBRARIES: tuple[Library, ...] = (
    Library(
        "governance_gateway",
        "governance-gateway",
        fixture_governance_gateway,
        lambda plan: _candidate(fixture_governance_gateway, plan.source),
    ),
    Library(
        "ccc",
        "cognitive-continuity-constitution",
        fixture_ccc,
        lambda plan: _candidate(fixture_ccc, plan.filing),
    ),
    Library(
        "augur",
        "augur",
        fixture_augur,
        lambda plan: _candidate(fixture_augur, plan.scenario),
    ),
    Library("dit", "dit", fixture_dit, lambda plan, release: release.text),
    Library(
        "conservation_kernel",
        "conservation-kernel",
        fixture_conservation_kernel,
        lambda plan, release: release.transformation,
    ),
)

_PLAN_FIELD = {"governance_gateway": "source", "ccc": "filing", "augur": "scenario"}


def load_gates() -> tuple[dict[str, CountingGate], dict[str, str]]:
    """Build each library's real gate. Returns (gates by key, why each missing one is)."""
    gates: dict[str, CountingGate] = {}
    missing: dict[str, str] = {}
    for lib in LIBRARIES:
        try:
            gates[lib.key] = CountingGate(lib.fixture.make_gate())
        except ImportError as exc:
            missing[lib.key] = f"{type(exc).__name__}: {exc}"
    return gates, missing


def build_pipeline(
    gates: dict[str, CountingGate], withhold: tuple[str, ...] = ()
) -> cgd.GovernedPipeline:
    """One GovernedPipeline: each fixture's gate in the slot for its declared end.

    ``withhold`` names libraries whose slot is given ``gate=None``, as if that
    library were not installed. The slot stays required, so the pipeline must
    fail closed. The gate is not replaced by anything.
    """
    alpha: list[cgd.Slot] = []
    omega: list[cgd.Slot] = []
    for lib in LIBRARIES:
        gate = None if lib.key in withhold else gates.get(lib.key)
        name = gates[lib.key].name if lib.key in gates else lib.key
        slot = cgd.Slot(gate=gate, candidate=lib.candidate, name=name)
        (alpha if lib.fixture.POSITION == "ALPHA" else omega).append(slot)
    return cgd.GovernedPipeline(alpha=alpha, omega=omega)


@dataclasses.dataclass(frozen=True)
class Scenario:
    key: str
    title: str
    note: str
    plan: Plan
    expect_outcome: str
    expect_work_calls: int
    withhold: tuple[str, ...] = ()
    #: Libraries whose gate, if it was not asked inside the pipeline, is asked
    #: once outside it on the same candidate, to show it would have refused.
    compare: tuple[str, ...] = ()


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        "S1",
        "Everything passes",
        "Every gate is handed its passing candidate.",
        Plan(),
        "pass",
        1,
    ),
    Scenario(
        "S2",
        "An OMEGA text gate refuses retryably",
        "The work writes a hedge ('may be'). dit answers RETRY: a reworded summary "
        "could pass. conservation_kernel passes.",
        Plan(wording="hedged"),
        "retry",
        1,
    ),
    Scenario(
        "S3",
        "An OMEGA gate refuses terminally; every other gate passed",
        "The work restates the shelf-life estimate as fact. conservation_kernel answers "
        "TERMINAL_BREACH. The three ALPHA gates and dit all passed, and the refusal still "
        "decides the whole run.",
        Plan(transformation="refused"),
        "terminal_breach",
        1,
    ),
    Scenario(
        "S3b",
        "A retry and a terminal breach together",
        "dit answers RETRY and conservation_kernel TERMINAL_BREACH. A RETRY does not stop "
        "evaluation, and the terminal verdict wins.",
        Plan(wording="hedged", transformation="refused"),
        "terminal_breach",
        1,
    ),
    Scenario(
        "S4",
        "An ALPHA gate refuses: the work is never called",
        "augur is handed a violent scenario and vetoes it. The two ALPHA gates before it "
        "passed. The work does not run and neither OMEGA gate is asked.",
        Plan(scenario="refused"),
        "terminal_breach",
        0,
    ),
    Scenario(
        "S5",
        "Two ALPHA gates would refuse; the first terminal one stops the rest",
        "The source record was edited after sealing (governance_gateway refuses) and the "
        "assistant asks to file its summary as human-established history (ccc would refuse). "
        "After the first TERMINAL_BREACH no later gate is asked, because a gate may have "
        "side effects.",
        Plan(source="refused", filing="refused"),
        "terminal_breach",
        0,
        compare=("ccc",),
    ),
    Scenario(
        "S6",
        "A required ALPHA library is missing",
        "The ccc slot is left empty, as if ccc were not installed. A required gate that "
        "is missing is a refusal, not a skipped check. (Simulated by this demo: the gate "
        "is withheld, nothing stands in for it.)",
        Plan(),
        "terminal_breach",
        0,
        withhold=("ccc",),
    ),
    Scenario(
        "S6b",
        "A required OMEGA library is missing",
        "The conservation_kernel slot is left empty. A result that cannot be judged is "
        "not produced, so the work is not started even though every ALPHA gate passed.",
        Plan(),
        "terminal_breach",
        0,
        withhold=("conservation_kernel",),
    ),
    Scenario(
        "S7",
        "An ALPHA gate raises",
        "ccc is handed text instead of an Attempt and its real gate raises TypeError. The "
        "pipeline does not crash: the exception becomes a TERMINAL_BREACH.",
        Plan(filing="malformed"),
        "terminal_breach",
        0,
    ),
    Scenario(
        "S7b",
        "An OMEGA gate raises after the work",
        "The work returns text instead of a TransformationCandidate, and the real "
        "conservation_kernel gate raises TypeError. The work did run, so the run is "
        "recorded as executed, and the result is withheld.",
        Plan(transformation="malformed"),
        "terminal_breach",
        1,
    ),
)


@dataclasses.dataclass(frozen=True)
class Report:
    scenario: Scenario
    decision: cgd.GovernedDecision
    work_calls: int
    checks: tuple[tuple[str, int], ...]
    comparisons: tuple[tuple[str, Any], ...] = ()

    @property
    def as_expected(self) -> bool:
        return (
            self.decision.outcome == self.scenario.expect_outcome
            and self.work_calls == self.scenario.expect_work_calls
        )


def run_scenario(
    pipelines: dict[tuple[str, ...], cgd.GovernedPipeline],
    gates: dict[str, CountingGate],
    scenario: Scenario,
) -> Report:
    """Run one scenario on the pipeline for its ``withhold`` set (built once, then reused)."""
    if scenario.withhold not in pipelines:
        pipelines[scenario.withhold] = build_pipeline(gates, scenario.withhold)
    for gate in gates.values():
        gate.checks = 0
    work = StubWork()
    decision = pipelines[scenario.withhold].run(scenario.plan, work)
    checks = tuple((gate.name, gate.checks) for gate in gates.values())
    comparisons = []
    for key in scenario.compare:
        lib = next(lib for lib in LIBRARIES if lib.key == key)
        candidate = _candidate(lib.fixture, getattr(scenario.plan, _PLAN_FIELD[key]))
        comparisons.append((lib.key, lib.fixture.make_gate().check(candidate)))
    return Report(scenario, decision, work.calls, checks, tuple(comparisons))


@dataclasses.dataclass(frozen=True)
class TamperReport:
    original_text: str
    edited_text: str
    binds_original: bool
    binds_edited: bool
    forged_binds: bool
    record_digest_ok: bool
    edited_record_digest_ok: bool

    @property
    def as_expected(self) -> bool:
        return (
            self.binds_original
            and not self.binds_edited
            and self.forged_binds
            and self.record_digest_ok
            and not self.edited_record_digest_ok
        )


def tamper_demo(approved: Report, refused: Report) -> TamperReport:
    """Edit things after the fact and ask the CNS contract whether they still bind.

    ``approved`` supplies a released text and the dit verdict issued on it;
    ``refused`` supplies a decision record that says TERMINAL_BREACH.
    """
    from cns.gate import subject_digest

    verdict = next(v for v in approved.decision.verdicts if v.gate == "dit")
    original = approved.decision.result.text
    edited = original.replace("dissolution 96.0%", "dissolution 99.9%")
    if edited == original:
        raise RuntimeError("the released text has no dissolution figure to edit")
    new_digest = subject_digest({"text": edited})
    forged = dataclasses.replace(verdict, subject_digest=new_digest)

    record = refused.decision.as_dict()

    def digest_of(rec: dict[str, Any]) -> str:
        return subject_digest({k: v for k, v in rec.items() if k != "digest"})

    edited_record = copy.deepcopy(record)
    edited_record["outcome"] = "pass"
    edited_record["result_withheld"] = False
    return TamperReport(
        original_text=original,
        edited_text=edited,
        binds_original=verdict.binds(verdict.subject, subject_digest({"text": original})),
        binds_edited=verdict.binds(verdict.subject, new_digest),
        forged_binds=forged.binds(verdict.subject, new_digest),
        record_digest_ok=digest_of(record) == record["digest"],
        edited_record_digest_ok=digest_of(edited_record) == record["digest"],
    )


@dataclasses.dataclass(frozen=True)
class DemoRun:
    versions: tuple[tuple[str, str], ...]
    reports: tuple[Report, ...]
    tamper: TamperReport

    @property
    def all_as_expected(self) -> bool:
        return all(r.as_expected for r in self.reports) and self.tamper.as_expected


def _version(distribution: str) -> str:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return "unknown"


def run_demo() -> DemoRun:
    """Run every scenario and the tamper check. Raises CnsNotInstalled or ImportError
    if CNS or a library's connector is missing; ``main`` turns that into exit status 2."""
    cgd.GovernedPipeline()  # raises CnsNotInstalled, naming what is missing, if CNS is absent
    gates, missing = load_gates()
    if missing:
        raise ImportError(
            "; ".join(f"{key}: {why}" for key, why in missing.items())
        )
    pipelines: dict[tuple[str, ...], cgd.GovernedPipeline] = {}
    reports = tuple(run_scenario(pipelines, gates, s) for s in SCENARIOS)
    by_key = {r.scenario.key: r for r in reports}
    versions = tuple(
        (name, _version(name))
        for name in ("cns", *(lib.distribution for lib in LIBRARIES))
    )
    return DemoRun(versions, reports, tamper_demo(by_key["S1"], by_key["S3"]))


def _wrap(prefix: str, text: str) -> str:
    return textwrap.fill(
        text,
        width=_WIDTH,
        initial_indent=prefix,
        subsequent_indent=" " * len(prefix),
        break_long_words=False,
        break_on_hyphens=False,
    )


def _render_header(run: DemoRun) -> list[str]:
    out = ["CNS governed decision demo", "=" * 26, ""]
    out.append("Installed: " + ", ".join(f"{name} {version}" for name, version in run.versions))
    out += ["", "The pipeline (one GovernedPipeline; each gate is the library's own CNS connector):"]
    for lib in LIBRARIES:
        end = lib.fixture.POSITION
        out.append(_wrap(f"  {end:<5} {lib.key}: ", lib.fixture.DESCRIPTION))
    out += [
        "  work  stub: writes a release summary and its transformation record from the plan "
        "and counts its calls. It is not a model.",
        "",
        "What the plan controls (ok / refused / malformed / hedged), one field per gate:",
        "  source          the batch record admitted by governance_gateway (refused: edited "
        "after sealing)",
        "  filing          the status ccc is asked to file the summary under (refused: "
        "HISTORICAL_RECORD; malformed: not an Attempt)",
        "  scenario        the planned action augur simulates (refused: a violent one)",
        "  wording         what the work writes (hedged: 'may be')",
        "  transformation  the typed record conservation_kernel judges (refused: the "
        "shelf-life estimate restated as fact; malformed: not a TransformationCandidate)",
        "",
        "APPROVED means every gate in this pipeline passed. It does not mean the summary is correct.",
        "",
    ]
    return out


def _render_report(report: Report) -> list[str]:
    s, d = report.scenario, report.decision
    synthetic = cgd.SYNTHETIC_SUBJECT_PREFIX
    out = [f"--- {s.key}: {s.title} ---", _wrap("  ", s.note)]
    out.append(f"  plan: {s.plan.line()}")
    if s.withhold:
        out.append(f"  library withheld: {', '.join(s.withhold)}")
    out.append("  verdicts, in the order they were evaluated:")
    for number, v in enumerate(d.verdicts, 1):
        origin = "   [issued by the combining layer, not by a gate]" if v.subject.startswith(synthetic) else ""
        out.append(f"    {number}. {v.gate}  {v.position.name}  {v.outcome.name}{origin}")
        out.append(_wrap("       reason: ", v.reason or "(none)"))
    if not d.verdicts:
        out.append("    (none)")
    if d.not_evaluated:
        out.append("  not evaluated:")
        for name, why in d.not_evaluated:
            out.append(_wrap(f"    {name}: ", why))
    else:
        out.append("  not evaluated: none")
    out.append("  gate check() calls: " + " ".join(f"{n}={c}" for n, c in report.checks))
    if report.comparisons:
        out.append("  for comparison, asked outside the pipeline on the same candidate:")
        for _key, v in report.comparisons:
            out.append(_wrap(f"    {v.gate}  {v.position.name}  {v.outcome.name}: ", v.reason or "(none)"))
    ran = "yes" if d.executed else "NO"
    out.append(f"  work executed: {ran} (work calls: {report.work_calls})")
    if not d.executed:
        result = "none, the work did not run"
    elif d.result_withheld:
        result = "withheld"
    else:
        result = f"released ({len(d.result.text)} characters)"
    out.append(f"  result: {result}")
    out.append(f"  final decision: {FINAL_LABEL[d.outcome]}")
    out.append(f"  decision digest: {d.digest[:16]}")
    verdict = "ok" if report.as_expected else "MISMATCH"
    out.append(
        f"  expected: {FINAL_LABEL[s.expect_outcome]} with {s.expect_work_calls} work call(s): {verdict}"
    )
    out.append("")
    return out


def _render_tamper(t: TamperReport) -> list[str]:
    out = [
        "--- S8: A verdict or a decision record is edited after the fact ---",
        _wrap(
            "  ",
            "The text approved in S1 is edited afterwards (dissolution 96.0% becomes 99.9%), and "
            "the digest of the edited text is recomputed. The decision record of S3 is edited "
            "(TERMINAL_BREACH becomes pass) and its digest recomputed.",
        ),
        f"  dit verdict binds to the text it judged: {t.binds_original}",
        f"  dit verdict binds to the edited text, digest recomputed: {t.binds_edited}",
        f"  a verdict built by hand with the recomputed digest binds: {t.forged_binds}",
        _wrap(
            "    ",
            "That last line is the limit: the digest is tamper-evidence, not tamper-proofing. "
            "It catches a verdict moved onto other text, not someone who rebuilds the verdict.",
        ),
        f"  S3 record: digest recomputes to the recorded digest: {t.record_digest_ok}",
        f"  S3 record edited to say pass: digest recomputes to the recorded digest: "
        f"{t.edited_record_digest_ok}",
        f"  expected: the edited text does not bind, the edited record does not verify: "
        f"{'ok' if t.as_expected else 'MISMATCH'}",
        "",
    ]
    return out


def _render_summary(run: DemoRun) -> list[str]:
    out = ["--- Summary ---", "  scenario  final decision    work calls  expected"]
    for r in run.reports:
        mark = "ok" if r.as_expected else "MISMATCH"
        out.append(
            f"  {r.scenario.key:<8}  {FINAL_LABEL[r.decision.outcome]:<16}  {r.work_calls:<10}  {mark}"
        )
    out.append(f"  {'S8':<8}  {'(tamper check)':<16}  {'-':<10}  {'ok' if run.tamper.as_expected else 'MISMATCH'}")
    out.append("")
    out.append("ALL SCENARIOS AS EXPECTED" if run.all_as_expected else "SOME SCENARIOS DID NOT END AS EXPECTED")
    return out


def render(run: DemoRun) -> str:
    """The whole output as one string."""
    lines = _render_header(run)
    for report in run.reports:
        lines += _render_report(report)
    lines += _render_tamper(run.tamper)
    lines += _render_summary(run)
    return "\n".join(lines) + "\n"


def main() -> int:
    try:
        run = run_demo()
    except ImportError as exc:
        print(f"The demo cannot run: {exc}", file=sys.stderr)
        print(
            "It needs CNS and the five libraries with their CNS connectors. "
            "Install them with: pip install -r requirements-demo.txt",
            file=sys.stderr,
        )
        return 2
    sys.stdout.write(render(run))
    return 0 if run.all_as_expected else 1


if __name__ == "__main__":
    sys.exit(main())
