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

from __future__ import annotations

import asyncio
import hashlib
import json
import random
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import structlog

from gflow_cli.api.dto import BatchSubmissionResult, GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest, Model
from gflow_cli.api.transports._common import extract_project_id
from gflow_cli.api.transports.ui_automation_video import (
    MODE_SWITCH_TRIGGER_SELECTORS,
    VideoGenerationMixin,
)
from gflow_cli.errors import (
    AuthExpiredError,
    BatchPartialError,
    ContentPolicyError,
    GFlowError,
    WafRejectionError,
    WireFormatError,
)

if TYPE_CHECKING:
    from playwright.async_api import Locator, Page, ViewportSize

# Lazy-imported at call time so ``import gflow_cli`` doesn't pay the
# Playwright import cost when another transport is selected.
try:  # pragma: no cover — re-bound at module import in production
    from playwright.async_api import async_playwright as async_playwright
except ImportError:  # pragma: no cover — Playwright is an install dependency
    async_playwright = None  # type: ignore[assignment]

log = structlog.get_logger(__name__)

# Flow editor entrypoint — ``?hl=en`` locks locale for selector stability.
FLOW_URL = "https://labs.google/fx/tools/flow?hl=en"
# URL fragment that distinguishes the project editor from the gallery.
_PROJECT_URL_FRAGMENT = "/project/"

# Image model picker (SOT flow-editor-map.json). Same arrow_drop_down trigger as
# video; options matched by product name (NOT localized — but the editor must be
# in English, forced via the --lang=en-US launch arg). 'Nano Banana 2' is not a
# substring of 'Nano Banana Pro', so has-text is unambiguous across the three.
IMAGE_MODEL_PICKER_TRIGGER = (
    "button[aria-haspopup='menu']:has(i.google-symbols:text-is('arrow_drop_down'))"
)
IMAGE_MODEL_OPTION_SELECTORS: dict[Model, str] = {
    Model.NARWHAL: "[role='menuitem']:has-text('Nano Banana 2')",
    Model.GEM_PIX_2: "[role='menuitem']:has-text('Nano Banana Pro')",
    Model.IMAGEN_3_5: "[role='menuitem']:has-text('Imagen 4')",
}

# Image-mode tab inside the mode-switch dropdown.  Selectors are tried in
# order; the leading ``aria-controls`` matches are language-independent
# (Flow's accessibility wiring keeps the IMAGE token across locales),
# the ``has-text`` variants are Portuguese/English fallbacks, and the
# icon-ligature is a last resort.  Mirror of
# :data:`ui_automation_video.VIDEO_TAB_IN_MENU_SELECTORS`.
IMAGE_TAB_IN_MENU_SELECTORS = (
    "[role='menu'] [role='tab'][aria-controls*='IMAGE']",
    "[role='tab'][aria-controls*='IMAGE']",
    "[role='menu'] [role='tab']:has-text('Imagem')",
    "[role='menu'] [role='tab']:has-text('Image')",
    "[role='menu'] [role='tab']:has(i:text('image'))",
)

# Browser viewport — matches the validated smoke (also matches the CG Worker).
_VIEWPORT = {"width": 1280, "height": 800}

# Hosts allowed when downloading generated PNGs. Flow's fifeUrl currently
# resolves to lh3.googleusercontent.com; the broader allow-list covers
# Google-owned redirect targets without leaking session cookies elsewhere.
# Suffix-match: "googleusercontent.com" matches "lh3.googleusercontent.com".
_ALLOWED_DOWNLOAD_HOST_SUFFIXES: tuple[str, ...] = (
    "googleusercontent.com",
    "googleapis.com",
    "google.com",
)


def _is_allowed_download_host(url: str) -> bool:
    """True if ``url``'s host ends with one of the allowed Google domains.

    Refuses URLs that lack a host or use a non-https scheme — both shapes
    are unexpected for Flow-issued fifeUrls and treating them as suspect
    is safer than treating them as trustworthy.
    """
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme != "https" or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in _ALLOWED_DOWNLOAD_HOST_SUFFIXES
    )


async def _capture_debug_screenshot(
    page: Any,
    out_dir: Path | None,
    filename: str,
) -> Path | None:
    """Best-effort viewport screenshot for debug troubleshooting.

    Writes to ``out_dir / filename`` and returns the path, or ``None``
    when ``out_dir`` is not provided. Captures only the current viewport
    (``full_page=False``) to bound the PII surface — even a viewport
    screenshot of a logged-in Flow page includes the user's avatar /
    email indicator in the top-right corner, so a warning is logged so
    the operator knows the file may contain identifying information.

    Failures during screenshot capture are swallowed — debugging aids
    must not become a second source of exceptions during a real failure.
    """
    if out_dir is None:
        return None
    out_dir.mkdir(parents=True, exist_ok=True)
    shot_path = out_dir / filename
    try:
        await page.screenshot(path=str(shot_path), full_page=False)
        log.warning(
            "ui_automation.debug_screenshot_may_contain_pii",
            path=str(shot_path),
            note=(
                "viewport may include account avatar / email indicator from "
                "the authenticated Google session"
            ),
        )
    except Exception as e:  # noqa: BLE001 — screenshot is best-effort
        log.debug("ui_automation.screenshot_capture_failed", error=str(e))
    return shot_path


# Prompt input selectors — Slate.js editor is the canonical target on
# Flow's editor page; the contenteditable/textarea fallbacks cover UI
# evolutions.
PROMPT_INPUT_SELECTORS = (
    'div[role="textbox"][data-slate-editor="true"]',
    'div[contenteditable="true"]',
    "textarea",
    '[aria-label*="prompt"]',
)

# Submit button selectors — arrow_forward icon is locale-stable.
# Use :text() inside :has() (not :has-text() which is invalid inside :has()).
SUBMIT_BUTTON_SELECTORS = (
    "button:has(i.google-symbols:text('arrow_forward'))",
    "button:has(i:text('arrow_forward'))",
    "button:has-text('arrow_forward')",
    'button[aria-label*="Create"]',
)

# "+ New project" CTA selectors. Pattern G13: the Material Symbols icon
# (``i.google-symbols`` with inner text ``add_2``) is locale-stable; the
# localized button label ("New project", "Novo projeto", ...) is not.
# Icon-class match is tried first; localized text variants are fallbacks.
NEW_PROJECT_SELECTORS = (
    "button:has(i.google-symbols:text('add_2'))",
    "button:has(i:text('add_2'))",
    "button:has-text('New project')",
    "button:has-text('Novo projeto')",
    "button:has-text('Nuevo proyecto')",
    "button:has-text('Nouveau projet')",
    "[role='button']:has-text('New project')",
    "a:has-text('New project')",
    r"button:text-matches('\+\s+\S+', 'i')",
    "[aria-label*='New project' i]",
    "[aria-label*='Project' i]",
)

# Onboarding bypass selectors — cookie banners, terms, landing pages.
# Handled gracefully if not found; localized variants included.
ONBOARDING_SELECTORS = (
    "button:has-text('Agree')",
    "button:has-text('Aceitar')",
    "button:has-text('I agree')",
    "button:has-text('Concordo')",
    "button:has-text('Accept')",
    "button:has-text('Create with Flow')",
    "button:has-text('Criar com o Flow')",
    "button:has-text('Get Started')",
    "button:has-text('Começar')",
)

