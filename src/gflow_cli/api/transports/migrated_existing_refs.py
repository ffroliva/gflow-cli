"""Referencing an image already in the project, on flow.google.com (#913, #546).

Moved out of ``migrated_composer.py`` (#1249 ratchet) so the #546 page-side heal could land
without growing that file; ``migrated_composer`` imports these under their old names.
"""

from __future__ import annotations

import asyncio
import unicodedata
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import structlog

from gflow_cli.errors import ReferenceNotFoundError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.image import ImageRef
    from gflow_cli.api.transports.migrated_composer import MigratedComposer

log = structlog.get_logger(__name__)

#: Flow writes captions with its own model; they reach the composer by typing.
_CAPTION_MAX = 120


def require_appended(before: list[dict[str, str]], chips: list[dict[str, str]]) -> None:
    """Refuse a mention that did not land after every chip already in the prompt (#997).

    A count cannot see a chip inserted mid-prompt; the earlier chips staying a prefix can.
    shortcut: chips that read identically (same caption and kind) cannot be told apart.
    Retryability is the class default, unmeasured for this condition (Bug Lane).
    """
    if chips[: len(before)] != before:
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: a reference attached out of order — it did not land after "
                f"the {len(before)} reference(s) already in the prompt, so the prompt is no "
                "longer the one requested. Refusing to submit"
            ),
            # The chip WAS found, so the class default ("not in the picker") would mislead.
            remediation_hint=(
                "The reference was found but attached out of order, so nothing was "
                "submitted. Re-run the command; if it repeats, report it with the "
                "number of references and your window size."
            ),
        )


def picker_query(ref: ImageRef) -> str:
    """The search text for an existing image: its Flow caption, only if safe to type.

    A newline would press Enter mid-query and ``@`` opens a nested mention. Rather than
    search a string other than the caption, refuse (SCENARIO #18, #19).
    """
    caption = ref.display_name
    if (
        not caption.strip()
        or "@" in caption
        # Control, format (zero-width, direction marks) and line/paragraph separators.
        or any(unicodedata.category(ch) in {"Cc", "Cf", "Zl", "Zp"} for ch in caption)
        or len(caption) > _CAPTION_MAX
    ):
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: image {ref.name} has no caption that can be searched "
                f"safely ({len(caption)} chars), so it cannot be referenced in place"
            ),
        )
    return caption.strip()


@runtime_checkable
class CaptionHealer(Protocol):
    """A resolver that can persist a caption found for a media id (``RefreshResolver``)."""

    def heal(self, media_id: str, name: str) -> None: ...


#: Each picker option as ``[token, title]``, in display order. One read, so a picker still
#: filling in cannot pair a caption with another image's token.
OPTION_PAIRS_JS = (
    "(sel) => [...document.querySelectorAll(sel)].map(o => {"
    " const img = o.querySelector('img');"
    " const m = ((img && img.getAttribute('src')) || '').match(/\\/asb\\/([A-Za-z0-9_-]+)/);"
    " const t = o.querySelector('.asset-title');"
    " return [m ? m[1] : '', ((t && t.textContent) || '').trim()]; })"
)


async def heal_catalog(
    name_resolver: object, refs: tuple[ImageRef, ...], stale: dict[str, str]
) -> None:
    """Tell the catalog the captions this run found on the page (#546).

    Only a resolver that is a :class:`CaptionHealer` (``refresh_resolver.RefreshResolver``)
    persists; it writes ``sync.source == "refresh"`` and honours ``history_prompts``. It is
    sync and opens the store, so it runs on a worker thread. A failed write never fails a
    run that attached: it is logged by media id only.
    """
    if not isinstance(name_resolver, CaptionHealer):
        return
    for ref in refs:
        if ref.display_name == stale.get(ref.name, ref.display_name):
            continue
        try:
            await asyncio.to_thread(name_resolver.heal, ref.name, ref.display_name)
        except Exception:  # noqa: BLE001 - the catalog write must never kill the generation
            log.warning("migrated.caption_heal_failed", media_id=ref.name)


async def refreshed_from_picker(
    composer: MigratedComposer, page: Page, refs: tuple[ImageRef, ...]
) -> tuple[ImageRef, ...] | None:
    """``refs`` with each caption replaced by the one the page shows now (#546).

    Measured on one profile (``docs/superpowers/spikes/2026-11-12-migrated-listing-captions.md``):
    with nothing typed, the ``@`` picker lists the project's most recent assets, and an
    option's title is its caption; the option is this image when its ``/asb/`` token is the
    grid tile's. A reference the page does not list, or whose shown caption cannot be typed
    safely, keeps its caption, and the caller's reload budget decides what happens next.

    ``None`` when no reference has a grid tile in this page load: nothing could be matched,
    so the caller should not spend its one look. A failed read is a hint that failed, never
    a reason to stop the run: it is logged and the references come back unchanged.
    """
    from gflow_cli.api.transports import migrated_composer as mc  # noqa: PLC0415 - cycle

    try:
        grid_js = mc._GRID_TOKEN_JS  # pyright: ignore[reportPrivateUsage]
        grid = {ref.name: str(await page.evaluate(grid_js, ref.name) or "") for ref in refs}
        if not any(grid.values()):
            return None
        await composer._caret_to_end(page)  # noqa: SLF001  # pyright: ignore[reportPrivateUsage]
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        pairs = await page.evaluate(OPTION_PAIRS_JS, mc.PICKER_OPTION)
        captions = {str(token): str(title) for token, title in pairs}
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001 - a failed look must not cost the reload that follows
        log.warning("migrated.caption_lookup_failed")
        return refs
    fresh: list[ImageRef] = []
    for ref in refs:
        name = captions.get(grid[ref.name], "").strip() if grid[ref.name] else ""
        candidate = replace(ref, display_name=name) if name and name != ref.display_name else ref
        try:
            picker_query(candidate)
        except ReferenceNotFoundError:
            candidate = ref  # the page shows a caption that cannot be typed safely
        if candidate is not ref:
            log.info("migrated.existing_reference_renamed", media_id=ref.name)
        fresh.append(candidate)
    return tuple(fresh)
