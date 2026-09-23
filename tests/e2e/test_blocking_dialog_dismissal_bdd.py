"""E2E for clearing Flow's promo modal before the first click (#859).

Binds ``tests/features/blocking_dialog_dismissal.feature``. The Gherkin's ``@e2e`` tags
become pytest markers via pytest-bdd, so ``-m e2e`` / ``-m e2e_auth`` select this file
exactly like a hand-written e2e — see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e and not a unit test.** The defect is *when* the dialog is in the DOM:
``ensure_editor``'s probe runs one frame after ``domcontentloaded`` and the modal mounts
after it, so the dismissal looks healthy while doing nothing. A mocked page is whatever
the test constructed before the test ran — it has no "later", so the broken ordering and
the fixed one are indistinguishable to it.

**Cost: zero.** Every Flow origin is served by ``page.route(...).fulfill(...)``, the
same harness as ``test_click_attribution_bdd.py`` — no Google, no profile, no credits.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from playwright.async_api import Page, Route, async_playwright
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import UiSelectorDriftError

scenarios("../features/blocking_dialog_dismissal.feature")

PROJECT_URL = "https://flow.google.com/project/e2e-blocking-dialog"

#: How long after load the promo panel mounts. Long enough that `ensure_editor`'s
#: instantaneous probe cannot see it, short enough that the driver's own 5 s click
#: budget is still running when it appears — the shape of the reported incident.
_DIALOG_DELAY_MS = 250

_STYLE = """
  body { margin: 0; font-family: sans-serif; }
  /* Below the dialog's own box on purpose: the element on top of the trigger must be
     the BACKDROP, which is what the live incident's post-mortem named. Put the trigger
     under the panel itself and the message names the panel, testing other geometry. */
  .settings-trigger-button { position: absolute; top: 400px; left: 40px;
                             width: 147px; height: 32px; }
  .cdk-overlay-backdrop { position: absolute; top: 0; left: 0;
                          width: 100vw; height: 100vh; z-index: 9999; }
  [role='dialog'] { position: absolute; top: 20px; left: 20px;
                    width: 320px; height: 200px; z-index: 10000; background: #fff; }
"""

#: Clicking the trigger mounts the overlay `_open_pane` waits for, so the pane-opens
#: assertions exercise the whole path rather than just "no exception".
_OPENS_PANE_JS = """
  document.querySelector('.settings-trigger-button').addEventListener('click', () => {
    const pane = document.createElement('div');
    pane.className = 'cdk-overlay-pane';
    pane.innerHTML = "<div role='radiogroup'><div role='radio'>16:9</div></div>";
    document.body.appendChild(pane);
  });
"""

#: Flow's promo panel, in the shape the incident reported: a `role="dialog"` div (not a
#: `<dialog>` element) over an Angular CDK backdrop, mounted *after* the editor settles.
#: `document.title` carries which gesture removed it, because a passing run leaves no
#: other trace and a failing one still has to be readable.
_DIALOG_JS = """
  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    const d = document.querySelector('[role=dialog]');
    if (!d) return;
    document.title = 'ESCAPE';
    if (__CLOSEABLE__) { d.remove(); document.querySelector('.cdk-overlay-backdrop').remove(); }
  });
  setTimeout(() => {
    const backdrop = document.createElement('div');
    backdrop.className = 'cdk-overlay-backdrop cdk-overlay-backdrop-showing';
    document.body.appendChild(backdrop);
    const dialog = document.createElement('div');
    dialog.setAttribute('role', 'dialog');
    dialog.innerHTML = __CLOSE_BUTTON__;
    document.body.appendChild(dialog);
    const close = dialog.querySelector('button');
    if (close) close.addEventListener('click', () => {
      document.title = 'CLOSE';
      if (__CLOSEABLE__) { dialog.remove(); backdrop.remove(); }
    });
  }, __DELAY__);
