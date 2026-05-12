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
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api.dto import GeneratedImage
from gflow_cli.api.image import GenerateImageRequest

if TYPE_CHECKING:
    from playwright.async_api import Page

log = structlog.get_logger(__name__)


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
        self._pw_cm: object | None = None
        self._ctx: object | None = None
        self._page: Page | None = None
        self._setup_done: bool = False
        self._owns_playwright: bool = False

    # ------------------------------------------------------------------
    # Lifecycle — implementations land in per-method units 3.2–3.11.
    # ------------------------------------------------------------------

    async def setup(self, profile_dir: Path, *, page: Page | None = None) -> None:
        raise NotImplementedError("UiAutomationTransport.setup — unit 3.2")

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
