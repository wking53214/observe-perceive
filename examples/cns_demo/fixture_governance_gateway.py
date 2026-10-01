"""Demo fixture for governance_gateway: a real ALPHA gate with real inputs.

Scenario. An AI assistant is asked to release a short status summary of a
regulated record. This fixture supplies the admission of the SOURCE record:
the check that runs before the summary is generated.

POSITION is 'ALPHA', for these reasons, all taken from the library itself:

* README section 1 names governance_gateway "ADMISSION, the door filter",
  the first stage of the live decision path, ahead of observation, policy,
  decision, conservation and execution. A refusal is ``NOT_ADMITTED``: the
  artifact "never reached policy". That is the definition of a gate judged
  BEFORE the work.
* ``governance_gateway.cns_connector`` states the mapping outright: "where it
  judges: ALPHA ... it judges the artifact before policy, decision,
  conservation or execution exist for it ... It has no outcome end and this
  connector does not invent one." ``CnsGate.position`` returns
  ``GatePosition.ALPHA``, and ``cns_chain(...).complete()`` is False by
  design.
* Its candidate is an ``Artifact`` (the source record), which exists before
  the work does. It never sees the generated summary, so it could not sit at
  the OMEGA end even if the demo wanted it there.

The candidate this gate judges is therefore the SOURCE artifact, handed to
``gate.check(...)`` before the summary is generated. When it passes, the
work reads ``candidate.payload`` (a frozen mapping) to write the summary.
The ok artifact's payload carries a batch release record:

    record_type, lot, product, status, reviewed_by, reviewed_on,
    tests = {assay_pct, dissolution_pct, endotoxin_eu_per_ml}

REFUSED_OUTCOME is 'TERMINAL_BREACH'. The refused candidate is the same
record with one lab result edited after it was sealed, so its stored SHA-256
integrity no longer matches its content. The gateway rejects it with
``INTEGRITY_FAILURE`` and the connector maps every refusal to
``TERMINAL_BREACH``, never ``RETRY``: the gateway "never repairs, promotes,
rewrites, or silently coerces", so no delta mends a tampered source. This
was verified by running it (see ``__main__``), not assumed. The self-check
also proves the two payloads differ in exactly one field,
``payload.tests.dissolution_pct`` (96.0 against 99.9), and that the stored
integrity strings are equal, so the refusal is the stale seal and nothing else.

What the gate does NOT do, so the demo does not claim it. It validates the
``scope`` field as a ``Scope`` enum member; it does not restrict the scope to
READ_ONLY (README section 4, "Scope enforcement": the gateway validates the
enum, and execution-scope binding is the orchestrator's job). The ok record
happens to be READ_ONLY; a correctly sealed EXECUTE record would also be
admitted, and the self-check pins that so DESCRIPTION cannot drift into
claiming otherwise. The SHA-256 seal is tamper evidence, not authentication:
a record edited and then resealed is admitted too (README section 3).

Shared subject. The ok and refused candidates have the same ``artifact_id``,
so their verdicts carry the same ``subject`` ('batch-release-L2291'). That is
correct for a tamper scenario (it is the same record). The two verdicts are
told apart by ``subject_digest``, which covers the whole judged content, so
any log or audit view of this demo must key on subject AND subject_digest, or
a pass and a refusal of "the same subject" will look like one record.

Import safety. Nothing here imports ``cns`` or ``governance_gateway`` when
this module loads; both are imported inside the functions that need them.
Without ``cns`` installed, ``make_gate()`` raises
``governance_gateway.cns_connector.CnsNotInstalled`` (an ``ImportError``).
The candidate functions need only governance_gateway, not cns. The
self-check enforces this itself: it loads this file in a fresh interpreter
and fails if ``cns`` or ``governance_gateway`` ended up imported, and it
fails if a module-level import statement names either.

Run as a script for the self-check:  python fixture_governance_gateway.py
"""

from __future__ import annotations

import ast
import dataclasses
import subprocess
import sys
from collections.abc import Mapping
from typing import Any

POSITION = "ALPHA"

REFUSED_OUTCOME = "TERMINAL_BREACH"

DESCRIPTION = (
    "Admits the source batch release record only if it is well formed, carries "
    "provenance, authority, an epistemic status and an explicit valid scope, and "
    "its SHA-256 seal still matches its content, so a record edited after sealing "
    "(and not resealed) never reaches the summary work."
)

