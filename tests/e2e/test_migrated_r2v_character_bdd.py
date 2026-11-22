"""E2E: an r2v clip on flow.google.com carries a named character entity (#723).

Binds ``tests/features/migrated_r2v_character.feature``. Selected by ``-m e2e_video``;
see ``docs/E2E_TESTING.md`` § BDD-bound e2e::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
    GFLOW_CLI_E2E_CHARACTER=<OneWordName> GFLOW_CLI_E2E_CHARACTER_ID=<entity-uuid> \\
    GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e_video tests/e2e/test_migrated_r2v_character_bdd.py -v

**Why an e2e.** The gate this replaces rested on a null ``MZZa6b`` reply measured on
2026-09-07. Offline tests cannot show whether Flow still answers an entity-bound submit
with a record, or whether the body still carries the entity id the guard requires.

**Cost.** The two generating scenarios spend Veo credits (12 per clip measured on
2026-10-26) and run only with ``GFLOW_CLI_E2E_RUN_VIDEO=1``. The id-only refusal is $0
and always runs. Each generating run uploads one small reference image to the project.
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
from pytest_bdd import given, scenarios, then, when

from gflow_cli.mcp import tools as mcp_tools
from tests.e2e._redacted_env import RedactedEnv

scenarios("../features/migrated_r2v_character.feature")

_PROMPT = "{name} walks along a jetty at dusk, holding the blue card from the reference"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} must be set (see the module docstring)")
    return value


def _require_paid() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend credits on the r2v character e2e")


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@given("a project on flow.google.com that holds the named character")
def _project(world: dict[str, Any]) -> None:
    world["project"] = _env("GFLOW_CLI_E2E_PROJECT")
    world["character"] = _env("GFLOW_CLI_E2E_CHARACTER")
    world["entity"] = _env("GFLOW_CLI_E2E_CHARACTER_ID")
    assert " " not in world["character"], "the by-name attach takes one word"


def _ref(tmp_path: Path) -> Path:
    from PIL import Image

    ref = tmp_path / "blue_card.png"
    Image.new("RGB", (640, 640), (40, 90, 160)).save(ref)
    return ref


def _run_cli(world: dict[str, Any], e2e_env: RedactedEnv, tmp_path: Path, *extra: str) -> None:
    out = tmp_path / "cli"
    world["out"] = out
    world["proc"] = subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "video",
            "r2v",
            _PROMPT.format(name=world["character"]),
            "--ref",
            str(_ref(tmp_path)),
            *extra,
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


@when("the CLI generates an r2v clip of the character with a local reference image")
def _cli(world: dict[str, Any], e2e_env: RedactedEnv, tmp_path: Path) -> None:
    _require_paid()
    _run_cli(
        world,
        e2e_env,
        tmp_path,
        "--reference-entity",
        world["entity"],
        "--reference-entity-name",
        world["character"],
    )


@when("the CLI generates an r2v clip with an entity id and no name")
def _cli_id_only(world: dict[str, Any], e2e_env: RedactedEnv, tmp_path: Path) -> None:
    _run_cli(world, e2e_env, tmp_path, "--reference-entity", world["entity"])


@then("the command exits 36 naming the id-only character, with no clip written")
def _cli_refused(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 36, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "entity id without a name" in proc.stdout + proc.stderr
    out = world["out"]
    assert not out.exists() or not list(out.glob("*.mp4"))


@then("the command succeeds and writes the clip")
def _cli_ok(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    clips = list(world["out"].glob("*.mp4"))
    assert clips and all(p.stat().st_size > 100_000 for p in clips), clips


def _log_events(stderr: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events


@then("the character attached was the requested entity")
def _cli_entity(world: dict[str, Any]) -> None:
    attached = [
        e
        for e in _log_events(world["proc"].stderr)
        if e.get("event") == "migrated.character_entities_attached"
    ]
    # The run succeeding is the body-guard proof: `expect_reference_ids` carries this id,
    # and a MZZa6b body without it is refused before Flow acts on it (#723).
    assert attached and attached[-1]["entity_ids"] == [world["entity"]], attached


@when("the MCP video tool generates an r2v clip of the character with a local reference image")
def _mcp(
    world: dict[str, Any],
    tmp_path: Path,
    e2e_profile_dir: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    del e2e_profile_dir  # fixture selects the real authenticated gflow home
    _require_paid()
    world["capture"] = install_log_capture
    world["result"] = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=_PROMPT.format(name=world["character"]),
            mode="r2v",
            aspect="16:9",
            reference_images=[str(_ref(tmp_path))],
            reference_entities=[world["entity"]],
            reference_entity_names=[world["character"]],
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )
    )


@then("the task completes with the clip on disk")
def _mcp_ok(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result["status"] == "completed", result
    files = [Path(p) for p in result["files"]]
    assert files and all(p.exists() and p.stat().st_size > 100_000 for p in files), files
    attached = [
        e
        for e in world["capture"].entries
        if e.get("event") == "migrated.character_entities_attached"
    ]
    assert attached and attached[-1]["entity_ids"] == [world["entity"]], attached
