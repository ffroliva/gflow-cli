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
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import urlparse

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import Aspect, GenerateImageRequest
from gflow_cli.api.transports.ui_automation_video import VideoGenerationMixin
from gflow_cli.errors import (
    AuthExpiredError,
    ContentPolicyError,
    WafRejectionError,
    WireFormatError,
)

if TYPE_CHECKING:
    from playwright.async_api import Page, ViewportSize

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

# Count → Flow count tab text.
_COUNT_TAB: dict[int, str] = {1: "1x", 2: "x2", 3: "x3", 4: "x4"}

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


def _extract_project_id(url: str) -> str | None:
    """Pull the project UUID out of a Flow editor URL, or None if absent."""
    if _PROJECT_URL_FRAGMENT not in url:
        return None
    try:
        return url.split(_PROJECT_URL_FRAGMENT)[1].split("?")[0]
    except (IndexError, ValueError):
        return None


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
            from gflow_cli.browser_manager import channel_for_profile  # noqa: PLC0415

            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                viewport=cast("ViewportSize", _VIEWPORT),
                locale="en-US",
                channel=channel_for_profile(profile_dir),
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--password-store=basic",
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
    async def _configure_generation_settings(
        page: Page,
        aspect_cli: str | None,
        count: int | None,
    ) -> None:
        """Open the per-generation settings panel and apply aspect ratio and count.

        Skips gracefully if the panel trigger cannot be found (non-fatal —
        generation will proceed with Flow's current default settings).
        """
        if aspect_cli is None and count is None:
            # Nothing to apply.
            return

        if not await UiAutomationTransport._open_gen_settings_panel(page):
            log.warning("ui_automation.gen_settings_panel_not_found", skipping=True)
            return

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
            count_text = _COUNT_TAB.get(count)
            if count_text is None:
                log.warning("ui_automation.unsupported_count", value=count)
            else:
                try:
                    tab = page.locator(f'[role="tab"]:text-is("{count_text}")').first
                    await tab.wait_for(state="visible", timeout=3_000)
                    await tab.click()
                    log.info("ui_automation.count_set", value=count, tab_text=count_text)
                except Exception as e:  # noqa: BLE001
                    log.warning("ui_automation.count_set_failed", value=count, error=str(e))

        await page.keyboard.press("Escape")
        await page.wait_for_timeout(400)

    # ------------------------------------------------------------------
    # Internal helpers — batchGenerateImages capture (unit 3.6)
    # ------------------------------------------------------------------

    @staticmethod
    def _attach_batch_response_listener(
        page: Page, *, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Synchronously register a ``page.on('response', ...)`` listener
        that records ``batchGenerateImages`` responses into a shared list.

        When ``project_id`` is provided, only responses whose URL contains
        ``/projects/{project_id}/`` are captured — this prevents stale
        responses from previously-visited projects accumulating in the list.

        Returns the shared list — the caller submits the prompt next, then
        polls / awaits that list via :meth:`_await_captured`. Registering
        the listener BEFORE issuing the prompt click eliminates the race
        where the click could fire before an ``asyncio.create_task``-
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
            captured.append({"status": response.status, "url": response.url, "body": body})
            log.info(
                "ui_automation.batch_response_captured",
                status=response.status,
                url=response.url,
            )

        page.on("response", on_response)
        return captured

    @staticmethod
    async def _await_captured(
        captured: list[dict[str, Any]],
        timeout_s: float = 180.0,
        *,
        expected_count: int = 1,
        poll_interval_s: float = 0.5,
    ) -> list[dict[str, Any]]:
        """Wait for ``expected_count`` batchGenerateImages responses.

        Flow generates N images via N separate API calls (not one call with
        N URLs). We poll until we have enough responses or the timeout expires.

        Raises ``TimeoutError`` if no responses arrive within ``timeout_s``.
        Returns all captured responses (may be fewer than expected_count if
        timeout fires after at least one response).
        """
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and len(captured) < expected_count:
            await asyncio.sleep(poll_interval_s)
        if not captured:
            raise TimeoutError(f"No batchGenerateImages response within {timeout_s:.1f}s.")
        if len(captured) < expected_count:
            log.warning(
                "ui_automation.fewer_responses_than_expected",
                got=len(captured),
                expected=expected_count,
            )
        return list(captured)

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
        captured = UiAutomationTransport._attach_batch_response_listener(page)
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
        project_id: str | None,
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
        _ = project_id  # accepted for Protocol parity; UI creates its own project
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

        # Resolve the project_id from the URL now that we're in the editor.
        nav_project_id = _extract_project_id(page.url)
        aspect_cli = _aspect_cli_from_enum(request.aspect)

        # Configure generation settings (aspect ratio + count) BEFORE attaching
        # the response listener so settings clicks don't interfere with capture.
        await self._configure_generation_settings(page, aspect_cli, request.count)

        # Attach the response listener SYNCHRONOUSLY before any prompt
        # action. asyncio.create_task is unsafe here: it defers the listener
        # registration until the new task gets event-loop scheduling, which
        # could happen AFTER _send_prompt's click on a busy loop. Splitting
        # attach/await eliminates that race. Project-ID filter prevents stale
        # responses from previously-visited projects accumulating in the list.
        captured = self._attach_batch_response_listener(page, project_id=nav_project_id)
        await self._send_prompt(page, request.prompt, out_dir)
        responses = await self._await_captured(captured, expected_count=request.count)

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
