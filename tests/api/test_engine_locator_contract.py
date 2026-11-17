"""Real-engine contract: ``.last`` / ``nth(-1)`` resolve a single match (#1319).

The migrated composer resolves its settings pane and its library picker with
``page.locator(OVERLAY).filter(has=...).last`` and then calls ``count()`` under it.
patchright 1.61.2 returned ``0`` for that chain whenever exactly one element
matched, so every migrated-host generation on that engine failed with exit 23
("0 option groups rendered") while Flow's UI was unchanged.

Fake pages cannot see a driver regression, so this test drives each installed
engine against a real browser (system Chrome) on a local page. An engine that is
not installed, or a host without Chrome, skips rather than fails.
"""

from __future__ import annotations

import asyncio
import importlib
from typing import Any

import pytest

_ENGINES = ("playwright", "patchright")


async def _counts(module: Any) -> dict[str, int]:
    async with module.async_playwright() as p:
        try:
            browser = await p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # no system Chrome on this host
            pytest.skip(f"system Chrome unavailable: {type(e).__name__}")
        try:
            page = await browser.new_page()
            # The composer's shape: two overlays, one holding the option group.
            await page.set_content(
                '<div class="pane">a</div><div class="pane"><b role="radiogroup"></b></div>'
            )
            filtered = page.locator(".pane").filter(has=page.locator("[role=radiogroup]"))
            single = page.locator("[role=radiogroup]")
            return {
                "filter.last": await filtered.last.count(),
                "filter.nth(-1)": await filtered.nth(-1).count(),
                "filter.last >> descendant": await filtered.last.locator(
                    "[role=radiogroup]"
                ).count(),
                "plain.last": await single.last.count(),
                "plain.nth(-1)": await single.nth(-1).count(),
            }
        finally:
            await browser.close()


@pytest.mark.parametrize("engine", _ENGINES)
def test_last_and_nth_minus_one_count_a_single_match(engine: str) -> None:
    try:
        module = importlib.import_module(f"{engine}.async_api")
    except ImportError:
        pytest.skip(f"{engine} is not installed")
    counts = asyncio.run(_counts(module))
    assert counts == dict.fromkeys(counts, 1), f"{engine}: {counts}"
