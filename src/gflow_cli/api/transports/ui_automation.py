"""D.2.4 UiAutomationTransport — Playwright persistent-context driver for Flow.

Empirically validated 2026-05-12: mirrors the proven CG Worker pattern
(``scripts/smoke_worker_style.py``). Playwright manages its own internal CDP
port, the strategy reuses a pre-authenticated profile dir, and prompts are
submitted by typing into Flow's editor — the same surface a human developer
uses on a Pro/Ultra plan. ``batchGenerateImages`` responses are captured via
``page.on("response")`` and parsed for image URLs.

Implementation arrives in per-method TDD units; this skeleton pins the
Protocol contract.
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any, ClassVar, cast

import structlog

from gflow_cli.api._retry import parse_retry_after as parse_retry_after
from gflow_cli.api.character import CharacterImageRequest as CharacterImageRequest
from gflow_cli.api.dto import (
    BatchSubmissionResult as BatchSubmissionResult,
)
from gflow_cli.api.dto import (
    GeneratedImage as GeneratedImage,
)
from gflow_cli.api.image import (
    Aspect as Aspect,
)
from gflow_cli.api.image import (
    GenerateImageRequest as GenerateImageRequest,
)
from gflow_cli.api.image import (
    ImageRef as ImageRef,
)
from gflow_cli.api.image import (
    Model as Model,
)
from gflow_cli.api.transports import (
    _ui_capture,
    _ui_navigation,
    _ui_prompt,
    _ui_session,
    _ui_settings,
)
from gflow_cli.api.transports._common import (
    await_url_settled as await_url_settled,
)
from gflow_cli.api.transports._common import (
    close_menu as close_menu,
)
from gflow_cli.api.transports._common import (
    count_visible as count_visible,
)
from gflow_cli.api.transports._common import (
    extract_project_id as extract_project_id,
)
from gflow_cli.api.transports._common import (
    flow_host_kind as flow_host_kind,
)
from gflow_cli.api.transports._common import (
    generation_error as generation_error,
)
from gflow_cli.api.transports._common import (
    migrated_route as migrated_route,
)
from gflow_cli.api.transports._common import (
    offered_menu_labels as offered_menu_labels,
)
from gflow_cli.api.transports._common import (
    raise_if_known_landing as raise_if_known_landing,
)
from gflow_cli.api.transports._common import (
    raise_if_migrated as raise_if_migrated,
)
from gflow_cli.api.transports._ui_capture import BatchCaptureMixin as BatchCaptureMixin
from gflow_cli.api.transports._ui_common import (
    _ALLOWED_DOWNLOAD_HOST_SUFFIXES as _ALLOWED_DOWNLOAD_HOST_SUFFIXES,
)
from gflow_cli.api.transports._ui_common import (
    _ASPECT_TAB_CANDIDATES as _ASPECT_TAB_CANDIDATES,
)
from gflow_cli.api.transports._ui_common import (
    _BLOCK_PROBE_JS as _BLOCK_PROBE_JS,
)
from gflow_cli.api.transports._ui_common import (
    _BODY_SLOT_MOUNT_POLL_MS as _BODY_SLOT_MOUNT_POLL_MS,
)
from gflow_cli.api.transports._ui_common import (
    _BODY_SLOT_MOUNT_TIMEOUT_S as _BODY_SLOT_MOUNT_TIMEOUT_S,
)
from gflow_cli.api.transports._ui_common import (
    _BODY_TRIPTYCH_PREAMBLE as _BODY_TRIPTYCH_PREAMBLE,
)
from gflow_cli.api.transports._ui_common import (
    _CHANGELOG_SCOPED_CLOSE as _CHANGELOG_SCOPED_CLOSE,
)
from gflow_cli.api.transports._ui_common import (
    _CLI_FROM_ASPECT as _CLI_FROM_ASPECT,
)
from gflow_cli.api.transports._ui_common import (
    _COUNT_TAB_COUNT as _COUNT_TAB_COUNT,
)
from gflow_cli.api.transports._ui_common import (
    _COUNT_TAB_TEXT_RE as _COUNT_TAB_TEXT_RE,
)
from gflow_cli.api.transports._ui_common import (
    _DIAGNOSTICS_SUBDIR as _DIAGNOSTICS_SUBDIR,
)
from gflow_cli.api.transports._ui_common import (
    _EVT_COUNT_SETTER_COMPLETED as _EVT_COUNT_SETTER_COMPLETED,
)
from gflow_cli.api.transports._ui_common import (
    _EVT_OVERLAY_DISMISSED as _EVT_OVERLAY_DISMISSED,
)
from gflow_cli.api.transports._ui_common import (
    _FORMAT_REWRITE_MIN_DELTA as _FORMAT_REWRITE_MIN_DELTA,
)
from gflow_cli.api.transports._ui_common import (
    _FORMAT_REWRITE_POLL_MS as _FORMAT_REWRITE_POLL_MS,
)
from gflow_cli.api.transports._ui_common import (
    _FORMAT_REWRITE_TIMEOUT_S as _FORMAT_REWRITE_TIMEOUT_S,
)
from gflow_cli.api.transports._ui_common import (
    _GENERIC_CLOSE as _GENERIC_CLOSE,
)
from gflow_cli.api.transports._ui_common import (
    _NEW_PROJECT_PROBE_TIMEOUT_MS as _NEW_PROJECT_PROBE_TIMEOUT_MS,
)
from gflow_cli.api.transports._ui_common import (
    _ONBOARDING_STRUCTURAL_SELECTORS as _ONBOARDING_STRUCTURAL_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    _ONBOARDING_TEXT_SELECTORS as _ONBOARDING_TEXT_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    _PROJECT_URL_FRAGMENT as _PROJECT_URL_FRAGMENT,
)
from gflow_cli.api.transports._ui_common import (
    _REF_VALUE_MAX_CHARS as _REF_VALUE_MAX_CHARS,
)
from gflow_cli.api.transports._ui_common import (
    _SUPPORTED_COUNTS as _SUPPORTED_COUNTS,
)
from gflow_cli.api.transports._ui_common import (
    _VIEWPORT as _VIEWPORT,
)
from gflow_cli.api.transports._ui_common import (
    CHANGELOG_IFRAME_SELECTORS as CHANGELOG_IFRAME_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    FLOW_URL as FLOW_URL,
)
from gflow_cli.api.transports._ui_common import (
    GEN_SETTINGS_BUTTON_SELECTORS as GEN_SETTINGS_BUTTON_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    IMAGE_MODEL_OPTION_SELECTORS as IMAGE_MODEL_OPTION_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    IMAGE_MODEL_PICKER_TRIGGER as IMAGE_MODEL_PICKER_TRIGGER,
)
from gflow_cli.api.transports._ui_common import (
    IMAGE_TAB_IN_MENU_SELECTORS as IMAGE_TAB_IN_MENU_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    NEW_PROJECT_SELECTORS as NEW_PROJECT_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    ONBOARDING_SELECTORS as ONBOARDING_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    OVERLAY_CLOSE_BUTTON_SELECTORS as OVERLAY_CLOSE_BUTTON_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    PROMPT_FORMAT_SELECTORS as PROMPT_FORMAT_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    PROMPT_INPUT_SELECTORS as PROMPT_INPUT_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    SUBMIT_BUTTON_SELECTORS as SUBMIT_BUTTON_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    TOP_BANNER_SELECTORS as TOP_BANNER_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    WELCOME_SCREEN_SELECTORS as WELCOME_SCREEN_SELECTORS,
)
from gflow_cli.api.transports._ui_common import (
    _AnyList as _AnyList,
)
from gflow_cli.api.transports._ui_common import (
    _capture_debug_screenshot as _capture_debug_screenshot,
)
from gflow_cli.api.transports._ui_common import (
    _collect_images_from_body as _collect_images_from_body,
)
from gflow_cli.api.transports._ui_common import (
    _count_tab_locator_for as _count_tab_locator_for,
)
from gflow_cli.api.transports._ui_common import (
    _count_tabs_locator as _count_tabs_locator,
)
from gflow_cli.api.transports._ui_common import (
    _elide_large_value as _elide_large_value,
)
from gflow_cli.api.transports._ui_common import (
    _extract_project_id as _extract_project_id,
)
from gflow_cli.api.transports._ui_common import (
    _images_from_responses as _images_from_responses,
)
from gflow_cli.api.transports._ui_common import (
    _is_allowed_download_host as _is_allowed_download_host,
)
from gflow_cli.api.transports._ui_common import (
    _jitter_ms as _jitter_ms,
)
from gflow_cli.api.transports._ui_common import (
    _JsonObj as _JsonObj,
)
from gflow_cli.api.transports._ui_common import (
    _prompt_hash_stable as _prompt_hash_stable,
)
from gflow_cli.api.transports._ui_common import (
    _reference_field_count as _reference_field_count,
)
from gflow_cli.api.transports._ui_common import (
    _summarize_batch_request_body as _summarize_batch_request_body,
)
from gflow_cli.api.transports._ui_common import (
    _try_iframe_close_button as _try_iframe_close_button,
)
from gflow_cli.api.transports._ui_common import (
    aspect_cli_from_enum as aspect_cli_from_enum,
)
from gflow_cli.api.transports._ui_prompt import PromptInputMixin as PromptInputMixin
from gflow_cli.api.transports._ui_settings import GenerationSettingsMixin as GenerationSettingsMixin
from gflow_cli.api.transports.migrated_composer import (
    MENU_ITEM as MENU_ITEM,
)
from gflow_cli.api.transports.migrated_composer import (
    ModelMenuMatcher as ModelMenuMatcher,
)
from gflow_cli.api.transports.migrated_composer import (
    migrated_images_prefer as migrated_images_prefer,
)
from gflow_cli.api.transports.ui_automation_entities import (
    entity_ids_from_request_body as _entity_ids_from_request_body,  # noqa: F401  # pyright: ignore[reportUnusedImport]
)
from gflow_cli.api.transports.ui_automation_video import (
    ENTITY_ATTACH_DRIFT_HINT as ENTITY_ATTACH_DRIFT_HINT,
)
from gflow_cli.api.transports.ui_automation_video import (
    MODE_SWITCH_TRIGGER_SELECTORS as MODE_SWITCH_TRIGGER_SELECTORS,
)
from gflow_cli.api.transports.ui_automation_video import (
    VideoGenerationMixin as VideoGenerationMixin,
)
from gflow_cli.api.transports.ui_automation_video import (
    screenshot_clause as screenshot_clause,
)
from gflow_cli.api.transports.ui_automation_video import (
    selector_drift_detail as selector_drift_detail,
)
from gflow_cli.api.transports.ui_automation_video import (
    zip_entity_refs as zip_entity_refs,
)
from gflow_cli.errors import (
    AuthExpiredError as AuthExpiredError,
)
from gflow_cli.errors import (
    BatchPartialError as BatchPartialError,
)
from gflow_cli.errors import (
    ConfigurationError as ConfigurationError,
)
from gflow_cli.errors import (
    ContentPolicyError as ContentPolicyError,
)
from gflow_cli.errors import (
    FlowAppError as FlowAppError,
)
from gflow_cli.errors import (
    GFlowError as GFlowError,
)
from gflow_cli.errors import (
    MentionIndexUnavailableError as MentionIndexUnavailableError,
)
from gflow_cli.errors import (
    RateLimitError as RateLimitError,
)
from gflow_cli.errors import (
    UiSelectorDriftError as UiSelectorDriftError,
)
from gflow_cli.errors import (
    WafRejectionError as WafRejectionError,
)
from gflow_cli.errors import (
    WireFormatError as WireFormatError,
)
from gflow_cli.profile_lease import ProfileLease as ProfileLease

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from playwright.async_api import Page, ViewportSize


# Lazy-imported at call time so ``import gflow_cli`` doesn't pay the
# Playwright import cost when another transport is selected.
try:  # pragma: no cover — re-bound at module import in production
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover — Playwright is an install dependency
    async_playwright = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)


class UiAutomationTransport(VideoGenerationMixin, BatchCaptureMixin):
    """D.2.4 — Playwright UI mimicry strategy.

    Drives the Flow editor on a logged-in Pro/Ultra profile through a
    Playwright-managed persistent context. The strategy never exposes an
    external CDP debug port; Playwright's internal port is sufficient and
    keeps the browser environment indistinguishable from a typical
    developer session.

    Lifecycle (Protocol § 4.1)::

        await transport.setup(profile_dir)
        images = await transport.generate_images(project_id=..., request=...)
        await transport.teardown()
    """

    _page: Page | None
    name = "ui_automation"

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        """Acquire a Page on the logged-in Flow editor.

        Idempotent — second call is a no-op.

        When ``page`` is provided (shared-page path), the caller owns the
        Playwright lifecycle; teardown() will not close the context. When
        ``page`` is None, the strategy opens its own persistent context
        against ``profile_dir`` and is responsible for its full lifecycle.

        An initial ``page.goto(FLOW_URL)`` is attempted; a navigation
        failure is logged but not raised — auth/UI recovery happens in
        ``generate_images``.
        """
        if self._setup_done:
            return

        if page is not None:
            # Shared-page path: caller owns Playwright lifecycle.
            self._page = page
            self._owns_playwright = False
            self._setup_done = True
            log.info("ui_automation.setup_shared_page")
            return

        # Engine selection (standalone-context path; the shared-page path above
        # already returned). Default playwright keeps the module symbol; only the
        # opt-in patchright engine routes through the resolver.
        from gflow_cli.api._engine import (
            active_engine,
            close_context_bounded,
            log_engine_selected,
            resolve_async_playwright,
        )
        from gflow_cli.config import BrowserEngine

        engine = active_engine()
        log_engine_selected(engine)
        if engine == BrowserEngine.PATCHRIGHT:
            pw_factory = resolve_async_playwright(engine)
        elif async_playwright is None:  # pragma: no cover — install-time guard
            msg = (
                "Playwright is required for UiAutomationTransport. "
                "Install via `uv sync` (it is a runtime dependency)."
            )
            raise RuntimeError(
                msg,
            )
        else:
            pw_factory = async_playwright

        pw_cm = pw_factory()
        # The two engines (playwright | patchright) expose a structurally identical
        # ``.chromium.launch_persistent_context`` surface; type as Any so this
        # standalone path is engine-agnostic without per-engine stubs.
        pw: Any = await pw_cm.__aenter__()
        ctx = None
        try:
            import os

            from gflow_cli.browser_manager import (
                browser_proxy_kwargs,
                channel_for_profile,
                disable_features_args,
                ensure_profile_engine_compatible,
                window_position_args,
            )
            from gflow_cli.config import get_settings

            # Own the profile BEFORE Chrome launches (D3). Contention raises
            # ProfileLockedError here; the except below tears the driver back
            # down. aacquire so a #478 opt-in wait polls with asyncio.sleep
            # instead of blocking the event loop.
            self._lease = await ProfileLease(profile_dir).aacquire()
            locale_env = os.getenv("GFLOW_CLI_LOCALE", "en-US")
            channel = channel_for_profile(profile_dir)
            # #477: refuse a bundled-Chromium open of a profile last written by
            # a newer Chromium — downgrade cleanup can shred the session store.
            ensure_profile_engine_compatible(profile_dir, channel)
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                viewport=cast("ViewportSize", _VIEWPORT),
                locale=locale_env,
                channel=channel,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                    "--disable-dev-shm-usage",
                    *window_position_args(get_settings().browser_window_position),
                    *disable_features_args(),
                ],
                **browser_proxy_kwargs(get_settings().browser_proxy),
            )
            # Hide the automation flag so reCAPTCHA Enterprise doesn't score
            # the session as a bot — navigator.webdriver=true causes low-score
            # tokens and HTTP 403 on batchGenerateImages.
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})",
            )
            self._pw_cm = pw_cm
            self._ctx = ctx
            page = cast("Page", ctx.pages[0] if ctx.pages else await ctx.new_page())
            self._page = page
            try:
                await page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
            except Exception as e:
                log.warning("ui_automation.flow_initial_goto_failed", error=str(e))
            self._owns_playwright = True
            self._setup_done = True
            log.info(
                "ui_automation.setup_own_context",
                profile_dir=str(profile_dir),
            )
        except BaseException:
            # Partial-setup leak guard. Catches BaseException (not just
            # Exception) so a CancelledError mid-setup ALSO tears down the
            # launched context (D4) — otherwise a cancelled launch would orphan
            # chrome and leak the lease. The context must be closed BEFORE the
            # driver exits — stopping only the driver leaves the detached
            # system-Chrome alive holding the profile dir (issue #293). Each
            # cleanup step is bounded + shielded so the cleanup itself cannot be
            # interrupted before the lease release; the original error re-raises.
            from gflow_cli.api._engine import (  # noqa: PLC0415
                CONTEXT_TEARDOWN_TIMEOUT_S,
                DRIVER_STOP_TIMEOUT_S,
                run_teardown_step,
            )

            if ctx is not None:
                await run_teardown_step(
                    close_context_bounded(ctx, owner="ui_automation"),
                    timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                    owner="ui_automation",
                    step="setup_context_close",
                )
            await run_teardown_step(
                pw_cm.__aexit__(None, None, None),
                timeout=DRIVER_STOP_TIMEOUT_S,
                owner="ui_automation",
                step="setup_driver_exit",
            )
            # Release the lease last — after context + driver are down (D3).
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            raise

    # ------------------------------------------------------------------
    # Internal helpers — auth detection (unit 3.3)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — gallery → editor navigation (unit 3.4)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — prompt submission (unit 3.5)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — generation settings (aspect ratio + count)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — generation settings (aspect ratio + count)
    # Moved to _ui_settings.GenerationSettingsMixin (#1249 wave 2)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — batchGenerateImages capture (unit 3.6)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Internal helpers — batchGenerateImages capture and image download
    # Moved to _ui_capture.BatchCaptureMixin (#1249 wave 2)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Protocol — generate_images (unit 3.9)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Character editor — navigation + passive-capture entry (T4)
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # Protocol — refresh_auth (unit 3.10) + teardown (unit 3.11)
    # ------------------------------------------------------------------


# Bind class into mixin modules for tests that patch UiAutomationTransport._x
_ui_session.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
_ui_navigation.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
_ui_prompt.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
_ui_settings.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
_ui_capture.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
