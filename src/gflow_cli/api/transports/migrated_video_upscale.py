"""Drive Flow's migrated ``flow.google.com`` editor to upscale/export generated videos.

Flow's video viewer download menu offers 270p (animated GIF), 720p (the original MP4)
and 1080p (the upscaled MP4); 4K is listed but disabled on the account measured.
"""

from __future__ import annotations

import asyncio
import base64
import struct
from typing import TYPE_CHECKING

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.transports.batchexecute import rpc_errors
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL
from gflow_cli.api.transports.migrated_recover import MIGRATED_CLIP_URL
from gflow_cli.api.transports.migrated_upscale import (
    DOWNLOAD_BUTTON_SELECTOR,
    find_download_menu_item,
    is_menu_item_disabled,
    missing_tile_hint,
)
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from playwright.async_api import ElementHandle, Page, Response

log = structlog.get_logger(__name__)

_DEFAULT_TIMEOUT_S = 120.0
VALID_VIDEO_SCALES = ("1080p", "720p", "270p")
_EXPORT_RPCIDS = ("p0UkFb", "jwpduf")
#: Minimum SHORT side of the exported track per MP4 scale. The short side is what
#: "1080p" names in either orientation, and it is what tells the upscale (1920x1080)
#: apart from the 720p original (1280x720), whose long side would clear a 1080 bar.
_MIN_SHORT_SIDE = {"1080p": 1080, "720p": 720}
_CONTAINER_BOXES = (b"moov", b"trak")
_TKHD_V0_PAYLOAD = 84


def mp4_track_dimensions(data: bytes) -> tuple[int, int] | None:
    """``(width, height)`` of the largest track in an MP4's ``moov/trak/tkhd`` boxes.

    tkhd ends with width and height as 16.16 fixed point in its last 8 bytes, for both
    version 0 and version 1 (version 1 only widens the leading time fields). Audio
    tracks carry 0x0, so the largest track is the video. ``None`` if none is readable.
    """
    best: tuple[int, int] | None = None

    def walk(start: int, end: int) -> None:
        nonlocal best
        pos = start
        while pos + 8 <= end:
            size, kind = struct.unpack_from(">I4s", data, pos)
            header = 8
            if size == 1 and pos + 16 <= end:
                size = struct.unpack_from(">Q", data, pos + 8)[0]
                header = 16
            elif size == 0:
                size = end - pos
            if size < header or pos + size > end:
                return
            if kind in _CONTAINER_BOXES:
                walk(pos + header, pos + size)
            elif kind == b"tkhd" and size - header >= _TKHD_V0_PAYLOAD:
                w, h = struct.unpack_from(">II", data, pos + size - 8)
                dims = (w >> 16, h >> 16)
                if best is None or dims[0] * dims[1] > best[0] * best[1]:
                    best = dims
            pos += size

    walk(0, len(data))
    return best


#: The export finished but its bytes are not the file asked for; a retry is free (the
#: 1080p export measured 0 credits, 2026-10-07) and the prompt is irrelevant here.
_EXPORT_HINT = (
    "Re-run the export; it spends no credits. If it keeps failing, Flow changed how it "
    "delivers exports — file a bug at https://github.com/ffroliva/gflow-cli/issues."
)


def _check_mp4_resolution(video_bytes: bytes, scale: str) -> None:
    """Refuse an MP4 smaller than ``scale`` — a preview or the 720p original."""
    dims = mp4_track_dimensions(video_bytes)
    if dims is None:
        raise WireFormatError(
            detail=f"{scale} export: could not read the MP4's track dimensions (no tkhd box)",
            route="video_upscale",
            remediation_hint=_EXPORT_HINT,
        )
    if min(dims) < _MIN_SHORT_SIDE[scale]:
        raise WireFormatError(
            detail=(
                f"{scale} export returned a {dims[0]}x{dims[1]} MP4, smaller than {scale} "
                "(a preview or the original was captured, not the export)"
            ),
            route="video_upscale",
            remediation_hint=_EXPORT_HINT,
        )


