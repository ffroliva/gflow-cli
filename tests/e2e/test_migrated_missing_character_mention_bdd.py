"""E2E: an ``@mention`` of a character the project lacks is a typed refusal (#1333).

Binds ``tests/features/migrated_missing_character_mention.feature``. Selected by
``-m e2e_image``; see ``docs/E2E_TESTING.md`` § BDD-bound e2e::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
        uv run pytest -m e2e_image tests/e2e/test_migrated_missing_character_mention_bdd.py -v

**Why an e2e.** The defect lived in the real picker: after an empty search its overlay
stays open and Backspace does not close it, so the retry's click on the prompt box timed
out. Only the live composer shows that Escape closes it and the run ends typed.

**Cost.** $0: the refusal comes before the submit, so no image quota is spent.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.mcp import tools as mcp_tools

scenarios("../features/migrated_missing_character_mention.feature")


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@given("a project on flow.google.com")
def _project(world: dict[str, Any]) -> None:
    project = os.environ.get("GFLOW_CLI_E2E_PROJECT", "").strip()
    if not project:
        pytest.skip("GFLOW_CLI_E2E_PROJECT must be set (see the module docstring)")
    world["project"] = project


@when(parsers.parse('the CLI generates "{prompt}"'))
def _cli(world: dict[str, Any], prompt: str, e2e_env: dict[str, str], tmp_path: Path) -> None:
    out = tmp_path / "cli"
    world["out"] = out
    world["proc"] = subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "image",
            "t2i",
            prompt,
            "--project",
            world["project"],
            "--aspect",
            "16:9",
            "-n",
            "1",
            "--out",
            str(out),
        ],
        # No colour codes, so the remediation text can be matched.
        env={k: v for k, v in e2e_env.items() if k != "FORCE_COLOR"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
        check=False,
    )


@then(parsers.parse('the command exits 32 naming "{name}", with no image written'))
def _cli_refused(world: dict[str, Any], name: str) -> None:
    proc = world["proc"]
    output = proc.stdout + proc.stderr
    assert proc.returncode == 32, output[-3000:]
    # Rich wraps long lines, so compare with whitespace collapsed.
    assert name in output and "gflow character list" in " ".join(output.split()), output[-3000:]
    assert "Locator.click" not in output, output[-3000:]
    out = world["out"]
    assert not out.exists() or not [p for p in out.iterdir() if p.is_file()]


@when(parsers.parse('the MCP image tool generates "{prompt}"'))
def _mcp(world: dict[str, Any], prompt: str, e2e_profile_dir: Path) -> None:
    del e2e_profile_dir  # fixture selects the real authenticated gflow home
    world["result"] = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt=prompt,
            aspect="16:9",
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )
    )


@then(parsers.parse('the task fails as reference-not-found naming "{name}", charged no'))
def _mcp_refused(world: dict[str, Any], name: str) -> None:
    result = world["result"]
    assert result["status"] == "failed", result
    error = result["error"]
    assert error["type"].endswith("/reference-not-found"), error
    assert name in error["detail"], error
    assert error["charged"] == "no", error
    assert not result.get("files"), result
