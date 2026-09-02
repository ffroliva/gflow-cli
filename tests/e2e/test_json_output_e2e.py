"""E2E test for ``--json`` machine-readable output.

Verifies that ``--json`` produces stdout that ``json.loads`` parses cleanly
(no Rich progress chatter, no structlog leakage on stdout — structlog is
routed to stderr by the prior commit). A worker shelling out to gflow keys
its retry / seed continuation off this exact shape, so any drift here is a
contract break.

Skipped by default; opt in with::

    GFLOW_CLI_E2E_PROFILE=<profile-name> uv run pytest -m e2e -v \\
        tests/e2e/test_json_output_e2e.py

The non-network commands (``gflow models --json``, ``gflow auth list
--json``) are also exercised by unit tests; this file adds the live
subprocess pass to prove no Rich / log leak on the real binary path.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.e2e


@pytest.mark.e2e_auth
def test_e2e_auth_list_json_shape() -> None:
    """`gflow auth list --json` emits a JSON array of profile records on
    stdout — pure JSON, no Rich table leak. Worker discovery (``listProfiles``
    on the aistudio side) keys off this shape."""
    proc = subprocess.run(
        [sys.executable, "-m", "gflow_cli", "auth", "list", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`gflow auth list --json` exited {proc.returncode}; stderr tail: {proc.stderr[-400:]!r}"
    )
    data = json.loads(proc.stdout)  # raises on any leak
    assert isinstance(data, list), f"expected a list, got {type(data).__name__}"
    if data:
        keys = sorted(data[0].keys())
        for required in ("name", "is_default", "cookies_present", "profile_dir"):
            assert required in keys, f"profile record missing required key {required!r}; got {keys}"


@pytest.mark.e2e_auth
def test_e2e_models_catalog_json_shape() -> None:
    """`gflow models --json` emits the image+video catalog as a single JSON
    object on stdout. Catalog content is enum-derived (no I/O), but the
    subprocess pass proves no Rich console leak."""
    proc = subprocess.run(
        [sys.executable, "-m", "gflow_cli", "models", "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, (
        f"`gflow models --json` exited {proc.returncode}; stderr tail: {proc.stderr[-400:]!r}"
    )
    data = json.loads(proc.stdout)
    assert set(data.keys()) >= {"image", "video"}, (
        f"catalog must have 'image' and 'video' keys; got {sorted(data.keys())}"
    )
    # Every advertised alias must be one the gen command's --model Choice
    # accepts — regression guard for the alias-filter fix folded into 0e318a9.
    from gflow_cli.cli_models import _VIDEO_CLI_MODELS
    from gflow_cli.image_batch import ALLOWED_MODELS

    for m in data["image"]["models"]:
        for alias in m["aliases"]:
            assert alias in ALLOWED_MODELS, (
                f"image alias {alias!r} ({m['name']}) is not in --model Choice"
            )
    for m in data["video"]["models"]:
        for alias in m["aliases"]:
            assert alias in _VIDEO_CLI_MODELS, (
                f"video alias {alias!r} ({m['name']}) is not in --model Choice"
            )


@pytest.mark.e2e_image
def test_e2e_image_t2i_json_shape(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """`gflow image t2i --json` produces a pure-JSON document with the
    ``image_result`` schema (status / command / project_id / model / count /
    images[]) when run against real Flow. Runs 1 image generation (zero credits).

    A worker keys ``images[0].seed`` for refine-regen continuity; this test
    asserts the field is present and an int, plus the on-disk file landed.
    """
    # ``e2e_env`` already creates ``tmp_path/out`` (see tests/e2e/conftest.py).
    out_dir = tmp_path / "out"
    # Drop the autouse-isolated GFLOW_CLI_HOME so the subprocess resolves to
    # the user's real platformdirs path (where the live Chrome session lives).
    env = dict(e2e_env)
    env.pop("GFLOW_CLI_HOME", None)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "image",
            "t2i",
            "a calm forest at dawn",
            "--model",
            "nano2",
            "--out",
            str(out_dir),
            "--json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    assert proc.returncode == 0, (
        f"`image t2i --json` exited {proc.returncode}; stderr tail: {proc.stderr[-600:]!r}"
    )

    # Pure JSON on stdout — anything Rich on stdout breaks json.loads.
    data = json.loads(proc.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "image t2i"
    assert data["count"] == 1
    img = data["images"][0]
    assert img["media_name"], "media_name must be non-empty"
    assert isinstance(img["seed"], int), (
        f"images[0].seed must be int for worker refine-regen seed continuity; "
        f"got {type(img['seed']).__name__}"
    )
    saved = Path(img["local_path"])
    assert saved.exists() and saved.stat().st_size > 0, (
        f"local_path must point to a downloaded image of non-zero size; "
        f"got {saved!r} (exists={saved.exists()})"
    )
    # JPEG extension fix (#96/#103 upstream) — Flow returns JPEG bytes; the
    # CLI must save with the correct extension. The exact case differs by
    # platform; the magic-byte check below is authoritative.
    head = saved.read_bytes()[:12]
    assert head.startswith(b"\xff\xd8\xff") or head.startswith(b"\x89PNG"), (
        f"saved image is neither JPEG nor PNG (magic={head!r})"
    )


@pytest.mark.e2e_video
def test_e2e_video_t2v_json_shape(e2e_env: dict[str, str], tmp_path: Path) -> None:
    """`gflow video t2v --json` produces a pure-JSON document with the
    ``video_result`` schema (status / command / media_id / generation_status /
    succeeded / local_path / request) when run against real Flow. Costs 1
    Veo credit (cheapest: veo-lite, 8s).
    """
    # ``e2e_env`` already creates ``tmp_path/out`` (see tests/e2e/conftest.py).
    out_dir = tmp_path / "out"
    # Use GFLOW_CLI_E2E_VIDEO_RUN guard so callers can opt out of the video
    # spend even when they pass `-m e2e_video`. Default is OFF.
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip(
            "set GFLOW_CLI_E2E_RUN_VIDEO=1 to include the video --json e2e (spends 1 Veo credit)"
        )

    # Drop the autouse-isolated GFLOW_CLI_HOME so the subprocess resolves to
    # the user's real platformdirs path (where the live Chrome session lives).
    env = dict(e2e_env)
    env.pop("GFLOW_CLI_HOME", None)

    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "gflow_cli",
            "video",
            "t2v",
            "a golden sunset over mountains",
            "--model",
            "veo-lite",
            "--duration",
            "8",
            "--aspect",
            "9:16",
            "--out-dir",
            str(out_dir),
            "--json",
        ],
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=600,
    )
    assert proc.returncode == 0, (
        f"`video t2v --json` exited {proc.returncode}; stderr tail: {proc.stderr[-600:]!r}"
    )
    data = json.loads(proc.stdout)
    assert data["status"] == "ok"
    assert data["command"] == "video t2v"
    assert data["succeeded"] is True
    assert data["media_id"], "media_id must be non-empty"
    assert data["request"]["mode"] == "t2v"
    saved = Path(data["local_path"])
    assert saved.exists() and saved.stat().st_size > 0, (
        f"local_path must point to a downloaded mp4 of non-zero size; "
        f"got {saved!r} (exists={saved.exists()})"
    )
    head = saved.read_bytes()[:32]
    assert b"ftyp" in head, f"mp4 magic bytes not in first 32 bytes of {saved}: {head!r}"
