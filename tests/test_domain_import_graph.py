"""Verify that pure domain modules isolate Playwright, patchright, and httpx."""

from __future__ import annotations

import importlib
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "module_name",
    [
        "gflow_cli.api.submit_policy",
        "gflow_cli.api.generation_route",
    ],
)
def test_domain_modules_import_no_playwright_patchright_or_httpx(module_name: str) -> None:
    """Task 3 domain rule: pure domain modules must not import Playwright, patchright or httpx."""
    code = (
        f"import {module_name}; "
        "import sys; "
        "forbidden = {'playwright', 'patchright', 'httpx'}; "
        "loaded = forbidden & sys.modules.keys(); "
        "assert not loaded, f'Forbidden modules loaded: {loaded}'"
    )
    res = subprocess.run(
        [sys.executable, "-I", "-c", code],
        capture_output=True,
        text=True,
        check=False,
    )
    assert res.returncode == 0, (
        f"Subprocess import check failed for {module_name}:\n"
        f"stdout: {res.stdout}\nstderr: {res.stderr}"
    )


def test_submit_policy_import_does_not_load_ui_transport() -> None:
    """The attempt-ended signal lives in the domain, so importing it loads no transport."""
    code = (
        "import gflow_cli.api.submit_policy as p, sys; "
        "assert p.DirectAttemptEnded and p.EndedOutcome and p.not_sent; "
        "forbidden = {'gflow_cli.api.transports.ui_automation', "
        "'gflow_cli.api.transports.direct_http'}; "
        "loaded = forbidden & sys.modules.keys(); "
        "assert not loaded, f'Transport modules loaded: {loaded}'"
    )
    res = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, res.stderr


@pytest.mark.parametrize(
    "name",
    [
        "Aspect",
        "AssetInfo",
        "FlowApiClient",
        "FlowApiError",
        "GenerateVideoRequest",
        "Mode",
        "ProjectInfo",
        "Tier",
    ],
)
def test_api_reexports(name: str) -> None:
    """Every current public re-export from gflow_cli.api remains importable."""
    api_module = importlib.import_module("gflow_cli.api")
    val = getattr(api_module, name)
    assert val is not None
