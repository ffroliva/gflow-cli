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

    # ------------------------------------------------------------------
    # Internal helpers — image URL extraction (unit 3.7)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_image_urls(response: dict[str, Any]) -> list[str]:
        """Extract image URLs from a batchGenerateImages response dict.

        Real wire shape (observed 2026-05-12)::

            body.media[].image.generatedImage.fifeUrl

        Legacy / fallback keys are accepted in case Flow's response
        evolves: ``image.uri``, ``image.downloadUrl``,
        ``image.encodedImage``, ``generatedImage.encodedImage``.

        Also walks ``body.requests[].media[]`` for batched-multi
        response shapes.
        """
        body: dict[str, Any] = cast("dict[str, Any]", response.get("body") or {})
        urls: list[str] = []

        def _pull(media_list: Any) -> None:
            if not isinstance(media_list, list):
                return
            for m_raw in cast("list[Any]", media_list):
                if not isinstance(m_raw, dict):
                    continue
                m: dict[str, Any] = cast("dict[str, Any]", m_raw)
                img: dict[str, Any] = cast("dict[str, Any]", m.get("image") or {})
                gen: dict[str, Any] = cast("dict[str, Any]", img.get("generatedImage") or {})
                u = (
                    gen.get("fifeUrl")
                    or img.get("uri")
                    or img.get("downloadUrl")
                    or img.get("encodedImage")
                    or gen.get("encodedImage")
                )
                if isinstance(u, str) and u:
                    urls.append(u)

        _pull(body.get("media", []))
        requests_obj = body.get("requests", [])
        if isinstance(requests_obj, list):
            for req_raw in cast("list[Any]", requests_obj):
                if isinstance(req_raw, dict):
                    req: dict[str, Any] = cast("dict[str, Any]", req_raw)
                    _pull(req.get("media", []))
        return urls

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
                    p = out_dir / f"image_{i:02d}.png"
                    p.write_bytes(resp.content)
                    paths.append(p)
                    log.info(
                        "ui_automation.png_saved",
                        path=str(p),
                        bytes=len(resp.content),
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
        project_id: str,
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
        page: Page = self._page

        await self._enter_editor(page)

        capture_task = asyncio.create_task(self._capture_batch_response(page))
        await self._send_prompt(page, request.prompt)
        response = await capture_task

        status = response.get("status")
        if status != 200:
            raise RuntimeError(
                f"batchGenerateImages returned HTTP {status} "
                f"(expected 200). Response URL: {response.get('url')}"
            )

        body: dict[str, Any] = cast("dict[str, Any]", response.get("body") or {})
        media_list_raw = body.get("media", [])
        if not isinstance(media_list_raw, list):
            raise RuntimeError("batchGenerateImages returned 200 but body.media is not a list.")

        images: list[GeneratedImage] = []
        for item_raw in cast("list[Any]", media_list_raw):
            if not isinstance(item_raw, dict):
                continue
            item: dict[str, Any] = cast("dict[str, Any]", item_raw)
            try:
                images.append(GeneratedImage.from_response_item(item))
            except ValueError as e:
                log.warning("ui_automation.parse_media_item_failed", error=str(e))

        if not images:
            raise RuntimeError("batchGenerateImages returned 200 but no parseable image URLs.")
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
