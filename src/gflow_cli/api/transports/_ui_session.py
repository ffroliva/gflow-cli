"""Session management, context lifecycle and navigation utilities for UiAutomationTransport.

Mixed into `UiAutomationTransport` via `UiSessionMixin` (#1249 wave 2).
"""

# pyright: reportPrivateUsage=false
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import structlog
from playwright.async_api import Page

from gflow_cli.api.transports._common import flow_host_kind
from gflow_cli.api.transports._ui_common import (
    _PROJECT_URL_FRAGMENT,
    ONBOARDING_SELECTORS,
    _jitter_ms,
)

if TYPE_CHECKING:
    from pathlib import Path

    from gflow_cli.api.transports.base import GenerationRequestRecorder, TransportSetup
    from gflow_cli.profile_lease import ProfileLease

log = structlog.get_logger(__name__)


class UiSessionMixin:
    """Context lifecycle, login verification and session parking."""

    async def _wait_jitter(self, page: Page, base_ms: int, variance: float = 0.25) -> None:
        """Wait for a jittered duration using page.wait_for_timeout."""
        jittered = _jitter_ms(base_ms, variance=variance)
        if jittered > 0:
            await page.wait_for_timeout(jittered)

    def __init__(self) -> None:
        self._pw_cm: Any | None = None
        self._ctx: Any | None = None
        self._page: Page | None = None
        self._setup_done: bool = False
        self._owns_playwright: bool = False
        # #792: set when a migrated run FAILED, so the park that would otherwise
        # run in a `finally` is deferred past FlowApiClient's incident capture.
        self._deferred_park_pending: bool = False
        # The route the last image call took ("labs" / "migrated"): the page is
        # parked on about:blank afterwards, so its URL no longer says (#1035).
        self.last_image_route: str | None = None
        # Set when a flow.google.com composer page is parked on about:blank (image,
        # video or upload run), so the client can still tell the host it served (#914).
        self.parked_after_migrated: bool = False
        # Cross-process profile lease (D3). Held ONLY on the standalone-context
        # path (setup with page=None), where this transport owns the persistent
        # context. On the shared-page path the caller (FlowApiClient) owns both
        # the context and the lease, so this stays None.
        self._lease: ProfileLease | None = None
        # Typed output/storage wiring handed in by FlowApiClient through the
        # public apply_setup() seam (SupportsTransportSetup). The private slots
        # below are this transport's own derived state — the client no longer
        # writes them directly.
        self.setup_config: TransportSetup | None = None
        # Counts-only incident sink for outgoing generation submits (#528).
        # None until apply_setup runs, and whenever incident capture is off.
        self._record_generation_request: GenerationRequestRecorder | None = None
        # Account locale segment injected by the client (#580). None => bare URLs.
        self._account_locale: str | None = None
        # Optional directory for debug screenshots — derived from the client's
        # `out_dir` constructor arg (#18). When None, the internal
        # _capture_debug_screenshot helper is a no-op.
        self._out_dir: Path | None = None
        # Optional cloud-storage configuration. Video downloads read these slots
        # inside VideoGenerationMixin._download_video.
        self._storage_uri: str | None = None
        self._output_dir: Path | None = None
        # Serialize concurrent generate_images calls — a single Playwright Page
        # cannot be safely shared across parallel asyncio tasks (each call
        # navigates, opens panels, and types into the same DOM). The lock
        # converts the N-parallel fan-out from generate_images_batch into N
        # sequential Page interactions, eliminating all race conditions.
        self._generate_lock: asyncio.Lock = asyncio.Lock()

    def apply_setup(self, config: TransportSetup) -> None:
        """Accept output/storage wiring publicly (SupportsTransportSetup seam).

        Stores the immutable record and derives the private slots the debug-
        screenshot and video-download paths read. ``out_dir`` is only adopted
        when set, preserving the prior "don't clobber with None" plumbing
        behaviour; ``storage_uri``/``output_dir`` mirror the config as-is.
        """
        self.setup_config = config
        if config.out_dir is not None:
            self._out_dir = config.out_dir
        self._storage_uri = config.storage_uri
        self._output_dir = config.output_dir
        self._record_generation_request = config.record_generation_request
        self._account_locale = config.account_locale

    @staticmethod
    async def _check_logged_in(page: Page) -> bool:
        """True if the page shows the authenticated Flow UI.

        Gates (pattern G13):
        - URL's host is one of Flow's own origins — ``labs.google`` or, since
          #639, the migrated ``flow.google.com``. Locale-stable: /fx/pt/tools/flow,
          /fx/es/tools/flow etc. all match, and so does the migrated /project/<id>.
        - /project/<uuid> URLs short-circuit to True (editor already open).
        - Otherwise reject if a top-level Sign-in CTA is visible.

        The host is PARSED and matched exactly. This gate used to be the
        substring test ``"labs.google" in page.url``, which any foreign URL
        satisfies simply by carrying the string in a path or query.

        A failure in the locator probe is treated as "no Sign-in button"
        — the URL gate already established Flow context, and a transient
        DOM error shouldn't force a re-auth loop.
        """
        if flow_host_kind(page.url) is None:
            return False
        if _PROJECT_URL_FRAGMENT in page.url:
            return True
        try:
            signin_button = await page.locator(
                "button:has-text('Sign in'), a:has-text('Sign in')",
            ).count()
        except Exception:
            signin_button = 0
        return signin_button == 0

    async def _bypass_onboarding(self, page: Page) -> None:
        """Click through cookie banners and 'Get Started' pages if they appear."""
        for selector in ONBOARDING_SELECTORS:
            try:
                loc = page.locator(selector).first
                if await loc.is_visible(timeout=1000):
                    await loc.click(force=True)
                    log.info("ui_automation.onboarding_bypassed", selector=selector)
                    await page.wait_for_timeout(1000)
            except Exception:
                continue

    async def _park_composer_page(self, page: Any, *, event: str) -> None:
        """Park a composer page on about:blank. Best-effort; never raises.

        The pooled page would otherwise stay on ``flow.google.com/project/<id>``:
        the next request on this client would be routed by that URL instead of by
        its own shape (an unmoved account's i2v -> exit 36, or a silently reused
        project), and the next borrower would inherit a mounted composer.
        """
        self.parked_after_migrated = True
        try:
            await page.goto("about:blank", wait_until="commit", timeout=5_000)
        except Exception as exc:  # noqa: BLE001 - parking is best-effort
            log.warning(event, error=str(exc)[:120])

    async def park_deferred_page(self) -> None:
        """Park a page whose park a FAILED migrated run deferred (#792).

        The park used to sit in a bare ``finally``, so on the failure path it
        navigated to about:blank BEFORE ``FlowApiClient._capture_incident`` staged
        the bundle -- whose own contract is to capture "while the page is still
        alive". Every migrated failure therefore shipped ``tag_counts.div = 0``, a
        white screenshot and ``host_category = "other"`` next to a network journal
        showing the app alive, which is unreadable as evidence.

        Draining it at the START of the next run -- before the route decision reads
        ``page.url`` -- is what makes deferring safe. Doing it at the client's
        failure boundary was not enough: ``generate_images`` used to be retried inside
        ``post_with_retry`` (``client.py``; once per call since #1010), so a failure
        could skip that boundary and re-enter a still-mounted composer. Draining
        here holds the invariant at the only point that consumes it, and resets the
        latch on every path (success, retry, or a later unrelated run).
        """
        if not self._deferred_park_pending:
            return
        self._deferred_park_pending = False
        page = self._page
        if page is not None:
            await self._park_composer_page(page, event="migrated.deferred_page_park_failed")

    async def refresh_auth(self) -> None:
        """No-op for the UI strategy.

        Flow's own JavaScript re-mints reCAPTCHA tokens and refreshes
        auth state inside the Page on every prompt submission. There is
        no separate token cache to refresh from this strategy's side.
        Kept on the Protocol surface for consistency with the HTTP
        strategies (S1/S2/S3) where refresh_auth has real work to do.
        """
        await asyncio.sleep(0)  # yield to event loop — Protocol-required async signature
        log.debug("ui_automation.refresh_auth_noop")

    async def teardown(self) -> None:
        """Close the Playwright context if this strategy owns it.

        Idempotent — safe to call multiple times. When ``_owns_playwright``
        is False (shared-page setup) the caller retains lifecycle
        ownership; this method releases nothing and just resets state.
        """
        if not self._setup_done:
            return
        from gflow_cli.api._engine import (  # noqa: PLC0415
            CONTEXT_TEARDOWN_TIMEOUT_S,
            DRIVER_STOP_TIMEOUT_S,
            close_context_bounded,
            run_teardown_step,
        )

        # Cancellation-complete teardown (D4): each step is bounded + shielded so
        # a CancelledError landing mid-close cannot skip the driver exit or the
        # lease release in `finally`; the original cancellation re-raises last.
        # Teardown order: (4) close context -> exit driver; (6) release lease.
        cancelled: BaseException | None = None
        try:
            if self._owns_playwright and self._pw_cm is not None:
                if self._ctx is not None:
                    # Bounded close + force-close fallback (issue #293) — same
                    # helper as FlowApiClient's teardown; this standalone path
                    # had the identical unbounded-close-and-swallow gap.
                    cancelled = (
                        await run_teardown_step(
                            close_context_bounded(self._ctx, owner="ui_automation"),
                            timeout=CONTEXT_TEARDOWN_TIMEOUT_S,
                            owner="ui_automation",
                            step="context_close",
                        )
                        or cancelled
                    )
                cancelled = (
                    await run_teardown_step(
                        self._pw_cm.__aexit__(None, None, None),
                        timeout=DRIVER_STOP_TIMEOUT_S,
                        owner="ui_automation",
                        step="driver_exit",
                    )
                    or cancelled
                )
        finally:
            # Release the profile lease last — after the context is closed and
            # the driver stopped (D3). No-op on the shared-page path (lease is
            # None). Field resets survive even a cancelled teardown.
            if self._lease is not None:
                self._lease.release()
                self._lease = None
            self._pw_cm = None
            self._ctx = None
            self._page = None
            self._setup_done = False
            self._owns_playwright = False
        if cancelled is not None:
            raise cancelled