#: The reason the library gives for the refused candidate, for the demo's log.
REFUSED_REASON = "INTEGRITY_FAILURE"

_ARTIFACT_ID = "batch-release-L2291"

#: The one lab result that differs between the sealed and the edited record.
_SEALED_DISSOLUTION_PCT = 96.0
_EDITED_DISSOLUTION_PCT = 99.9

#: What the self-check forbids this module from importing at load time.
_FORBIDDEN_AT_IMPORT = ("cns", "governance_gateway")


def _library() -> Any:
    """Import the library on demand, so loading this module never needs it."""
    import governance_gateway

    return governance_gateway


def make_gate() -> Any:
    """A new, independent ``cns.gate.Gate``: the library's own ``CnsGate``.

    Each call builds a fresh ``GovernanceGateway`` inside a fresh ``CnsGate``;
    no instance is cached or shared. Raises ``CnsNotInstalled`` when CNS is
    absent (the connector checks at construction, not on first use).
    """
    from governance_gateway.cns_connector import CnsGate

    return CnsGate()


def _payload(dissolution_pct: float) -> dict:
    """The batch release payload. The sealed and edited records both come from here.

    ``dissolution_pct`` is the only parameter, so the refused candidate cannot
    drift from the ok one in any other field.
    """
    return {
        "record_type": "batch_release",
        "lot": "L-2291",
        "product": "Compound X 10 mg tablets",
        "status": "released",
        "reviewed_by": "QA-117",
        "reviewed_on": "2026-09-14",
        "tests": {
            "assay_pct": 99.2,
            "dissolution_pct": dissolution_pct,
            "endotoxin_eu_per_ml": 0.12,
        },
    }


def _sealed_record(scope_name: str = "READ_ONLY") -> Any:
    """A freshly built, correctly sealed source artifact. New objects every call."""
    lib = _library()
    return lib.Artifact.create(
        artifact_id=_ARTIFACT_ID,
        payload=_payload(_SEALED_DISSOLUTION_PCT),
        provenance={
            "source_system": "LIMS",
            "export_id": "EXP-2026-09-14-0042",
            "exported_at": "2026-09-14T09:30:00Z",
        },
        epistemic_status=lib.EpistemicStatus.FACT,
        authority=lib.Authority(actor="qa-reviewer-117", grant="GRANT-READ-SUMMARY-117"),
        scope=lib.Scope[scope_name],
    )


def ok_candidate() -> Any:
    """The source record as sealed, which the gateway admits. Fresh each call."""
    return _sealed_record()


def refused_candidate() -> Any:
    """The same record with a lab result edited after sealing. Fresh each call.

    ``dissolution_pct`` is changed from 96.0 to 99.9 while the stored
    ``integrity`` still holds the digest of the original, so the gateway's
    recomputed digest disagrees and it refuses with ``INTEGRITY_FAILURE``.
    The edited payload comes from the same ``_payload`` builder as the ok one,
    so ``dissolution_pct`` is the only field that can differ (the self-check
    verifies that).
    """
    sealed = _sealed_record()
    # replace() keeps the stale integrity string and re-runs __post_init__,
    # which freezes the new payload but does not re-seal it.
    return dataclasses.replace(sealed, payload=_payload(_EDITED_DISSOLUTION_PCT))


def _differences(a: Any, b: Any, path: str) -> list:
    """Paths at which two frozen artifact values differ (mappings and tuples are walked)."""
    if isinstance(a, Mapping) and isinstance(b, Mapping):
        found: list = []
        for key in sorted(set(a) | set(b)):
            here = f"{path}.{key}"
            found += [here] if key not in a or key not in b else _differences(a[key], b[key], here)
        return found
    if isinstance(a, tuple) and isinstance(b, tuple) and len(a) == len(b):
        found = []
        for index, (left, right) in enumerate(zip(a, b)):
            found += _differences(left, right, f"{path}[{index}]")
        return found
    return [] if a == b else [path]


def _artifact_differences(a: Any, b: Any) -> list:
    found: list = []
    for field in dataclasses.fields(a):
        found += _differences(getattr(a, field.name), getattr(b, field.name), field.name)
    return found