# Changelog / "What's new" iframe selectors — these src patterns match the
# gstatic CDN paths Flow uses for its release-note overlays. Two patterns are
# included: the /flow/ prefix form and the bare /changelogs/ form.
CHANGELOG_IFRAME_SELECTORS = (
    "iframe[src*='/flow/changelogs/']",
    "iframe[src*='/changelogs/']",
)

# Close-button selectors tried after a changelog iframe is detected.
# Ordered from most-specific to most-generic so a precise match wins first.
# All are tried before the Escape fallback.
OVERLAY_CLOSE_BUTTON_SELECTORS = (
    "[aria-label='Close']",
    "[aria-label='close']",
    "[aria-label='Dismiss']",
    "[aria-label='dismiss']",
    "[aria-label='Cancel']",
    "button:has(i.google-symbols:text('close'))",
    "button:has(i:text('close'))",
    "[role='dialog'] button:has(i:text('close'))",
    "[role='dialog'] button[aria-label*='close' i]",
    "button[data-dismiss]",
)


# Generation settings trigger — the button shows the current ratio icon.
# All 5 ratio icon names are enumerated so the selector is ratio-invariant.
GEN_SETTINGS_BUTTON_SELECTORS = (
    "button:has(i.google-symbols:text('crop_16_9'))",
    "button:has(i.google-symbols:text('crop_9_16'))",
    "button:has(i.google-symbols:text('crop_square'))",
    "button:has(i.google-symbols:text('crop_portrait'))",
    "button:has(i.google-symbols:text('crop_landscape'))",
)

# CLI string → ordered list of candidate tab labels to try in the Flow gen
# settings panel. Most ratios are labelled with their colon-numeric form
# ("16:9"), but the "1:1" tab is sometimes rendered as "Square" or "1×1"
# (multiplication sign U+00D7) — we try a small cascade and the first
# locator that becomes visible wins.
_ASPECT_TAB_CANDIDATES: dict[str, tuple[str, ...]] = {
    "16:9": ("16:9",),
    "9:16": ("9:16",),
    "1:1": ("1:1", "Square", "1×1", "1x1"),
    "4:3": ("4:3",),
    "3:4": ("3:4",),
}

# Supported image-count values for the xN selector.
_SUPPORTED_COUNTS: frozenset[int] = frozenset({1, 2, 3, 4})

# Number of count tabs Flow renders in the settings panel (1 through 4).
_COUNT_TAB_COUNT = 4

# Structured event name emitted at every exit path of `_set_count`.
# Extracted to a module-level constant to satisfy SonarCloud S1192
# (duplicate literal) and keep the spelling consistent across log sites.
_EVT_COUNT_SETTER_COMPLETED = "ui_automation.count_setter_completed"

# Regex that matches count-tab text exactly: "1x", "x2", "x3", "x4".
# These are the ONLY role="tab" elements whose text fits this pattern —
# Mode tabs ("image\nImagem") and Aspect tabs ("16:9", "crop_square") do not.
# The pattern is locale-invariant: Flow never translates the digit+x label.
_COUNT_TAB_TEXT_RE = re.compile(r"^(1x|x[2-4])$")

# Subdirectory inside out_dir where diagnostic artefacts are written.
# Keeps count_before/after screenshots and DOM dumps out of the user-facing
# output directory so file-count assertions on *.png never pick them up.
_DIAGNOSTICS_SUBDIR = "_diagnostics"


def _count_tabs_locator(page: Page) -> Locator:
    """Return a Playwright Locator that matches ONLY the 4 count tabs.

    Filters ``role="tab"`` elements by text matching :data:`_COUNT_TAB_TEXT_RE`
    (``1x``, ``x2``, ``x3``, ``x4``). This pattern is unique to count tabs —
    Mode tabs and Aspect tabs never match it — so the filter survives all three
    Radix tablists being present in the DOM simultaneously.

    DOM evidence: ``tmp/dom_dump.json`` captured on profile denon82 (Portuguese
    locale, 2026-05-22) shows all three tablists rendered; count tabs are the
    only ones whose ``text`` is ``"1x"`` / ``"x2"`` / ``"x3"`` / ``"x4"``.
    """
    return page.locator('[role="tab"]').filter(has_text=_COUNT_TAB_TEXT_RE)


# Reverse map: domain Aspect enum → CLI string accepted by the settings panel.
_CLI_FROM_ASPECT: dict[Aspect, str] = {
    Aspect.PORTRAIT: "9:16",
    Aspect.LANDSCAPE: "16:9",
    Aspect.SQUARE: "1:1",
    Aspect.LANDSCAPE_FOUR_THREE: "4:3",
    Aspect.PORTRAIT_THREE_FOUR: "3:4",
}


def _aspect_cli_from_enum(aspect: Aspect) -> str | None:
    """Map the domain Aspect enum to the CLI string the settings panel expects."""
    return _CLI_FROM_ASPECT.get(aspect)


