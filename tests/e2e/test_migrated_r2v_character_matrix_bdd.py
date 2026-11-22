"""E2E: r2v with saved characters, one or two per clip, on flow.google.com (#723).

Binds ``tests/features/migrated_r2v_character_matrix.feature``::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
    GFLOW_CLI_E2E_CAST="A=<Name>:<entity-uuid>,B=<Name>:<entity-uuid>" \\
    GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e_video tests/e2e/test_migrated_r2v_character_matrix_bdd.py -v

**Why a matrix.** One character failing in video (Kael, 7 of 7, Flow status 4) and
another passing (Pip) says nothing about which property of a character matters. A
stylized cast member (A) and a photoreal one (B), both without a voice, in the same
project and on the same build, separate the character from the code.

**Cost.** Every row spends Veo credits (12 per clip measured on 2026-10-26) and runs only
with ``GFLOW_CLI_E2E_RUN_VIDEO=1``. Each run with a reference uploads one small image.
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
import structlog
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.mcp import tools as mcp_tools
from tests.e2e._redacted_env import RedactedEnv

scenarios("../features/migrated_r2v_character_matrix.feature")

_PROMPT = "{names} on a jetty at dusk, beside the blue card from the reference"
_PROMPT_NO_REF = "{names} on a jetty at dusk"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} must be set (see the module docstring)")
    return value


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@given("a project on flow.google.com with the matrix cast")
def _project(world: dict[str, Any]) -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend credits on the character matrix")
    world["project"] = _env("GFLOW_CLI_E2E_PROJECT")
    cast: dict[str, tuple[str, str]] = {}
    for item in _env("GFLOW_CLI_E2E_CAST").split(","):
        letter, rest = item.split("=", 1)
        name, entity = rest.split(":", 1)
        assert " " not in name, "the by-name attach takes one word"
        cast[letter.strip()] = (name.strip(), entity.strip())
    world["cast"] = cast


def _members(world: dict[str, Any], cast: str) -> list[tuple[str, str]]:
    return [world["cast"][letter] for letter in cast.split("+")]


def _ref(tmp_path: Path) -> Path:
    from PIL import Image

    ref = tmp_path / "blue_card.png"
    Image.new("RGB", (640, 640), (40, 90, 160)).save(ref)
    return ref


@when(parsers.parse('the CLI generates an r2v clip of cast "{cast}" with a local reference image'))
def _cli(world: dict[str, Any], e2e_env: RedactedEnv, tmp_path: Path, cast: str) -> None:
    members = _members(world, cast)
    entity_args: list[str] = []
    for name, entity in members:
        entity_args += ["--reference-entity", entity, "--reference-entity-name", name]
    out = tmp_path / "cli"
    world["out"] = out
    world["proc"] = subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "video",
            "r2v",
            _PROMPT.format(names=" and ".join(n for n, _ in members)),
            "--ref",
            str(_ref(tmp_path)),
            *entity_args,
            "--project",
            world["project"],
            "--aspect",
            "16:9",
            "--out-dir",
            str(out),
        ],
        env=e2e_env | {"GFLOW_CLI_LOG_LEVEL": "INFO"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=1200,
        check=False,
    )


@then("the command succeeds and writes the clip")
def _cli_ok(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-4000:]
    clips = list(world["out"].glob("*.mp4"))
    assert clips and all(p.stat().st_size > 100_000 for p in clips), clips


@then(parsers.parse('the characters attached were exactly cast "{cast}"'))
def _cli_entities(world: dict[str, Any], cast: str) -> None:
    attached: list[dict[str, Any]] = []
    for line in world["proc"].stderr.splitlines():
        if line.startswith("{"):
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("event") == "migrated.character_entities_attached":
                attached.append(event)
    expected = sorted(entity for _, entity in _members(world, cast))
    assert attached and sorted(attached[-1]["entity_ids"]) == expected, attached


@when(
    parsers.parse(
        'the MCP video tool generates an r2v clip of cast "{cast}" with reference "{ref}"'
    )
)
def _mcp(
    world: dict[str, Any],
    tmp_path: Path,
    e2e_profile_dir: Path,
    install_log_capture: structlog.testing.LogCapture,
    cast: str,
    ref: str,
) -> None:
    del e2e_profile_dir  # fixture selects the real authenticated gflow home
    members = _members(world, cast)
    names = " and ".join(n for n, _ in members)
    world["capture"] = install_log_capture
    world["result"] = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=(_PROMPT if ref == "blue" else _PROMPT_NO_REF).format(names=names),
            mode="r2v",
            aspect="16:9",
            reference_images=[str(_ref(tmp_path))] if ref == "blue" else None,
            reference_entities=[entity for _, entity in members],
            reference_entity_names=[name for name, _ in members],
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )
    )


@then(parsers.parse('the task completes with the clip on disk, attaching exactly cast "{cast}"'))
def _mcp_ok(world: dict[str, Any], cast: str) -> None:
    result = world["result"]
    assert result["status"] == "completed", result
    files = [Path(p) for p in result["files"]]
    assert files and all(p.exists() and p.stat().st_size > 100_000 for p in files), files
    attached = [
        e
        for e in world["capture"].entries
        if e.get("event") == "migrated.character_entities_attached"
    ]
    expected = sorted(entity for _, entity in _members(world, cast))
    assert attached and sorted(attached[-1]["entity_ids"]) == expected, attached
