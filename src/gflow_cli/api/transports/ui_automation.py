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

from typing import TYPE_CHECKING, Any, cast

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
from gflow_cli.api.transports._ui_character import (
    CharacterGenerationMixin as CharacterGenerationMixin,
)
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
from gflow_cli.api.transports._ui_image import ImageGenerationMixin as ImageGenerationMixin
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
    from pathlib import Path

    from playwright.async_api import Page, ViewportSize


# Lazy-imported at call time so ``import gflow_cli`` doesn't pay the
# Playwright import cost when another transport is selected.
try:  # pragma: no cover — re-bound at module import in production
    from playwright.async_api import async_playwright
except ImportError:  # pragma: no cover — Playwright is an install dependency
    async_playwright = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)


class UiAutomationTransport(VideoGenerationMixin, ImageGenerationMixin):
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


# Bind class into mixin modules for tests that patch UiAutomationTransport._x
_ui_settings.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
_ui_capture.UiAutomationTransport = UiAutomationTransport  # type: ignore[attr-defined]
