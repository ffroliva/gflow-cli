"""Drive Flow's migrated ``flow.google.com`` editor to upscale generated images.

Google moved Flow from ``labs.google`` onto ``flow.google.com`` (#639). On that frontend,
upscaling is triggered through the image detail view download menu, which calls the
``SPrCad`` RPC over ``batchexecute`` and returns base64 image bytes directly.
"""

from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import Page, Response

log = structlog.get_logger(__name__)

UPSCALE_RPCID = "SPrCad"
_DEFAULT_TIMEOUT_S = 90.0

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8"


def is_png_or_jpeg(data: bytes) -> bool:
    """True if data begins with a PNG or JPEG magic-byte signature."""
    return data.startswith(_PNG_MAGIC) or data.startswith(_JPEG_MAGIC)


async def upscale_image_migrated(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    target_resolution: TargetResolution,
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> bytes:
    """Upscale an image on the migrated ``flow.google.com`` frontend.

    Navigates to the project, selects the image tile by ``data-media-id``, opens
    the download menu, checks whether the requested scale is available on the
    current account tier, triggers the upscale, and decodes the resulting
    ``SPrCad`` payload into raw image bytes.

    Returns:
        Raw decoded JPEG/PNG bytes.
    """
    project_url = MIGRATED_PROJECT_URL.format(project_id=project_id)
    log.info("migrated_upscale.navigate", project_id=project_id, media_id=media_id)
    await page.goto(project_url, wait_until="domcontentloaded")

    # Locate image tile strictly matching media_id
    img_sel = f'img[data-media-id="{media_id}"]'
    try:
        tile_img = await page.wait_for_selector(img_sel, timeout=30_000)
    except PlaywrightTimeoutError as exc:
        raise WireFormatError(
            detail=f"Could not locate image tile for media_id {media_id}",
            route="image_upscale",
        ) from exc
    if not tile_img:
        raise WireFormatError(
            detail=f"Could not locate image tile for media_id {media_id}",
            route="image_upscale",
        )

    await tile_img.click()
    await page.wait_for_timeout(1000)

    # Click the download button in the image detail viewer (exact icon match)
    try:
        download_btn = await page.wait_for_selector(
            'button:has(mat-icon:text-is("download")), '
            'button:has(.google-symbols:text-is("download"))',
            timeout=15_000,
        )
    except PlaywrightTimeoutError as exc:
        raise WireFormatError(
            detail="Download button not found in image detail view",
            route="image_upscale",
        ) from exc
    if not download_btn:
        raise WireFormatError(
            detail="Download button not found in image detail view",
            route="image_upscale",
        )
    await download_btn.click()
    await page.wait_for_timeout(500)

    # Check 2K and 4K menu items availability
    scale_label = "4K" if target_resolution is TargetResolution.RES_4K else "2K"
    btn_target = None
    try:
        btn_target = await page.wait_for_selector(
            f'[role="menuitem"]:has-text("{scale_label}")', timeout=5000
        )
    except PlaywrightTimeoutError as exc:
        raise UiSelectorDriftError(
            detail=f"migrated upscale: menu item for {scale_label} was not found on {page.url}",
            route="image_upscale",
        ) from exc

    if btn_target is None:
        raise UiSelectorDriftError(
            detail=f"migrated upscale: menu item for {scale_label} was not found on {page.url}",
            route="image_upscale",
        )

    is_disabled = await btn_target.is_disabled() or (
        await btn_target.get_attribute("aria-disabled") == "true"
    )
    if is_disabled:
        if target_resolution is TargetResolution.RES_4K:
            raise UpscaleUnavailableError(
                detail=(
                    "4K upscale requires a Flow Ultra subscription. "
                    "Your account supports up to 2K (use --scale 2k)."
                ),
                route="upsampleImage",
                status=403,
            )
        raise UpscaleUnavailableError(
            detail=f"{scale_label} upscale is not available on this account.",
            route="upsampleImage",
            status=403,
        )

    loop = asyncio.get_running_loop()
    found_b64: asyncio.Future[str] = loop.create_future()

    async def on_response(response: Response) -> None:
        if "batchexecute" in response.url and UPSCALE_RPCID in response.url:
            try:
                text = await response.text()
                errors = rpc_errors(text)
                if any(e.rpcid == UPSCALE_RPCID for e in errors):
                    err = next(e for e in errors if e.rpcid == UPSCALE_RPCID)
                    if not found_b64.done():
                        found_b64.set_exception(
                            WireFormatError(
                                detail=f"SPrCad RPC refused: code={err.code} reasons={err.reasons}",
                                route="image_upscale",
                            )
                        )
                    return
                frames = parse_frames(text)
                for rpcid, payload in frames:
                    if rpcid == UPSCALE_RPCID and isinstance(payload, list):
                        items = cast("list[Any]", payload)
                        if len(items) >= 2:
                            b64_val = items[1]
                            if isinstance(b64_val, str) and len(b64_val) > 0:
                                if not found_b64.done():
                                    found_b64.set_result(b64_val)
            except Exception as exc:  # noqa: BLE001
                log.warning("migrated_upscale.parse_error", error=str(exc))
                if not found_b64.done():
                    found_b64.set_exception(
                        WireFormatError(
                            detail=f"Failed to parse SPrCad response: {exc}",
                            route="image_upscale",
                        )
                    )

    page.on("response", on_response)
    try:
        # Override anchor click and preserve original to restore later
        await page.evaluate("""() => {
            window._origAnchorClick = HTMLAnchorElement.prototype.click;
            HTMLAnchorElement.prototype.click = function() {};
        }""")
        await btn_target.click()

        try:
            b64_data = await asyncio.wait_for(found_b64, timeout=timeout_s)
        except TimeoutError as exc:
            raise TransportTimeoutError(
                detail=(
                    f"Timed out after {timeout_s}s waiting for {UPSCALE_RPCID} response from Flow"
                ),
                route="image_upscale",
            ) from exc
    finally:
        page.remove_listener("response", on_response)
        try:
            await page.evaluate("""() => {
                if (window._origAnchorClick) {
                    HTMLAnchorElement.prototype.click = window._origAnchorClick;
                    delete window._origAnchorClick;
                }
            }""")
        except Exception:  # noqa: BLE001
            pass

    try:
        image_bytes = base64.b64decode(b64_data)
    except ValueError as exc:
        raise WireFormatError(
            detail="upsampleImage returned undecodable image data",
            route="upsampleImage",
        ) from exc

    if not is_png_or_jpeg(image_bytes):
        raise WireFormatError(
            detail="upscaled output is not a valid PNG/JPEG",
            route="upsampleImage",
        )

    log.info(
        "migrated_upscale.completed",
        media_id=media_id,
        resolution=target_resolution.name,
        bytes=len(image_bytes),
    )
    return image_bytes