async def _wait_for(page: Page, selector: str, timeout_ms: int) -> ElementHandle | None:
    try:
        return await page.wait_for_selector(selector, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return None


async def _open_download_menu(page: Page, *, project_id: str, media_id: str) -> None:
    """Open the clip's download menu: the clip editor first, the project grid second."""
    await page.goto(
        MIGRATED_CLIP_URL.format(project_id=project_id, media_id=media_id),
        wait_until="domcontentloaded",
    )
    download_btn = await _wait_for(page, DOWNLOAD_BUTTON_SELECTOR, 15_000)

    if not download_btn:
        await page.goto(
            MIGRATED_PROJECT_URL.format(project_id=project_id), wait_until="domcontentloaded"
        )
        tile_sel = f'img[data-media-id="{media_id}"], [data-media-id="{media_id}"]'
        video_tile = await _wait_for(page, tile_sel, 15_000)
        if not video_tile:
            raise UiSelectorDriftError(
                detail=(
                    f"migrated video upscale: video tile for media_id {media_id} not found "
                    f"on {page.url}"
                ),
                route="video_upscale",
                remediation_hint=missing_tile_hint(media_id, project_id),
            )
        await video_tile.click()
        await page.wait_for_timeout(1000)
        download_btn = await _wait_for(page, DOWNLOAD_BUTTON_SELECTOR, 15_000)

    if not download_btn:
        raise UiSelectorDriftError(
            detail=(
                f"migrated video upscale: download button not found for media_id {media_id} "
                f"on {page.url}"
            ),
            route="video_upscale",
        )
    await download_btn.click()
    await page.wait_for_timeout(500)


async def upscale_video_migrated(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    scale: str = "1080p",
    timeout_s: float = _DEFAULT_TIMEOUT_S,
) -> bytes:
    """Upscale/export a video on the migrated ``flow.google.com`` frontend.

    Opens the clip's download menu, clicks the requested quality option (1080p, 720p,
    or 270p), and captures the rendered blob. An MP4 is checked to really be at least
    the requested resolution (``tkhd``), so a preview or the 720p original is refused.

    Returns:
        Raw video (MP4 or GIF) bytes.
    """
    scale_norm = scale.strip().lower()
    if scale_norm not in VALID_VIDEO_SCALES:
        msg = f"Unsupported video upscale scale {scale!r}. Choose from {VALID_VIDEO_SCALES}"
        raise ValueError(msg)

    log.info(
        "migrated_video_upscale.navigate",
        project_id=project_id,
        media_id=media_id,
        scale=scale_norm,
    )
    await _open_download_menu(page, project_id=project_id, media_id=media_id)

    btn_target = await find_download_menu_item(page, scale_norm, route="video_upscale")
    if await is_menu_item_disabled(btn_target):
        raise UpscaleUnavailableError(
            detail=f"{scale_norm} video option is not available or disabled on this account.",
            route="video_upscale",
            status=403,
            remediation_hint=(
                f"{scale_norm} video export is disabled on your account plan. "
                "Check available options in Google Flow."
            ),
        )

    loop = asyncio.get_running_loop()
    failed: asyncio.Future[None] = loop.create_future()

    def _fail(exc: WireFormatError) -> None:
        if not failed.done():
            failed.set_exception(exc)

    async def on_response(response: Response) -> None:
        if "batchexecute" not in response.url:
            return
        try:
            text = await response.text()
            for err in rpc_errors(text):
                if err.rpcid in _EXPORT_RPCIDS:
                    _fail(
                        WireFormatError(
                            detail=(
                                f"Video export RPC {err.rpcid} refused: code={err.code} "
                                f"reasons={err.reasons}"
                            ),
                            route="video_upscale",
                        )
                    )
        except Exception as exc:  # noqa: BLE001 - any unreadable reply fails the export
            log.warning("migrated_video_upscale.parse_error", error=str(exc))
            _fail(
                WireFormatError(
                    detail=f"Failed to read a video export response: {exc}",
                    route="video_upscale",
                )
            )

    try:
        page.on("response", on_response)

        # Hook URL.createObjectURL and HTMLAnchorElement.prototype.click
        expected_type = "gif" if scale_norm == "270p" else "video"
        await page.evaluate(
            """(expected) => {
            window._videoCapturedBase64 = null;
            window._capturing = true;
            window._origVideoCreateObjectURL = URL.createObjectURL;
            window._origVideoAnchorClick = HTMLAnchorElement.prototype.click;

            URL.createObjectURL = function(blob) {
                if (window._capturing && blob && blob.size > 1000) {
                    const mime = (blob.type || '').toLowerCase();
                    if (mime.includes(expected) || (expected === 'video' && mime.includes('mp4'))) {
                        const reader = new FileReader();
                        reader.onloadend = function() {
                            window._videoCapturedBase64 = reader.result.split(',')[1];
                        };
                        reader.readAsDataURL(blob);
                    }
                }
                return window._origVideoCreateObjectURL.call(URL, blob);
            };

            HTMLAnchorElement.prototype.click = function() {};
        }""",
            expected_type,
        )

        await btn_target.click()

        poll_interval = 1.0
        deadline = loop.time() + timeout_s
        b64_data: str | None = None
        while loop.time() < deadline:
            if failed.done():
                await failed  # re-raises the export refusal / unreadable reply
            await asyncio.sleep(poll_interval)
            b64_data = await page.evaluate("() => window._videoCapturedBase64")
            if b64_data:
                break
        else:
            raise TransportTimeoutError(
                detail=(
                    f"Timed out waiting for {scale_norm} video stream from Flow after {timeout_s}s"
                ),
                route="video_upscale",
            )

        try:
            video_bytes = base64.b64decode(b64_data, validate=True)
        except ValueError as exc:
            raise WireFormatError(
                detail="video upscale returned undecodable stream data",
                route="video_upscale",
                remediation_hint=_EXPORT_HINT,
            ) from exc

        if scale_norm == "270p":
            if not video_bytes.startswith(b"GIF8"):
                raise WireFormatError(
                    detail="upscaled output is not a valid GIF",
                    route="video_upscale",
                    remediation_hint=_EXPORT_HINT,
                )
        else:
            if len(video_bytes) < 8 or video_bytes[4:8] != b"ftyp":
                raise WireFormatError(
                    detail="upscaled output is not a valid MP4",
                    route="video_upscale",
                    remediation_hint=_EXPORT_HINT,
                )
            _check_mp4_resolution(video_bytes, scale_norm)

        log.info(
            "migrated_video_upscale.completed",
            media_id=media_id,
            scale=scale_norm,
            bytes=len(video_bytes),
        )
        return video_bytes
    finally:
        page.remove_listener("response", on_response)
        if not failed.done():
            failed.cancel()
        elif not failed.cancelled():
            failed.exception()  # mark retrieved: a late refusal after a capture is moot
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
        except Exception:  # noqa: BLE001 - best-effort restore on a page that may be gone
            pass
