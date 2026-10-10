"""Live proof that image and video upscaling work on Flow's migrated host (#914, #880).

Spends zero credits for 2K image upscale (uses an already-generated image from the catalog).
The video 1080p arm is opt-in: `-m e2e_video` AND `GFLOW_CLI_E2E_RUN_VIDEO=1`, like every
other video e2e. Its export was measured free (1 observation, 2026-10-07: balance 700 ->
690 across the export plus a 10-credit veo-lite control), but one observation is not a
guarantee, so it stays behind the paid-video opt-in.

Run with:
    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e tests/e2e/test_migrated_upscale_e2e.py -v
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
import structlog
from PIL import Image

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.migrated_video_upscale import mp4_track_dimensions
from gflow_cli.config import get_settings
from gflow_cli.data.queries import list_images, list_videos

pytestmark = [pytest.mark.e2e]

#: 2K's nominal long side is 2048; a 1K source tops out well below this.
_2K_LONG_SIDE_FLOOR = 2000


def _events(capture: structlog.testing.LogCapture) -> list[str]:
    return [str(e["event"]) for e in capture.entries]


@pytest.fixture
def real_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the user's actual catalog, not the per-test isolated one."""
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()


@pytest.mark.e2e_image
@pytest.mark.asyncio
async def test_migrated_image_upscale_2k_e2e(
    e2e_profile_dir: Path,
    real_catalog: None,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Live proof: 2K image upscale on flow.google.com via SPrCad wire ($0)."""
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE required")

    db_path = get_settings().resolved_db_path()
    rows = list_images(db_path=db_path, profile=profile, limit=20, offset=0)
    usable = [r for r in rows if r.project_id]
    if not usable:
        pytest.skip(f"no catalogued images for profile {profile!r} to upscale")

    # The NEWEST row, not the first one with a file on disk: the on-disk preference
    # reached back to an image no longer on its project's grid and failed exit 23 on
    # untouched develop as well (A/B-controlled, 2026-10-10, #957). The size comparison
    # below still runs whenever this row's original happens to be on disk.
    row = usable[0]
    assert row.project_id is not None
    out_file = tmp_path / f"{row.media_id}_2k.jpg"

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        result = await client.upsample_image(
            media_id=row.media_id,
            project_id=row.project_id,
            target_resolution=TargetResolution.RES_2K,
            out_path=out_file,
        )

    saved_path = Path(str(result))
    assert saved_path.exists(), f"reported {saved_path} but nothing written"
    data = saved_path.read_bytes()
    assert len(data) > 100_000, f"implausibly small for 2K image: {len(data)} bytes"
    assert data[:8] == b"\x89PNG\r\n\x1a\n" or data[:3] == b"\xff\xd8\xff", (
        "output is not a valid PNG or JPEG"
    )
    with Image.open(saved_path) as im:
        out_w, out_h = im.size
    assert max(out_w, out_h) >= _2K_LONG_SIDE_FLOOR, f"not 2K: {out_w}x{out_h}"
    if row.local_path and Path(row.local_path).is_file():
        with Image.open(row.local_path) as src:
            src_w, src_h = src.size
        assert out_w > src_w and out_h > src_h, (
            f"upscale {out_w}x{out_h} is not larger than source {src_w}x{src_h}"
        )
    # flow.google.com was the host driven: the migrated transport ran end to end.
    seen = _events(install_log_capture)
    for required in ("migrated_upscale.navigate", "migrated_upscale.completed"):
        assert required in seen, f"{required} missing; events: {seen}"


@pytest.mark.e2e_image
@pytest.mark.asyncio
async def test_mcp_upscale_image_2k_e2e(
    e2e_profile_dir: Path,
    real_catalog: None,
    tmp_path: Path,
) -> None:
    """The MCP twin, run separately: ``gflow_upscale_image`` resolves the profile, finds
    the project and drives the same migrated transport ($0). The CLI run proves the
    service; only this proves the adapter an agent actually calls (#957)."""
    from gflow_cli.mcp import tools

    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE required")
    rows = list_images(
        db_path=get_settings().resolved_db_path(), profile=profile, limit=20, offset=0
    )
    row = next((r for r in rows if r.project_id), None)
    if row is None:
        pytest.skip(f"no catalogued images for profile {profile!r} to upscale")

    result: dict[str, Any] = await tools.gflow_upscale_image(
        media_id=row.media_id, scale="2k", out_dir=str(tmp_path), profile=profile
    )

    assert result.get("status") == "ok", f"MCP upscale failed: {result}"
    saved = Path(result["path"])
    assert saved.is_file(), f"reported {saved} but nothing written"
    with Image.open(saved) as im:
        assert max(im.size) >= _2K_LONG_SIDE_FLOOR, f"not 2K: {im.size}"


@pytest.mark.e2e_video
@pytest.mark.asyncio
async def test_migrated_video_upscale_1080p_e2e(
    e2e_profile_dir: Path,
    real_catalog: None,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Live proof: 1080p video upscale on flow.google.com."""
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to include the 1080p video export e2e")
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE required")

    db_path = get_settings().resolved_db_path()
    rows = list_videos(db_path=db_path, profile=profile, limit=20, offset=0)
    usable = [r for r in rows if r.project_id]
    if not usable:
        pytest.skip(f"no catalogued videos for profile {profile!r} to upscale")

    row = usable[0]
    assert row.project_id is not None
    out_file = tmp_path / f"{row.media_id}_1080p.mp4"

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        result = await client.upsample_video(
            media_id=row.media_id,
            project_id=row.project_id,
            scale="1080p",
            out_path=out_file,
        )

    saved_path = Path(str(result))
    assert saved_path.exists(), f"reported {saved_path} but nothing written"
    data = saved_path.read_bytes()
    assert len(data) > 100_000, f"implausibly small for 1080p video: {len(data)} bytes"
    assert data[4:8] == b"ftyp", "output is not a valid MP4"
    dims = mp4_track_dimensions(data)
    assert dims is not None and min(dims) >= 1080, f"not 1080p: track {dims}"
    seen = _events(install_log_capture)
    for required in ("migrated_video_upscale.navigate", "migrated_video_upscale.completed"):
        assert required in seen, f"{required} missing; events: {seen}"


@pytest.mark.e2e_video
@pytest.mark.asyncio
async def test_migrated_video_export_270p_gif_e2e(
    e2e_profile_dir: Path,
    real_catalog: None,
    tmp_path: Path,
    install_log_capture: structlog.testing.LogCapture,
) -> None:
    """Live proof: the 270p animated-GIF export lands within its longer budget.

    Flow renders the GIF client-side with a variable delay (40 s to past 120 s measured on
    2026-10-07), which is why this arm exists apart from the 1080p one.
    """
    if os.environ.get("GFLOW_CLI_E2E_RUN_VIDEO", "") != "1":
        pytest.skip("set GFLOW_CLI_E2E_RUN_VIDEO=1 to include the 270p GIF export e2e")
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE required")

    db_path = get_settings().resolved_db_path()
    usable = [
        r for r in list_videos(db_path=db_path, profile=profile, limit=20, offset=0) if r.project_id
    ]
    if not usable:
        pytest.skip(f"no catalogued videos for profile {profile!r} to export")
    row = usable[0]
    assert row.project_id is not None

    async with FlowApiClient(profile_dir=e2e_profile_dir) as client:
        result = await client.upsample_video(
            media_id=row.media_id,
            project_id=row.project_id,
            scale="270p",
            out_path=tmp_path / f"{row.media_id}_270p.gif",
        )

    data = Path(str(result)).read_bytes()
    assert data[:4] == b"GIF8", "output is not a GIF"
    assert len(data) > 100_000, f"implausibly small for an animated GIF: {len(data)} bytes"
    seen = _events(install_log_capture)
    assert "migrated_video_upscale.completed" in seen, f"events: {seen}"
