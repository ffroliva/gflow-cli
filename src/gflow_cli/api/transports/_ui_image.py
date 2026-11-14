"""Image generation, single and batch, reference uploads and entity assertions.

Mixed into `UiAutomationTransport` via `ImageGenerationMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage
from gflow_cli.api.image import GenerateImageRequest, ImageRef
from gflow_cli.api.transports._common import (
    generation_error,
    migrated_route,
    raise_if_migrated,
)
from gflow_cli.api.transports._ui_character import CharacterGenerationMixin
from gflow_cli.api.transports._ui_common import (
    _extract_project_id,
    _images_from_responses,
    _prompt_hash_stable,
)
from gflow_cli.api.transports.migrated_composer import migrated_images_prefer
from gflow_cli.api.transports.ui_automation_video import (
    ENTITY_ATTACH_DRIFT_HINT,
    zip_entity_refs,
)
from gflow_cli.errors import (
    BatchPartialError,
    ContentPolicyError,
    GFlowError,
    MentionIndexUnavailableError,
    WireFormatError,
)

if TYPE_CHECKING:
    from pathlib import Path

    from playwright.async_api import Page


log = structlog.get_logger(__name__)


class ImageGenerationMixin(CharacterGenerationMixin):
    """Image generation, single and batch, reference uploads and entity assertions."""

    if TYPE_CHECKING:
        import contextlib
        from collections.abc import AsyncGenerator

        @staticmethod
        async def _attach_references(
            page: Page,
            images: list[Path],
            *,
            out_dir: Path | None,
            timeout_s: float = 120.0,
            prefer_existing: bool = False,
        ) -> None: ...

        @staticmethod
        async def _attach_image_uuid_refs(
            page: Page,
            refs: list[tuple[str, str, str, str]],
            *,
            out_dir: Path | None,
            name_resolver: Callable[[str], str | None] | None = None,
        ) -> None: ...

        @staticmethod
        async def _attach_character_entities(
            page: Page,
            entities: list[tuple[str, str]],
            *,
            out_dir: Path | None,
        ) -> None: ...

        @contextlib.asynccontextmanager
        async def _intercept_reference_entities(
            self,
            page: Page,
            expected_entities: set[str],
        ) -> AsyncGenerator[None, None]:
            yield

    async def generate_images(
        self,
        *,
        project_id: str | None,
        request: GenerateImageRequest,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Submit ``request.prompt`` through Flow's editor and return the
        generated images as DTOs.

        If ``project_id`` is provided, navigates to that project. Otherwise
        creates a new one.

        Raises ``RuntimeError`` if setup() has not been called, the
        ``batchGenerateImages`` response is non-200, or the response is
        200 but contains no image URLs.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_images()"
            raise RuntimeError(
                msg,
            )
        async with self._generate_lock:
            return await self._generate_images_locked(
                request, project_id=project_id, name_resolver=name_resolver
            )

    async def upload_reference(self, *, project_id: str, path: Path) -> ImageRef | None:
        """Upload ``path`` into the project on flow.google.com and return its handle.

        ``None`` when this page routes to the labs driver: the client then uses the REST
        upload. On flow.google.com the composer toolbar uploads it and names the media id
        and the run-unique caption it was listed under, which is everything a later
        in-place mention needs (#913). Serialized with generation, parked after use.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before upload_reference()"
            raise RuntimeError(msg)
        from gflow_cli.api.transports.migrated_composer import MigratedComposer  # noqa: PLC0415
        from gflow_cli.config import get_settings  # noqa: PLC0415

        async with self._generate_lock:
            page: Page = self._page
            await self.park_deferred_page()
            route = migrated_route(page.url, get_settings().flow_host, prefer_migrated=True)
            if route == "labs":
                return None
            if route == "blocked":
                raise_if_migrated(page, at="reference_upload_kill_switch")
            composer = MigratedComposer()
            try:
                if (
                    await composer.ensure_editor_for(page, project_id, needs_classic=True)
                    == "agent_only"
                ):
                    from gflow_cli.api.transports import agent_only_composer as agent  # noqa: PLC0415

                    raise agent.refuse_unported("image-to-image")
                media_id, display_name = await composer.upload(page, project_id, path)
            except Exception:
                # Same failure discipline as image generation (#792): keep the page for
                # the incident bundle; the next run drains the park.
                self._deferred_park_pending = True
                raise
            await self._park_composer_page(page, event="migrated.upload_page_park_failed")
            log.info("migrated.reference_uploaded", media_id=media_id)
            return ImageRef(name=media_id, display_name=display_name, in_project=True)

    def uses_page_owned_image_recaptcha(self) -> bool:
        """This transport drives Flow's own page, which mints its own reCAPTCHA token on
        click -- nothing here ever reads ``request.recaptcha_token`` (#891). The client
        uses the capability to skip its pre-mint. Narrow optional capability rather than
        a Protocol member: only UI automation owns a page."""
        return True

    async def _generate_images_locked(
        self,
        request: GenerateImageRequest,
        *,
        project_id: str | None = None,
        name_resolver: Callable[[str], str | None] | None = None,
    ) -> list[GeneratedImage]:
        """Serialized body of generate_images — called under self._generate_lock."""
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )

        page: Page = self._page  # type: ignore[assignment]  # guard in caller
        out_dir = self._out_dir

        from gflow_cli.api.transports.migrated_composer import run_images  # noqa: PLC0415
        from gflow_cli.config import get_settings  # noqa: PLC0415

        # #792: a FAILED migrated run leaves this page on the project URL on
        # purpose, so the incident bundle is captured from a live page instead of
        # about:blank. Drain that deferred park HERE — before the route decision
        # reads page.url, and before anything re-enters a still-mounted composer.
        # It also covers a client that still retries this call: since #1010 the
        # page-owned image call runs once, but a caller re-entering after a failure
        # must not resume on a dirty composer.
        await self.park_deferred_page()
        flow_host = get_settings().flow_host
        prefer = migrated_images_prefer(request, page_url=page.url, project_id=project_id)
        route = migrated_route(page.url, flow_host, prefer_migrated=prefer)
        if route == "labs":
            await self._enter_editor(page, out_dir, project_id=project_id)
            # Dismiss any Flow changelog / "What's new" overlay that may be on top
            # of the editor before we click into settings / submit (#26).
            await self._dismiss_blocking_overlays(page, out_dir)
            prefer = migrated_images_prefer(request, page_url=page.url, project_id=project_id)
            route = migrated_route(page.url, flow_host, prefer_migrated=prefer)
        if route == "blocked":
            raise_if_migrated(page, at="image_flow_host_kill_switch")
        self.last_image_route = route
        if route == "migrated":
            try:
                result = await run_images(
                    page,
                    request,
                    project_id=project_id,
                    out_dir=out_dir,
                    name_resolver=name_resolver,
                )
            except Exception:
                # #792: do NOT park here. FlowApiClient stages the incident bundle
                # from THIS page at its failure boundary, and parking first hands
                # the triager about:blank. `park_deferred_page` runs it after.
                self._deferred_park_pending = True
                raise
            except BaseException:
                # Cancellation/KeyboardInterrupt: no bundle is staged for these, so
                # nothing is waiting on the page. Attempt the park inline -- but a
                # re-delivered cancel can pre-empt it at the `goto` await, and
                # `except Exception` cannot catch that. The next run's drain is the
                # guarantee; this is the courtesy.
                self._deferred_park_pending = True
                await self._park_composer_page(page, event="migrated.image_page_park_failed")
                raise
            # Park off the project so the next borrower of this page does not
            # inherit a mounted composer (on the FAILURE path this is deferred to
            # the next run -- see park_deferred_page, #792).
            await self._park_composer_page(page, event="migrated.image_page_park_failed")
            return result

        # Determine the arm this command REQUIRES: explicit --ui-mode / env, or
        # inferred — agent instructions (-i) are an agentic-only surface, so they
        # force agentic. get_ui_driver switches to the required arm as a
        # PREREQUISITE, VERIFIES via a DOM re-probe, and fails fast
        # (UiModeUnavailableError, exit 28) if the arm is unreachable — so -i can
        # never silently no-op on a classic bind, and no credits are spent. The
        # cohort flaps per page load, so this runs every generation (no caching).
        from gflow_cli.config import infer_required_ui_mode, resolve_ui_mode

        base_mode = request.ui_mode if request.ui_mode is not None else resolve_ui_mode(None)
        required_mode = infer_required_ui_mode(
            base_mode, has_instructions=bool(request.instructions)
        )
        # Inject ``self`` into the classic driver at construction (via the
        # factory) — never mutate ``_transport`` onto the driver after the fact.
        ui_driver = await get_ui_driver(page, ui_mode=required_mode, transport=self)

        # Select Image mode explicitly. If the account was last in Video mode,
        # an unguarded submission goes to the video endpoint and the image
        # listener never observes ``batchGenerateImages``.
        await ui_driver.switch_to_image_mode(page, out_dir=out_dir)

        # Resolve the project_id from the URL now that we're in the editor.
        nav_project_id = _extract_project_id(page.url)

        # Configure generation settings (aspect ratio + count) BEFORE attaching
        # the response listener so settings clicks don't interfere with capture.
        await ui_driver.configure_image_settings(
            page,
            request,
            out_dir=out_dir,
        )

        # I2I: bind local reference images through the editor's media dialog —
        # the same add_2 dialog as video R2V, via the inherited _attach_references.
        # The REST uploadImage path 401s, and passive capture needs the refs IN
        # the UI (not just a wire body), so we attach + let Flow's JS include them.
        if request.ref_paths:
            # prefer_existing: dedup a repeated local ref by selecting the
            # already-uploaded library asset (named by its filename) instead of
            # re-uploading it (#314). Image i2i only; R2V keeps upload-every-time.
            await self._attach_references(
                page, list(request.ref_paths), out_dir=out_dir, prefer_existing=True
            )

        # Pre-generated image UUID refs: attach by selecting the EXISTING Flow
        # asset in the reference picker (no duplicate upload — founder principle),
        # falling back to uploading the local file only when it can't be located.
        if request.refs:
            await self._attach_image_uuid_refs(
                page,
                [(r.name, r.display_name, r.local_path, r.local_sha256) for r in request.refs],
                out_dir=out_dir,
                name_resolver=name_resolver,
            )

        # Entity references: attach locked CHARACTER entities via the Personagens
        # picker (inherited from the video transport). The entity must live in the
        # project we generate in (pass --project / project_id). Flow's JS then
        # includes them on the submit; the request logger below records whether the
        # outgoing batchGenerateImages carries `referenceEntities` (spike signal).
        if len(request.reference_entity_names) > len(request.reference_entities):
            # A character resolved by name only (#723: the character catalog failed) has
            # no id, and this picker attaches by id — refuse rather than drop it.
            raise MentionIndexUnavailableError(detail="the character source is unavailable")
        if request.reference_entities:
            await self._attach_character_entities(
                page,
                zip_entity_refs(request.reference_entities, request.reference_entity_names),
                out_dir=out_dir,
            )

        # Wrap generation submission in the reference entities interceptor context.
        # This programmatically filters outgoing batchGenerateImages requests so that
        # only the explicitly requested reference_entities are permitted to go to the server,
        # preventing stale or poisoned entities from smuggling themselves into unrelated runs.
        expected_ents = set(request.reference_entities)
        async with self._intercept_reference_entities(page, expected_ents):
            # Agentic path: DOM scraping (page-level network capture is dead in this
            # cohort — requests are Web-Worker-delegated, so 0 entries are captured).
            # The request + expected count are handed to the driver directly.
            from gflow_cli.api.transports.drivers.agentic import (  # noqa: PLC0415
                AgenticFlowUiDriver,
            )

            if isinstance(ui_driver, AgenticFlowUiDriver):
                return await ui_driver.submit_images(page, request, request.count, out_dir=out_dir)

            # Classic path: network-capture via response listener (unchanged).
            # Attach the response listener SYNCHRONOUSLY before any prompt
            # action. asyncio.create_task is unsafe here: it defers the listener
            # registration until the new task gets event-loop scheduling, which
            # could happen AFTER _send_prompt's click on a busy loop. Splitting
            # attach/await eliminates that race. Project-ID filter prevents stale
            # responses from previously-visited projects accumulating in the list.
            captured, detach = self._attach_batch_response_listener(page, project_id=nav_project_id)
            # Also log the OUTGOING request body summary AND collect the entity ids
            # it carries — the #170 submit backstop reads the sink after the run.
            request_bodies: list[dict[str, Any]] = []
            req_log_detach = self._attach_batch_request_logger(
                page,
                project_id=nav_project_id,
                sink=request_bodies,
                record_generation_request=self._record_generation_request,
            )
            # Record submit_time BEFORE the click so the post-submit-time filter
            # in _await_captured can distinguish this prompt's responses from any
            # stale entries that arrived between listener attach and the click.
            submit_time = time.monotonic()
            responses: list[dict[str, Any]] = []
            try:
                await ui_driver.send_prompt(page, request.prompt, out_dir=out_dir)
                responses = await self._await_captured(
                    captured,
                    expected_count=request.count,
                    submit_time=submit_time,
                )
            finally:
                detach()
                req_log_detach()

        # Collect images from ALL captured responses (Flow makes one API call
        # per image when count > 1).
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
            raise ContentPolicyError(
                detail="batchGenerateImages returned 200 but no parseable media items",
                route=first_error_route or "",
            )
        # Submit backstop (issue #170): the run is only a success if every
        # requested character entity actually rode the wire. A missed UI attach
        # (e.g. the include selector clicked the wrong element) would otherwise
        # return a plain text-only generation as if it used the character.
        if request.reference_entities:
            self._assert_image_entities_attached(
                request_bodies, expected=list(request.reference_entities)
            )
        return images

    @staticmethod
    def _assert_image_entities_attached(
        request_bodies: list[dict[str, Any]],
        *,
        expected: list[str],
    ) -> None:
        """Defense-in-depth mirror of the video path's _assert_entities_attached.

        *request_bodies* is the sink filled by :meth:`_attach_batch_request_logger`
        (one entry per captured outgoing ``batchGenerateImages`` body). Every
        *expected* entity id must appear in at least one captured submit;
        otherwise raise :class:`WireFormatError` — never report a text-only
        generation as an entity-referenced success.
        """
        seen: set[str] = set()
        for body in request_bodies:
            ids = body.get("entity_ids")
            if isinstance(ids, set):
                seen |= cast("set[str]", ids)
        missing = [e for e in expected if e not in seen]
        if missing:
            raise WireFormatError(
                detail=(
                    f"captured batchGenerateImages submit is missing "
                    f"referenceEntities {missing} — the staged character "
                    f"never rode the wire"
                ),
                route="flowMedia:batchGenerateImages",
                remediation_hint=ENTITY_ATTACH_DRIFT_HINT,
                discovery={"entity_attach_context": "image"},
            )
        log.info("ui_automation.image_entities_attached", entity_ids=sorted(seen))

    # ------------------------------------------------------------------
    # Public batch API — generate_images_batch (stay-mounted, v3-3)
    # ------------------------------------------------------------------

    async def generate_images_batch(
        self,
        *,
        prompts: list[GenerateImageRequest],
        jitter_range: tuple[float, float],
        continue_on_error: bool = False,
    ) -> list[BatchSubmissionResult]:
        """Submit all prompts into one Flow project and return per-prompt results.

        Opens the editor once, configures+submits each prompt with jitter
        between submissions, awaits and parses responses in submission order.
        The editor stays mounted for the full batch lifetime — this is the
        bug fix for --same-project=1 no-op (each call to generate_images
        previously created a new project and discarded the caller's project_id).

        With ``continue_on_error=False`` (default): the first per-prompt
        failure stops further submissions, remaining listeners are detached,
        and ``BatchPartialError`` is raised carrying any already-completed
        ``BatchSubmissionResult`` records so the orchestrator can salvage
        paid-for images before re-raising.

        With ``continue_on_error=True``: all prompts are submitted regardless
        of per-prompt failures; failed prompts produce results with
        ``status="fail"`` and a non-None ``error`` field.
        """
        if not self._setup_done or self._page is None:
            msg = "UiAutomationTransport.setup() must be called before generate_images_batch()"
            raise RuntimeError(
                msg,
            )
        # The batch path drives labs selectors only — `run_images` is the single-image
        # port. Without this it ran those selectors against flow.google.com and failed
        # as selector drift (exit 23), blaming a frontend that was fine. Refuse before
        # any submit so the user gets the non-retryable exit 36 and the real reason.
        raise_if_migrated(self._page, at="image_batch_unported")
        async with self._generate_lock:
            return await self._generate_images_batch_locked(
                prompts=prompts,
                jitter_range=jitter_range,
                continue_on_error=continue_on_error,
            )

    async def _run_one_prompt_in_batch(
        self,
        *,
        page: Page,
        idx: int,
        req: GenerateImageRequest,
        project_id: str,
        out_dir: Path | None,
        ui_driver: Any,
    ) -> tuple[BatchSubmissionResult, GFlowError | None]:
        """Single prompt's lifecycle inside a batch: configure → attach
        listener → submit → await → detach → parse.

        Returns ``(result, fatal_error)``.  ``fatal_error`` is ``None`` on
        success or whenever the failure was non-fatal (the caller can
        continue).  When non-``None`` it carries the :class:`GFlowError`
        the caller should propagate via :class:`BatchPartialError`.  Detach
        is guaranteed exactly once on every code path.
        """
        prompt_hash = _prompt_hash_stable(req.prompt)

        def _fail(exc: BaseException) -> tuple[BatchSubmissionResult, GFlowError]:
            g_exc = (
                exc
                if isinstance(exc, GFlowError)
                else GFlowError(detail=str(exc), route="generate_images_batch")
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=g_exc,
                ),
                g_exc,
            )

        # Step 1 — configure settings (aspect + count) for this prompt.
        #
        # #593 gap audit: this is an overlay epoch, and it was the last unguarded
        # one. A batch dismisses overlays ONCE during setup; from prompt 2 on,
        # these settings clicks are the first act after `_await_captured` — a
        # multi-second generation wait on a page that never navigates, so neither
        # the navigation gate nor `_probe_selector_cascade` covers them. And the
        # failure here is silent, not loud: `_open_gen_settings_panel` returns
        # False when nothing matches and the caller falls back to Flow's current
        # defaults, so a modal that mounted during prompt 1 does not fail prompt 2
        # — it generates it at the wrong aspect/count. One probe on the happy path.
        try:
            await self._require_unblocked(page, out_dir, epoch=f"batch prompt {idx}")
            await ui_driver.configure_image_settings(
                page,
                req,
                out_dir=out_dir,
                prompt_idx=idx,
            )
        except Exception as exc:
            return _fail(exc)

        # Agentic path: DOM scraping — no page-level listener (Web-Worker-delegated).
        from gflow_cli.api.transports.drivers.agentic import (  # noqa: PLC0415
            AgenticFlowUiDriver,
        )

        if isinstance(ui_driver, AgenticFlowUiDriver):
            try:
                images = await ui_driver.submit_images(page, req, req.count, out_dir=out_dir)
            except Exception as exc:
                return _fail(exc)
            return (
                BatchSubmissionResult(
                    status="ok",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=tuple(images),
                    error=None,
                ),
                None,
            )

        # Classic path: network-capture via response listener (unchanged).
        # Step 2 — attach a fresh listener JUST for this prompt.
        # Attaching after configure ensures settings-panel clicks never land
        # in the listener window.  Detach happens immediately after
        # _await_captured returns so no two listeners are ever live at once.
        captured, detach = self._attach_batch_response_listener(page, project_id=project_id)
        # Record submit_time BEFORE the click — defense-in-depth: the
        # post-submit-time filter in _await_captured rejects any stale
        # entries that slipped into the freshly-attached listener before
        # the click fired.
        submit_time = time.monotonic()

        # Step 3 — submit the prompt.
        try:
            await ui_driver.send_prompt(page, req.prompt, out_dir=out_dir)
        except Exception as exc:
            detach()
            return _fail(exc)

        # Step 4 — await THIS prompt's responses, then detach immediately.
        try:
            responses = await self._await_captured(
                captured,
                expected_count=req.count,
                submit_time=submit_time,
            )
        except Exception as exc:
            detach()
            return _fail(exc)
        detach()

        # Step 5 — parse responses.
        if len(responses) < req.count:
            err = GFlowError(
                detail=f"_await_captured timed out: got {len(responses)}/{req.count}",
                route="generate_images_batch",
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=err,
                ),
                err,
            )

        images, first_error_status, first_error_route, first_error_body = _images_from_responses(
            responses
        )
        if not images:
            # #528: classify rather than hand back a bare GFlowError with no
            # remediation — a policy 400 here needs the same guidance the
            # single-prompt path gets.
            err = (
                generation_error(
                    status=first_error_status,
                    route=first_error_route or "generate_images_batch",
                    body=first_error_body,
                )
                if first_error_status is not None
                else GFlowError(
                    detail="no parseable images (every response was 200 with no media)",
                    route="generate_images_batch",
                )
            )
            return (
                BatchSubmissionResult(
                    status="fail",
                    project_id=project_id,
                    prompt_idx=idx,
                    prompt_hash=prompt_hash,
                    images=(),
                    error=err,
                ),
                err,
            )

        return (
            BatchSubmissionResult(
                status="ok",
                project_id=project_id,
                prompt_idx=idx,
                prompt_hash=prompt_hash,
                images=tuple(images),
                error=None,
            ),
            None,
        )

    async def _generate_images_batch_locked(
        self,
        *,
        prompts: list[GenerateImageRequest],
        jitter_range: tuple[float, float],
        continue_on_error: bool,
    ) -> list[BatchSubmissionResult]:
        """Serialized body of generate_images_batch — called under self._generate_lock.

        Strictly serial submission (Worker pattern): each prompt's full
        lifecycle (configure → attach → submit → await → detach → parse)
        completes before the next prompt's listener is attached.  Only one
        listener is active at a time, making cross-contamination structurally
        impossible even when Flow's response payload carries no per-submission
        identifier.

        The editor stays mounted for the full batch (same-project invariant
        intact) — only the submit/await cycle is serial.
        """
        from gflow_cli.api.transports.drivers.factory import (  # noqa: PLC0415
            get_ui_driver,
        )

        page: Any = self._page  # type: ignore[assignment]
        out_dir = self._out_dir

        # ---- Batch-setup phase (once per batch) ----
        await self._enter_editor(page, out_dir)
        project_id = _extract_project_id(page.url)
        if project_id is None:
            msg = (
                f"Could not extract project_id from editor URL after _enter_editor. URL: {page.url}"
            )
            raise RuntimeError(
                msg,
            )

        try:
            await self._dismiss_blocking_overlays(page, out_dir)
        except Exception:
            # Orphaned-project warning: _enter_editor succeeded (server-side project
            # was created) but a later setup step failed. Log so the user can find
            # their orphaned project on the Flow UI.
            log.warning(
                "ui_automation.orphaned_project_warning",
                project_id=project_id,
                page_url=page.url,
                failed_step="_dismiss_blocking_overlays",
            )
            raise

        # Probe the DOM for the active UI cohort AFTER _enter_editor.  The
        # cohort flaps per page load; bind once per batch (the editor stays
        # mounted so the cohort is stable for this batch's lifetime).
        # Classic driver requires a transport reference for send_prompt.
        # The image-batch manifest carries no per-item -i instructions
        # (single-prompt only), so ``has_instructions`` is fixed False — but the
        # call still routes through infer_required_ui_mode so batch inherits the
        # same policy as the single path (#595: auto ≡ classic). It had its own
        # inline resolve_ui_mode and so kept binding AUTO after the single path
        # stopped. Pass instructions through here if batch ever gains them.
        from gflow_cli.config import infer_required_ui_mode, resolve_ui_mode

        required_mode = infer_required_ui_mode(resolve_ui_mode(None), has_instructions=False)
        # Inject ``self`` into the classic driver at construction (via the
        # factory) — never mutate ``_transport`` onto the driver after the fact.
        ui_driver = await get_ui_driver(page, ui_mode=required_mode, transport=self)

        try:
            await ui_driver.switch_to_image_mode(page, out_dir=out_dir)
        except Exception:
            log.warning(
                "ui_automation.orphaned_project_warning",
                project_id=project_id,
                page_url=page.url,
                failed_step="_switch_to_image_mode",
            )
            raise

        # ---- Serial per-prompt cycle: each prompt's lifecycle (configure →
        # attach → submit → await → detach → parse) is encapsulated in
        # ``_run_one_prompt_in_batch``.  This outer loop only manages
        # iteration, result collection, fail-fast control, and inter-prompt
        # jitter.
        results: list[BatchSubmissionResult] = []
        submit_error: GFlowError | None = None

        for idx, req in enumerate(prompts):
            result, fatal_err = await self._run_one_prompt_in_batch(
                page=page,
                idx=idx,
                req=req,
                project_id=project_id,
                out_dir=out_dir,
                ui_driver=ui_driver,
            )
            results.append(result)

            # Fail-fast: break before the next submission so we do not spend
            # credits on prompts the caller will not see in the success path.
            if result.status == "fail" and not continue_on_error and fatal_err is not None:
                submit_error = fatal_err
                break

            # Jitter between iterations (anti-bot cadence) — not after the last.
            if idx < len(prompts) - 1:
                await asyncio.sleep(random.uniform(*jitter_range))  # noqa: S311 - human-pacing jitter, not cryptography

        # Fail-fast: surface partial-results salvage so orchestrator can download
        # already-paid-for images before re-raising.
        if submit_error is not None and not continue_on_error:
            raise BatchPartialError(
                detail=f"batch failed at prompt index {len(results)}: {submit_error!s}",
                route="generate_images_batch",
                partial_results=tuple(r for r in results if r.status == "ok"),
                cause=submit_error,
            )

        return results
