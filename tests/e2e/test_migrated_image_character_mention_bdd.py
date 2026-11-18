"""E2E: ``@Character`` on flow.google.com images binds the character entity (#723).

Binds ``tests/features/migrated_image_character_mention.feature``. Selected by
``-m e2e_image``; see ``docs/E2E_TESTING.md`` § BDD-bound e2e::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_PROJECT=<project-uuid> \\
    GFLOW_CLI_E2E_CHARACTER=<OneWordName> \\
        uv run pytest -m e2e_image tests/e2e/test_migrated_image_character_mention_bdd.py -v

**Why an e2e.** Offline tests model the picker as measured on 2026-10-13: character and
media options share one DOM shape, and an image captioned with the character's name
ranked above the character. Only the real composer shows whether the committed chip is
still an entity and whether the ``ogiZ0b`` submit carries its id.

**Cost.** $0 in credits; two images from Flow's daily image quota. Each run adds images
whose captions may start with the character's name — which is the case this protects.
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

scenarios("../features/migrated_image_character_mention.feature")

_PROJECT_ENV = "GFLOW_CLI_E2E_PROJECT"
_CHARACTER_ENV = "GFLOW_CLI_E2E_CHARACTER"


def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} must be set (see the module docstring)")
    return value


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


@given("a project on flow.google.com that holds the named character")
def _project(world: dict[str, Any]) -> None:
    world["project"] = _env(_PROJECT_ENV)
    world["character"] = _env(_CHARACTER_ENV)
    assert " " not in world["character"], "the by-name fallback takes one word"


def _prompt(world: dict[str, Any], template: str) -> str:
    return template.replace("<character>", world["character"])


def _run_cli(
    world: dict[str, Any], e2e_env: dict[str, str], tmp_path: Path, mode: str, *extra: str
) -> None:
    out = tmp_path / "cli"
    world["out"] = out
    world["proc"] = subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "image",
            mode,
            *extra,
            "--project",
            world["project"],
            "--aspect",
            "16:9",
            "--out",
            str(out),
        ],
        # The steps read INFO events from stderr; the CLI defaults to WARNING (#1041, #1332).
        env={**e2e_env, "GFLOW_CLI_LOG_LEVEL": "INFO", "GFLOW_CLI_LOG_FORMAT": "json"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=900,
        check=False,
    )


@when(parsers.parse('the CLI generates "{template}"'))
def _cli(world: dict[str, Any], template: str, e2e_env: dict[str, str], tmp_path: Path) -> None:
    _run_cli(world, e2e_env, tmp_path, "t2i", _prompt(world, template))


@when(parsers.parse('the CLI generates "{template}" with a local reference image'))
def _cli_i2i(world: dict[str, Any], template: str, e2e_env: dict[str, str], tmp_path: Path) -> None:
    from PIL import Image

    ref = tmp_path / "style.png"
    Image.new("RGB", (640, 640), (40, 90, 160)).save(ref)
    _run_cli(world, e2e_env, tmp_path, "i2i", _prompt(world, template), "--ref", str(ref))


@when(parsers.parse('the CLI generates "{template}" with an entity id and no name'))
def _cli_id_only(
    world: dict[str, Any], template: str, e2e_env: dict[str, str], tmp_path: Path
) -> None:
    entity = "00000000-0000-4000-8000-000000000000"
    _run_cli(world, e2e_env, tmp_path, "t2i", template, "--reference-entity", entity)


@then("the command exits 36 naming the id-only character, with no image written")
def _cli_refused(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 36, proc.stdout[-2000:] + proc.stderr[-2000:]
    assert "entity id without a name" in proc.stdout + proc.stderr
    out = world["out"]
    assert not out.exists() or not [p for p in out.iterdir() if p.is_file()]


@then("the command succeeds and writes the image")
def _cli_ok(world: dict[str, Any]) -> None:
    proc = world["proc"]
    assert proc.returncode == 0, proc.stdout[-2000:] + proc.stderr[-2000:]
    images = [p for p in world["out"].iterdir() if p.is_file()]
    assert images and all(p.stat().st_size > 10_000 for p in images), images


def _log_events(stderr: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        if line.startswith("{"):
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    # No events at all is a logging setup problem, not a missing character (#1332).
    assert events, "the CLI wrote no JSON log events; check GFLOW_CLI_LOG_LEVEL/_FORMAT"
    return events


@then("the submit carried exactly the character and no other reference")
def _cli_entity(world: dict[str, Any]) -> None:
    events = _log_events(world["proc"].stderr)
    attached = [e for e in events if e.get("event") == "migrated.character_entities_attached"]
    assert attached, "no character was attached"
    entity_ids = attached[-1]["entity_ids"]
    assert len(entity_ids) == 1 and entity_ids[0], attached[-1]
    guarded = [e for e in events if e.get("event") == "migrated.image_submit_guarded"]
    # The guard holds the ogiZ0b body to EXACTLY the expected references: "passed" with
    # one character and no media means nothing leaked into the generation (#723).
    assert guarded and guarded[-1]["outcome"] == "passed", guarded
    assert (guarded[-1]["body_media"], guarded[-1]["body_characters"]) == (0, 1), guarded


@then("the submit carried exactly the uploaded image and the character")
def _cli_entity_and_media(world: dict[str, Any]) -> None:
    guarded = [
        e
        for e in _log_events(world["proc"].stderr)
        if e.get("event") == "migrated.image_submit_guarded"
    ]
    assert guarded and guarded[-1]["outcome"] == "passed", guarded
    assert (guarded[-1]["body_media"], guarded[-1]["body_characters"]) == (1, 1), guarded


@when(parsers.parse('the MCP image tool generates "{template}"'))
def _mcp(
    world: dict[str, Any],
    template: str,
    e2e_profile_dir: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    del e2e_profile_dir  # fixture selects the real authenticated gflow home
    world["capture"] = install_log_capture
    world["result"] = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt=_prompt(world, template),
            aspect="16:9",
            profile=os.environ["GFLOW_CLI_E2E_PROFILE"].strip(),
            project=world["project"],
            wait=True,
        )
    )


@then("the task completes with the image on disk")
def _mcp_ok(world: dict[str, Any]) -> None:
    result = world["result"]
    assert result["status"] == "completed", result
    files = [Path(p) for p in result["files"]]
    assert files and all(p.exists() and p.stat().st_size > 10_000 for p in files), files
    attached = [
        e
        for e in world["capture"].entries
        if e.get("event") == "migrated.character_entities_attached"
    ]
    assert attached and attached[-1]["entity_ids"][0], "the queued run attached no character"
    guarded = [
        e for e in world["capture"].entries if e.get("event") == "migrated.image_submit_guarded"
    ]
    assert guarded and guarded[-1]["outcome"] == "passed", guarded
    assert (guarded[-1]["body_media"], guarded[-1]["body_characters"]) == (0, 1), guarded
