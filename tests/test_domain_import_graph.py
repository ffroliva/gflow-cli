"""Verify that pure domain modules import no Playwright or httpx."""

from __future__ import annotations

import ast
from pathlib import Path

SUBMIT_POLICY_PATH = (
    Path(__file__).resolve().parents[1] / "src" / "gflow_cli" / "api" / "submit_policy.py"
)
FORBIDDEN = {"playwright", "httpx"}


def test_submit_policy_imports_no_playwright_or_httpx() -> None:
    """Task 3 domain rule: submit_policy is pure domain with no Playwright, httpx or I/O."""
    assert SUBMIT_POLICY_PATH.exists(), f"{SUBMIT_POLICY_PATH} does not exist"
    content = SUBMIT_POLICY_PATH.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(SUBMIT_POLICY_PATH))

    offences: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in FORBIDDEN:
                    offences.append(f"line {node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                top = node.module.split(".")[0]
                if top in FORBIDDEN:
                    offences.append(f"line {node.lineno}: from {node.module} import ...")

    assert not offences, "submit_policy.py imports forbidden modules:\n" + "\n".join(offences)


def test_submit_policy_and_generation_route_import_no_playwright_or_httpx() -> None:
    """Coordinator alias: generation_route moves to Task 4; this verifies submit_policy."""
    test_submit_policy_imports_no_playwright_or_httpx()
