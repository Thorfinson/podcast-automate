"""Print which test modules exercise each source module of podcast_automate.

A test module counts for a source module when it imports it (`from podcast_automate.X import ...`,
`from podcast_automate import X`, `import podcast_automate.X`, also inside functions) or names it in a
patch target (`patch("podcast_automate.X.name")`). Only tests/test_*.py are read; the fixture
modules are left out because editing one already selects the full suite (AGENTS.md).

A source module that no test names directly is listed at the end with the source modules that import
it and the test modules reached through them.

Run from the repository root:  python scripts/test_map.py
"""

from __future__ import annotations

import ast
import collections
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = "podcast_automate"
SOURCE = ROOT / "src" / PACKAGE
TESTS = ROOT / "tests"


def source_modules() -> list[str]:
    return sorted(path.stem for path in SOURCE.glob("*.py") if path.stem not in {"__init__", "__main__"})


def dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{dotted(node.value)}.{node.attr}"
    return ""


def imported_modules(tree: ast.AST, modules: set[str], *, relative: bool) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if relative and node.level == 1:
                parts = (node.module or "").split(".")
                if node.module is None:
                    found.update(alias.name for alias in node.names if alias.name in modules)
                elif parts[0] in modules:
                    found.add(parts[0])
                continue
            parts = (node.module or "").split(".")
            if node.level or parts[0] != PACKAGE:
                continue
            if len(parts) > 1:
                found.add(parts[1])
            else:
                found.update(alias.name for alias in node.names if alias.name in modules)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == PACKAGE and len(parts) > 1:
                    found.add(parts[1])
    return found & modules


def patched_modules(tree: ast.AST, modules: set[str]) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or "patch" not in dotted(node.func).split("."):
            continue
        if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
            continue
        parts = node.args[0].value.split(".")
        if parts[0] == PACKAGE and len(parts) > 2 and parts[1] in modules:
            found.add(parts[1])
    return found


def test_mapping(modules: set[str]) -> dict[str, set[str]]:
    mapping: dict[str, set[str]] = collections.defaultdict(set)
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for module in imported_modules(tree, modules, relative=False) | patched_modules(tree, modules):
            mapping[module].add(path.stem)
    return mapping


def source_importers(modules: set[str]) -> dict[str, set[str]]:
    importers: dict[str, set[str]] = collections.defaultdict(set)
    for name in modules:
        tree = ast.parse((SOURCE / f"{name}.py").read_text(encoding="utf-8"))
        for imported in imported_modules(tree, modules, relative=True):
            if imported != name:
                importers[imported].add(name)
    return importers


def reached_through(module: str, mapping: dict[str, set[str]], importers: dict[str, set[str]]) -> tuple[list[str], set[str]]:
    """Walk up the importers until mapped modules are found; return them and their tests."""
    seen, queue, via, tests = {module}, collections.deque([module]), [], set()
    while queue:
        for importer in sorted(importers.get(queue.popleft(), ())):
            if importer in seen:
                continue
            seen.add(importer)
            if mapping.get(importer):
                via.append(importer)
                tests |= mapping[importer]
            else:
                queue.append(importer)
    return via, tests


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    modules = set(source_modules())
    mapping = test_mapping(modules)
    for module in sorted(mapping):
        print(f"{module} → {', '.join(sorted(mapping[module]))}")
    unmapped = sorted(modules - set(mapping))
    if unmapped:
        importers = source_importers(modules)
        print("\nNot named by any test (reached through importers):")
        for module in unmapped:
            via, tests = reached_through(module, mapping, importers)
            if via:
                print(f"{module} → via {', '.join(via)}: {', '.join(sorted(tests))}")
            else:
                print(f"{module} → no importer reaches a test")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