def _prompt_hash_stable(text: str) -> str:
    """Truncated sha256 matching image_batch._prompt_hash prefix length.

    Inlined here to avoid src/gflow_cli/api/transports importing image_batch
    (would create a circular dependency). 8-char prefix is sufficient for
    structlog event correlation within a single batch run.
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


def _extract_project_id(url: str) -> str | None:
    """Thin alias for `extract_project_id` from `_common`.

    Kept for back-compat with any existing call sites and tests that import
    the private name directly from this module.
    """
    return extract_project_id(url)


def _collect_images_from_body(body: dict[str, Any], images: list[GeneratedImage]) -> None:
    """Append parseable GeneratedImage entries from one batchGenerateImages body."""
    media_list_raw = body.get("media", [])
    if not isinstance(media_list_raw, list):
        return
    for item_raw in cast("list[Any]", media_list_raw):
        if not isinstance(item_raw, dict):
            continue
        item: dict[str, Any] = cast("dict[str, Any]", item_raw)
        try:
            images.append(GeneratedImage.from_response_item(item))
        except ValueError as e:
            log.warning("ui_automation.parse_media_item_failed", error=str(e))


def _images_from_responses(
    responses: list[dict[str, Any]],
) -> tuple[list[GeneratedImage], int | None, str]:
    """Process captured batchGenerateImages responses.

    Returns ``(images, first_error_status, first_error_route)``. Raises
    :class:`AuthExpiredError` on 401 and :class:`WafRejectionError` on 403,
    which the caller must surface — these are not first-error candidates.
    """
    images: list[GeneratedImage] = []
    first_error_status: int | None = None
    first_error_route: str = ""

    for response in responses:
        status = response.get("status")
        body: dict[str, Any] = cast("dict[str, Any]", response.get("body") or {})
        route_str: str = str(response.get("url", ""))

        if status == 401:
            raise AuthExpiredError(
                detail="batchGenerateImages returned HTTP 401 — session expired",
                status=401,
                route=route_str,
            )
        if status == 403:
            log.warning(
                "ui_automation.batch_403_body",
                body_prefix=str(body)[:200],
                route=route_str,
            )
            raise WafRejectionError(
                detail=(
                    "batchGenerateImages HTTP 403 — reCAPTCHA score too low or WAF "
                    "fingerprint mismatch. Re-authenticate and retry."
                ),
                status=403,
                route=route_str,
            )
        if status != 200:
            first_error_status = first_error_status or status
            first_error_route = first_error_route or route_str
            continue

        _collect_images_from_body(body, images)

    return images, first_error_status, first_error_route


class UiAutomationTransport(VideoGenerationMixin):
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

    name = "ui_automation"

    def __init__(self) -> None:
        self._pw_cm: Any | None = None
        self._ctx: Any | None = None
        self._page: Page | None = None
        self._setup_done: bool = False
        self._owns_playwright: bool = False
        # Optional directory for debug screenshots — set by FlowApiClient
        # from its `out_dir` constructor arg (#18). When None, the internal
        # _capture_debug_screenshot helper is a no-op.
        self._out_dir: Path | None = None
        # Serialize concurrent generate_images calls — a single Playwright Page
        # cannot be safely shared across parallel asyncio tasks (each call
        # navigates, opens panels, and types into the same DOM). The lock
        # converts the N-parallel fan-out from generate_images_batch into N
        # sequential Page interactions, eliminating all race conditions.
        self._generate_lock: asyncio.Lock = asyncio.Lock()

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

        if async_playwright is None:  # pragma: no cover — install-time guard
            raise RuntimeError(
                "Playwright is required for UiAutomationTransport. "
                "Install via `uv sync` (it is a runtime dependency)."
            )

        pw_cm = async_playwright()
        pw = await pw_cm.__aenter__()
        try:
            import os

            from gflow_cli.browser_manager import channel_for_profile  # noqa: PLC0415

            locale_env = os.getenv("GFLOW_CLI_LOCALE", "en-US")
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                viewport=cast("ViewportSize", _VIEWPORT),
                locale=locale_env,
                channel=channel_for_profile(profile_dir),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
                    # locale="en-US" only sets Accept-Language; Chrome still picks
                    # its UI language from the profile/system and Flow then serves
                    # /fx/<locale>/ with a localized editor (breaking text-based
                    # selectors like the I2V frame slots). --lang forces the UI to
                    # English so Flow stays on /fx/tools/flow for ANY profile.
                    "--lang=en-US",
                ],
            )
            # Hide the automation flag so reCAPTCHA Enterprise doesn't score
            # the session as a bot — navigator.webdriver=true causes low-score
            # tokens and HTTP 403 on batchGenerateImages.
            await ctx.add_init_script(
                "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"
            )
            self._pw_cm = pw_cm
            self._ctx = ctx
            self._page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            try:
                await self._page.goto(FLOW_URL, wait_until="networkidle", timeout=45_000)
            except Exception as e:  # noqa: BLE001 — initial nav is best-effort
                log.warning("ui_automation.flow_initial_goto_failed", error=str(e))
            self._owns_playwright = True
            self._setup_done = True
            log.info(
                "ui_automation.setup_own_context",
                profile_dir=str(profile_dir),
            )
        except Exception:
            # Partial-setup leak guard.
            await pw_cm.__aexit__(None, None, None)
            raise

    # ------------------------------------------------------------------
    # Internal helpers — auth detection (unit 3.3)
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_logged_in(page: Page) -> bool:
        """True if the page shows the authenticated Flow UI.

        Gates (pattern G13):
        - URL is on labs.google AND contains /flow (locale-stable;
          /fx/pt/tools/flow, /fx/es/tools/flow, etc. all match).
        - URL is NOT on accounts.google.com.
        - /project/<uuid> URLs short-circuit to True (editor already open).
        - Otherwise reject if a top-level Sign-in CTA is visible.

        A failure in the locator probe is treated as "no Sign-in button"
        — the URL gate already established Flow context, and a transient
        DOM error shouldn't force a re-auth loop.
        """
        if "accounts.google.com" in page.url:
            return False
        on_flow = "labs.google" in page.url and "/flow" in page.url
        if not on_flow:
            return False
        if _PROJECT_URL_FRAGMENT in page.url:
            return True
        try:
            signin_button = await page.locator(
                "button:has-text('Sign in'), a:has-text('Sign in')"
            ).count()
        except Exception:  # noqa: BLE001 — defensive: transient DOM probe
            signin_button = 0
        return signin_button == 0

    # ------------------------------------------------------------------
    # Internal helpers — gallery → editor navigation (unit 3.4)
    # ------------------------------------------------------------------

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

    async def _dismiss_blocking_overlays(
        self,
        page: Page,
        out_dir: Path | None = None,
    ) -> bool:
        """Dismiss Flow changelog / "What's new" iframes and any blocking overlays.

        Called at stable interaction boundaries (after editor navigation, before
        UI interactions that could be intercepted by a changelog popup).

        Strategy:
        1. Check whether any changelog iframe is currently visible.
        2. If none found, return False immediately (cheap; no log noise).
        3. If found, try each close-button selector in OVERLAY_CLOSE_BUTTON_SELECTORS.
           On first visible match: force-click it, log the selector used, return True.
        4. If no close button is discoverable, press Escape as a fallback and return True.
        5. If Escape raises (extremely rare — keyboard unavailable), capture a debug
           screenshot (if out_dir provided) and return False so the caller can decide
           how to proceed. The structured warning carries enough info to identify the
           blocking element.

        Returns True if a dismissal action was taken, False if the page was
        clear (no overlay) or if dismissal could not be confirmed.
        """
        # Step 1 — detect whether a changelog iframe is blocking the UI.
        iframe_found = False
        for sel in CHANGELOG_IFRAME_SELECTORS:
            try:
                if await page.locator(sel).first.is_visible(timeout=1500):
                    iframe_found = True
                    log.info("ui_automation.overlay_detected", selector=sel)
                    break
            except Exception:  # noqa: BLE001 — probe failure means no match
                continue

        if not iframe_found:
            return False

        # Step 2 — try explicit close buttons first.
        for close_sel in OVERLAY_CLOSE_BUTTON_SELECTORS:
            try:
                loc = page.locator(close_sel).first
                if await loc.is_visible(timeout=500):
                    await loc.click(force=True)
                    await page.wait_for_timeout(500)
                    log.info(
                        "ui_automation.overlay_dismissed",
                        selector=close_sel,
                        method="close_button",
                    )
                    return True
            except Exception:  # noqa: BLE001 — selector miss or stale element
                continue

        # Step 3 — Escape fallback (regression test case: iframe present, no close button).
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(500)
            log.info(
                "ui_automation.overlay_dismissed",
                selector="<none>",
                method="escape",
            )
            return True
        except Exception as exc:  # noqa: BLE001 — keyboard unavailable in some sandboxes
            shot_path = await _capture_debug_screenshot(
                page, out_dir, "debug_overlay_dismiss_failed.png"
            )
            log.warning(
                "ui_automation.overlay_dismiss_failed",
                error=str(exc),
                screenshot=str(shot_path),
                note=(
                    "A changelog iframe was detected but could not be dismissed — "
                    "no close button found and Escape raised. Manual intervention "
                    "may be needed (open Flow in Chrome, dismiss the 'What's new' "
                    "popup, close Chrome cleanly)."
                ),
            )
            return False

    async def _enter_editor(self, page: Page, out_dir: Path | None = None) -> None:
        """Always create a fresh project — click "+ New project" on the gallery
        and wait for ``/project/`` navigation.

        When the URL already contains ``/project/`` (Flow's PWA restored the
        previous project on browser launch), this navigates back to the
        gallery first, then falls through to the "+ New project" click —
        the alternative (returning early) would reuse the restored project
        and accumulate images across CLI invocations.

        Tries each selector in :data:`NEW_PROJECT_SELECTORS` in order —
        locale-stable icon-class first, localized text fallbacks after. On
        total failure a debug screenshot is written to ``out_dir`` (if
        provided) and ``RuntimeError`` is raised with the captured URL +
        path.
        """
        if _PROJECT_URL_FRAGMENT in page.url:
            # Flow's PWA restores the last-visited project URL on next browser
            # launch (persistent context). Returning early here would reuse the
            # old project, accumulating images across CLI invocations instead of
            # starting fresh. Navigate back to the gallery first, then fall
            # through to the "+ New project" click below.
            # Do NOT use wait_until="networkidle" — PWAs re-render incrementally
            # and networkidle is flaky. The selector wait_for below is the real
            # readiness gate.
            log.info("ui_automation.navigating_to_gallery", restored_url=page.url)
            await page.goto(FLOW_URL, timeout=45_000)
            await self._bypass_onboarding(page)

        await page.wait_for_timeout(3000)
        await self._bypass_onboarding(page)
        for selector in NEW_PROJECT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=5000)
                log.info("ui_automation.clicking_new_project", selector=selector)
                await loc.click()
                try:
                    await page.wait_for_url(
                        lambda url: _PROJECT_URL_FRAGMENT in url, timeout=15_000
                    )
                    log.info("ui_automation.entered_editor", url=page.url)
                    return
                except Exception:  # noqa: BLE001 — try next selector
                    log.warning(
                        "ui_automation.new_project_click_did_not_navigate",
                        selector=selector,
                    )
            except Exception:  # noqa: BLE001 — selector didn't match; try next
                continue

        shot_path = await _capture_debug_screenshot(page, out_dir, "debug_new_project.png")
        raise RuntimeError(
            f"Could not find 'New project' CTA on Flow gallery. URL: {page.url}. "
            f"Screenshot: {shot_path}"
        )

    @staticmethod
    async def _switch_to_image_mode(page: Page, *, out_dir: Path | None = None) -> None:
        """Open the 2-step mode dropdown and switch to Image mode.

        Mirror of :meth:`VideoGenerationMixin._switch_to_video_mode`.  Without
        this, an account whose last-used Flow mode was Video silently routes
        ``image t2i`` / ``image batch`` prompts to the video endpoint — no
        ``batchGenerateImages`` response is observed, and the listener times
        out after 3 minutes (an image typically completes in ~15 s).

        The dropdown is closed afterwards (via :kbd:`Escape`) so the caller's
        :meth:`_configure_generation_settings` can open it fresh.
        """
        trigger = await VideoGenerationMixin._probe_selector_cascade(
            page, "mode_switch_trigger", MODE_SWITCH_TRIGGER_SELECTORS
        )
        if trigger is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_mode_trigger.png")
            raise RuntimeError(
                f"mode-switch dropdown trigger not found on the Flow editor. Screenshot: {shot}"
            )
        await trigger.click()
        await page.wait_for_timeout(800)
        image_tab = await VideoGenerationMixin._probe_selector_cascade(
            page, "image_mode_tab", IMAGE_TAB_IN_MENU_SELECTORS
        )
        if image_tab is None:
            shot = await _capture_debug_screenshot(page, out_dir, "debug_no_image_tab.png")
            raise RuntimeError(f"Image tab not found in the mode dropdown. Screenshot: {shot}")
        await image_tab.click()
        await page.wait_for_timeout(1200)
        await page.keyboard.press("Escape")
        await page.wait_for_timeout(200)
        log.info("ui_automation.image_mode_entered")

    # ------------------------------------------------------------------
    # Internal helpers — prompt submission (unit 3.5)
    # ------------------------------------------------------------------

    async def _send_prompt(
        self,
        page: Page,
        prompt_text: str,
        out_dir: Path | None = None,
    ) -> None:
        """Type ``prompt_text`` into Flow's editor and submit.

        Selectors are tried in priority order; the first visible match
        wins. The text input is cleared first (Slate.js requires real
        keyboard events — ``.fill()`` bypasses onChange handlers).

        Submission is preferred via the Create button; if no submit
        button is visible, Enter is pressed as a fallback.

        On input-not-found, a debug screenshot is written to ``out_dir``
        (if provided) and ``RuntimeError`` is raised.
        """
        input_box = None
        for selector in PROMPT_INPUT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=10_000)
                input_box = loc
                log.info("ui_automation.prompt_input_found", selector=selector)
                break
            except Exception:  # noqa: BLE001 — try next selector
                continue

        if input_box is None:
            shot_path = await _capture_debug_screenshot(page, out_dir, "debug_prompt_not_found.png")
            raise RuntimeError(
                f"Prompt input not found in Flow UI. URL: {page.url}. Screenshot: {shot_path}"
            )

        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        # insert_text fires a single beforeinput event that Slate.js handles
        # natively — near-instant vs keyboard.type() which is ~1.5s/char.
        await page.keyboard.insert_text(prompt_text)
        await page.wait_for_timeout(500)

        for sel in SUBMIT_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=2_000)
                await btn.click()
                log.info("ui_automation.prompt_submitted", via=sel)
                return
            except Exception:  # noqa: BLE001 — try next submit selector
                continue

        log.info("ui_automation.prompt_submitted", via="enter_key_fallback")
        await page.keyboard.press("Enter")

    # ------------------------------------------------------------------
    # Internal helpers — generation settings (aspect ratio + count)
    # ------------------------------------------------------------------

    @staticmethod
    async def _open_gen_settings_panel(page: Page) -> bool:
        """Try selectors in order to open the per-generation settings panel.

        Returns True on success, False if no selector matched (non-fatal —
        caller falls back to Flow's current defaults).
        """
        for sel in GEN_SETTINGS_BUTTON_SELECTORS:
            try:
                btn = page.locator(sel).first
                await btn.wait_for(state="visible", timeout=3_000)
                await btn.click()
                await page.wait_for_timeout(600)
                log.info("ui_automation.gen_settings_opened", via=sel)
                return True
            except Exception:  # noqa: BLE001
                continue
        return False

    @staticmethod
    async def _read_displayed_count(page: Page) -> int | None:
        """Read the currently-displayed image count from the settings panel.

        Locale-invariant: filters ``[aria-selected="true"]`` tabs by
        :data:`_COUNT_TAB_TEXT_RE` so only count tabs ("1x", "x2", "x3",
        "x4") are considered.  Mode tabs ("image\\nImagem") and Aspect tabs
        ("16:9", etc.) are selected simultaneously in the Radix tablist DOM
        and would poison the old unfiltered ``[aria-selected="true"]`` query.

        Returns the integer count (1–4) extracted from the matched tab's text,
        or ``None`` when no count tab is selected / visible.
        """
        try:
            selected = page.locator('[role="tab"][aria-selected="true"]').filter(
                has_text=_COUNT_TAB_TEXT_RE
            )
            if await selected.count() == 0:
                return None
            text = (await selected.first.text_content(timeout=500) or "").strip()
            m = re.search(r"\d", text)
            return int(m.group()) if m else None
        except Exception:  # noqa: BLE001 — probe failure is non-fatal
            return None

    @staticmethod
    async def _is_settings_panel_open(page: Page) -> bool:
        """True if the generation-settings panel count tabs are currently visible.

        Uses :func:`_count_tabs_locator` — the panel is open when at least one
        count tab (text matching ``1x`` / ``x2`` / ``x3`` / ``x4``) is visible.
        This is locale-invariant and immune to Mode/Aspect tab false-positives.
        """
        try:
            return await _count_tabs_locator(page).first.is_visible()
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    async def _select_image_model(page: Page, model: Model) -> None:
        """Click the image model picker and select `model` (Nano Banana 2 / Nano
        Banana Pro / Imagen 4). Must run with the gen-settings panel open. Without
        this the generation uses Flow's UI-default model and ``--model`` is a
        no-op. Non-fatal on miss (logged at WARNING — the wrong model has a real
        cost/quality impact, so a miss is a genuine signal)."""
        option_sel = IMAGE_MODEL_OPTION_SELECTORS.get(model)
        if option_sel is None:
            log.warning("ui_automation.image_model_unknown", model=model.value)
            return
        try:
            trigger = page.locator(IMAGE_MODEL_PICKER_TRIGGER).first
            await trigger.wait_for(state="visible", timeout=4000)
            await trigger.click()
            await page.wait_for_timeout(500)
            option = page.locator(option_sel).first
            await option.wait_for(state="visible", timeout=4000)
            await option.click()
            await page.wait_for_timeout(500)
            log.info("ui_automation.image_model_selected", model=model.value)
        except Exception as e:  # noqa: BLE001 — non-fatal; Flow default applies
            log.warning(
                "ui_automation.image_model_not_set",
                model=model.value,
                error=str(e)[:80],
                note="Flow default model applies",
            )
            await page.keyboard.press("Escape")  # close a stray model menu

    @staticmethod
    async def _configure_generation_settings(
        page: Page,
        aspect_cli: str | None,
        count: int | None,
        *,
        model: Model | None = None,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Open the per-generation settings panel and apply model, aspect ratio,
        and count.

        When ``out_dir`` and ``prompt_idx`` are both provided, diagnostic
        screenshots are saved as ``count_before_prompt_{idx}.png`` and
        ``count_after_prompt_{idx}.png`` so future count-drift can be
        diagnosed without re-instrumenting the code.

        Skips gracefully if the panel trigger cannot be found (non-fatal —
        generation will proceed with Flow's current default settings).
        """
        if aspect_cli is None and count is None and model is None:
            # Nothing to apply.
            return

        # Phase 3 — before screenshot (diagnostic, best-effort).
        if out_dir is not None and prompt_idx is not None:
            diag_dir = out_dir / _DIAGNOSTICS_SUBDIR
            diag_dir.mkdir(parents=True, exist_ok=True)
            await _capture_debug_screenshot(page, diag_dir, f"count_before_prompt_{prompt_idx}.png")

        if not await UiAutomationTransport._open_gen_settings_panel(page):
            log.warning("ui_automation.gen_settings_panel_not_found", skipping=True)
            return

        if model is not None:
            await UiAutomationTransport._select_image_model(page, model)

        if aspect_cli:
            candidates = _ASPECT_TAB_CANDIDATES.get(aspect_cli, (aspect_cli,))
            clicked = False
            last_err: str | None = None
            for tab_text in candidates:
                # `:text-is(...)` is exact-match — preferred for short labels
                # like "1:1" because `:has-text(...)` substring-matches and
                # would clash with longer tabs that include the label.
                try:
                    tab = page.locator(f'[role="tab"]:text-is("{tab_text}")').first
                    await tab.wait_for(state="visible", timeout=2_000)
                    await tab.click()
                    clicked = True
                    log.info(
                        "ui_automation.aspect_ratio_set",
                        value=aspect_cli,
                        matched_label=tab_text,
                    )
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = str(e)
                    continue
            if not clicked:
                log.warning(
                    "ui_automation.aspect_ratio_set_failed",
                    value=aspect_cli,
                    candidates_tried=list(candidates),
                    error=last_err,
                )

        if count is not None:
            if count not in _SUPPORTED_COUNTS:
                log.warning("ui_automation.unsupported_count", value=count)
            else:
                await UiAutomationTransport._set_count(
                    page, count, out_dir=out_dir, prompt_idx=prompt_idx
                )

        # Phase 3 — after screenshot (diagnostic, best-effort).
        if out_dir is not None and prompt_idx is not None:
            diag_dir = out_dir / _DIAGNOSTICS_SUBDIR
            diag_dir.mkdir(parents=True, exist_ok=True)
            await _capture_debug_screenshot(page, diag_dir, f"count_after_prompt_{prompt_idx}.png")

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

    @staticmethod
    async def _dump_count_panel_dom(
        page: Page,
        out_dir: Path | None,
        prompt_idx: int | None,
    ) -> None:
        """Diagnostic dump of the count-tab area of the editor to out_dir.

        Writes a JSON file enumerating candidate structural patterns so we can
        derive locale-invariant selectors from real DOM evidence (per issue #24).

        Captures:
          - All elements with role="tab", role="tablist", role="radiogroup",
            role="radio" — count, aria-label, aria-selected, text content.
          - All buttons inside any visible Material panel — text, aria-label,
            leading digit if any, google-symbols icon ligature children.
          - Document title + page URL for context.

        Safe-by-default: no-op if out_dir is None or prompt_idx is None.
        Failures swallowed (this is diagnostic).
        """
        if out_dir is None or prompt_idx is None:
            return
        try:
            snapshot = await page.evaluate("""() => {
                const result = {
                    url: location.href,
                    title: document.title,
                    roles: {},
                    buttons_with_digits: [],
                    google_symbols_ligatures: [],
                };
                for (const role of ['tab', 'tablist', 'radiogroup', 'radio']) {
                    const els = Array.from(document.querySelectorAll('[role="' + role + '"]'));
                    result.roles[role] = els.map(el => ({
                        text: (el.innerText || '').slice(0, 120),
                        aria_label: el.getAttribute('aria-label'),
                        aria_selected: el.getAttribute('aria-selected'),
                        aria_controls: el.getAttribute('aria-controls'),
                        id: el.id || null,
                        classes: el.className.toString().slice(0, 200),
                    }));
                }
                // Buttons whose visible text starts with a digit (count-tab candidates).
                for (const btn of document.querySelectorAll('button')) {
                    const text = (btn.innerText || '').trim();
                    if (/^\\d/.test(text)) {
                        result.buttons_with_digits.push({
                            text: text.slice(0, 120),
                            aria_label: btn.getAttribute('aria-label'),
                            aria_selected: btn.getAttribute('aria-selected'),
                            role: btn.getAttribute('role'),
                            parent_role: btn.parentElement?.getAttribute('role') || null,
                            parent_class: (btn.parentElement?.className
                                ?.toString().slice(0, 200)) || null,
                        });
                    }
                }
                // Google Symbols icons present anywhere — gives us the ligature names Flow uses.
                const _gsQuery = 'i.google-symbols, span.google-symbols';
                for (const el of document.querySelectorAll(_gsQuery)) {
                    const lig = (el.innerText || '').trim();
                    if (lig) result.google_symbols_ligatures.push({
                        ligature: lig,
                        parent_text: (el.parentElement?.innerText || '').trim().slice(0, 80),
                        parent_role: el.parentElement?.getAttribute('role'),
                        parent_aria_label: el.parentElement?.getAttribute('aria-label'),
                    });
                }
                return result;
            }""")
            diag_dir = out_dir / _DIAGNOSTICS_SUBDIR
            diag_dir.mkdir(parents=True, exist_ok=True)
            target = diag_dir / f"count_panel_dom_prompt_{prompt_idx}.json"
            target.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False), encoding="utf-8")
            log.info(
                "ui_automation.count_panel_dom_dumped",
                target=str(target),
                tabs_count=len(snapshot.get("roles", {}).get("tab", [])),
                digit_buttons_count=len(snapshot.get("buttons_with_digits", [])),
                ligatures_count=len(snapshot.get("google_symbols_ligatures", [])),
            )
        except Exception as exc:  # noqa: BLE001 — diagnostic; never mask real failures
            log.warning(
                "ui_automation.count_panel_dom_dump_failed",
                error=str(exc),
                prompt_idx=prompt_idx,
            )

    @staticmethod
    async def _set_count(
        page: Page,
        count: int,
        *,
        out_dir: Path | None = None,
        prompt_idx: int | None = None,
    ) -> None:
        """Click the count tab by position — locale-invariant, read-back verify with retry.

        Algorithm (#24 DOM-evidence-driven rewrite):
        1. Ensure the settings panel is open without toggling it closed
           (stay-mounted batch: panel may already be open from the prior prompt).
        2. Read the currently-displayed count via :func:`_count_tabs_locator`
           filtered by :data:`_COUNT_TAB_TEXT_RE` — immune to Mode/Aspect tabs.
        3. If it already matches ``count``, return early (no click needed).
        4. Call ``_count_tabs_locator(page).nth(count - 1)`` and click —
           positional within the filtered set, no text matching.
        5. Read back the digit and confirm the change.
        6. Retry up to 3 attempts total; raise ``RuntimeError`` on non-convergence.

        When read-back returns ``None`` (unrecognised locale text), the
        position-based click is trusted — it is deterministic regardless.

        Four structlog events are emitted for diagnosability:
        - ``ui_automation.count_setter_entered``
        - ``ui_automation.count_click_attempted``
        - ``ui_automation.count_click_result``
        - ``ui_automation.count_setter_completed``
        """
        panel_open = await UiAutomationTransport._is_settings_panel_open(page)
        initial_displayed = await UiAutomationTransport._read_displayed_count(page)

        log.info(
            "ui_automation.count_setter_entered",
            desired_count=count,
            panel_currently_visible=panel_open,
            initial_displayed_count=initial_displayed,
        )

        # Ensure the panel is open — open it only if it's currently closed
        # (avoid toggling a stay-mounted open panel closed).
        if not panel_open:
            opened = await UiAutomationTransport._open_gen_settings_panel(page)
            if not opened:
                log.warning(
                    "ui_automation.count_setter_panel_open_failed",
                    desired_count=count,
                )
                # Non-fatal: completed event records failure.
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=initial_displayed,
                    success=False,
                    attempts=0,
                )
                return

        # Diagnostic DOM dump — captured after panel is confirmed open, before any
        # tab click attempt. Produces count_panel_dom_prompt_{idx}.json in out_dir
        # so the real DOM structure is visible for selector research (issue #24).
        await UiAutomationTransport._dump_count_panel_dom(page, out_dir, prompt_idx)

        _max_attempts = 3
        # Reuse the initial read — avoids a redundant DOM round-trip.
        displayed: int | None = initial_displayed

        for attempt in range(1, _max_attempts + 1):
            # If current display already matches desired, we're done.
            if displayed == count:
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt - 1,
                )
                return

            # Locate count tabs via text-pattern filter (locale-invariant).
            # _count_tabs_locator returns role="tab" elements whose text matches
            # ^(1x|x[2-4])$ — unique to count tabs across all three Radix tablists.
            panel_visible_before = await UiAutomationTransport._is_settings_panel_open(page)
            clicked = False
            click_error: str | None = None

            tabs_locator = _count_tabs_locator(page)
            # 0-indexed: count=1 → nth(0), count=2 → nth(1), etc.
            target_tab = tabs_locator.nth(count - 1)
            selector_desc = f"nth({count - 1}) of _count_tabs_locator"
            log.info(
                "ui_automation.count_click_attempted",
                target=f"count={count}",
                selector=selector_desc,
                panel_visible=panel_visible_before,
                current_displayed_count=displayed,
            )
            try:
                await target_tab.wait_for(state="visible", timeout=3_000)
                await target_tab.click()
                await page.wait_for_timeout(300)
                clicked = True
            except Exception as e:  # noqa: BLE001
                click_error = str(e)

            # Read back to verify (digit-extraction, locale-agnostic).
            displayed = await UiAutomationTransport._read_displayed_count(page)
            log.info(
                "ui_automation.count_click_result",
                target=f"count={count}",
                success=clicked,
                current_displayed_count_after=displayed,
                error=click_error,
            )

            # Success when the click landed AND read-back digit matches, OR
            # when read-back returned None (unrecognised locale text — position
            # click was deterministic so trust it).
            if clicked and (displayed is None or displayed == count):
                log.info(
                    _EVT_COUNT_SETTER_COMPLETED,
                    desired_count=count,
                    final_displayed_count=displayed,
                    success=True,
                    attempts=attempt,
                    readback_trusted=(displayed is None),
                )
                return

            # Brief pause before retry to allow React re-render.
            if attempt < _max_attempts:
                await page.wait_for_timeout(500)

        # All attempts exhausted without convergence.
        log.info(
            _EVT_COUNT_SETTER_COMPLETED,
            desired_count=count,
            final_displayed_count=displayed,
            success=False,
            attempts=_max_attempts,
        )
        raise RuntimeError(
            f"_set_count({count}) failed to update Flow UI; "
            f"still showing {displayed!r} after {_max_attempts} attempts"
        )

    # ------------------------------------------------------------------
    # Internal helpers — batchGenerateImages capture (unit 3.6)
    # ------------------------------------------------------------------

    @staticmethod
    def _attach_batch_response_listener(
        page: Page, *, project_id: str | None = None
    ) -> tuple[list[dict[str, Any]], Callable[[], None]]:
        """Synchronously register a ``page.on('response', ...)`` listener
        that records ``batchGenerateImages`` responses into a shared list.

        When ``project_id`` is provided, only responses whose URL contains
        ``/projects/{project_id}/`` are captured — this prevents stale
        responses from previously-visited projects accumulating in the list.

        Returns ``(captured, detach_fn)``:
        - ``captured`` is the shared list — the caller submits the prompt
          next, then polls / awaits that list via :meth:`_await_captured`.
        - ``detach_fn`` removes the handler from the page when called; it is
          idempotent (safe to call multiple times).

        Registering the listener BEFORE issuing the prompt click eliminates
        the race where the click could fire before an ``asyncio.create_task``-
        scheduled listener attaches.
        """
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if "batchGenerateImages" not in response.url:
                return
            # Log EVERY batchGenerateImages response BEFORE the project_id
            # filter so live verification can diagnose listener-miss bugs
            # (e.g., URL contains a different project_id than the editor URL).
            log.info(
                "ui_automation.batch_response_seen",
                url=response.url,
                status=response.status,
                filter_project_id=project_id,
            )
            if project_id and f"/projects/{project_id}/" not in response.url:
                log.warning(
                    "ui_automation.batch_response_dropped_project_id_mismatch",
                    url=response.url,
                    filter_project_id=project_id,
                )
                return
            try:
                body = await response.json()
            except Exception as e:  # noqa: BLE001 — parse failures are non-fatal
                log.warning(
                    "ui_automation.batch_response_parse_failed",
                    error=str(e),
                    url=response.url,
                )
                return
            captured.append(
                {
                    "status": response.status,
                    "url": response.url,
                    "body": body,
                    "ts": time.monotonic(),
                }
            )
            log.info(
                "ui_automation.batch_response_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)

        _detached = False

        def detach() -> None:
            nonlocal _detached
            if _detached:
                return
            _detached = True
            try:
                page.remove_listener("response", on_response)
            except Exception:  # noqa: BLE001 — idempotent on already-removed
                pass

        return captured, detach

    @staticmethod
    async def _await_captured(
        captured: list[dict[str, Any]],
        timeout_s: float = 180.0,
        *,
        expected_count: int = 1,
        submit_time: float = 0.0,
        poll_interval_s: float = 0.5,
        straggler_window_s: float = 2.5,
    ) -> list[dict[str, Any]]:
        """Wait for ``expected_count`` batchGenerateImages responses.

        Flow generates N images via N separate API calls (not one call with
        N URLs). We poll until we have enough fresh responses (those whose
        ``ts >= submit_time``) or the timeout expires.

        **Defense A — post-submit-time filter (primary correctness fix):**
        Each captured entry carries a ``ts`` field written by the handler at
        append time (``time.monotonic()``). Only entries with
        ``entry["ts"] >= submit_time`` count toward ``expected_count``.  This
        eliminates the cross-contamination bug where a listener attached
        *before* the submit click inherits stale responses from prior prompts
        that arrived in the window between attach and click.

        When ``submit_time`` is 0.0 (the default, used by
        ``_capture_batch_response`` and legacy callers), all entries pass the
        filter, preserving backwards compatibility.

        **Straggler window:** after the count threshold is first reached the
        method waits an additional ``straggler_window_s`` seconds so that any
        slower same-submission responses (e.g. the last of a 2-image batch)
        can arrive before the list is snapshotted. This mirrors the Worker
        pattern (``_wait_for_n_new_images`` in the compile-growth monorepo).

        Raises ``TimeoutError`` if no fresh responses arrive within
        ``timeout_s``.  Returns the underlying response dicts (entries without
        the ``ts`` wrapper key) for entries with ``ts >= submit_time``.
        """
        deadline = time.monotonic() + timeout_s

        def _fresh() -> list[dict[str, Any]]:
            return [e for e in captured if e.get("ts", 0.0) >= submit_time]

        # Poll until we have enough fresh responses or the deadline passes.
        while time.monotonic() < deadline and len(_fresh()) < expected_count:
            await asyncio.sleep(poll_interval_s)

        fresh = _fresh()
        if not fresh:
            raise TimeoutError(f"No batchGenerateImages response within {timeout_s:.1f}s.")
        if len(fresh) < expected_count:
            log.warning(
                "ui_automation.fewer_responses_than_expected",
                got=len(fresh),
                expected=expected_count,
            )
        else:
            # Threshold reached — wait for any slow stragglers from this same
            # submission before snapshotting the list.
            await asyncio.sleep(straggler_window_s)
            fresh = _fresh()

        # Return entries stripped of the internal `ts` bookkeeping key so
        # callers (_images_from_responses, tests) receive plain response dicts.
        return [{k: v for k, v in e.items() if k != "ts"} for e in fresh]

    @staticmethod
    async def _capture_batch_response(
        page: Page,
        timeout_s: float = 120.0,
        *,
        poll_interval_s: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Convenience wrapper: attach + await in one call.

        Useful when the caller has no work to interleave between attach
        and wait. ``generate_images`` does NOT use this — it splits the
        two halves so the listener is attached synchronously before
        ``_send_prompt`` issues the click.
        """
        captured, _detach = UiAutomationTransport._attach_batch_response_listener(page)
        return await UiAutomationTransport._await_captured(
            captured, timeout_s, poll_interval_s=poll_interval_s
        )

    # ------------------------------------------------------------------
    # Internal helpers — image download (unit 3.8)
    # ------------------------------------------------------------------

    @staticmethod
    async def _download(
        urls: list[str],
        out_dir: Path,
        cookies: dict[str, str],
    ) -> list[Path]:
        """Download each URL into ``out_dir`` using session cookies.

        Saves to ``out_dir / image_NN.png`` (zero-padded index). Individual
        download failures are logged and skipped — the function returns the
        list of paths that DID write successfully.

        URLs whose host is not in :data:`_ALLOWED_DOWNLOAD_HOST_SUFFIXES`
        are skipped before any HTTP request is made — this prevents
        session cookies from being forwarded to a non-Google host through
        a malicious or compromised fifeUrl. Redirects are also disabled
        (``follow_redirects=False``) so an open-redirect on an allowed
        host cannot rebound the request to a third party.
        """
        import httpx  # local import — httpx is a runtime dependency

        out_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        async with httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=False,
            cookies=cookies,
        ) as client:
            for i, url in enumerate(urls):
                if not _is_allowed_download_host(url):
                    log.error(
                        "ui_automation.download_host_rejected",
                        url=url,
                        allowed_suffixes=list(_ALLOWED_DOWNLOAD_HOST_SUFFIXES),
                    )
                    continue
                try:
                    resp = await client.get(url)
                    resp.raise_for_status()
                    # Auto-detect extension from Content-Type / magic bytes.
                    ct = resp.headers.get("content-type", "")
                    if "jpeg" in ct or "jpg" in ct or resp.content[:3] == b"\xff\xd8\xff":
                        ext = ".jpg"
                    else:
                        ext = ".png"
                    p = out_dir / f"image_{i:02d}{ext}"
                    p.write_bytes(resp.content)
                    paths.append(p)
                    log.info(
                        "ui_automation.image_saved",
                        path=str(p),
                        bytes=len(resp.content),
                        format=ext,
                    )
                except Exception as e:  # noqa: BLE001 — log and skip
                    log.error(
                        "ui_automation.download_failed",
                        url=url,
                        error=str(e),
                    )
        return paths

    # ------------------------------------------------------------------
    # Protocol — generate_images (unit 3.9)
    # ------------------------------------------------------------------

    async def generate_images(
        self,
        *,
        project_id: str | None,  # noqa: ARG002
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Submit ``request.prompt`` through Flow's editor and return the
        generated images as DTOs.

        ``project_id`` is accepted for Protocol parity but the UI flow
        creates a new Flow project on each call (Flow's gallery → editor
        navigation is the same surface a human uses). Downloading the
        actual PNG bytes is the caller's responsibility; the DTOs carry
        the ``fife_url`` which expires roughly 6 hours after generation.

        Raises ``RuntimeError`` if setup() has not been called, the
        ``batchGenerateImages`` response is non-200, or the response is
        200 but contains no image URLs.
        """
        # project_id is accepted for Protocol parity; the UI transport creates
        # its own Flow project on each call rather than reusing a supplied one.
        if not self._setup_done or self._page is None:
            raise RuntimeError(
                "UiAutomationTransport.setup() must be called before generate_images()"
            )
        async with self._generate_lock:
            return await self._generate_images_locked(request)

    async def _generate_images_locked(
        self,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        """Serialized body of generate_images — called under self._generate_lock.

        Extracts into a private method so the lock wrapper in generate_images
        stays a single line, keeping the public method's intent clear.
        """
        page: Page = self._page  # type: ignore[assignment]  # guard in caller
        out_dir = self._out_dir

        await self._enter_editor(page, out_dir)
        # Dismiss any Flow changelog / "What's new" overlay that may be on top
        # of the editor before we click into settings / submit (#26).
        await self._dismiss_blocking_overlays(page, out_dir)
        # Select Image mode explicitly. If the account was last in Video mode,
        # an unguarded submission goes to the video endpoint and the image
        # listener never observes ``batchGenerateImages``.
        await self._switch_to_image_mode(page, out_dir=out_dir)

        # Resolve the project_id from the URL now that we're in the editor.
        nav_project_id = _extract_project_id(page.url)
        aspect_cli = _aspect_cli_from_enum(request.aspect)

        # Configure generation settings (aspect ratio + count) BEFORE attaching
        # the response listener so settings clicks don't interfere with capture.
        await self._configure_generation_settings(
            page, aspect_cli, request.count, model=request.model
        )

        # I2I: bind local reference images through the editor's media dialog —
        # the same add_2 dialog as video R2V, via the inherited _attach_references.
        # The REST uploadImage path 401s, and passive capture needs the refs IN
        # the UI (not just a wire body), so we attach + let Flow's JS include them.
        if request.ref_paths:
            await self._attach_references(page, list(request.ref_paths), out_dir=out_dir)

        # Attach the response listener SYNCHRONOUSLY before any prompt
        # action. asyncio.create_task is unsafe here: it defers the listener
        # registration until the new task gets event-loop scheduling, which
        # could happen AFTER _send_prompt's click on a busy loop. Splitting
        # attach/await eliminates that race. Project-ID filter prevents stale
        # responses from previously-visited projects accumulating in the list.
        captured, _detach = self._attach_batch_response_listener(page, project_id=nav_project_id)
        # Record submit_time BEFORE the click so the post-submit-time filter
        # in _await_captured can distinguish this prompt's responses from any
        # stale entries that arrived between listener attach and the click.
        submit_time = time.monotonic()
        await self._send_prompt(page, request.prompt, out_dir)
        responses = await self._await_captured(
            captured, expected_count=request.count, submit_time=submit_time
        )

        # Collect images from ALL captured responses (Flow makes one API call
        # per image when count > 1).
        images, first_error_status, first_error_route = _images_from_responses(responses)

        if first_error_status is not None and not images:
            raise WireFormatError(
                detail=f"batchGenerateImages returned HTTP {first_error_status}",
                status=first_error_status,
                route=first_error_route,
            )

        if not images:
            raise ContentPolicyError(
                detail="batchGenerateImages returned 200 but no parseable media items",
                route=first_error_route or "",
            )
        return images

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
            raise RuntimeError(
                "UiAutomationTransport.setup() must be called before generate_images_batch()"
            )
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
    ) -> tuple[BatchSubmissionResult, GFlowError | None]:
        """Single prompt's lifecycle inside a batch: configure → attach
        listener → submit → await → detach → parse.

        Returns ``(result, fatal_error)``.  ``fatal_error`` is ``None`` on
        success or whenever the failure was non-fatal (the caller can
        continue).  When non-``None`` it carries the :class:`GFlowError`
        the caller should propagate via :class:`BatchPartialError`.  Detach
        is guaranteed exactly once on every code path.
        """
        aspect_cli = _aspect_cli_from_enum(req.aspect)
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
        try:
            await self._configure_generation_settings(
                page, aspect_cli, req.count, model=req.model, out_dir=out_dir, prompt_idx=idx
            )
        except Exception as exc:  # noqa: BLE001 — broad on purpose; wrap into GFlowError
            return _fail(exc)

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
            await self._send_prompt(page, req.prompt, out_dir)
        except Exception as exc:  # noqa: BLE001
            detach()
            return _fail(exc)

        # Step 4 — await THIS prompt's responses, then detach immediately.
        try:
            responses = await self._await_captured(
                captured, expected_count=req.count, submit_time=submit_time
            )
        except Exception as exc:  # noqa: BLE001
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

        images, first_error_status, _ = _images_from_responses(responses)
        if not images:
            err = GFlowError(
                detail=f"no parseable images (first_error_status={first_error_status})",
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
        page: Any = self._page  # type: ignore[assignment]
        out_dir = self._out_dir

        # ---- Batch-setup phase (once per batch) ----
        await self._enter_editor(page, out_dir)
        project_id = _extract_project_id(page.url)
        if project_id is None:
            raise RuntimeError(
                f"Could not extract project_id from editor URL after _enter_editor. URL: {page.url}"
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

        try:
            await self._switch_to_image_mode(page, out_dir=out_dir)
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
            )
            results.append(result)

            # Fail-fast: break before the next submission so we do not spend
            # credits on prompts the caller will not see in the success path.
            if result.status == "fail" and not continue_on_error and fatal_err is not None:
                submit_error = fatal_err
                break

            # Jitter between iterations (anti-bot cadence) — not after the last.
            if idx < len(prompts) - 1:
                await asyncio.sleep(random.uniform(*jitter_range))

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
    # Protocol — refresh_auth (unit 3.10) + teardown (unit 3.11)
    # ------------------------------------------------------------------

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
        if self._owns_playwright and self._pw_cm is not None:
            try:
                if self._ctx is not None:
                    await self._ctx.close()
            except Exception as e:  # noqa: BLE001 — log and continue cleanup
                log.warning("ui_automation.context_close_failed", error=str(e))
            try:
                await self._pw_cm.__aexit__(None, None, None)
            except Exception as e:  # noqa: BLE001 — log and continue cleanup
                log.warning("ui_automation.playwright_exit_failed", error=str(e))
        self._pw_cm = None
        self._ctx = None
        self._page = None
        self._setup_done = False
        self._owns_playwright = False
