"""Character editor navigation, model selection and character image generation.

Mixed into `UiAutomationTransport` via `CharacterGenerationMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any, ClassVar, cast

import structlog

from gflow_cli.api.character import CharacterImageRequest
from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.transports._common import (
    generation_error,
)
from gflow_cli.api.transports._ui_capture import BatchCaptureMixin
from gflow_cli.api.transports._ui_common import (
    _AnyList,
    _capture_debug_screenshot,
    _images_from_responses,
    _JsonObj,
)
from gflow_cli.api.transports.migrated_composer import MENU_ITEM, ModelMenuMatcher
from gflow_cli.api.transports.ui_automation_video import (
    screenshot_clause,
)
from gflow_cli.errors import ConfigurationError, FlowAppError, UiSelectorDriftError

if TYPE_CHECKING:
    from playwright.async_api import Page


log = structlog.get_logger(__name__)


class CharacterGenerationMixin(BatchCaptureMixin):
    """Character editor navigation, model selection and character image generation."""

    if TYPE_CHECKING:

        @staticmethod
        async def _is_flow_app_crash(page: Page) -> bool: ...

    # Selector for the character editor's Slate prompt textbox — used as the
    # "editor mounted" readiness anchor.  Its first alternative IS
    # PROMPT_INPUT_SELECTORS[0];
    # duplicated here as a named constant so the character-editor path is
    # self-documenting without importing the tuple.
    # Two frontends, one feature. labs.google renders the character editor with
    # React + Slate; flow.google.com renders the SAME editor — same tRPC backend,
    # same aisandbox REST, same entities — with Angular + ProseMirror (recon
    # 2026-09-06, scripts/dev/spike_migrated_character_editor_anchor.py). Asking
    # only for Slate made a present feature look absent for 20 s and then get
    # reported as "labs-only, ever", which was wrong. Both anchors are library
    # build artefacts rather than display strings, so neither translates.
    _CHARACTER_EDITOR_READY_SELECTOR = (
        'div[role="textbox"][data-slate-editor="true"], div.ProseMirror[contenteditable="true"]'
    )

    # Slot-add button for character image slots 1+ (body / accessories).
    #
    # Live DOM evidence (2026-06-02 spike, character editor with slot 0 filled)
    # disproved the old ``button:has(...).nth(1)`` heuristic.  Two ``add_2``
    # ligatures exist in the editor DOM and they are STRUCTURALLY DISTINCT:
    #
    #   1. Slot-add (the target):  ``<div role="button"
    #      aria-label="Adicionar imagem do personagem"><i ...>add_2</i></div>``
    #      — a ``[role=button]`` div whose ONLY accessible content is the
    #      ``add_2`` icon (icon-only; no visible/sibling text-label span).
    #   2. The decoy:  ``<button type="button" aria-haspopup="dialog">
    #      <i ...>add_2</i><span style="...clip...">…</span></button>`` — a real
    #      ``<button>`` carrying a sibling ``<span>`` text label.
    #
    # The old ``button:has(i.google-symbols:text('add_2'))`` selector did not
    # even MATCH the slot-add (it is a ``div[role=button]``, not ``<button>``);
    # ``.nth(1)`` of that locator landed on the wrong control entirely.
    #
    # Robust, language-agnostic discriminator: the slot-add is the ``add_2``
    # icon hosted in a ``[role=button]`` whose stripped ``inner_text`` is EXACTLY
    # the ligature ``"add_2"`` (icon-only).  The decoy's hidden label makes its
    # inner_text longer, so it is excluded.  The localised aria-label
    # ("Adicionar imagem do personagem") is NEVER the primary anchor — see
    # [[flow-locale-leak-icon-ligatures]] — it may only serve as a positional
    # hint.  ``text-is`` (exact match) is used so partial-ligature collisions
    # (e.g. ``add_2_box``) cannot match.
    #: Both ligature carriers: labs uses `<i class="google-symbols">`, the migrated
    #: Angular frontend uses `<mat-icon>`. Anchoring on one host's carrier is what
    #: made the whole character surface look absent on the other (2026-09-06).
    _CHARACTER_SLOT_ADD_SELECTOR = (
        "[role='button']:has(i.google-symbols:text-is('add_2')), "
        "[role='button']:has(mat-icon:text-is('add_2'))"
    )
    # The exact ligature an icon-only slot-add candidate's inner_text reduces to.
    _CHARACTER_SLOT_ADD_LIGATURE = "add_2"

    # Current two-mode character editor (live 2026-07-26): Create Body reuses
    # the existing Slate prompt box instead of mounting a second one. The
    # language-independent ``accessibility_new`` icon activates body mode; the
    # generated-face reference chip (image + ``cancel`` icon) proves the mode
    # transition settled before the shared prompt box is safe to edit.
    #: Stays SCOPED on both hosts. The project-level "Characters" navigation control
    #: carries the same ``accessibility_new`` ligature, so a bare selector activates
    #: navigation instead of body mode.
    #:
    #: labs scopes it as the sibling of the portrait image button. The migrated
    #: editor gives us something better: each slot control is wrapped in its own
    #: ``<flow-slot-chip-button>`` custom element (recon 2026-09-06,
    #: ``scripts/dev/spike_migrated_character_body_controls.py``), which is a
    #: component boundary rather than a layout accident — the navigation control is
    #: not a slot chip, so it cannot match. The migrated host has **no** ``add_2``
    #: control at all; body mode is entered through this chip directly.
    #:
    #: The chip ships ``disabled`` until a portrait exists, which is why callers
    #: must wait for it to become enabled rather than clicking on sight.
    _CHARACTER_BODY_MODE_SELECTOR = (
        "button:has(img) + button:has(i.google-symbols:text-is('accessibility_new')), "
        "flow-slot-chip-button:has(mat-icon:text-is('accessibility_new')) button"
    )
    #: The settle signal for body mode: proof the generated face actually mounted as
    #: a reference, so the SHARED prompt box now belongs to body mode. Editing it
    #: earlier types the body prompt into the face composer (#395).
    #:
    #: labs mounts a dismissable card around a ``media.getMediaUrlRedirect`` image.
    #: The migrated host serves reference images from a different origin entirely —
    #: ``flow-content.google/image/<id>`` — and mounts a bare ``<img>`` with no card
    #: chrome. Measured 2026-09-06 (``spike_migrated_body_reference_chip.py``):
    #: absent before the Create body click, present after it, so it is a genuine
    #: transition signal and not something that is simply always on the page.
    _CHARACTER_BODY_REFERENCE_SELECTOR = (
        "button[data-card-open]:has(img[src*='media.getMediaUrlRedirect'])"
        ":has(i.google-symbols:text-is('cancel')), "
        "img[src*='flow-content.google/image/']"
    )

    # Character-editor model picker.  The editor shows a model chip ("🍌 Nano
    # Banana 2") with an ``arrow_drop_down`` ligature; clicking it opens a menu
    # whose options are the product names.  Product names ("Nano Banana 2" /
    # "Nano Banana Pro") are NOT localized, so text matching is acceptable here.
    #
    # ⚠️ NOT yet spiked from the live DOM — this is a reasonable best-effort
    # selector cascade, live-confirmed later.  A failed pick is NON-FATAL:
    # generation proceeds with Flow's default model.
    #: The ligature carrier differs by host — labs renders `<i class="google-symbols">`,
    #: flow.google.com renders `<mat-icon>` — so both are listed. A run that omitted the
    #: mat-icon variants logged `model-picker trigger not found` on the migrated host and
    #: generated on whatever tier the editor happened to open on (live 2026-09-06).
    _CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS = (
        "button:has(i.google-symbols:text-is('arrow_drop_down'))",
        "button:has(mat-icon:text-is('arrow_drop_down'))",
        "[role='button']:has(i.google-symbols:text-is('arrow_drop_down'))",
        "[role='button']:has(mat-icon:text-is('arrow_drop_down'))",
    )

    #: Every character model is a "Nano Banana …" tier, so the model chip is the one
    #: `arrow_drop_down` control whose own text names one. The editor renders several
    #: such controls; picking `.first` blindly is how a picker ends up driving the
    #: wrong menu. Product names are not localized (see :data:`CHARACTER_MODELS`).
    _CHARACTER_MODEL_CHIP_HINT = "Nano Banana"

    #: How each CLI alias is recognised among the editor's menu entries. Reuses the
    #: migrated composer's matcher so both hosts obey the same #539 rule: match in
    #: Python, and REFUSE an ambiguous hit rather than resolving `.first`.
    #: "Nano Banana 2" is a prefix of "Nano Banana 2 Lite", so it must exclude it;
    #: "Nano Banana Pro" shares no prefix and needs no exclusion. Product names are
    #: not localized, which is why matching entries by display text is permitted
    #: here where the locale-invariance rule otherwise forbids it.
    _CHARACTER_MODEL_MATCHERS: ClassVar[dict[str, ModelMenuMatcher]] = {
        "nano2": ModelMenuMatcher("Nano Banana 2", excludes="Lite"),
        "nanopro": ModelMenuMatcher("Nano Banana Pro", excludes=None),
    }

    async def _find_character_model_chip(self, page: Any) -> tuple[Any | None, str]:
        """Return the model chip and the text it currently shows.

        Prefers a candidate whose text names a model tier over a bare positional
        `.first`, so a page carrying several `arrow_drop_down` controls cannot
        hand the picker somebody else's menu. Falls back to the first candidate
        when none names a tier — better to try than to skip the selection.
        """
        # Wait for SOMETHING to mount before enumerating. A bare count() races the
        # editor: the readiness gate only proves the prompt box exists, and the
        # model chip lands after it. Dropping this wait during a refactor turned a
        # working picker back into "model-picker trigger not found" (2026-09-06).
        for trig_sel in self._CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS:
            try:
                await page.locator(trig_sel).first.wait_for(state="visible", timeout=4000)
                break
            except Exception:  # noqa: BLE001,PERF203 - try the next carrier
                continue

        fallback: Any | None = None
        fallback_text = ""
        for trig_sel in self._CHARACTER_MODEL_PICKER_TRIGGER_SELECTORS:
            locator = page.locator(trig_sel)
            for index in range(await locator.count()):
                candidate = locator.nth(index)
                text = (await candidate.text_content() or "").strip()
                if self._CHARACTER_MODEL_CHIP_HINT.casefold() in text.casefold():
                    return candidate, text
                if fallback is None:
                    fallback, fallback_text = candidate, text
        return fallback, fallback_text

    async def _select_character_model(
        self,
        page: Any,
        model_alias: str,
        out_dir: Any = None,
    ) -> None:
        """Apply the requested character model, or raise. Never silently proceed.

        ``model_alias`` is the friendly CLI alias (``"nano2"`` / ``"nanopro"``).

        **This is a hard gate, deliberately.** It used to be best-effort: every
        failure logged a warning and let the generation run on whatever tier the
        editor happened to show. For a CLI that is the worst outcome — the user
        gets a paid artifact from a model they did not ask for, and nothing in
        the output says so. Both halves of that were seen live on 2026-09-06:
        ``--model nano2`` generating on Pro because the picker assumed nano2 was
        the default, and a later run whose menu-item click timed out, leaving the
        tier unchanged while the generation proceeded.

        Aborting is **free**. Everything here happens before the prompt is
        submitted, so a raise costs no quota and no credits.

        The selection is verified by re-reading the chip afterwards rather than
        trusting the click, because the click is exactly what was observed to
        fail. One retry absorbs a menu that re-renders under us; anything else
        raises.

        Raises:
            :class:`~gflow_cli.errors.ConfigurationError`: the alias is unknown,
                or the menu does not offer exactly one entry matching it.
            :class:`~gflow_cli.errors.UiSelectorDriftError`: the picker or its
                menu could not be driven, or the tier did not apply.
        """
        matcher = self._CHARACTER_MODEL_MATCHERS.get(model_alias.lower())
        if matcher is None:
            raise ConfigurationError(
                detail=(
                    f"unknown character model {model_alias!r}; "
                    f"known aliases: {', '.join(sorted(self._CHARACTER_MODEL_MATCHERS))}"
                ),
                remediation_hint="Pass --model nano2 or --model nanopro.",
            )

        trigger, current = await self._find_character_model_chip(page)
        if trigger is None:
            await _capture_debug_screenshot(page, out_dir, "debug_character_model_picker.png")
            raise UiSelectorDriftError(
                detail=(
                    "character editor: the model picker (an arrow_drop_down control naming a "
                    f"model tier) was not found, so --model {model_alias} could not be applied. "
                    f"URL: {page.url}."
                ),
            )

        if matcher.matches(current):
            # Logged because the path is otherwise invisible: a run that
            # short-circuits emits no model event, and a field timeline cannot
            # tell "already on the tier you asked for" from "never looked".
            log.info(
                "ui_automation.character_model_already_selected",
                model=current,
                requested=model_alias,
            )
            return

        last_seen = current
        for attempt in (1, 2):
            await trigger.click(timeout=4000)
            # ``:visible`` is load-bearing. The editor keeps several menus in the
            # DOM at once (two `flow-add-menu`s plus the model `mat-menu`), so an
            # unscoped `[role='menuitem']` list mixes the open overlay's items
            # with hidden ones from the others. The text list and the clickable
            # list then disagree, and `nth(i)` — an index into the text list —
            # lands on a hidden node: `Locator.click: Timeout 4000ms exceeded`,
            # every time, while the same selector worked whenever the other menus
            # happened not to be mounted. Measured live 2026-09-06.
            items = page.locator(f"{MENU_ITEM}:visible")
            try:
                await items.first.wait_for(state="visible", timeout=5000)
            except Exception as e:
                await _capture_debug_screenshot(page, out_dir, "debug_character_model_picker.png")
                raise UiSelectorDriftError(
                    detail=(
                        "character editor: the model menu ([role='menuitem']) did not open, so "
                        f"--model {model_alias} could not be applied. URL: {page.url}."
                    ),
                ) from e

            # Matched in Python rather than through a `has_text` filter: an
            # *exclusion* is not expressible as has_text, the menu is read back for
            # the refusal diagnostic anyway, and more than one hit must REFUSE
            # instead of resolving `.first` (#539 — a `.first` on an ambiguous
            # selector picks a tier the user never asked for, and Flow bills it).
            offered = [t.strip() for t in await items.all_text_contents()]
            hits = [i for i, text in enumerate(offered) if matcher.matches(text)]
            if len(hits) != 1:
                await self._dismiss_character_model_menu(page)
                raise ConfigurationError(
                    detail=(
                        f"--model {model_alias} matched {len(hits)} entries in the character "
                        f"model menu; offered: {', '.join(offered) or '(none)'}"
                    ),
                    remediation_hint=(
                        "Pass a --model that names exactly one offered entry."
                        if hits
                        else "Pass --model with one of the offered names."
                    ),
                )

            try:
                await items.nth(hits[0]).click(timeout=4000)
            except Exception:  # noqa: BLE001 - a flaky click is what the retry is for
                log.warning(
                    "ui_automation.character_model_click_failed",
                    requested=model_alias,
                    attempt=attempt,
                )
                await self._dismiss_character_model_menu(page)
                continue
            await page.wait_for_timeout(400)

            # Verify, never assume: the click is precisely what was seen to fail.
            _, last_seen = await self._find_character_model_chip(page)
            if matcher.matches(last_seen):
                log.info(
                    "ui_automation.character_model_selected",
                    model=last_seen,
                    requested=model_alias,
                    attempt=attempt,
                )
                return
            log.warning(
                "ui_automation.character_model_not_applied",
                requested=model_alias,
                chip=last_seen,
                attempt=attempt,
            )

        await _capture_debug_screenshot(page, out_dir, "debug_character_model_picker.png")
        raise UiSelectorDriftError(
            detail=(
                f"character editor: --model {model_alias} did not apply — the model chip still "
                f"reads {last_seen!r} after two attempts. Refusing to generate on a tier you did "
                f"not ask for. URL: {page.url}."
            ),
        )

    async def _dismiss_character_model_menu(self, page: Any) -> None:
        """Close an open model menu; never fatal."""
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(200)
        except Exception:  # noqa: BLE001 - dismissal is a courtesy
            log.debug("ui_automation.character_model_menu_dismiss_failed", exc_info=True)

    _CHARACTER_ROUTE_ATTEMPTS = 4
    _CHARACTER_ROUTE_BACKOFF_MS = 2_500

    async def _settle_on_character_route(
        self,
        page: Any,
        *,
        entity_id: str,
        url: str,
    ) -> None:
        """Ensure the browser actually CAME TO REST on ``/character/{entity_id}``.

        Flow bounces this route back to the project page when the entity is not
        yet queryable — a race gflow loses because it navigates immediately
        after ``flow.createEntity`` (live 2026-07-28).

        This must be checked explicitly because the project page **also** mounts
        a Slate prompt box, so the editor-ready wait below is satisfied on the
        WRONG surface. The generation then goes to the project composer, which
        sends no ``entityContext``, and Flow files the image as a plain project
        image with no ``parentEntityId`` — the #395 symptom. A redirect is
        therefore silent data loss, not a cosmetic detail.

        Re-navigates a few times to let the entity settle. If the route still
        will not stick, raise rather than type into the project composer.
        """
        for attempt in range(1, self._CHARACTER_ROUTE_ATTEMPTS + 1):
            if entity_id in str(page.url):
                if attempt > 1:
                    log.info(
                        "ui_automation.character_route_settled",
                        entity_id=entity_id,
                        attempt=attempt,
                    )
                return
            log.warning(
                "ui_automation.character_route_bounced",
                entity_id=entity_id,
                landed_on=str(page.url),
                attempt=attempt,
                note="Flow redirected away from the character editor; retrying",
            )
            if attempt == self._CHARACTER_ROUTE_ATTEMPTS:
                break
            await page.wait_for_timeout(self._CHARACTER_ROUTE_BACKOFF_MS)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            await self._settle_if_redirecting(page)
            await self._dismiss_blocking_overlays(page, self._out_dir)

        shot = await _capture_debug_screenshot(
            page, self._out_dir, "debug_character_route_bounced.png"
        )
        msg = (
            f"Flow kept redirecting the character editor for entity {entity_id!r} "
            f"back to {page.url!r} after {self._CHARACTER_ROUTE_ATTEMPTS} attempts. "
            "Generating here would submit through the PROJECT composer and Flow "
            "would file the image as a plain project image instead of binding it "
            f"to the character.{screenshot_clause(shot)}"
        )
        raise FlowAppError(detail=msg)

    async def _enter_character_editor(
        self,
        page: Any,
        *,
        project_id: str,
        entity_id: str,
        locale: str | None,
    ) -> None:
        """Navigate to the Flow character editor for an existing entity.

        Does NOT create a new project — navigates directly to the existing
        project's character editor URL via ``page.goto``.

        Readiness gate: waits for the prompt textbox
        (``div[role=textbox][data-slate-editor='true']``) to become visible,
        confirming the Slate editor has mounted.  Overlays are dismissed
        after navigation so they don't block subsequent interactions.
        """
        from gflow_cli.api import routes

        url = routes.character_editor_url(locale, project_id, entity_id)
        log.info(
            "ui_automation.entering_character_editor",
            url=url,
            project_id=project_id,
            entity_id=entity_id,
        )
        await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
        # #580: settle BEFORE any DOM work — #395's "character-route bounce" is
        # this exact race. `_settle_on_character_route` below only checks that
        # `entity_id` is still in the URL, which a locale-only redirect PRESERVES,
        # so it returns instantly and cannot substitute for this wait.
        await self._settle_if_redirecting(page)
        await self._dismiss_blocking_overlays(page, self._out_dir)
        await self._settle_on_character_route(page, entity_id=entity_id, url=url)

        # NO migrated-host guard here, deliberately. One stood here and was
        # wrong: flow.google.com renders the character editor in full — name,
        # voice, personality, Upload / Add from project, Create portrait /
        # Create body — against the SAME labs tRPC and aisandbox backend
        # (recon 2026-09-06, scripts/dev/spike_migrated_vs_labs_provenance.py:
        # the migrated page itself calls flow.projectInitialData and
        # v1:checkAppAvailability). What differed was the view layer, React +
        # Slate vs Angular + ProseMirror, so the readiness selector missed and
        # a present feature was read as an absent one. The guard then turned
        # that misreading into a confident, non-retryable "this host will never
        # do it". Do not reinstate it without live evidence that the editor is
        # actually gone.

        # Wait for the Slate editor to mount — the prompt textbox is the
        # reliable "editor ready" anchor for the character editor surface.
        try:
            await page.locator(self._CHARACTER_EDITOR_READY_SELECTOR).first.wait_for(
                state="visible",
                timeout=20_000,
            )
        except Exception as exc:
            shot = await _capture_debug_screenshot(
                page,
                self._out_dir,
                "debug_character_editor_not_ready.png",
            )
            # Flow's web app crashes on this route often enough to be the
            # DOMINANT cause of a missing textbox (live 2026-07-27: the
            # incident bundle's ui.json reported title category
            # `flow_app_crash` with zero ligatures — Flow's React error
            # boundary, not the editor). Reuse the mode-switch path's
            # classifier so the user gets the typed, retryable FlowAppError
            # (exit 31) that says "Flow broke, retry" instead of a bare
            # RuntimeError blaming a selector that was never on the page.
            if await self._is_flow_app_crash(page):
                raise FlowAppError(
                    detail=(
                        "Flow's web app crashed (client-side exception) instead of "
                        f"rendering the character editor at {page.url}, so there is "
                        f"no prompt box to drive.{screenshot_clause(shot)}"
                    )
                ) from exc
            msg = (
                f"Character editor not ready: prompt textbox not visible "
                f"within 20 s. URL: {page.url}.{screenshot_clause(shot)}"
            )
            raise RuntimeError(msg) from exc

        log.info("ui_automation.character_editor_ready", url=page.url)

    @staticmethod
    def _workflows_from_responses(
        responses: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Extract workflow metadata from captured batchGenerateImages responses.

        Returns a flat list of workflow dicts, each containing at minimum:
        ``name``, ``metadata`` (with ``primaryMediaId``), ``projectId``, and
        ``parentEntityId``.  The ``parentEntityId`` field binds the generation
        result to the character entity.

        Only workflow entries present in 200-status responses are returned;
        error responses are silently skipped (``_images_from_responses`` already
        raises on 401/403 before this is called).
        """
        workflows: list[dict[str, Any]] = []
        for response in responses:
            if response.get("status") != 200:
                continue
            body: dict[str, Any] = cast(_JsonObj, response.get("body") or {})
            raw_workflows = body.get("workflows", [])
            if not isinstance(raw_workflows, list):
                continue
            for wf_raw in cast(_AnyList, raw_workflows):
                if not isinstance(wf_raw, dict):
                    continue
                wf: dict[str, Any] = cast(_JsonObj, wf_raw)
                # Only surface workflows that carry the character-binding field.
                if "parentEntityId" not in wf:
                    log.debug(
                        "ui_automation.workflow_missing_parent_entity_id",
                        workflow_name=wf.get("name"),
                    )
                workflows.append(wf)
        return workflows

    async def generate_character_images(
        self,
        *,
        project_id: str,
        entity_id: str,
        request: CharacterImageRequest,
        image_reference_index: int,
        locale: str | None,
        format_prompt: bool = False,
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        """Navigate to the character editor and generate images via passive capture.

        Reuses the same generate lock, listener, and parse helpers as the
        image-generation path — the difference is that we navigate to an
        EXISTING project's character editor (not a new project), and we
        return workflow metadata alongside the images so the caller can bind
        results to the character entity via ``parentEntityId``.

        ``image_reference_index``:
          - 0 → face slot (no body-mode interaction needed; the slot is
            already active in the character editor).
          - >= 1 → body / accessory slot; activate body mode and wait for its
            structural settle signal before submitting the prompt.

        Returns ``(images, workflows)`` where each ``workflows`` entry carries
        at minimum ``name``, ``metadata.primaryMediaId``, ``projectId``, and
        ``parentEntityId``.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_character_images()"
            raise RuntimeError(msg)

        async with self._generate_lock:
            return await self._generate_character_images_locked(
                project_id=project_id,
                entity_id=entity_id,
                request=request,
                image_reference_index=image_reference_index,
                locale=locale,
                format_prompt=format_prompt,
            )

    async def _generate_character_images_locked(
        self,
        *,
        project_id: str,
        entity_id: str,
        request: CharacterImageRequest,
        image_reference_index: int,
        locale: str | None,
        format_prompt: bool = False,
    ) -> tuple[list[GeneratedImage], list[dict[str, Any]]]:
        """Serialized body of generate_character_images — called under _generate_lock."""
        page: Any = self._page  # type: ignore[assignment]  # guard in caller
        out_dir = self._out_dir

        await self._enter_character_editor(
            page,
            project_id=project_id,
            entity_id=entity_id,
            locale=locale,
        )
        await self._dismiss_blocking_overlays(page, out_dir)

        # Characters have NO aspect-ratio control and NO per-generation settings
        # panel (the live editor renders neither — the old
        # _configure_generation_settings call only ever logged
        # ``gen_settings_panel_not_found`` and skipped).  Model selection uses
        # the editor's OWN model dropdown instead.  ``request.model`` is the
        # friendly CLI alias ("nano2" / "nanopro").  Best-effort, non-fatal:
        # a failed pick proceeds with Flow's default model.  Character
        # generation is always exactly one image per slot.
        await self._select_character_model(page, request.model, out_dir)

        # Face (index 0) needs no mode interaction; body/accessories do.
        # Activating body mode auto-attaches the generated face as a reference.
        # Current Flow reuses the Slate box; legacy cohorts mount another one.
        # The body path replaces the settled composer with gflow's own prompt.
        is_body_slot = image_reference_index >= 1
        boxes_before = 0
        shared_body_box = False
        if is_body_slot:
            # Snapshot the count for legacy cohorts before activating body
            # mode. Current Flow proves the shared box is safe by mounting the
            # generated-face reference; legacy Flow proves it by count rise.
            boxes_before = await self._count_character_prompt_boxes(page)
            shared_body_box = await self._click_character_slot_add(page, out_dir)

        # Attach listener BEFORE submit to eliminate the race condition.
        captured, detach = self._attach_batch_response_listener(page, project_id=project_id)
        submit_time = time.monotonic()
        responses: list[dict[str, Any]] = []
        try:
            if is_body_slot:
                await self._submit_body_prompt(
                    page,
                    request.prompt,
                    boxes_before=boxes_before,
                    shared_body_box=shared_body_box,
                    out_dir=out_dir,
                    format_prompt=format_prompt,
                )
            else:
                await self._send_prompt(page, request.prompt, out_dir, format_prompt=format_prompt)
            responses = await self._await_captured(
                captured,
                expected_count=1,
                submit_time=submit_time,
            )
        finally:
            detach()

        images, first_error_status, first_error_route, first_error_body = _images_from_responses(
            responses
        )

        if first_error_status is not None and not images:
            # #528: a 400 here is a content-policy rejection, not a wire problem.
            raise generation_error(
                status=first_error_status,
                route=first_error_route,
                body=first_error_body,
            )

        if not images:
            from gflow_cli.errors import ContentPolicyError

            raise ContentPolicyError(
                detail="batchGenerateImages returned 200 but no parseable media items",
                route=first_error_route or "",
            )

        workflows = self._workflows_from_responses(responses)
        return images, workflows

    async def _click_character_slot_add(
        self,
        page: Any,
        out_dir: Any = None,
    ) -> bool:
        """Activate body mode; return whether it uses the settled shared box.

        Current Flow (live 2026-07-26) exposes a Create Body button carrying
        the locale-independent ``accessibility_new`` icon. Clicking it reuses
        the existing Slate box. The generated-face reference chip must become
        visible before this method returns ``True``; that structural signal
        proves the shared box now belongs to body mode.

        Legacy DOM evidence (2026-06-02 spike) showed the character editor renders
        two ``add_2`` ligatures that are STRUCTURALLY distinct (see the
        ``_CHARACTER_SLOT_ADD_SELECTOR`` constant for the full DOM): the slot-add
        target is a ``[role=button]`` whose accessible content is icon-ONLY,
        while the decoy is a ``<button>`` that also carries a hidden text-label
        ``<span>``.  The previous ``.nth(1)`` heuristic was wrong — it indexed
        into a ``<button>``-only locator that never even matched the slot-add
        div.

        Selection logic (language-agnostic):

        1. Locate every ``[role=button]`` that hosts an ``add_2`` icon.
        2. Keep only the icon-ONLY candidates — those whose stripped
           ``inner_text`` is exactly the ``add_2`` ligature.  The decoy's hidden
           label lengthens its inner_text, excluding it.
        3. If exactly one icon-only candidate remains, click it.  If several
           remain, prefer the first (the slot-add renders adjacent to the
           character image slots, ahead of any other icon-only ``add_2``).

        The localised ``aria-label`` is intentionally NOT part of the predicate
        ([[flow-locale-leak-icon-ligatures]]); the icon-only test is the anchor.

        The legacy path returns ``False`` because it mounts a second prompt
        box. Non-fatal here: a miss is logged at WARNING.  Enforcement lives in
        ``_locate_body_prompt_box`` — if the body composer's own prompt box
        never mounts, the body step ABORTS instead of typing into whichever
        slot is currently active (which would overwrite the portrait prompt).
        """
        body_mode = page.locator(self._CHARACTER_BODY_MODE_SELECTOR).first
        try:
            await body_mode.wait_for(state="visible", timeout=5_000)
        except Exception as exc:
            body_mode_error = str(exc)[:120]
        else:
            references_before = int(
                await page.locator(self._CHARACTER_BODY_REFERENCE_SELECTOR).count()
            )
            await body_mode.click()
            try:
                face_references = page.locator(self._CHARACTER_BODY_REFERENCE_SELECTOR)
                await face_references.first.wait_for(state="visible", timeout=5_000)
                references_now = int(await face_references.count())
                if references_now <= references_before:
                    raise RuntimeError(
                        "generated-face reference did not mount after Create Body click"
                    )
            except Exception as exc:
                shot = await _capture_debug_screenshot(
                    page, out_dir, "debug_character_body_mode_unsettled.png"
                )
                msg = (
                    "Create Body was selected, but its generated-face reference did not mount. "
                    "Aborting before prompt editing; the legacy control will not be tried after "
                    f"a partial mode transition. URL: {page.url}.{screenshot_clause(shot)}"
                )
                raise RuntimeError(msg) from exc
            await page.wait_for_timeout(400)
            log.info(
                "ui_automation.character_body_mode_activated",
                selector=self._CHARACTER_BODY_MODE_SELECTOR,
                settle_signal=self._CHARACTER_BODY_REFERENCE_SELECTOR,
                shared_body_box=True,
                references_before=references_before,
                references_now=references_now,
            )
            return True

        try:
            loc = page.locator(self._CHARACTER_SLOT_ADD_SELECTOR)
            await loc.first.wait_for(state="visible", timeout=5_000)
            total = await loc.count()
            chosen = None
            chosen_index = -1
            for i in range(total):
                cand = loc.nth(i)
                try:
                    raw = await cand.inner_text()
                except Exception:
                    continue
                # Icon-only ⇔ the accessible text reduces to just the ligature.
                # The decoy carries a hidden label span → longer inner_text.
                if raw.strip() == self._CHARACTER_SLOT_ADD_LIGATURE:
                    chosen = cand
                    chosen_index = i
                    break
            if chosen is None:
                raise RuntimeError(f"no icon-only add_2 [role=button] among {total} candidate(s)")
            await chosen.click()
            await page.wait_for_timeout(400)
            log.info(
                "ui_automation.character_slot_add_clicked",
                image_reference_index=1,
                selector=self._CHARACTER_SLOT_ADD_SELECTOR,
                candidates=total,
                chosen_index=chosen_index,
            )
            return False
        except Exception as exc:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_character_slot_add.png")
            log.warning(
                "ui_automation.character_slot_add_failed",
                error=str(exc)[:120],
                body_mode_error=body_mode_error,
                screenshot=str(shot),
                note=(
                    "slot-add button not found; the body step will abort "
                    "unless the body prompt box is already mounted"
                ),
            )
            return False
