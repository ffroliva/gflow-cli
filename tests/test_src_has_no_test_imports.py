"""Product code never imports test tooling (#1272).

``_intercept_reference_entities`` used to import ``unittest.mock`` and special-case ``Mock``
pages, a test seam living in the transport. Test doubles belong in ``tests/``; product code
behaves the same whatever it is handed.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "gflow_cli"
TEST_TOOLING = ("unittest", "pytest", "tests")


def _test_imports(path: Path) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        found += [
            f"{path.relative_to(SRC.parent)}:{node.lineno} imports {n}"
            for n in names
            if n.split(".")[0] in TEST_TOOLING
        ]
    return found


def test_no_module_under_src_imports_test_tooling() -> None:
    offenders = [hit for p in sorted(SRC.rglob("*.py")) for hit in _test_imports(p)]
    assert offenders == []
