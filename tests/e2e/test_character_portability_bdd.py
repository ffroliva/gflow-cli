"""E2E: portable characters on flow.google.com — create from files, export, import (#639).

Binds ``tests/features/character_portability.feature``. Selected by ``-m e2e_character``;
see ``docs/E2E_TESTING.md`` § BDD-bound e2e::

    GFLOW_CLI_E2E_RUN_CHARACTER=1 GFLOW_CLI_E2E_PROFILE=<profile A> \\
    GFLOW_CLI_E2E_PROFILE_B=<profile B> \\
        uv run pytest -m e2e_character tests/e2e/test_character_portability_bdd.py -v

**Why an e2e.** Offline tests model the ``Zzl0ze`` read and the editor's rpc replies as
measured on 2026-10-16. Only the real editor shows that its Upload still binds a file to
the character, that a write commits, and that a character rebuilt on another account
reads back the same. Plan: ``docs/superpowers/plans/2026-10-16-character-portability``.

**Cost.** $0 for every scenario except the two ``@e2e_video`` ones, which spend ~12 Veo
credits per 8 s clip and run only with ``GFLOW_CLI_E2E_RUN_VIDEO=1`` (they skip before any
setup without it); ``@e2e_image`` ones spend image quota. Every run leaves throwaway
projects (named ``e2e portability …``) and characters on each account it touches.
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

from gflow_cli.mcp import tools as mcp_tools

scenarios("../features/character_portability.feature")

_RUN_ENV = "GFLOW_CLI_E2E_RUN_CHARACTER"
_PROFILE_A_ENV = "GFLOW_CLI_E2E_PROFILE"
_PROFILE_B_ENV = "GFLOW_CLI_E2E_PROFILE_B"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        pytest.skip(f"{name} must be set (see the module docstring)")
    return value


@pytest.fixture
def world(tmp_path: Path) -> dict[str, Any]:
    if os.environ.get(_RUN_ENV, "").strip() != "1":
        pytest.skip(f"set {_RUN_ENV}=1 to run the character e2e tests")
    return {"tmp": tmp_path, "profile_a": _require(_PROFILE_A_ENV)}


def _gflow(env: dict[str, str], profile: str, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed argv, our own CLI
        [sys.executable, "-m", "gflow_cli", *args, "--profile", profile, "--json"],
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


def _new_project(env: dict[str, str], profile: str) -> str:
    payload = _ok(_gflow(env, profile, "project", "create", "--name", "e2e portability"))
    return str(payload["project_id"])


def _images(tmp: Path) -> tuple[Path, Path]:
    """Two distinct portrait-shaped JPEGs; the editor binds whatever is uploaded."""
    from PIL import Image

    portrait, body = tmp / "portrait.jpg", tmp / "body.jpg"
    Image.new("RGB", (720, 1280), (180, 120, 90)).save(portrait, quality=90)
    Image.new("RGB", (720, 1280), (60, 90, 150)).save(body, quality=90)
    return portrait, body


def _create(world: dict[str, Any], env: dict[str, str], name: str, voice: str, pers: str) -> None:
    portrait, body = _images(world["tmp"])
    world["proc"] = _gflow(
        env,
        world["profile_a"],
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


def _listed(env: dict[str, str], profile: str, project: str, name: str) -> dict[str, Any]:
    payload = _ok(_gflow(env, profile, "character", "list", "--project", project))
    matches = [c for c in payload["characters"] if c["display_name"] == name]
    assert len(matches) == 1, payload["characters"]
    return matches[0]


# --- Given ---------------------------------------------------------------------


@given("a throwaway project on profile A")
def _project_a(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    world["project"] = _new_project(e2e_env, world["profile_a"])


@given(parsers.parse('a throwaway project on profile A holding the character "{name}"'))
def _project_a_with(world: dict[str, Any], e2e_env: dict[str, str], name: str) -> None:
    world["project"] = _new_project(e2e_env, world["profile_a"])
    _create(world, e2e_env, name, "aoede", "calm, precise")
    _ok(world["proc"])


@given(
    parsers.parse(
        'a throwaway project on profile A holding the characters "{first}" and "{second}"'
    )
)
def _project_a_with_two(
    world: dict[str, Any], e2e_env: dict[str, str], first: str, second: str
) -> None:
    _require_video()  # only the video scenarios hold two characters: skip before any setup
    world["project"] = _new_project(e2e_env, world["profile_a"])
    for name, voice in ((first, "aoede"), (second, "leda")):
        _create(world, e2e_env, name, voice, "friendly")
        _ok(world["proc"])


@given("a second throwaway project on profile A")
def _second_a(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    world["target"] = _new_project(e2e_env, world["profile_a"])
    world["target_profile"] = world["profile_a"]


@given("a throwaway project on profile B")
def _project_b(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    profile_b = _require(_PROFILE_B_ENV)
    assert profile_b != world["profile_a"], "profile B must be a different account"
    world["target"] = _new_project(e2e_env, profile_b)
    world["target_profile"] = profile_b


# --- When ----------------------------------------------------------------------


@when(
    parsers.parse(
        'the CLI creates "{name}" from a portrait and a body image with voice "{voice}" '
        'and personality "{pers}"'
    )
)
def _cli_create(
    world: dict[str, Any], e2e_env: dict[str, str], name: str, voice: str, pers: str
) -> None:
    _create(world, e2e_env, name, voice, pers)


@when(
    parsers.parse(
        'the MCP create tool builds "{name}" from a portrait and a body image with voice '
        '"{voice}" and personality "{pers}"'
    )
)
def _mcp_create(
    world: dict[str, Any], e2e_profile_dir: Path, name: str, voice: str, pers: str
) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    portrait, body = _images(world["tmp"])
    result = asyncio.run(
        mcp_tools.gflow_character_create(
            project=world["project"],
            name=name,
            image=str(portrait),
            body_image=str(body),
            voice=voice,
            personality=pers,
            profile=world["profile_a"],
        )
    )
    assert result["status"] == "ok", result


@when(parsers.parse('the CLI exports "{name}" to a bundle folder'))
def _cli_export(world: dict[str, Any], e2e_env: dict[str, str], name: str) -> None:
    world["bundle"] = world["tmp"] / "bundle"
    _ok(
        _gflow(
            e2e_env,
            world["profile_a"],
            "character",
            "export",
            "--project",
            world["project"],
            "--name",
            name,
            "--out",
            str(world["bundle"]),
        )
    )


@when(parsers.parse('the CLI exports "{first}" and "{second}" to bundle folders'))
def _cli_export_two(
    world: dict[str, Any], e2e_env: dict[str, str], first: str, second: str
) -> None:
    world["bundles"] = []
    world["names"] = (first, second)
    for name in (first, second):
        out = world["tmp"] / f"bundle_{name}"
        _ok(
            _gflow(
                e2e_env,
                world["profile_a"],
                "character",
                "export",
                "--project",
                world["project"],
                "--name",
                name,
                "--out",
                str(out),
            )
        )
        world["bundles"].append(out)


def _require_video() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "").strip() != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend Veo credits on this scenario")


@when(
    parsers.parse(
        'the CLI generates a video on profile B from "{prompt}" with both character folders '
        "in that project"
    )
)
def _cli_video_two(world: dict[str, Any], e2e_env: dict[str, str], prompt: str) -> None:
    out = world["tmp"] / "clip.mp4"
    folders = [arg for b in world["bundles"] for arg in ("--character", str(b))]
    world["proc"] = proc = _gflow(
        e2e_env,
        world["target_profile"],
        "video",
        "t2v",
        prompt,
        *folders,
        "--project",
        world["target"],
        "--aspect",
        "16:9",
        # Pinned: an unset model is whatever the account last picked, and on some
        # accounts that one offers no 8 s duration (e2e run, 2026-10-18).
        "--model",
        "omni-flash",
        "--duration",
        "8",
        "--output",
        str(out),
    )
    _ok(proc)
    world["clip"] = out


@when(
    parsers.parse(
        'the MCP video tool generates "{prompt}" in the second project with both character folders'
    )
)
def _mcp_video_two(
    world: dict[str, Any], e2e_profile_dir: Path, install_log_capture: Any, prompt: str
) -> None:
    del e2e_profile_dir
    world["capture"] = install_log_capture
    world["mcp_generated"] = asyncio.run(
        mcp_tools.gflow_generate_video(
            prompt=prompt,
            mode="t2v",
            aspect="16:9",
            model="omni_flash",  # pinned, as the CLI scenario explains
            duration=8,
            project=world["target"],
            characters=[str(b) for b in world["bundles"]],
            profile=world["target_profile"],
            wait=True,
        )
    )


@when("the CLI imports that bundle into the second project")
@when("the CLI imports that bundle into the project on profile B")
def _cli_import(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    _ok(
        _gflow(
            e2e_env,
            world["target_profile"],
            "character",
            "import",
            str(world["bundle"]),
            "--project",
            world["target"],
        )
    )


@when("the MCP character list tool reads the project")
def _mcp_list(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    del e2e_profile_dir  # selects the real authenticated gflow home
    world["mcp"] = asyncio.run(
        mcp_tools.gflow_character_list(project=world["project"], profile=world["profile_a"])
    )


@when(parsers.parse('the MCP export tool writes "{name}" to a bundle folder'))
def _mcp_export(world: dict[str, Any], e2e_profile_dir: Path, name: str) -> None:
    del e2e_profile_dir
    world["bundle"] = world["tmp"] / "bundle-mcp"
    result = asyncio.run(
        mcp_tools.gflow_character_export(
            project=world["project"],
            name=name,
            out=str(world["bundle"]),
            profile=world["profile_a"],
        )
    )
    assert result["status"] == "ok", result


@when("the MCP import tool imports that bundle into the second project")
def _mcp_import(world: dict[str, Any], e2e_profile_dir: Path) -> None:
    del e2e_profile_dir
    result = asyncio.run(
        mcp_tools.gflow_character_import(
            bundle_dir=str(world["bundle"]),
            project=world["target"],
            profile=world["target_profile"],
        )
    )
    assert result["status"] == "ok", result


@when(
    parsers.parse(
        'the CLI generates an image on profile B from "{prompt}" with that character folder '
        "and no project"
    )
)
def _cli_generate_new_project(world: dict[str, Any], e2e_env: dict[str, str], prompt: str) -> None:
    world["proc"] = proc = _gflow(
        e2e_env,
        world["target_profile"],
        "image",
        "t2i",
        prompt,
        "--character",
        str(world["bundle"]),
    )
    world["generated"] = payload = _ok(proc)
    world["target"] = payload["project_id"]


@when(
    parsers.parse(
        'the CLI generates an image on profile B from "{prompt}" with that character folder '
        "in that project"
    )
)
def _cli_generate_same_project(world: dict[str, Any], e2e_env: dict[str, str], prompt: str) -> None:
    world["proc"] = proc = _gflow(
        e2e_env,
        world["target_profile"],
        "image",
        "t2i",
        prompt,
        "--character",
        str(world["bundle"]),
        "--project",
        world["target"],
    )
    world["generated"] = _ok(proc)


@when(
    parsers.parse(
        'the MCP image tool generates "{prompt}" in the second project with that character folder'
    )
)
def _mcp_generate(
    world: dict[str, Any], e2e_profile_dir: Path, install_log_capture: Any, prompt: str
) -> None:
    del e2e_profile_dir
    world["capture"] = install_log_capture
    world["mcp_generated"] = asyncio.run(
        mcp_tools.gflow_generate_image(
            prompt=prompt,
            project=world["target"],
            characters=[str(world["bundle"])],
            profile=world["target_profile"],
            wait=True,
        )
    )


# --- Then ----------------------------------------------------------------------


@then("the command succeeds")
def _succeeds(world: dict[str, Any]) -> None:
    _ok(world["proc"])


@then(
    parsers.parse(
        'listing the project shows "{name}" with voice "{voice}", {n:d} images '
        'and personality "{pers}"'
    )
)
def _listed_a(
    world: dict[str, Any], e2e_env: dict[str, str], name: str, voice: str, n: int, pers: str
) -> None:
    char = _listed(e2e_env, world["profile_a"], world["project"], name)
    assert (char["voice"], len(char["workflow_ids"]), char["personality"]) == (voice, n, pers)


@then(
    parsers.parse(
        'listing the second project shows "{name}" with voice "{voice}", {n:d} images '
        'and personality "{pers}"'
    )
)
@then(
    parsers.parse(
        'listing that project on profile B shows "{name}" with voice "{voice}", {n:d} images '
        'and personality "{pers}"'
    )
)
def _listed_target(
    world: dict[str, Any], e2e_env: dict[str, str], name: str, voice: str, n: int, pers: str
) -> None:
    char = _listed(e2e_env, world["target_profile"], world["target"], name)
    assert (char["voice"], len(char["workflow_ids"]), char["personality"]) == (voice, n, pers)


@then(parsers.parse('it lists "{name}" with voice "{voice}" and {n:d} images'))
def _mcp_listed(world: dict[str, Any], name: str, voice: str, n: int) -> None:
    result = world["mcp"]
    assert result["status"] == "ok", result
    matches = [c for c in result["characters"] if c["display_name"] == name]
    assert len(matches) == 1, result
    assert (matches[0]["voice"], len(matches[0]["workflow_ids"])) == (voice, n)


@then("the bundle holds a manifest, a portrait and a body image, and no URL or account id")
def _bundle_shape(world: dict[str, Any]) -> None:
    bundle: Path = world["bundle"]
    names = {p.name for p in bundle.iterdir()}
    assert "manifest.json" in names, names
    assert any(n.startswith("portrait.") for n in names), names
    assert any(n.startswith("body.") for n in names), names
    assert len(names) == 3, names
    manifest = (bundle / "manifest.json").read_text(encoding="utf-8")
    assert "https://" not in manifest
    assert world["project"] not in manifest
    assert "@" not in manifest


@then(parsers.parse('the image is written and the project it created lists "{name}" once'))
def _generated_and_listed_once(world: dict[str, Any], e2e_env: dict[str, str], name: str) -> None:
    images = world["generated"]["images"]
    assert images and all(Path(i["local_path"]).stat().st_size > 1024 for i in images), images
    # The character must ride the generation, not merely exist in the project.
    assert "migrated.character_entities_attached" in world["proc"].stderr
    _listed(e2e_env, world["target_profile"], world["target"], name)  # asserts exactly one


@then(parsers.parse('the queued image is written and the second project lists "{name}" once'))
def _mcp_generated_and_listed_once(
    world: dict[str, Any], e2e_env: dict[str, str], name: str
) -> None:
    result = world["mcp_generated"]
    assert result["status"] == "completed", result
    assert result["files"] and all(Path(p).stat().st_size > 1024 for p in result["files"])
    attached = [
        e
        for e in world["capture"].entries
        if e.get("event") == "migrated.character_entities_attached"
    ]
    assert attached, "the queued run attached no character"
    _listed(e2e_env, world["target_profile"], world["target"], name)


def _two_attached(events: list[dict[str, Any]], names: tuple[str, ...]) -> None:
    attached = [e for e in events if e.get("event") == "migrated.character_entities_attached"]
    assert attached, "no character rode the generation"
    assert sorted(attached[-1]["names"]) == sorted(names), attached[-1]
    assert len(set(attached[-1]["entity_ids"])) == len(names), attached[-1]
    # Each chip where the prompt names it (user report 2026-10-17): the prompt opens with
    # text, so a chip-first layout means the characters were stuck in front again.
    typed = [e for e in events if e.get("event") == "migrated.prompt_typed" and e.get("layout")]
    assert typed, "no prompt layout was logged"
    layout = typed[-1]["layout"]
    assert layout[0].startswith("text:"), layout
    assert layout.count("chip") == len(names), layout
    assert all(a != b for a, b in zip(layout, layout[1:], strict=False)), layout


@then(
    "the video is written, both characters rode it where the prompt names them, and that "
    "project lists each once"
)
def _video_two(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    assert world["clip"].stat().st_size > 100_000
    events = [
        json.loads(line) for line in world["proc"].stderr.splitlines() if line.startswith("{")
    ]
    _two_attached(events, world["names"])
    for name in world["names"]:
        _listed(e2e_env, world["target_profile"], world["target"], name)


@then(
    "the queued video is written, both characters rode it where the prompt names them, and "
    "the second project lists each once"
)
def _mcp_video_two_listed(world: dict[str, Any], e2e_env: dict[str, str]) -> None:
    result = world["mcp_generated"]
    assert result["status"] == "completed", result
    assert result["files"] and all(Path(p).stat().st_size > 100_000 for p in result["files"])
    _two_attached(world["capture"].entries, world["names"])
    for name in world["names"]:
        _listed(e2e_env, world["target_profile"], world["target"], name)
