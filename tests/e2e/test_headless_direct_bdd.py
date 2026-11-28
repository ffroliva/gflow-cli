"""Live E2E: measured shapes run direct, at the visibility planned before launch.

Binds ``tests/features/headless_direct_live.feature`` (see ``docs/E2E_TESTING.md``
§ BDD-bound e2e). Runs the real ``gflow`` in a subprocess with the default transport
(``direct_http``); nothing sets ``GFLOW_CLI_TRANSPORT``. The video scenarios spend Veo
credits (4 per text-to-video clip, measured; the upscale scenario makes one first)::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_RUN_VIDEO=1 \\
        uv run pytest -m e2e tests/e2e/test_headless_direct_bdd.py -v

The ``route`` and ``visibility`` keys of the ``--json`` result are the observability fields
of the same release (``docs/superpowers/plans/2026-11-27-headless-direct-transport``,
Task 17). The ``headed`` row needs a display.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import TYPE_CHECKING, Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from gflow_cli.api.transports.migrated_video_upscale import mp4_track_dimensions

if TYPE_CHECKING:
    from pathlib import Path

    from tests.e2e._redacted_env import RedactedEnv

scenarios("../features/headless_direct_live.feature")

# Omni 1.2 Flash, 4 s, 360p: the measured text-to-video key; 16:9 is also the measured
# 1080p upscale source (generation_route.MEASURED_UPSCALE_SOURCES).
_T2V_KEY = ("--model", "omni-1.2-flash", "--duration", "4", "--resolution", "360p")


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


def _gflow(world: dict[str, Any], *args: str) -> subprocess.CompletedProcess[str]:
    env: RedactedEnv = world["env"]
    return subprocess.run(
        [sys.executable, "-m", "gflow_cli", *args],
        capture_output=True,
        text=True,
        check=False,
        env=env | {"GFLOW_CLI_BROWSER_VISIBILITY": world["visibility"]},
    )


def _require_video_opt_in() -> None:
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to spend Veo credits on this scenario")


def _t2v(world: dict[str, Any], out: Path, aspect: str) -> dict[str, Any]:
    proc = _gflow(
        world,
        *("video", "t2v", "a paper boat drifting on a pond", *_T2V_KEY),
        *("--aspect", aspect, "--out-file", str(out), "--json"),
    )
    assert proc.returncode == 0, f"gflow video t2v failed (exit {proc.returncode})"
    return json.loads(proc.stdout)


@given(parsers.parse('browser visibility "{visibility}"'))
def _visibility(world: dict[str, Any], e2e_env: RedactedEnv, visibility: str) -> None:
    world["env"] = e2e_env
    world["visibility"] = visibility


@when("I run text-to-video at a measured key")
def _run_t2v(world: dict[str, Any], tmp_path: Path) -> None:
    _require_video_opt_in()
    world["out"] = tmp_path / "t2v.mp4"
    world["result"] = _t2v(world, world["out"], "9:16")


@then(parsers.parse('the JSON result reports route "{route}" and visibility "{launch}"'))
def _reports(world: dict[str, Any], route: str, launch: str) -> None:
    assert world["result"]["route"] == route
    assert world["result"]["visibility"] == launch


@then("a 360p clip is written")
def _clip_360p(world: dict[str, Any]) -> None:
    assert mp4_track_dimensions(world["out"].read_bytes()) == (360, 640)


@given("a catalogued clip at the measured upscale source key")
def _source_clip(world: dict[str, Any], tmp_path: Path) -> None:
    _require_video_opt_in()
    world["source"] = _t2v(world, tmp_path / "source.mp4", "16:9")["media_id"]


@when("I upscale it to 1080p")
def _upscale(world: dict[str, Any], tmp_path: Path) -> None:
    world["out_dir"] = tmp_path / "upscaled"
    proc = _gflow(
        world,
        *("video", "upscale", world["source"], "--scale", "1080p"),
        *("--out", str(world["out_dir"])),
    )
    assert proc.returncode == 0, f"gflow video upscale failed (exit {proc.returncode})"


@then("a 1080p clip is written")
def _clip_1080p(world: dict[str, Any]) -> None:
    (clip,) = world["out_dir"].rglob("*_1080p.mp4")
    assert mp4_track_dimensions(clip.read_bytes()) == (1920, 1080)


@when("I run a four-row image batch")
def _image_batch(world: dict[str, Any], tmp_path: Path) -> None:
    world["out_dir"] = tmp_path / "images"
    config = tmp_path / "batch.json"
    rows = [{"text": f"a lighthouse at dusk, study {i}"} for i in range(4)]
    config.write_text(json.dumps({"prompts": rows}), encoding="utf-8")
    proc = _gflow(world, "run", "--config", str(config), "--output-dir", str(world["out_dir"]))
    assert proc.returncode == 0, f"gflow run failed (exit {proc.returncode})"


@then("four images are written")
def _four_images(world: dict[str, Any]) -> None:
    images = [p for p in world["out_dir"].rglob("*") if p.suffix in {".png", ".jpg", ".jpeg"}]
    assert len(images) == 4
