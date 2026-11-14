"""Existing reference attachment, character mentions and chips.

A part of `MigratedComposer`, moved byte-identical from
`migrated_composer.py` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.image import ImageRef
from gflow_cli.api.transports._mc_common import (
    _CARET_TO_END_JS,
    _GRID_TOKEN_JS,
    _OPTION_TITLES_JS,
    _OPTION_TOKENS_JS,
    ARROW_SETTLE_MS,
    ARROW_STEP_MS,
    COMPOSER,
    EXISTING_REF_RELOAD_PAUSE_S,
    EXISTING_REF_WAIT_S,
    FRAME_SEARCH_ATTEMPTS,
    FRAME_SEARCH_RETRY_PAUSE_S,
    MENTION_CHIP,
    PICKER_OPTION,
)
from gflow_cli.api.transports._mc_upload_frame import UploadFrameMixin
from gflow_cli.api.transports.migrated_existing_refs import (
    heal_catalog as _heal_catalog,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    picker_query as _picker_query,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    refreshed_from_picker,
)
from gflow_cli.api.transports.migrated_existing_refs import (
    require_appended as _require_appended,
)
from gflow_cli.errors import ReferenceNotFoundError

if TYPE_CHECKING:
    from playwright.async_api import Page

    from gflow_cli.api.image import GenerateImageRequest

log = structlog.get_logger(__name__)


class MentionMixin(UploadFrameMixin):
    """Existing reference attachment, character mentions and chips."""

    async def reference_existing(
        self,
        page: Page,
        project_id: str,
        request: GenerateImageRequest,
        *,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> tuple[str, ...]:
        """Apply the image settings and mention ``request.refs`` in place, reloading on a miss.

        A just-generated image can be missing from this page load's grid or picker
        search and present after a reload (measured twice, live e2e 2026-10-01). A reload
        resets the settings and the composer, so each attempt redoes all three steps.

        #546 on this host: the picker is searched by the catalog's caption, which goes
        stale when the image is renamed in Flow. The labs listing the resolver reads is
        retired here, so the first miss with a grid tile reads the current captions from
        the page itself (``refreshed_from_picker``) and the reload that follows retries
        with them; the reload also clears the stale ``@query`` text. A caption found this
        way is handed to ``name_resolver`` after the attach, when it can persist one.
        Cached name = optimization; listing = truth; UUID = identity.
        """
        refs = request.refs
        for ref in refs:
            _picker_query(ref)  # an unusable caption is refused before any reload
        stale = {ref.name: ref.display_name for ref in refs}
        deadline = time.monotonic() + EXISTING_REF_WAIT_S
        look_up = True
        while True:
            try:
                tokens = await self.await_existing_references(page, refs)
                await self.apply_image_settings(page, request)
                media_ids = await self.attach_existing_references(page, refs, tokens)
                await _heal_catalog(name_resolver, refs, stale)
                return media_ids
            except ReferenceNotFoundError:
                if look_up:  # one look, spent only when a tile could be matched
                    found = await refreshed_from_picker(self, page, refs)  # pyright: ignore[reportArgumentType]
                    look_up, refs = found is None, found or refs
                if time.monotonic() >= deadline:
                    raise
            log.info("migrated.existing_reference_reload")
            await page.wait_for_timeout(EXISTING_REF_RELOAD_PAUSE_S * 1000)
            await page.reload(wait_until="domcontentloaded", timeout=45_000)
            if await self.ensure_editor(page, project_id) == "agent_only":
                from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415

                raise agent.refuse_unported("image-to-image")

    async def await_existing_references(
        self, page: Page, refs: tuple[ImageRef, ...]
    ) -> dict[str, str]:
        """Each reference's grid thumbnail token in this page load; a missing one raises.

        Measured (live e2e, 2026-10-01): the project grid is the asset list fetched when
        the editor loads, and it is not updated in place. A row that opened the editor a
        second after its parent was generated polled for 30 s without the tile; the next
        row, after a reload, found it at once. :meth:`reference_existing` reloads.
        """
        tokens = {
            ref.name: str(await page.evaluate(_GRID_TOKEN_JS, ref.name) or "") for ref in refs
        }
        missing = [media_id for media_id, token in tokens.items() if not token]
        if missing:
            log.info("migrated.existing_reference_not_listed", missing=len(missing))
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: image {missing[0]} is not in this project's grid, so "
                    "it cannot be referenced in place"
                ),
            )
        return tokens

    async def attach_existing_references(
        self, page: Page, refs: tuple[ImageRef, ...], tokens: dict[str, str]
    ) -> tuple[str, ...]:
        """Mention images already in the project, chosen by identity; upload nothing.

        Measured (docs/superpowers/spikes/2026-10-01-batch-ref-dropped.md, Gate): the
        ``@`` picker is project-scoped and lists matches by Flow's caption, but captions
        collide and the older match comes first. Each option's thumbnail is
        ``/asb/<token>``; the project grid tile ``img[data-media-id=<uuid>]`` carries the
        same token. So: search by caption, select the option whose token is the
        reference's, and confirm a chip landed. ``_image_body_problem`` still checks the
        submit carries the ids.
        """
        for count, ref in enumerate(refs, start=1):
            query = _picker_query(ref)
            await self._mention_by_token(
                page, query, tokens[ref.name], ref.name, expect_chips=count
            )
        media_ids = tuple(ref.name for ref in refs)
        # Distinct from `migrated.references_attached` (the upload path): this one means
        # nothing was uploaded.
        log.info("migrated.existing_references_attached", count=len(refs), media_ids=media_ids)
        return media_ids

    async def _mention_by_token(
        self, page: Page, query: str, token: str, media_id: str, *, expect_chips: int
    ) -> None:
        """One search in this page load; a miss closes the picker and raises.

        Measured (live e2e, 2026-10-01): when the picker does not offer a just-generated
        image, searching again in the same page load does not help (0 options three times
        over 30 s), while a fresh editor load offers it at once. So retrying belongs to
        :meth:`reference_existing`, which reloads.
        """
        before = await self._caret_to_end(page)
        await page.keyboard.type("@", delay=120)
        await page.wait_for_timeout(2200)
        await page.keyboard.type(query, delay=100)
        await page.wait_for_timeout(2500)
        tokens = [str(t) for t in await page.evaluate(_OPTION_TOKENS_JS, PICKER_OPTION)]
        if token in tokens:
            # The option's position: >0 means another image shares the caption (#21).
            log.info("migrated.existing_reference_option", option_index=tokens.index(token))
            for _ in range(tokens.index(token)):
                await page.keyboard.press("ArrowDown")
                await page.wait_for_timeout(ARROW_SETTLE_MS)
            await page.keyboard.press("Enter")
            await page.wait_for_timeout(2500)
            chips = await self.read_chips(page)
            # A query can also match a character; only a media chip is this image.
            if len(chips) == expect_chips and chips[-1].get("reference_type") == "media":
                _require_appended(before, chips)
                await page.keyboard.type(" ", delay=80)
                return
        else:
            log.info("migrated.mention_miss", offered=len(tokens), by="token")
            # The picker is a dialog over the composer (measured, gate #39): close it.
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: image {media_id} did not attach as a reference "
                f"({len(tokens)} picker option(s) offered for its caption, none of them it)"
            ),
        )

    async def attach_references(
        self, page: Page, project_id: str, paths: tuple[Path, ...]
    ) -> tuple[str, ...]:
        """Upload each local reference, mention it in the prompt, return the media ids.

        Uploading reuses the i2v toolbar path, so the app's own ``maseQ`` reply names the
        media id — the id :func:`_r2v_body_problem` then asserts the submit carries.
        Binding is different from i2v though: a reference is not a chip slot but an ``@``
        mention in the prompt document, committed with **Enter**. A typed query alone
        leaves the picker open and inserts nothing (measured 2026-09-05), which is the
        failure that would otherwise generate a clip with no references on it.
        """
        media_ids: list[str] = []
        display_names: list[str] = []
        for path in paths:
            # Mentioned by the name the upload was LISTED under, never the source file's:
            # reference images are re-used across runs by design (#792, _unique_display_name).
            media_id, display_name = await self._upload_via_toolbar(page, project_id, path)
            media_ids.append(media_id)
            display_names.append(display_name)
        # Compose after every upload: the file chooser takes keyboard focus, so mentions
        # cannot be interleaved with uploading.
        await self.clear_composer(page)
        for i, name in enumerate(display_names):
            await self._mention_by_name(page, name, expect_chips=i + 1)
        log.info("migrated.references_attached", count=len(paths), media_ids=media_ids)
        return tuple(media_ids)

    async def attach_character_entities(
        self,
        page: Page,
        *,
        entity_ids: tuple[str, ...],
        names: tuple[str, ...],
        clear: bool = True,
    ) -> tuple[str, ...]:
        """Mention each character by name, then prove the chip is that ENTITY (#723).

        The gesture is ``@``, the name, then the option TITLED exactly the name
        (:meth:`_mention_character`) — never the first option, and never a trial commit:
        a chip deleted from the prompt still rides the submit. Then the verification,
        which is not optional:

        **Flow lists characters and media in that one picker and does not rank them.**
        Measured 2026-09-07: the same ``@Kael`` query committed
        ``data-reference-type="entity"`` with the real entity id on one gesture, and
        ``reference_type="media"`` — a JPEG that merely shared the name — on another.
        A media chip where a character was asked for produces a clip that looks right and
        drifts on the next cut, which is the failure characters exist to prevent. So every
        chip is read back and checked for BOTH its kind and its id before anything is
        submitted, exactly as the labs path asserts ``referenceEntities`` on the wire
        (``ui_automation_video.py`` ``_assert_entities_attached``).

        ``names`` drive the gesture and may outnumber ``entity_ids``: a character
        mentioned by name with no catalog to look it up in (#723) has no id to check, so
        only its chip kind is verified. Returns the entity ids the committed chips carry.
        """
        if clear:
            await self.clear_composer(page)
        # An r2v run can carry media references AND characters, so judge only the chips
        # THIS call adds: media chips already on the prompt are legitimate, and clearing
        # them would silently drop references the caller asked for.
        base = len(await self.read_chips(page))
        for i, name in enumerate(names):
            await self._mention_character(page, name, expect_chips=base + i + 1)

        chips = (await self.read_chips(page))[base:]
        not_entities = [c for c in chips if c.get("reference_type") != "entity"]
        if not_entities:
            got = ", ".join(f"{c.get('text')!r} ({c.get('reference_type')})" for c in not_entities)
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for character(s) {', '.join(names)} but the "
                    f"picker committed {got}. Flow offers characters and media under one "
                    f"search and does not rank them, so a file sharing the name can win. "
                    f"Refusing to spend credits on a generation that would carry the wrong "
                    f"reference"
                ),
            )
        # A by-name character has no id to check, so its chip's text (the character's
        # name, measured 2026-09-07) is what proves the picker committed the right one.
        got_names = sorted(str(c.get("text", "")).casefold() for c in chips)
        if got_names != sorted(n.casefold() for n in names):
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for {len(names)} character(s) by name but the "
                    "committed chips carry different names. Refusing to submit"
                ),
            )
        attached = {c.get("entity_id", "") for c in chips}
        missing = [e for e in entity_ids if e not in attached]
        if missing:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: the prompt carries entity chips {sorted(attached)} but "
                    f"{missing} was requested — a chip of the right kind is not proof it is "
                    f"the right character. Refusing to submit"
                ),
            )
        committed = tuple(c.get("entity_id", "") for c in chips)
        log.info(
            "migrated.character_entities_attached",
            count=len(chips),
            names=list(names),
            entity_ids=list(committed),
        )
        return committed

    async def _mention_character(self, page: Page, name: str, *, expect_chips: int) -> None:
        """Commit the picker option TITLED exactly *name* — one gesture, nothing on trial.

        Measured 2026-10-13 (``scripts/dev/spike_picker_entity_marker.py``, and see
        ``docs/superpowers/spikes/2026-10-13-migrated-image-character-mention.md``): character and
        media options are structurally identical and differ only by a translated subtitle,
        and an image captioned "Sussy cooking…" ranked above the character "Sussy". Trying
        options and deleting the media chips is NOT an option: a deleted chip leaves the
        prompt but its reference stays in the composer's request state and rides the
        submit (live, 2026-10-13: five references on a run whose prompt showed one; chip
        removal measured by ``spike_picker_chip_removal.py``, the leak reproduced by
        ``spike_image_submit_references.py --trial-media``).

        ``.asset-title`` holds the asset's own name — user data, not a translated label —
        so the option whose trimmed title equals the name (case-insensitive) is the
        character, reached with one ArrowDown run and committed with Enter. Several exact
        matches (a media asset literally titled the name) are refused before anything is
        committed, as is no match; a single match that commits a media chip is refused
        too (the caller aborts the run before submit, so that chip never reaches Flow).
        """
        titles: list[str] = []
        before: list[dict[str, str]] = []
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            before = await self._caret_to_end(page)
            # Real keystrokes: see `_mention_by_name`.
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type(name, delay=100)
            await page.wait_for_timeout(2500)
            titles = [str(t) for t in await page.evaluate(_OPTION_TITLES_JS, PICKER_OPTION)]
            if titles or attempt == FRAME_SEARCH_ATTEMPTS:
                break
            # The search had not settled: clear the typed `@name` and ask again.
            log.info("migrated.mention_miss", name_chars=len(name), attempt=attempt, offered=0)
            for _ in range(len(name) + 1):
                await page.keyboard.press("Backspace")
            await page.wait_for_timeout(FRAME_SEARCH_RETRY_PAUSE_S * 1000)
        exact = [i for i, t in enumerate(titles) if t.strip().casefold() == name.casefold()]
        # Titles are asset names, often prompt-like captions: report counts, not text,
        # so an error detail (stderr, MCP, the catalog) never carries them.
        offered = f"{len(titles)} option(s)"
        if len(exact) != 1:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(800)
            why = (
                "no option is titled exactly that"
                if not exact
                else f"{len(exact)} options are titled exactly that, and a character and a "
                "media asset cannot be told apart before one is committed"
            )
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: asked for character {name!r} but {why}; the picker "
                    f"offered {offered}. Refusing before submit — rename the colliding "
                    "asset in Flow, or give the character a one-word name that is its full "
                    "name"
                ),
            )
        index = exact[0]
        # A click on the option commits nothing (measured 2026-10-13); the keyboard does.
        # Only the FINAL highlight has to settle before Enter (`ARROW_SETTLE_MS`), so the
        # arrows go in quickly and the settle is paid once, not per step.
        for _ in range(index):
            await page.keyboard.press("ArrowDown")
            await page.wait_for_timeout(ARROW_STEP_MS)
        if index:
            await page.wait_for_timeout(ARROW_SETTLE_MS)
        await page.keyboard.press("Enter")
        await page.wait_for_timeout(2500)
        chips = await self.read_chips(page)
        log.info("migrated.character_option", option_index=index, offered=len(titles))
        if len(chips) != expect_chips:
            raise ReferenceNotFoundError(
                detail=(
                    f"migrated host: committing the option titled {name!r} committed no chip "
                    f"({len(chips)} chip(s), expected {expect_chips}); the picker offered "
                    f"{offered}"
                ),
            )
        _require_appended(before, chips)
        # The kind check lives in `attach_character_entities`: a media chip here is
        # refused there, before any submit.

    async def _caret_to_end(self, page: Page) -> list[dict[str, str]]:
        """Focus the composer with the caret at its END; return the chips already there.

        Every ``@`` mention starts here, so a new chip can only be appended (#997).
        """
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.evaluate(_CARET_TO_END_JS, COMPOSER)
        return await self.read_chips(page)

    async def clear_composer(self, page: Page) -> None:
        await page.locator(COMPOSER).first.click(timeout=5000)
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
        await page.wait_for_timeout(600)

    async def read_chips(self, page: Page) -> list[dict[str, str]]:
        """Every mention now in the prompt, with the kind that decides its wire slot."""
        return await page.evaluate(
            "(sel) => [...document.querySelectorAll(sel)].map(c => ({"
            "  text: (c.textContent || '').trim(),"
            "  entity_id: c.getAttribute('data-entity-id') || '',"
            "  reference_type: c.getAttribute('data-reference-type') || '',"
            "}))",
            MENTION_CHIP,
        )

    async def _mention_by_name(self, page: Page, name: str, *, expect_chips: int) -> None:
        """Insert one mention chip for *name*, and verify it landed.

        Retries like :meth:`_pick_frame_by_name` does, and for the same measured reason:
        the mention picker is backed by the same server-side asset search, which does not
        always index a fresh upload by the first query. Enter is pressed only when the
        picker offers something: with no picker open it submits the generation (see
        docs/superpowers/spikes/2026-11-04-external-prs-1142-1145.md). The picker owns the
        query text, so Escape cancels a miss without touching existing chips, and the run
        is refused if an attached chip disappeared.
        """
        offered: list[str] = []
        chips: list[dict[str, str]] = []
        for attempt in range(1, FRAME_SEARCH_ATTEMPTS + 1):
            prior = await self._caret_to_end(page)
            before = len(prior)
            # `keyboard.type`, never `insert_text`: the latter dispatches input events with
            # no key events, so the mention plugin opens a picker with no query behind it
            # and every later gesture is a no-op. `send_prompt` keeps insert_text on
            # purpose — a newline in prompt text must not submit — so they cannot share a
            # path.
            await page.keyboard.type("@", delay=120)
            await page.wait_for_timeout(2200)
            await page.keyboard.type(name, delay=100)
            await page.wait_for_timeout(2500)
            offered = [t.strip() for t in await page.locator(PICKER_OPTION).all_text_contents()]
            if offered:
                await page.keyboard.press("Enter")
                await page.wait_for_timeout(2500)
            chips = await self.read_chips(page)
            if len(chips) == expect_chips:
                _require_appended(prior, chips)
                await page.keyboard.type(" ", delay=80)
                return
            log.info(
                "migrated.mention_miss",
                name=name,
                attempt=attempt,
                chips=len(chips),
                offered=len(offered),
            )
            await page.keyboard.press("Escape")
            chips = await self.read_chips(page)
            if len(chips) < before:
                raise ReferenceNotFoundError(
                    detail=(
                        f"migrated host: clearing the failed {name!r} query removed an "
                        f"already-attached reference ({before} chip(s) before, "
                        f"{len(chips)} after) — the prompt is no longer the one that was "
                        "built, so this run is abandoned rather than submitted"
                    ),
                )
            if attempt == FRAME_SEARCH_ATTEMPTS:
                break
            await page.wait_for_timeout(FRAME_SEARCH_RETRY_PAUSE_S * 1000)
        raise ReferenceNotFoundError(
            detail=(
                f"migrated host: {name!r} did not attach as a reference in "
                f"{FRAME_SEARCH_ATTEMPTS} attempts ({len(chips)} chip(s), expected "
                f"{expect_chips}); the picker offered: {', '.join(offered[:6]) or '<nothing>'}"
                " — if the asset IS listed above, the commit gesture is what failed, not "
                "the lookup: a cohort whose picker needs its own confirm (#792) is a "
                "different failure from a missing asset"
            ),
        )
