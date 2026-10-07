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
from gflow_cli.api.transports.batchexecute import parse_frames, rpc_errors
from gflow_cli.api.transports.migrated_composer import MIGRATED_PROJECT_URL
from gflow_cli.errors import (
    TransportTimeoutError,
    UiSelectorDriftError,
    UpscaleUnavailableError,
    WireFormatError,
)
from gflow_cli.paths import extension_from_magic

if TYPE_CHECKING:  # pragma: no cover - typing only
    from collections.abc import Awaitable, Callable

    from playwright.async_api import ElementHandle, Locator, Page, Response

log = structlog.get_logger(__name__)

UPSCALE_RPCID = "SPrCad"
_DEFAULT_TIMEOUT_S = 90.0
_MENU_TIMEOUT_MS = 5_000

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


async def find_download_menu_item(page: Page, token: str, *, route: str) -> Locator:
    """The open download menu's item for resolution ``token``, picked by that token.

    Measured 2026-10-07 (scripts/dev/spike_upscale_menu_anchors.py, en) against the
    contributor's 2026-09-30 pt capture: every item is a bare
    ``button[role=menuitem][mat-menu-item]`` with no per-option attribute or icon; the
    ORDER differs between the two (video 270p/720p/1080p vs 720p/1080p/270p) and the
    WORDS are translated ("2K (Aprimorada)", "Tamanho original"), but the resolution
    token itself is identical in en and pt. So the token is the only stable anchor —
    it is a format name, not a translated label. A missing item is selector drift
    (exit 23); a present-but-disabled one is the caller's tier gate (exit 22).
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
    if await match.count() == 0:
        raise UiSelectorDriftError(
            detail=f"migrated upscale: menu item for {token} was not found on {page.url}",
            route=route,
        )
    return match.first


async def is_menu_item_disabled(item: Locator) -> bool:
    return await item.is_disabled() or await item.get_attribute("aria-disabled") == "true"


async def wait_for_or_none(page: Page, selector: str, timeout_ms: int) -> ElementHandle | None:
    """``page.wait_for_selector`` that answers ``None`` instead of raising on a timeout."""
    try:
        return await page.wait_for_selector(selector, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return None


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
    tile = await wait_for_or_none(page, tile_selector, tile_timeout_ms)
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
    """A response listener that settles ``found_b64`` from the first ``SPrCad`` reply."""

    async def on_response(response: Response) -> None:
        if "batchexecute" not in response.url or UPSCALE_RPCID not in response.url:
            return
        try:
            b64_val = _sprcad_b64(await response.text())
        except WireFormatError as exc:
            settle_exception(found_b64, exc)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("migrated_upscale.parse_error", error=str(exc))
            settle_exception(
                found_b64,
                WireFormatError(
                    detail=f"Failed to parse SPrCad response: {exc}", route="image_upscale"
                ),
            )
            return
        if b64_val and not found_b64.done():
            found_b64.set_result(b64_val)

    return on_response


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
    btn_target = await find_download_menu_item(page, scale_label, route="image_upscale")
    if await is_menu_item_disabled(btn_target):
        raise _unavailable_error(target_resolution)

    found_b64: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    on_response = _sprcad_listener(found_b64)
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