def _module_level_imports(source: str) -> list:
    """Top-level module names imported by statements that run when the file loads.

    Function bodies are skipped (they run only when called); class bodies and
    conditionals are walked, because they run at import.
    """
    names: list = []

    def walk(nodes: list) -> None:
        for node in nodes:
            if isinstance(node, ast.Import):
                names.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.append((node.module or "").split(".")[0] if node.level == 0 else ".")
            elif not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for _, value in ast.iter_fields(node):
                    if isinstance(value, list):
                        walk([item for item in value if isinstance(item, ast.AST)])

    walk(ast.parse(source).body)
    return names


_LOAD_PROBE = (
    "import importlib.util, sys\n"
    "spec = importlib.util.spec_from_file_location('fixture_under_check', sys.argv[1])\n"
    "module = importlib.util.module_from_spec(spec)\n"
    "spec.loader.exec_module(module)\n"
    f"print(','.join(n for n in {_FORBIDDEN_AT_IMPORT!r} if n in sys.modules))\n"
)


def _imported_when_loaded() -> Any:
    """Which forbidden packages a fresh interpreter has imported after loading this file.

    Returns a list of names, or None when the check itself could not run.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-I", "-B", "-c", _LOAD_PROBE, __file__],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    return [name for name in done.stdout.strip().split(",") if name]


def _check(condition: bool, label: str, failures: list) -> None:
    print(f"  [{'ok' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def _native(artifact: Any) -> Any:
    """The library's own verdict on an artifact, from a brand new GovernanceGateway."""
    return _library().GovernanceGateway().evaluate(artifact)


