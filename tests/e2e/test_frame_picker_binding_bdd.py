"""E2E for the Frames picker's name match on the migrated composer (#860).

Binds ``tests/features/frame_picker_binding.feature``. The Gherkin's ``@e2e`` tags
become pytest markers via pytest-bdd, so ``-m e2e`` / ``-m e2e_auth`` select this file
exactly like a hand-written e2e — see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e and not a unit test.** #860 is a defect in what a *locator* reads: the
picker tile is ``<mat-icon>image</mat-icon><span>name</span>`` and Playwright's text
engine concatenates the two, so ``filter(has_text=re.compile(r"^\\s*name\\s*$"))``
matches nothing while the asset is plainly listed. A fake locator returns whatever
list the test wrote into it and happily "matches" the anchored pattern, so the mocked
version of this test passes against the bug. Only a real DOM can falsify it.

**Cost: zero.** Every Flow origin is served by ``page.route(...).fulfill(...)``, the
same harness as ``test_click_attribution_bdd.py`` — no Google, no profile, no credits.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from playwright.async_api import Page, Route, async_playwright
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports import migrated_composer
from gflow_cli.api.transports.migrated_composer import (
    BOUND_CHIP,
    EMPTY_CHIP,
    PICKER_OPTION,
    MigratedComposer,
)
from gflow_cli.errors import ReferenceNotFoundError

scenarios("../features/frame_picker_binding.feature")

PROJECT_URL = "https://flow.google.com/project/e2e-frame-picker-binding"

#: What this run uploaded. The trailing tag is `_unique_display_name`'s (#792).
UPLOADED = "hero-ab12cd34.png"
#: Yesterday's copy of the same file: same stem, same suffix, different tag.
STALE = "hero-99887766.png"
MEDIA_ID = "e2e-0000-media-id"

_STYLE = """
  body { margin: 0; font-family: sans-serif; }
  .cdk-overlay-pane { position: absolute; top: 40px; left: 0; background: #fff; }
  button.asset-item { display: block; width: 260px; height: 28px; }
  button.chip-container, button.chip-container img { display: block; width: 40px; height: 40px; }
"""

#: The picker, in the shape the failing run reported. The `mat-icon` is load-bearing:
#: its ligature text is what the tile concatenates into the file name, and removing it
#: would make every scenario here pass against the bug.
_SCRIPT = """
  const ASSETS = __ASSETS__;
  document.querySelector('flow-prompt-box button.empty-chip')
    .addEventListener('click', () => {
      const pane = document.createElement('div');
      pane.className = 'cdk-overlay-pane';
      const picker = document.createElement('flow-add-menu-popover-content');
      const search = document.createElement('input');
      search.type = 'text';
      picker.appendChild(search);
      for (const name of ASSETS) {
        const opt = document.createElement('button');
        opt.className = 'asset-item';
        opt.setAttribute('role', 'option');
        opt.innerHTML = "<mat-icon>image</mat-icon><span class='asset-name'>"
                        + name + "</span>";
        opt.addEventListener('click', () => {
          document.title = name;
          pane.remove();
          const box = document.querySelector('flow-prompt-box');
          const empty = box.querySelector('button.empty-chip');
          if (empty) empty.remove();
          const chip = document.createElement('button');
          chip.className = 'chip-container';
          chip.innerHTML = "<img alt='" + name + "' src='data:image/gif;base64,"
                           + "R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7'>";
          box.appendChild(chip);
        });
        picker.appendChild(opt);
      }
      pane.appendChild(picker);
      document.body.appendChild(pane);
    });
"""


def _page(assets: list[str]) -> str:
    """A migrated-host prompt box with one empty Start chip and a library of `assets`."""
    return (
        f"<!doctype html><html><head><style>{_STYLE}</style></head><body>"
        "<flow-prompt-box><button class='empty-chip'>Start</button></flow-prompt-box>"
        f"<script>{_SCRIPT.replace('__ASSETS__', json.dumps(assets))}</script>"
        "</body></html>"
    )


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _drive(html: str, world: dict[str, Any]) -> None:
    """Launch a real browser, serve `html` for every Flow URL, run the picker."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page: Page = await (await browser.new_context()).new_page()

            async def _handler(route: Route) -> None:
                await route.fulfill(status=200, content_type="text/html", body=html)

            await page.route("https://flow.google.com/**", _handler)
            await page.goto(PROJECT_URL, wait_until="domcontentloaded")
            # Read the surface before driving it: mount the picker once, record what a
            # real locator sees, then reload so the driver starts from a pristine page.
            # After a successful pick the pane is gone, so this cannot be read afterwards.
            await page.locator(EMPTY_CHIP).first.click(timeout=4000)
            await page.locator(PICKER_OPTION).first.wait_for(state="visible", timeout=4000)
            world["listed"] = await page.locator(PICKER_OPTION).all_text_contents()
            await page.goto(PROJECT_URL, wait_until="domcontentloaded")
            try:
                await MigratedComposer()._pick_frame_by_name(page, UPLOADED, MEDIA_ID)  # noqa: SLF001
                world["error"] = None
            except BaseException as exc:  # noqa: BLE001 - the failure IS the assertion
                world["error"] = exc
            finally:
                # Read the page's record BEFORE the browser goes away; the title
                # survives a failed run, `document` does not.
                world["clicked"] = await page.title()
                world["chips"] = await page.locator(BOUND_CHIP).count()
        finally:
            await browser.close()


# --------------------------------------------------------------------------- given


@given("a Frames picker listing the uploaded frame")
def _lists_it(world: dict[str, Any]) -> None:
    world["html"] = _page([UPLOADED])


@given("a Frames picker listing an older copy of the same file")
def _lists_a_stale_copy(world: dict[str, Any]) -> None:
    # Stale first, so "the first option" is the wrong answer.
    world["html"] = _page([STALE, UPLOADED])


@given("a Frames picker that never lists the uploaded frame")
def _lists_something_else(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    world["html"] = _page([STALE])
    # The miss path is three searches of FRAME_PICKER_OPEN_S with a pause between --
    # half a minute of waiting for a page that mounts synchronously. The timeout is
    # not what this scenario is about, so it is shortened rather than served.
    monkeypatch.setattr(migrated_composer, "FRAME_PICKER_OPEN_S", 0.5)
    monkeypatch.setattr(migrated_composer, "FRAME_SEARCH_RETRY_PAUSE_S", 0.01)


# ---------------------------------------------------------------------------- when


@when("the driver picks the uploaded frame by name")
def _pick(world: dict[str, Any]) -> None:
    asyncio.run(_drive(world["html"], world))


# ---------------------------------------------------------------------------- then


@then("the picker's own text content concatenates the icon into the name")
def _concatenates(world: dict[str, Any]) -> None:
    # Without this the scenario could go green for the wrong reason: a tile that does
    # NOT concatenate is not the surface #860 happens on, and the anchored matcher
    # would have bound it too.
    assert f"image{UPLOADED}" in world["listed"], world["listed"]


@then("the Start chip binds the uploaded frame and nothing is raised")
def _bound(world: dict[str, Any]) -> None:
    assert world["error"] is None, f"the picker refused an asset it was listing: {world['error']!r}"
    assert world["clicked"] == UPLOADED, (
        f"bound {world['clicked']!r}, not the run's own upload {UPLOADED!r} — a match "
        "loose enough to take a stale copy gives away what the run-unique tag buys (#792)"
    )
    assert world["chips"] == 1, world["chips"]


@then("it fails with exit 32 naming the file and what the picker did list")
def _refused(world: dict[str, Any]) -> None:
    from gflow_cli.errors import EXIT_CODE_MAP

    error = world["error"]
    assert isinstance(error, ReferenceNotFoundError), f"expected exit 32, got {error!r}"
    assert EXIT_CODE_MAP[ReferenceNotFoundError] == 32
    text = str(error)
    assert UPLOADED in text, text
    assert STALE in text, f"the listing is the diagnostic and it is missing: {text}"


@then("no chip was bound")
def _nothing_bound(world: dict[str, Any]) -> None:
    assert world["clicked"] != STALE, "bound the stale copy it was refusing to find"
    assert world["chips"] == 0, world["chips"]
