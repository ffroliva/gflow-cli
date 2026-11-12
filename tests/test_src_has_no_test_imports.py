"""Product code never imports test tooling, nor asks whether it runs under a test (#1272).

``_intercept_reference_entities`` used to import ``unittest.mock`` and special-case ``Mock``
pages, and ``mcp/server.py`` skipped its stdout redirect when ``"pytest" in sys.modules``:
test seams living in product code. Test doubles and patches belong in ``tests/``; product code
behaves the same whatever it runs under.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "gflow_cli"
# Top-level names of test tooling. `pytest_*` plugins (pytest_asyncio, ...) match by prefix.
TEST_TOOLING = {"unittest", "pytest", "_pytest", "mock", "tests"}
TEST_ENV = {"PYTEST_CURRENT_TEST", "PYTEST_XDIST_WORKER"}


def _is_test_tooling(module: str) -> bool:
    top = module.split(".")[0]
    return top in TEST_TOOLING or top.startswith("pytest_")


def _offences(path: Path) -> list[str]:
    where = path.relative_to(SRC.parent)
    found: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        names: list[str] = []
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif (
            isinstance(node, ast.Call)
            and getattr(node.func, "attr", getattr(node.func, "id", "")) == "import_module"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            names = [node.args[0].value]
        found += [f"{where}:{node.lineno} imports {n}" for n in names if _is_test_tooling(n)]
        # `"pytest" in sys.modules`, `os.environ["PYTEST_CURRENT_TEST"]`: asking about the test run.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value in TEST_ENV or (
                _is_test_tooling(node.value) and node.value == node.value.split(".")[0]
            ):
                found.append(f"{where}:{node.lineno} names {node.value!r}")
    return found


def test_no_module_under_src_imports_or_detects_test_tooling() -> None:
    files = sorted(SRC.rglob("*.py"))
    assert files, f"no source files under {SRC}"
    offenders = [hit for p in files for hit in _offences(p)]
    assert offenders == []
