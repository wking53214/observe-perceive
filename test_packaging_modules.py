"""Every local module the package imports must be listed in pyproject py-modules.

An installed copy imports only what pyproject lists. `cassette` and `pediatric_cassette` were imported at module
level by listed modules (observe_consolidated and the cassette seam) and were not listed, so
`import observe_consolidated` failed from an installed wheel.
"""
import ast
import pathlib
import re

ROOT = pathlib.Path(__file__).parent


def test_every_local_module_the_package_imports_is_listed_in_pyproject():
    mods = {p.stem for p in ROOT.glob("*.py")
            if not p.name.startswith("test_") and p.name not in ("conftest.py", "__init__.py")}
    text = (ROOT / "pyproject.toml").read_text()
    listed = set(re.findall(r'"([a-z_0-9]+)"', text[text.index("py-modules"):]))
    frontier, seen, missing = listed & mods, set(), set()
    while frontier:
        m = frontier.pop()
        seen.add(m)
        for n in ast.walk(ast.parse((ROOT / f"{m}.py").read_text())):
            if isinstance(n, ast.Import):
                names = [a.name.split(".")[0] for a in n.names]
            elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
                names = [n.module.split(".")[0]]
            else:
                names = []
            for x in names:
                if x in mods and x not in seen:
                    if x not in listed:
                        missing.add(x)
                    frontier.add(x)
    assert not missing, f"imported by the package but not in pyproject py-modules: {sorted(missing)}"
