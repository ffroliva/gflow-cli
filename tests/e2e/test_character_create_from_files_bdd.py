"""E2E: create a character from local images on flow.google.com, CLI and MCP (#639).

Binds ``tests/features/character_create_from_files.feature``. Selected by
``-m e2e_character``; see ``docs/E2E_TESTING.md`` § BDD-bound e2e::

    GFLOW_CLI_E2E_RUN_CHARACTER=1 GFLOW_CLI_E2E_PROFILE=<profile> \\
        uv run pytest -m e2e_character tests/e2e/test_character_create_from_files_bdd.py -v

**Why an e2e.** Offline tests model the editor's ``C4BZMd`` / ``maseQ`` / ``rzMKMb``
replies as measured. Only the real editor shows that its Upload still binds a file to the
character, that each write commits, and that the character reads back with every field.
The MCP twin is a separate surface, so it gets its own scenario.

**Cost.** $0: a project create, uploads, edits and reads; nothing is generated. Every run
leaves throwaway projects (named ``e2e character from files``) and their characters.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.mcp import character_tools

scenarios("../features/character_create_from_files.feature")

_RUN_ENV = "GFLOW_CLI_E2E_RUN_CHARACTER"


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    if os.environ.get(_RUN_ENV, "").strip() != "1":
        pytest.skip(f"set {_RUN_ENV}=1 to run the character e2e tests")
    return {"tmp": tmp_path}


def _gflow(env: dict[str, str], *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [sys.executable, "-m", "gflow_cli", *args, "--profile", env["GFLOW_CLI_PROFILE"], "--json"],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
        check=False,
    )


def _ok(proc: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    return json.loads(proc.stdout)


def _images(tmp: Path) -> tuple[Path, Path]:
    """Two distinct portrait-shaped JPEGs; the editor binds whatever is uploaded."""
    from PIL import Image

    portrait, body = tmp / "portrait.jpg", tmp / "body.jpg"
    Image.new("RGB", (720, 1280), (180, 120, 90)).save(portrait, quality=90)
    Image.new("RGB", (720, 1280), (60, 90, 150)).save(body, quality=90)
    return portrait, body


@given("a throwaway project")
def _project(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    payload = _ok(_gflow(e2e_env, "project", "create", "--name", "e2e character from files"))
    world["project"] = str(payload["project_id"])


@when(
    parsers.parse(
        'the CLI creates "{name}" from a portrait and a body image with voice "{voice}" '
        'and personality "{pers}"'
    )
)
def _cli_create(
    world: dict[str, Any], e2e_env: dict[str, str], name: str, voice: str, pers: str
) -> None:
    portrait, body = _images(world["tmp"])
    world["proc"] = _gflow(
        e2e_env,
        "character",
        "create",
        "--project",
        world["project"],
        "--name",
        name,
        "--image",
        str(portrait),
        "--body-image",
        str(body),
        "--voice",
        voice,
        "--personality",
        pers,
    )


@when(
    parsers.parse(
        'the MCP create tool builds "{name}" from a portrait and a body image with voice '
        '"{voice}" and personality "{pers}"'
    )
)
def _mcp_create(
    world: dict[str, Any],
    e2e_env: dict[str, str],
    e2e_profile_dir: Path,
    name: str,
    voice: str,
    pers: str,
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    portrait, body = _images(world["tmp"])
    world["mcp"] = asyncio.run(
        character_tools.gflow_character_create(
            project=world["project"],
            name=name,
            image=str(portrait),
            body_image=str(body),
            voice=voice,
            personality=pers,
            profile=e2e_env["GFLOW_CLI_PROFILE"],
        )
    )


@then("the command succeeds")
def _succeeds(world: dict[str, Any]) -> None:
    payload = _ok(world["proc"])
    assert len(payload["character"]["workflow_ids"]) == 2, payload


@then("the tool succeeds")
def _tool_succeeds(world: dict[str, Any]) -> None:
    result = world["mcp"]
    assert result["status"] == "ok", result
    assert len(result["character"]["workflow_ids"]) == 2, result


@then(
    parsers.parse(
        'listing the project shows "{name}" with voice "{voice}", {n:d} images '
        'and personality "{pers}"'
    )
)
def _listed(
    world: dict[str, Any], e2e_env: dict[str, str], name: str, voice: str, n: int, pers: str
) -> None:
    payload = _ok(_gflow(e2e_env, "character", "list", "--project", world["project"]))
    matches = [c for c in payload["characters"] if c["display_name"] == name]
    assert len(matches) == 1, payload["characters"]
    char = matches[0]
    assert (char["voice"], len(char["workflow_ids"]), char["personality"]) == (voice, n, pers)
