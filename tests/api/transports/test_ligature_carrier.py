"""The ligature carrier differs by host, and the class-only anchor must span both (#730).

Flow renders the SAME Material Symbols ligature under different tags depending on which
frontend serves the account:

* ``labs.google``           → ``<i class="google-symbols">arrow_forward</i>``
* ``flow.google.com``       → ``<mat-icon class="… google-symbols …">arrow_forward</mat-icon>``

A selector anchored on ``i.google-symbols`` therefore matches **zero** on the migrated host
while the control is fully visible — measured 2026-09-07 on `denon82`, every ligature, both
surfaces (``docs/superpowers/spikes/2026-09-07-ligature-carrier-and-name-drift.md``). That
split shipped twice: #727 (`--format-prompt` a silent no-op) and #731 (published docs
asserting stale selectors as VERIFIED).

The fix rests on one claim: **the `google-symbols` class sits on both carriers, so a
class-only anchor covers both hosts.** On the live migrated host that was observed —
`.google-symbols` counted identically to `mat-icon`, every time. On **labs it was never
observed**: no unmoved account exists here, so the `<i>` half is CSS semantics, asserted.

This file settles it with a real CSS engine and no account, no network and no credits:
a two-carrier fixture, headless Chromium, one `count()`. If the assertion below ever fails,
the premise of #730 is wrong and the class-only anchors must be reverted to explicit
per-carrier cascades.

Deliberately a *precondition*, not a mock: a `MagicMock` page would happily agree with
whatever the author believed, which is precisely how an unverified selector claim reaches
production. Only a browser can answer whether a selector resolves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gflow_cli.api.transports.ui_automation import (
    IMAGE_MODEL_PICKER_TRIGGER,
    SUBMIT_BUTTON_SELECTORS,
)

if TYPE_CHECKING:  # pragma: no cover
    from collections.abc import AsyncIterator

    from playwright.async_api import Page

# Both carriers, same ligature, plus decoys: a span that also carries the class (the repo's
# own `_gsQuery` already treats spans as carriers), and a longer ligature that must NOT be
# matched by an exact `:text-is`.
_TWO_CARRIER_PAGE = """
<button id="labs"><i class="google-symbols">arrow_forward</i></button>
<button id="migrated">
  <mat-icon class="mat-icon notranslate google-symbols">arrow_forward</mat-icon>
</button>
<button id="span-carrier"><span class="google-symbols">arrow_drop_down</span></button>
<button id="decoy-longer"><mat-icon class="google-symbols">arrow_forward_ios</mat-icon></button>
<button id="decoy-no-class"><i>arrow_forward</i></button>
"""


@pytest.fixture
async def page() -> AsyncIterator[Page]:
    """A real headless Chromium page, or skip if the browser isn't installed."""
    playwright_api = pytest.importorskip("playwright.async_api")
    try:
        async with playwright_api.async_playwright() as pw:
            try:
                browser = await pw.chromium.launch()
            except Exception as exc:  # pragma: no cover - environment dependent
                pytest.skip(f"chromium unavailable: {type(exc).__name__}: {exc}")
            page = await browser.new_page()
            try:
                yield page
            finally:
                await browser.close()
    except NotImplementedError as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"playwright cannot start here: {exc}")


@pytest.mark.asyncio
class TestClassOnlyAnchorSpansBothCarriers:
    async def test_class_only_matches_i_and_mat_icon(self, page: Page) -> None:
        """THE precondition for #730. If this fails, the class-only sweep is wrong."""
        await page.set_content(_TWO_CARRIER_PAGE)

        assert await page.locator(".google-symbols:text-is('arrow_forward')").count() == 2, (
            "the class-only anchor must match BOTH the labs <i> and the migrated <mat-icon>"
        )

    async def test_the_tag_qualified_anchor_misses_the_migrated_carrier(self, page: Page) -> None:
        """Why the sweep exists — reproduced offline, so it cannot be argued with.

        This is the shipped `i.google-symbols` form. It sees the labs carrier and is
        blind to the migrated one, which is the entire #727 / #731 failure in one number.
        """
        await page.set_content(_TWO_CARRIER_PAGE)

        assert await page.locator("i.google-symbols:text-is('arrow_forward')").count() == 1
        # One mat-icon holds the exact ligature; the other holds `arrow_forward_ios`, which
        # `:text-is` correctly refuses. Asserting 2 here was my own error, caught by the
        # fixture — the decoy is doing exactly the job it was added for.
        assert await page.locator("mat-icon:text-is('arrow_forward')").count() == 1

    async def test_exact_text_still_excludes_a_longer_ligature(self, page: Page) -> None:
        """`:text-is` must stay exact — `arrow_forward_ios` is a real Material Symbol."""
        await page.set_content(_TWO_CARRIER_PAGE)

        ids = await page.locator(
            "button:has(.google-symbols:text-is('arrow_forward'))"
        ).evaluate_all("els => els.map(e => e.id)")
        assert sorted(ids) == ["labs", "migrated"], f"unexpected match set: {ids}"

    async def test_class_only_ignores_an_unclassed_carrier(self, page: Page) -> None:
        """A bare `<i>` with no class is not an icon carrier and must not match."""
        await page.set_content(_TWO_CARRIER_PAGE)

        ids = await page.locator(
            "button:has(.google-symbols:text-is('arrow_forward'))"
        ).evaluate_all("els => els.map(e => e.id)")
        assert "decoy-no-class" not in ids

    async def test_shipped_submit_cascade_resolves_on_both_carriers(self, page: Page) -> None:
        """The load-bearing submit path, against the SHIPPED constant.

        `SUBMIT_BUTTON_SELECTORS[0]` is what fires first. On the migrated host it was
        observed matching nothing, and only the third entry (`has-text`, which matches the
        mat-icon's *text* rather than its tag) rescued the submit — working by luck.
        """
        await page.set_content(_TWO_CARRIER_PAGE)

        matched = await page.locator(SUBMIT_BUTTON_SELECTORS[0]).count()
        assert matched == 2, (
            f"{SUBMIT_BUTTON_SELECTORS[0]!r} matched {matched} of 2 carriers — the submit "
            "button is anchored on one frontend's carrier tag"
        )

    async def test_shipped_image_model_picker_resolves_on_both_carriers(self, page: Page) -> None:
        """`IMAGE_MODEL_PICKER_TRIGGER` has no carrier twin and no text fallback."""
        await page.set_content(
            "<button aria-haspopup='menu' id='labs'>"
            "<i class='google-symbols'>arrow_drop_down</i></button>"
            "<button aria-haspopup='menu' id='migrated'>"
            "<mat-icon class='google-symbols'>arrow_drop_down</mat-icon></button>"
        )

        matched = await page.locator(IMAGE_MODEL_PICKER_TRIGGER).count()
        assert matched == 2, (
            f"{IMAGE_MODEL_PICKER_TRIGGER!r} matched {matched} of 2 carriers — a miss here is "
            "silent: the picker is best-effort and generation proceeds on whatever tier the "
            "editor opened at"
        )
