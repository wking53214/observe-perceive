"""logic_stamp.py -- a digest of what a piece of scoring code DOES, not of how it is written.

Change control needs to know that two builds with different clinical behaviour are different builds. The parameter-set version only
covers the constants somebody remembered to list in it; a changed point value, a new syndrome threshold or a moved gate cutoff
inside a function changed behaviour and left the version where it was (see docs/race/CHANGE_CONTROL_AND_VALIDATION.md).

`logic_digest(*objects)` hashes the syntax tree of each function or class it is given, in a form that

  * ignores comments, blank lines, line numbers, formatting and docstrings (editing documentation is not a model change),
  * ignores logging calls (a log message is not a clinical rule),
  * is identical across the Python versions this library supports (it reads only node kinds and the names and constants they
    carry, in field order, never `ast.dump`, whose fields differ between versions and would make every recorded fingerprint
    change when an installation upgrades Python),
  * changes whenever a constant, a comparison, an operator, a name or the structure changes.

It is stdlib only so any module can import it.
"""
from __future__ import annotations

import ast
import functools
import hashlib
import inspect
import json
import textwrap
from typing import Any, Dict, List, Optional

__all__ = ["logic_digest", "source_digest"]

_LOGGER_NAMES = frozenset({"logger", "log", "logging"})


def _is_docstring(stmt: ast.stmt) -> bool:
    return (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and isinstance(stmt.value.value, str))


def _is_logging(stmt: ast.stmt) -> bool:
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    func = stmt.value.func
    return isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name) and func.value.id in _LOGGER_NAMES


def _walk(node: ast.AST, out: List[str], first_in_body: bool = False) -> None:
    kind = type(node).__name__
    out.append(kind)
    if isinstance(node, ast.Constant):
        out.append(repr(node.value))
    elif isinstance(node, ast.Name):
        out.append(node.id)
    elif isinstance(node, ast.Attribute):
        out.append(node.attr)
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        out.append(node.name)
    elif isinstance(node, (ast.arg, ast.keyword)):
        out.append(str(node.arg))
    elif isinstance(node, ast.alias):
        out.append(f"{node.name}:{node.asname}")
    elif isinstance(node, ast.ImportFrom):
        out.append(f"{node.module}:{node.level}")
    for field in node._fields:
        value = getattr(node, field, None)
        if isinstance(value, list):
            for position, child in enumerate(value):
                if not isinstance(child, ast.AST):
                    continue
                # a body's leading docstring and any logging statement are not logic
                if field in ("body", "orelse", "finalbody") and isinstance(child, ast.stmt):
                    if (position == 0 and field == "body" and _is_docstring(child)
                            and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))):
                        continue
                    if _is_logging(child):
                        continue
                _walk(child, out)
        elif isinstance(value, ast.AST):
            _walk(value, out)
    out.append(")")


@functools.lru_cache(maxsize=256)
def source_digest(source: str) -> str:
    """The digest of one piece of source text (see the module docstring for what it ignores)."""
    tree = ast.parse(textwrap.dedent(source))
    out: List[str] = []
    _walk(tree, out)
    return hashlib.sha256("\x1f".join(out).encode("utf-8")).hexdigest()


def logic_digest(*objects: Any, constants: Optional[Dict[str, Any]] = None) -> str:
    """One digest over the given functions and classes (by their source) and the given constants (by value).

    Objects are digested in the order given, each labelled by its qualified name, so reordering or renaming one changes the
    result. `constants` is a mapping of name to JSON-serialisable value (a set must be passed as a sorted list)."""
    parts: List[str] = []
    for obj in objects:
        label = getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(obj)
        parts.append(f"{label}:{source_digest(inspect.getsource(obj))}")
    if constants:
        parts.append("constants:" + hashlib.sha256(
            json.dumps(constants, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")).hexdigest())
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()