"""

#: The close affordance `DIALOG_CLOSE` matches: a button carrying the `close` ligature.
_CLOSE_BUTTON = '"<button><mat-icon>close</mat-icon></button><p>Take Flow on the road</p>"'
_NO_CLOSE_BUTTON = '"<p>Take Flow on the road</p>"'


def _page(*, dialog: bool = True, closeable: bool = True, close_button: bool = True) -> str:
    script = _OPENS_PANE_JS
    if dialog:
        script += (
            _DIALOG_JS.replace("__CLOSEABLE__", "true" if closeable else "false")
            .replace("__CLOSE_BUTTON__", _CLOSE_BUTTON if close_button else _NO_CLOSE_BUTTON)
            .replace("__DELAY__", str(_DIALOG_DELAY_MS))
        )
    return (
        f"<!doctype html><html><head><style>{_STYLE}</style></head><body>"
        "<button class='settings-trigger-button' aria-label='Settings trigger'>settings</button>"
        f"<script>{script}</script>"
        "</body></html>"
    )


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _drive(html: str, world: dict[str, Any]) -> None:
    """Launch a real browser, serve `html` for every Flow URL, open the settings pane."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page: Page = await (await browser.new_context()).new_page()

            async def _handler(route: Route) -> None:
                await route.fulfill(status=200, content_type="text/html", body=html)

            await page.route("https://flow.google.com/**", _handler)
            await page.goto(PROJECT_URL, wait_until="domcontentloaded")
            # Let the modal mount, exactly as it does between `ensure_editor` returning
            # and the first real click. Nothing in the driver waits for it.
            await page.wait_for_timeout(_DIALOG_DELAY_MS * 3)
            try:
                await MigratedComposer()._open_pane(page)  # noqa: SLF001
                world["error"] = None
            except BaseException as exc:  # noqa: BLE001 - the failure IS the assertion
                world["error"] = exc
            finally:
                # Read the page's own record BEFORE the browser goes away; the title
                # survives a failed run, `document` does not.
                world["gesture"] = await page.title()
        finally:
            await browser.close()


# --------------------------------------------------------------------------- given


@given("a Flow project page whose promo dialog appears only after the editor settles")
def _late_dialog(world: dict[str, Any]) -> None:
    world["html"] = _page()


@given("a Flow project page whose promo dialog offers no close button")
def _no_close_button(world: dict[str, Any]) -> None:
    world["html"] = _page(close_button=False)


@given("a Flow project page whose promo dialog ignores its own close button")
def _stuck_dialog(world: dict[str, Any]) -> None:
    world["html"] = _page(closeable=False)


@given("a Flow project page with no promo dialog")
def _no_dialog(world: dict[str, Any]) -> None:
    world["html"] = _page(dialog=False)


# ---------------------------------------------------------------------------- when


@when("the driver opens the settings pane")
def _open(world: dict[str, Any]) -> None:
    asyncio.run(_drive(world["html"], world))


# ---------------------------------------------------------------------------- then


@then("the pane opens and nothing is raised")
def _pane_opened(world: dict[str, Any]) -> None:
    assert world["error"] is None, f"the settings pane never opened: {world['error']!r}"


@then("the dialog was dismissed by its close button")
def _closed_by_button(world: dict[str, Any]) -> None:
    assert world["gesture"] == "CLOSE", (
        f"page recorded {world['gesture']!r} — Escape is the fallback, and preferring it "
        "over a close button the dialog offers is a gesture aimed at whatever else has focus"
    )


@then("the dialog was dismissed by Escape")
def _closed_by_escape(world: dict[str, Any]) -> None:
    assert world["gesture"] == "ESCAPE", world["gesture"]


@then("it fails with exit 23")
def _exit_23(world: dict[str, Any]) -> None:
    from gflow_cli.errors import EXIT_CODE_MAP, is_retryable

    error = world["error"]
    assert isinstance(error, UiSelectorDriftError), f"expected exit 23, got {error!r}"
    assert EXIT_CODE_MAP[UiSelectorDriftError] == 23
    # A flag is a claim: this raise site's answer is PRESERVED, not re-measured.
    assert is_retryable(error) is False


@then("the message names the covering backdrop")
def _names_backdrop(world: dict[str, Any]) -> None:
    text = str(world["error"])
    assert "cdk-overlay-backdrop" in text, text


@then("no dismissal gesture was made")
def _nothing_pressed(world: dict[str, Any]) -> None:
    # An unconditional Escape would close the settings pane the next line opens, so
    # "it did nothing" is the assertion, not an absence of evidence.
    assert world["gesture"] not in ("CLOSE", "ESCAPE"), world["gesture"]
