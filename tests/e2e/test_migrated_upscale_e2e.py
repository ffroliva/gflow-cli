"""Live proof that image and video upscaling work on Flow's migrated host (#914, #880).

Spends zero credits for 2K image upscale (uses an already-generated image from the catalog).
The video 1080p arm is opt-in via `-m e2e_video`.

Run with:
    GFLOW_CLI_E2E_PROFILE=<profile> uv run pytest -m e2e tests/e2e/test_migrated_upscale_e2e.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.config import get_settings
from gflow_cli.data.queries import list_images, list_videos

pytestmark = [pytest.mark.e2e]


@pytest.fixture
def real_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the user's actual catalog, not the per-test isolated one."""
    from gflow_cli.config import reset_settings

    monkeypatch.delenv("GFLOW_CLI_DB_PATH", raising=False)
    reset_settings()


@pytest.mark.e2e_image
@pytest.mark.asyncio
async def test_migrated_image_upscale_2k_e2e(
    e2e_profile_dir: Path, real_catalog: None, tmp_path: Path
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
    assert data[:8] == b"\x89PNG\r\n\x1a\n" or data[:2] == b"\xff\xd8", (
        "output is not a valid PNG or JPEG"
    )


@pytest.mark.e2e_video
@pytest.mark.asyncio
async def test_migrated_video_upscale_1080p_e2e(
    e2e_profile_dir: Path, real_catalog: None, tmp_path: Path
) -> None:
    """Live proof: 1080p video upscale on flow.google.com."""
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
