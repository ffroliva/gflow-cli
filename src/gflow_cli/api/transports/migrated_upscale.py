"""Drive Flow's migrated ``flow.google.com`` editor to upscale generated images.

Google moved Flow from ``labs.google`` onto ``flow.google.com`` (#639). On that frontend,
upscaling is triggered through the image detail view download menu, which calls the
``SPrCad`` RPC over ``batchexecute`` and returns base64 image bytes directly.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.batchexecute import parse_frames
from gflow_cli.errors import UpscaleUnavailableError, WireFormatError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from pathlib import Path

    from playwright.async_api import Page, Response

log = structlog.get_logger(__name__)

MIGRATED_PROJECT_URL = "https://flow.google.com/project/{project_id}"
UPSCALE_RPCID = "SPrCad"
_DEFAULT_TIMEOUT_S = 90.0

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"


def _is_png_or_jpeg(data: bytes) -> bool:
    return data.startswith(_PNG_MAGIC) or data.startswith(_JPEG_MAGIC)


async def upscale_image_migrated(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    target_resolution: TargetResolution,
    out_path: Path,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> Path:
    """Upscale an image on the migrated ``flow.google.com`` frontend.

    Navigates to the project, selects the image tile by ``data-media-id``, opens
    the download menu, checks whether the requested scale is available on the
    current account tier, triggers the upscale, and decodes the resulting
    ``SPrCad`` payload.
    """
    project_url = MIGRATED_PROJECT_URL.format(project_id=project_id)
    log.info("migrated_upscale.navigate", project_id=project_id, media_id=media_id)
    await page.goto(project_url, wait_until="domcontentloaded")

    # Locate image tile by data-media-id with fallback
    img_sel = f'img[data-media-id="{media_id}"]'
    tile_img = await page.wait_for_selector(img_sel, timeout=30_000)
    if not tile_img:
        tile_img = await page.wait_for_selector("flow-image-tile img", timeout=5000)
    if not tile_img:
        raise WireFormatError(
            detail=f"Could not locate image tile for media_id {media_id}",
            route="image_upscale",
        )

    await tile_img.click()
    await page.wait_for_timeout(1000)

    # Click the download button in the image detail viewer
    download_btn = await page.wait_for_selector(
        'button:has(mat-icon:has-text("download")), '
        'button[aria-label*="download" i], '
        'button[aria-label*="baixar" i]',
        timeout=15_000,
    )
    if not download_btn:
        raise WireFormatError(
            detail="Download button not found in image detail view",
            route="image_upscale",
        )
    await download_btn.click()
    await page.wait_for_timeout(500)

    # Check 2K and 4K menu items availability
    btn_2k = await page.wait_for_selector('button[role="menuitem"]:has-text("2K")', timeout=5000)
    btn_4k = await page.wait_for_selector('button[role="menuitem"]:has-text("4K")', timeout=5000)

    can_2k = btn_2k is not None and not await btn_2k.is_disabled()
    can_4k = btn_4k is not None and not await btn_4k.is_disabled()

    if target_resolution is TargetResolution.RES_4K:
        if not can_4k:
            raise UpscaleUnavailableError(
                detail=(
                    "4K upscale requires a Flow Ultra subscription. "
                    "Your account supports up to 2K (use --scale 2k)."
                ),
                route="upsampleImage",
                status=403,
            )
        target_btn = btn_4k
    elif target_resolution is TargetResolution.RES_2K:
        if not can_2k:
            raise UpscaleUnavailableError(
                detail="2K upscale is not available on this account.",
                route="upsampleImage",
                status=403,
            )
        target_btn = btn_2k
    else:
        msg = f"Unsupported target resolution {target_resolution}"
        raise ValueError(msg)

    loop = asyncio.get_running_loop()
    found_b64: asyncio.Future[str] = loop.create_future()

    async def on_response(response: Response) -> None:
        if "batchexecute" in response.url and UPSCALE_RPCID in response.url:
            try:
                text = await response.text()
                frames = parse_frames(text)
                for rpcid, payload in frames:
                    if rpcid == UPSCALE_RPCID and isinstance(payload, list) and len(payload) >= 2:
                        b64_str = payload[1]
                        if isinstance(b64_str, str) and len(b64_str) > 0:
                            if not found_b64.done():
                                found_b64.set_result(b64_str)
            except Exception:  # noqa: BLE001
                pass

    page.on("response", on_response)
    try:
        # Prevent default blob download link click from navigating or closing page
        await page.evaluate("""() => {
            HTMLAnchorElement.prototype.click = function() {};
        }""")
        await target_btn.click()

        b64_data = await asyncio.wait_for(found_b64, timeout=timeout_s)
    finally:
        page.remove_listener("response", on_response)

    try:
        image_bytes = base64.b64decode(b64_data)
    except ValueError as exc:
        raise WireFormatError(
            detail="upsampleImage returned undecodable image data",
            route="upsampleImage",
        ) from exc

    if not _is_png_or_jpeg(image_bytes):
        raise WireFormatError(
            detail="upscaled output is not a valid PNG/JPEG",
            route="upsampleImage",
        )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(image_bytes)
    log.info(
        "migrated_upscale.completed",
        media_id=media_id,
        resolution=target_resolution.name,
        bytes=len(image_bytes),
        path=str(out_path),
    )
    return out_path
