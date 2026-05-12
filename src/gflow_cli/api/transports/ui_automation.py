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

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest

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

# Browser viewport — matches the validated smoke (also matches the CG Worker).
_VIEWPORT = {"width": 1280, "height": 800}

# Prompt input selectors — Slate.js editor is the canonical target on
# Flow's editor page; the contenteditable/textarea fallbacks cover UI
# evolutions.
PROMPT_INPUT_SELECTORS = (
    'div[role="textbox"][data-slate-editor="true"]',
    'div[contenteditable="true"]',
    "textarea",
    '[aria-label*="prompt"]',
)

# Submit button selectors — the canonical button wraps an
# ``arrow_forward`` Material Symbols icon. Localized labels follow.
SUBMIT_BUTTON_SELECTORS = (
    'button:has(i.google-symbols:has-text("arrow_forward"))',
    'button:has-text("arrow_forward"):has-text("Create")',
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


class UiAutomationTransport:
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
            ctx = await pw.chromium.launch_persistent_context(
                str(profile_dir),
                headless=False,
                viewport=cast("ViewportSize", _VIEWPORT),
                locale="en-US",
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
        if "/project/" in page.url:
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

    async def _enter_editor(self, page: Page, out_dir: Path | None = None) -> None:
        """Click "+ New project" on the gallery and wait for /project/ nav.

        No-op when the URL already contains ``/project/``. Tries each
        selector in :data:`NEW_PROJECT_SELECTORS` in order — locale-stable
        icon-class first, localized text fallbacks after. On total failure
        a debug screenshot is written to ``out_dir`` (if provided) and
        ``RuntimeError`` is raised with the captured URL + path.
        """
        if "/project/" in page.url:
            log.info("ui_automation.editor_already_open", url=page.url)
            return

        await page.wait_for_timeout(3000)
        for selector in NEW_PROJECT_SELECTORS:
            try:
                loc = page.locator(selector).first
                await loc.wait_for(state="visible", timeout=5000)
                log.info("ui_automation.clicking_new_project", selector=selector)
                await loc.click()
                try:
                    await page.wait_for_url(lambda url: "/project/" in url, timeout=15_000)
                    log.info("ui_automation.entered_editor", url=page.url)
                    return
                except Exception:  # noqa: BLE001 — try next selector
                    log.warning(
                        "ui_automation.new_project_click_did_not_navigate",
                        selector=selector,
                    )
            except Exception:  # noqa: BLE001 — selector didn't match; try next
                continue

        shot_path: Path | None = None
        if out_dir is not None:
            out_dir.mkdir(parents=True, exist_ok=True)
            shot_path = out_dir / "debug_new_project.png"
            try:
                await page.screenshot(path=str(shot_path), full_page=True)
            except Exception:  # noqa: BLE001 — screenshot best-effort
                pass
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
            shot_path: Path | None = None
            if out_dir is not None:
                out_dir.mkdir(parents=True, exist_ok=True)
                shot_path = out_dir / "debug_prompt_not_found.png"
                try:
                    await page.screenshot(path=str(shot_path), full_page=True)
                except Exception:  # noqa: BLE001 — screenshot best-effort
                    pass
            raise RuntimeError(
                f"Prompt input not found in Flow UI. URL: {page.url}. Screenshot: {shot_path}"
            )

        await input_box.click()
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Delete")
        # Slate.js requires real keyboard events; .fill() bypasses onChange.
        await page.keyboard.type(prompt_text)
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
    # Internal helpers — batchGenerateImages capture (unit 3.6)
    # ------------------------------------------------------------------

    @staticmethod
    async def _capture_batch_response(
        page: Page,
        timeout_s: float = 120.0,
        *,
        poll_interval_s: float = 0.5,
    ) -> dict[str, Any]:
        """Register a Playwright ``response`` listener and return the first
        ``batchGenerateImages`` response.

        Non-matching responses are ignored. Response-body parse failures
        are logged but do not propagate — the capture loop simply doesn't
        record that response and keeps waiting.

        Raises ``TimeoutError`` if no matching response arrives within
        ``timeout_s``.
        """
        captured: list[dict[str, Any]] = []

        async def on_response(response: Any) -> None:
            if "batchGenerateImages" not in response.url:
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
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline and not captured:
            await asyncio.sleep(poll_interval_s)
        if not captured:
            raise TimeoutError(f"No batchGenerateImages response within {timeout_s:.1f}s.")
        return captured[0]

    async def refresh_auth(self) -> None:
        raise NotImplementedError("UiAutomationTransport.refresh_auth — unit 3.10")

    async def generate_images(
        self,
        *,
        project_id: str,
        request: GenerateImageRequest,
    ) -> list[GeneratedImage]:
        raise NotImplementedError("UiAutomationTransport.generate_images — unit 3.9")

    async def teardown(self) -> None:
        raise NotImplementedError("UiAutomationTransport.teardown — unit 3.11")
