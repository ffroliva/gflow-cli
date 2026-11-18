"""Drive Flow's migrated ``flow.google.com`` editor to upscale generated images.

Google moved Flow from ``labs.google`` onto ``flow.google.com`` (#639). On that frontend,
upscaling is triggered through the image detail view download menu, which calls the
``SPrCad`` RPC over ``batchexecute`` and returns base64 image bytes directly.
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import TYPE_CHECKING, Any, cast

import structlog
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.api.image_upscale import TargetResolution
from gflow_cli.api.transports._common import raise_if_known_landing
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
    nothing_submitted,
)
from gflow_cli.paths import extension_from_magic

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Awaitable, Callable

    from playwright.async_api import Locator, Page, Response

log = structlog.get_logger(__name__)

UPSCALE_RPCID = "SPrCad"
_DEFAULT_TIMEOUT_S = 90.0
_MENU_TIMEOUT_MS = 5_000
_ITEM_SETTLE_MS = 1_500

# The download button in the image / video detail view (icon ligature, not a label).
DOWNLOAD_BUTTON_SELECTOR = (
    'button:has(mat-icon:text-is("download")), button:has(.google-symbols:text-is("download"))'
)


def missing_tile_hint(media_id: str, project_id: str) -> str:
    """A tile that is not in the project is likelier a wrong id or project than drift."""
    return (
        f"Check that media {media_id} belongs to project {project_id}: pass the owning "
        "project with --project (a catalog-resolved project may not hold it). If it does, "
        "Flow's page changed — file a bug at https://github.com/ffroliva/gflow-cli/issues."
    )


def menu_token_pattern(token: str) -> re.Pattern[str]:
    """Match ``token`` (``2K``, ``1080p``, …) as a whole token, case-insensitively.

    ``2K`` matches "2K (Aprimorada)" but not "12K"; ``1080p`` never matches "080p".
    """
    return re.compile(rf"(?<![0-9A-Za-z]){re.escape(token)}(?![0-9A-Za-z])", re.IGNORECASE)


#: A whole scale token (``270p``, ``2K``): what tells "Flow lists other scales" from drift.
#: Matched against the words of a label, so no lookaround is needed for token boundaries.
_SCALE = re.compile(r"\d+[pk]", re.IGNORECASE | re.ASCII)
_WORD_SPLIT = re.compile(r"[^\da-z]+", re.IGNORECASE | re.ASCII)


async def find_download_menu_item(
    page: Page, token: str, *, route: str, accepted: tuple[str, ...]
) -> Locator:
    """The open download menu's item for resolution ``token``, picked by that token.

    Measured 2026-10-07 (scripts/dev/spike_upscale_menu_anchors.py, en) against the
    contributor's 2026-09-30 pt capture: every item is a bare
    ``button[role=menuitem][mat-menu-item]`` with no per-option attribute or icon; the
    ORDER differs between the two (video 270p/720p/1080p vs 720p/1080p/270p) and the
    WORDS are translated ("2K (Aprimorada)", "Tamanho original"), but the resolution
    token itself is identical in en and pt. So the token is the only stable anchor —
    it is a format name, not a translated label. A present-but-disabled item is the
    caller's tier gate (exit 22).

    Flow decides the offered scales per media (#1338, measured 2026-11-18): a Veo 3.1
    Lite clip lists only 270p and 720p, an Omni 1.2 Flash clip 270p, 360p and 720p. So
    a token missing from a menu whose other items carry scale tokens is Flow not
    offering it (exit 22, naming what it does offer and which of those ``accepted``
    holds); a menu with no scale token at all, or one whose text does carry ``token``,
    means the anchor itself missed (exit 23).
    """
    items = page.locator('[role="menuitem"]')
    try:
        await items.first.wait_for(state="visible", timeout=_MENU_TIMEOUT_MS)
    except PlaywrightTimeoutError as exc:
        raise UiSelectorDriftError(
            detail=f"migrated upscale: the download menu did not open on {page.url}",
            route=route,
        ) from exc
    match = items.filter(has_text=menu_token_pattern(token))
    try:
        # A grace period before "not offered" (exit 22, do not retry) is concluded.
        await match.first.wait_for(state="visible", timeout=_ITEM_SETTLE_MS)
        return match.first
    except PlaywrightTimeoutError:
        pass
    texts = await items.all_inner_texts()
    offered = [w for t in texts for w in _WORD_SPLIT.split(t) if _SCALE.fullmatch(w)]
    if offered and token.lower() not in {o.lower() for o in offered}:
        usable = [o for o in offered if o.lower() in {a.lower() for a in accepted}]
        raise UpscaleUnavailableError(
            detail=(
                f"Flow does not offer {token} for this media: its download menu on "
                f"{page.url} lists {', '.join(offered)}."
            ),
            route=route,
            remediation_hint=(
                f"Flow decides which scales it offers for each item. Of those listed, "
                f"gflow can export: {', '.join(usable)}."
                if usable
                else "Flow decides which scales it offers for each item, and none of the "
                "listed ones is a scale gflow can export here."
            ),
        )
    raise UiSelectorDriftError(
        detail=f"migrated upscale: menu item for {token} was not found on {page.url}",
        route=route,
    )


async def is_menu_item_disabled(item: Locator) -> bool:
    return await item.is_disabled() or await item.get_attribute("aria-disabled") == "true"


async def wait_for_or_none(page: Page, selector: str, timeout_ms: int) -> Locator | None:
    """The first visible match of ``selector``, or ``None`` on a timeout.

    A Locator, never an ``ElementHandle`` (#957): Flow re-renders the grid and the detail
    view after they first appear, and a handle resolved once is then clicked detached —
    ``Element is not attached to the DOM``. A Locator re-resolves on every action.
    """
    target = page.locator(selector).first
    try:
        await target.wait_for(timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return None
    return target


async def click_download_button(page: Page, *, media_id: str, prefix: str, route: str) -> None:
    """Open the download menu of the media shown in the detail view."""
    download_btn = await wait_for_or_none(page, DOWNLOAD_BUTTON_SELECTOR, 15_000)
    if not download_btn:
        raise UiSelectorDriftError(
            detail=f"{prefix}: download button not found for media_id {media_id} on {page.url}",
            route=route,
        )
    await download_btn.click()
    await page.wait_for_timeout(500)


#: How long one grid load is watched for the tile before reloading it (#1067).
TILE_RELOAD_EVERY_MS = 10_000


async def open_tile_download_menu(
    page: Page,
    *,
    project_id: str,
    media_id: str,
    tile_selector: str,
    tile_timeout_ms: int,
    kind: str,
    prefix: str,
    route: str,
) -> None:
    """Open ``media_id``'s tile on the project grid, then its download menu."""
    await page.goto(
        MIGRATED_PROJECT_URL.format(project_id=project_id), wait_until="domcontentloaded"
    )
    left_ms = tile_timeout_ms - TILE_RELOAD_EVERY_MS
    tile = await wait_for_or_none(page, tile_selector, min(tile_timeout_ms, TILE_RELOAD_EVERY_MS))
    if not tile:
        # A --project Flow will not open lands on a known page, not drift (#1053).
        await raise_if_known_landing(page, requested=f"project {project_id}", at=route)
    while not tile and left_ms > 0:
        # #1067: the grid is a load-time listing, so a tile made seconds ago shows only
        # after a reload; waiting on the same load never gains it.
        log.info("migrated_upscale.tile_not_listed_reloading", kind=kind)
        await page.reload(wait_until="domcontentloaded")
        tile = await wait_for_or_none(page, tile_selector, min(left_ms, TILE_RELOAD_EVERY_MS))
        left_ms -= TILE_RELOAD_EVERY_MS
    if not tile:
        raise UiSelectorDriftError(
            detail=f"{prefix}: {kind} tile for media_id {media_id} not found on {page.url}",
            route=route,
            remediation_hint=missing_tile_hint(media_id, project_id),
        )
    await tile.click()
    await page.wait_for_timeout(1000)
    await click_download_button(page, media_id=media_id, prefix=prefix, route=route)


def _unavailable_error(target_resolution: TargetResolution) -> UpscaleUnavailableError:
    if target_resolution is TargetResolution.RES_4K:
        return UpscaleUnavailableError(
            detail=(
                "4K upscale requires a Flow Ultra subscription. "
                "Your account supports up to 2K (use --scale 2k)."
            ),
            route="upsampleImage",
            status=403,
        )
    return UpscaleUnavailableError(
        detail="2K upscale is not available on this account.",
        route="upsampleImage",
        status=403,
        remediation_hint=(
            "Flow offers no 2K upscale on this account's plan; check the options in "
            "Google Flow's download menu for this image."
        ),
    )


def _sprcad_b64(text: str) -> str | None:
    """The upscaled image's base64 in an ``SPrCad`` reply; raises on an RPC refusal."""
    for err in rpc_errors(text):
        if err.rpcid == UPSCALE_RPCID:
            raise WireFormatError(
                detail=f"SPrCad RPC refused: code={err.code} reasons={err.reasons}",
                route="image_upscale",
            )
    for rpcid, payload in parse_frames(text):
        if rpcid != UPSCALE_RPCID or not isinstance(payload, list):
            continue
        items = cast("list[Any]", payload)
        if len(items) >= 2 and isinstance(items[1], str) and items[1]:
            return items[1]
    return None


def _sprcad_listener(found_b64: asyncio.Future[str]) -> Callable[[Response], Awaitable[None]]:
    """A response listener that settles ``found_b64`` from the first ``SPrCad`` reply.

    An unreadable body is skipped, not a failure: Chrome evicted it on 6 of 12 measured
    upscales, and the page's own blob still carried the image (#1027).
    """

    async def on_response(response: Response) -> None:
        if "batchexecute" not in response.url or UPSCALE_RPCID not in response.url:
            return
        try:
            text = await response.text()
        except Exception as exc:  # noqa: BLE001 - evicted body (Network.getResponseBody)
            log.debug("migrated_upscale.body_unreadable", error=str(exc))
            return
        try:
            b64_val = _sprcad_b64(text)
        except WireFormatError as exc:
            settle_exception(found_b64, exc)
            return
        if b64_val and not found_b64.done():
            found_b64.set_result(b64_val)

    return on_response


async def install_capture_hooks(page: Page, expected_type: str) -> None:
    """Keep the blob Flow hands to its download link, and stop the link reaching Downloads.

    The page reads the reply with ``fetch``, so the blob survives when Chrome evicts the
    DevTools copy of the body (#1027). ``expected_type`` is matched against the blob MIME.
    """
    await page.evaluate(
        """(expected) => {
            window._gflowCapturedBase64 = null;
            window._capturing = true;
            window._gflowOrigCreateObjectURL = URL.createObjectURL;
            window._gflowOrigAnchorClick = HTMLAnchorElement.prototype.click;

            URL.createObjectURL = function(blob) {
                if (window._capturing && blob && blob.size > 1000) {
                    const mime = (blob.type || '').toLowerCase();
                    if (mime.includes(expected) || (expected === 'video' && mime.includes('mp4'))) {
                        const reader = new FileReader();
                        reader.onloadend = function() {
                            if (!window._capturing) return;  // a late read from a finished run
                            window._gflowCapturedBase64 = reader.result.split(',')[1];
                        };
                        reader.readAsDataURL(blob);
                    }
                }
                return window._gflowOrigCreateObjectURL.call(URL, blob);
            };

            HTMLAnchorElement.prototype.click = function() {};
        }""",
        expected_type,
    )


async def restore_capture_hooks(page: Page) -> None:
    try:
        await page.evaluate("""() => {
            window._capturing = false;
            delete window._gflowCapturedBase64;
            if (window._gflowOrigCreateObjectURL) {
                URL.createObjectURL = window._gflowOrigCreateObjectURL;
                delete window._gflowOrigCreateObjectURL;
            }
            if (window._gflowOrigAnchorClick) {
                HTMLAnchorElement.prototype.click = window._gflowOrigAnchorClick;
                delete window._gflowOrigAnchorClick;
            }
        }""")
    except Exception:  # noqa: BLE001 - best-effort restore on a page that may be gone
        pass


async def _await_image(page: Page, found_b64: asyncio.Future[str], timeout_s: float) -> str:
    """The ``SPrCad`` reply's image, or the page's blob of it when Chrome lost the body."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        if found_b64.done():
            return await found_b64  # re-raises an SPrCad refusal
        captured: str | None = await page.evaluate("() => window._gflowCapturedBase64")
        if captured:
            return captured
        await asyncio.sleep(min(0.5, max(deadline - loop.time(), 0)))
    raise TransportTimeoutError(
        detail=f"Timed out after {timeout_s}s waiting for {UPSCALE_RPCID} response from Flow",
        route="image_upscale",
    )


def settle_exception(future: asyncio.Future[Any], exc: BaseException) -> None:
    """Fail ``future`` with ``exc`` unless it already settled (first outcome wins)."""
    if not future.done():
        future.set_exception(exc)


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
    with nothing_submitted():  # #1320: the menu click below is the first send
        log.info("migrated_upscale.navigate", project_id=project_id, media_id=media_id)
        await open_tile_download_menu(
            page,
            project_id=project_id,
            media_id=media_id,
            tile_selector=f'img[data-media-id="{media_id}"]',
            tile_timeout_ms=30_000,
            kind="image",
            prefix="migrated upscale",
            route="image_upscale",
        )

        scale_label = "4K" if target_resolution is TargetResolution.RES_4K else "2K"
        btn_target = await find_download_menu_item(
            page, scale_label, route="image_upscale", accepted=("2K", "4K")
        )
        if await is_menu_item_disabled(btn_target):
            raise _unavailable_error(target_resolution)

    found_b64: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    on_response = _sprcad_listener(found_b64)
    page.on("response", on_response)
    try:
        await install_capture_hooks(page, "image")
        await btn_target.click()
        b64_data = await _await_image(page, found_b64, timeout_s)
    finally:
        page.remove_listener("response", on_response)
        if not found_b64.done():
            found_b64.cancel()
        elif not found_b64.cancelled():
            found_b64.exception()  # mark retrieved: a late refusal after a capture is moot
        await restore_capture_hooks(page)

    try:
        image_bytes = base64.b64decode(b64_data)
    except ValueError as exc:
        raise WireFormatError(
            detail="upsampleImage returned undecodable image data",
            route="upsampleImage",
        ) from exc

    if extension_from_magic(image_bytes) not in (".jpg", ".png"):
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
