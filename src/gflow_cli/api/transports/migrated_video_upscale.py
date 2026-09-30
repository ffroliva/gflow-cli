"""Drive Flow's migrated ``flow.google.com`` editor to upscale/export generated videos.

Google Flow provides video export/upscaling options in the video viewer menu:
- 1080p (Aprimorada - Full HD MP4)
- 720p (Tamanho original - Original MP4)
- 270p (GIF animado - Animated GIF)
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.errors import UpscaleUnavailableError, WireFormatError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from playwright.async_api import Page

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
_DEFAULT_TIMEOUT_S = 120.0
VALID_VIDEO_SCALES = ("1080p", "720p", "270p")


async def upscale_video_migrated(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    scale: str = "1080p",
    out_path: Path,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Path:
    """Upscale/export a video on the migrated ``flow.google.com`` frontend.

    Navigates to the project, selects the video tile, opens the download menu,
    clicks the requested quality option (1080p, 720p, or 270p), and captures
    the rendered blob stream.
    """
    scale_norm = scale.strip().lower()
    if scale_norm not in VALID_VIDEO_SCALES:
        msg = f"Unsupported video upscale scale {scale!r}. Choose from {VALID_VIDEO_SCALES}"
        raise ValueError(msg)

    edit_url = f"https://flow.google.com/project/{project_id}/edit/{media_id}"
    log.info(
        "migrated_video_upscale.navigate",
        project_id=project_id,
        media_id=media_id,
        scale=scale_norm,
    )
    await page.goto(edit_url, wait_until="domcontentloaded")

    download_btn = None
    try:
        download_btn = await page.wait_for_selector(
            'button:has(mat-icon:has-text("download")), '
            'button:has(.google-symbols:has-text("download"))',
            timeout=15_000,
        )
    except PlaywrightTimeoutError:
        pass

    if not download_btn:
        # Fallback: navigate to project gallery and select video tile scoped to media_id
        project_url = MIGRATED_PROJECT_URL.format(project_id=project_id)
        await page.goto(project_url, wait_until="domcontentloaded")
        tile_sel = f'img[data-media-id="{media_id}"], [data-media-id="{media_id}"]'
        try:
            video_tile = await page.wait_for_selector(tile_sel, timeout=15_000)
            if video_tile:
                await video_tile.click()
                await page.wait_for_timeout(1000)
                download_btn = await page.wait_for_selector(
                    'button:has(mat-icon:has-text("download")), '
                    'button:has(.google-symbols:has-text("download"))',
                    timeout=15_000,
                )
        except PlaywrightTimeoutError:
            pass

    if not download_btn:
        raise WireFormatError(
            detail=f"Download button not found for video media_id {media_id}",
            route="video_upscale",
        )
    await download_btn.click()
    await page.wait_for_timeout(500)

    # Locate scale menu item
    btn_target = None
    try:
        btn_target = await page.wait_for_selector(
            f'[role="menuitem"]:has-text("{scale_norm}")', timeout=5000
        )
    except PlaywrightTimeoutError:
        pass

    if not btn_target or await btn_target.is_disabled():
        raise UpscaleUnavailableError(
            detail=f"{scale_norm} video option is not available or disabled on this account.",
            route="video_upscale",
            status=403,
        )

    # Hook URL.createObjectURL and HTMLAnchorElement.prototype.click
    await page.evaluate("""() => {
        window._videoCapturedBase64 = null;
        window._capturing = true;
        window._origVideoCreateObjectURL = URL.createObjectURL;
        window._origVideoAnchorClick = HTMLAnchorElement.prototype.click;

        URL.createObjectURL = function(blob) {
            if (window._capturing) {
                const reader = new FileReader();
                reader.onloadend = function() {
                    window._videoCapturedBase64 = reader.result.split(',')[1];
                };
                reader.readAsDataURL(blob);
            }
            return window._origVideoCreateObjectURL.call(URL, blob);
        };

        HTMLAnchorElement.prototype.click = function() {};
    }""")

    try:
        await btn_target.click()

        # Poll for captured base64 data
        poll_interval = 1.0
        deadline = asyncio.get_running_loop().time() + timeout_s
        b64_data: str | None = None

        while asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(poll_interval)
            b64_data = await page.evaluate("() => window._videoCapturedBase64")
            if b64_data:
                break

        if not b64_data:
            raise WireFormatError(
                detail=(
                    f"Timed out waiting for {scale_norm} video stream from Flow after {timeout_s}s"
                ),
                route="video_upscale",
            )

        try:
            video_bytes = base64.b64decode(b64_data)
        except ValueError as exc:
            raise WireFormatError(
                detail="video upscale returned undecodable stream data",
                route="video_upscale",
            ) from exc

        # Validate magic bytes
        if scale_norm == "270p":
            if not video_bytes.startswith(b"GIF8"):
                raise WireFormatError(
                    detail="upscaled output is not a valid GIF",
                    route="video_upscale",
                )
        else:
            if len(video_bytes) < 8 or video_bytes[4:8] != b"ftyp":
                raise WireFormatError(
                    detail="upscaled output is not a valid MP4",
                    route="video_upscale",
                )

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(video_bytes)
        log.info(
            "migrated_video_upscale.completed",
            media_id=media_id,
            scale=scale_norm,
            bytes=len(video_bytes),
            path=str(out_path),
        )
        return out_path
    finally:
        try:
            await page.evaluate("""() => {
                window._capturing = false;
                if (window._origVideoCreateObjectURL) {
                    URL.createObjectURL = window._origVideoCreateObjectURL;
                    delete window._origVideoCreateObjectURL;
                }
                if (window._origVideoAnchorClick) {
                    HTMLAnchorElement.prototype.click = window._origVideoAnchorClick;
                    delete window._origVideoAnchorClick;
                }
            }""")
        except Exception:  # noqa: BLE001
            pass
