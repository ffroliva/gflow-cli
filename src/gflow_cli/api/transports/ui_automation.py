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
