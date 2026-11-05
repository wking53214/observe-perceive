"""The OBSERVE gate exists in two repositories (observe-perceive upstream, OBSERVE vendored). Drift is a defect.

The gate's source (the helpers, the class and the two constants that belong to it) is fingerprinted here. The same
test and the same fingerprint live in both repositories. Change the gate in observe-perceive first, then copy it into
OBSERVE, then update EXPECTED in both test files in the same change. A copy that was edited on one side only fails
here, in that repository, with the new fingerprint printed.
"""

import ast
import hashlib
from pathlib import Path

#: sha256 over the gate's source text, in name order. Update in BOTH repositories together.
EXPECTED = "174cd2788c3b99b29060abf46a58a6f7e6d82a5591ff46c50bf465547ceebd55"

_FUNCS = {"_engine_set", "_record_problem", "_cassette_of"}
_CLASSES = {"ObserveCnsGate"}
_ASSIGNS = {"_BESIDE_FINGERPRINT", "_UNBOUND_VERDICT_FIELDS"}


def gate_fingerprint(path):
    source = Path(path).read_text()
    parts = {}
    for node in ast.parse(source).body:
        if isinstance(node, ast.FunctionDef) and node.name in _FUNCS:
            name = node.name
        elif isinstance(node, ast.ClassDef) and node.name in _CLASSES:
            name = node.name
        elif isinstance(node, ast.Assign) and any(getattr(t, "id", None) in _ASSIGNS for t in node.targets):
            name = node.targets[0].id
        else:
            continue
        parts[name] = ast.get_source_segment(source, node)
    missing = (_FUNCS | _CLASSES | _ASSIGNS) - set(parts)
    assert not missing, f"the gate lost {sorted(missing)}"
    text = "\n".join(f"{name}\n{parts[name]}" for name in sorted(parts))
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_observe_gate_matches_the_recorded_fingerprint():
    actual = gate_fingerprint(Path(__file__).with_name("cns_connector.py"))
    assert actual == EXPECTED, (
        "The OBSERVE gate in cns_connector.py changed. It is kept identical in observe-perceive and OBSERVE. "
        f"Apply the same change in the other repository, then set EXPECTED to {actual} in both test files."
    )