def _self_check() -> int:
    try:
        _library()
    except ImportError as exc:
        print(f"governance_gateway could not be imported: {type(exc).__name__}: {exc}")
        print("The fixture module itself loaded fine; install governance_gateway to run the gate.")
        return 2
    try:
        gate = make_gate()
    except ImportError as exc:  # CnsNotInstalled is an ImportError
        print(f"make_gate() could not run: {type(exc).__name__}: {exc}")
        if type(exc).__name__ == "CnsNotInstalled":
            print("governance_gateway imported fine; the gate itself needs cns installed.")
        else:
            print("An import the gate needs failed; see the message above.")
        return 2

    from cns.gate import Gate, GateChain, GateOutcome, GatePosition, resolve, subject_digest, unbound
    from governance_gateway import GateReason, GovernanceGateway
    from governance_gateway.cns_connector import CnsGate, judged_content

    failures: list = []
    print(f"gate name      : {gate.name}")
    print(f"POSITION       : {POSITION} (gate declares {gate.position.name})")
    print(f"REFUSED_OUTCOME: {REFUSED_OUTCOME}")
    print(f"DESCRIPTION    : {DESCRIPTION}")

    ok_in, bad_in = ok_candidate(), refused_candidate()

    # Run the gate with a spy on the library's own evaluate(), to see that the
    # verdicts come from it and not from something shaped like it.
    seen: list = []
    original = GovernanceGateway.evaluate

    def spy(self: Any, artifact: Any) -> Any:
        seen.append(artifact)
        return original(self, artifact)

    GovernanceGateway.evaluate = spy
    try:
        ok_res, bad_res = gate.check(ok_in), gate.check(bad_in)
    finally:
        GovernanceGateway.evaluate = original

    for label, res in (("ok     ", ok_res), ("refused", bad_res)):
        print(
            f"{label}: position={res.position.name} outcome={res.outcome.name} "
            f"reason={res.reason!r} subject={res.subject!r} "
            f"digest={res.subject_digest[:16]}... bound={res.bound()}"
        )
    ok_native, bad_native = _native(ok_in), _native(bad_in)
    print(f"native ok      : accepted={ok_native.accepted} reason={ok_native.reason}")
    print(f"native refused : accepted={bad_native.accepted} reason={bad_native.reason}")

    print("checks")
    _check(isinstance(gate, Gate), "gate satisfies cns.gate.Gate", failures)
    _check(type(gate) is CnsGate and type(gate).__module__ == "governance_gateway.cns_connector",
           "gate is the library's own governance_gateway.cns_connector.CnsGate", failures)
    _check(len(seen) == 2 and seen[0] is ok_in and seen[1] is bad_in,
           "gate.check() calls the library's GovernanceGateway.evaluate once, on the exact candidate",
           failures)
    _check(ok_native.accepted is True and ok_native.reason is None
           and ok_res.outcome is GateOutcome.PASS and ok_res.reason == "",
           "ok: native verdict is accepted, and the gate agrees (PASS, no reason)", failures)
    _check(bad_native.accepted is False and bad_native.reason is GateReason.INTEGRITY_FAILURE
           and bad_res.outcome is GateOutcome[REFUSED_OUTCOME]
           and bad_res.reason == bad_native.reason.value == REFUSED_REASON,
           f"refused: native verdict is INTEGRITY_FAILURE, and the gate agrees ({REFUSED_OUTCOME})",
           failures)
    _check(gate.position is GatePosition.ALPHA and gate.position.name == POSITION,
           "POSITION matches the gate's declared position", failures)
    _check(GateChain(alpha=(gate,)).misplaced() == (), "gate sits in the alpha slot", failures)
    _check(ok_res.position is GatePosition.ALPHA and bad_res.position is GatePosition.ALPHA,
           "both verdicts carry position ALPHA", failures)
    _check(unbound([ok_res, bad_res]) == (), "both verdicts are bound", failures)
    _check(ok_res.binds(ok_in.artifact_id, subject_digest(judged_content(ok_in))),
           "ok verdict binds to the ok artifact", failures)
    _check(not ok_res.binds(bad_in.artifact_id, subject_digest(judged_content(bad_in))),
           "ok verdict does not bind to the edited artifact", failures)
    _check(ok_res.subject_digest != bad_res.subject_digest,
           "edited record digests differently from the sealed one", failures)
    _check(ok_res.subject == bad_res.subject == _ARTIFACT_ID,
           "ok and refused verdicts share the subject (same record); only subject_digest tells them apart",
           failures)
    _check(resolve([ok_res]) is GateOutcome.PASS, "resolve([ok]) is PASS", failures)
    _check(resolve([ok_res, bad_res]) is GateOutcome[REFUSED_OUTCOME],
           "resolve([ok, refused]) is the refusal outcome", failures)

    differing = _artifact_differences(ok_in, bad_in)
    _check(differing == ["payload.tests.dissolution_pct"],
           f"refused differs from ok in exactly one field: {differing}", failures)
    _check(ok_in.integrity == bad_in.integrity and ok_in.integrity == ok_in.expected_integrity()
           and bad_in.integrity != bad_in.expected_integrity(),
           "the refusal is a stale seal: stored integrity is equal, only the edited one disagrees "
           "with its content", failures)
    execute_in = _sealed_record("EXECUTE")
    _check(gate.check(execute_in).outcome is GateOutcome.PASS and execute_in.scope.name == "EXECUTE",
           "a correctly sealed EXECUTE-scope record is admitted too: the gate validates the scope "
           "enum, it does not restrict to READ_ONLY (update DESCRIPTION if this ever changes)",
           failures)
    squashed = "".join(ch for ch in DESCRIPTION.lower() if ch.isalpha())
    _check("readonly" not in squashed,
           "DESCRIPTION does not claim the gate enforces a read-only scope", failures)

    other = make_gate()
    _check(other is not gate and other._inner is not gate._inner,
           "make_gate() twice gives independent gates", failures)
    _check(other.check(ok_candidate()) == ok_res and other.check(refused_candidate()) == bad_res,
           "a second gate gives the same verdicts (no shared state)", failures)
    a, b = ok_candidate(), ok_candidate()
    _check(a is not b and a.payload is not b.payload and a == b,
           "ok_candidate() returns a fresh, equal object each call", failures)
    c, d = refused_candidate(), refused_candidate()
    _check(c is not d and c.payload is not d.payload and c == d,
           "refused_candidate() returns a fresh, equal object each call", failures)

    with open(__file__, encoding="utf-8") as handle:
        top_level = _module_level_imports(handle.read())
    _check(not set(top_level) & set(_FORBIDDEN_AT_IMPORT),
           f"no module-level import of cns or governance_gateway (module-level imports: "
           f"{sorted(set(top_level))})", failures)
    loaded = _imported_when_loaded()
    _check(loaded == [],
           "loading this file in a fresh interpreter imports neither cns nor governance_gateway"
           + ("" if loaded is not None else " (the check could not run)"), failures)

    print("PASS" if not failures else f"FAILED: {len(failures)} check(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_self_check())
