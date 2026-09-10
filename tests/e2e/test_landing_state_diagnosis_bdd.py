"""E2E for landing-state diagnosis (#756, #773, the 2026-09-10 RED canary).

Binds ``tests/features/landing_state_diagnosis.feature``. The Gherkin's ``@e2e``
tags become pytest markers via pytest-bdd, so this file is selected by ``-m e2e``
and ``-m e2e_auth`` exactly like a hand-written e2e — see
``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e and not a unit test.** The thing under test is what a real page's
URL *is* after a real client-side redirect. `goto` returns before that redirect
runs (issue #639), which is the trap this fix has to survive; a mocked page whose
`url` is whatever the test assigned cannot express it, and would pass against a
fix that reads the URL at the wrong moment. Only a real ``Page`` navigating for
real can falsify that.

**Cost: zero.** Every Flow origin is served by Playwright route interception, so
nothing reaches Google, no credit is spent, and no authenticated profile is
needed — the same harness as ``test_account_chooser_e2e.py``. The HTML stands in
for Flow's markup, so this proves the *mechanism* (land somewhere unexpected →
say where, and do not blame the anchor), not that `/about` is still Flow's
redirect target.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from playwright.async_api import Route, async_playwright
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.errors import AuthExpiredError, FlowAppError, UiSelectorDriftError, is_retryable

scenarios("../features/landing_state_diagnosis.feature")

PROJECT_ID = "e2e-landing-project"
PROJECT_URL = f"https://flow.google.com/project/{PROJECT_ID}"
ABOUT_URL = "https://flow.google.com/about"
LABS_GALLERY = "https://labs.google/fx/tools/flow?hl=en"
LABS_SIGNIN = "https://labs.google/fx/api/auth/signin?error=Callback"

# Flow's hop is client-side (spike 2026-09-04): `goto` returns on
# domcontentloaded and the redirect runs after. Reproducing it as a script —
# not as an HTTP 302 — is what makes this test able to fail the #639 way.
_REDIRECT_HTML = "<!doctype html><html><body><script>location.replace({!r})</script></body></html>"
_BARE_HTML = "<!doctype html><html><body><h1>{}</h1></body></html>"


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _serve(page: Any, pages: dict[str, str]) -> None:
    """Serve each URL prefix from local HTML; anything else gets a bare page."""

    async def _handler(route: Route) -> None:
        url = route.request.url
        body = next((html for prefix, html in pages.items() if url.startswith(prefix)), None)
        await route.fulfill(
            status=200,
            content_type="text/html",
            body=body if body is not None else _BARE_HTML.format("unrouted"),
        )

    for host in ("https://flow.google.com/**", "https://labs.google/**"):
        await page.route(host, _handler)


async def _drive(pages: dict[str, str], start: str, run: Any) -> BaseException | None:
    """Launch a real browser, serve `pages`, navigate to `start`, run `run(page)`."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, pages)
            await page.goto(start, wait_until="domcontentloaded")
            try:
                await run(page)
            except BaseException as exc:  # noqa: BLE001 - the failure IS the assertion
                return exc
            return None
        finally:
            await browser.close()


# --------------------------------------------------------------------------- given


@given("a real browser whose Flow requests are served locally, spending nothing")
def _harness(world: dict[str, Any]) -> None:
    world["pages"] = {}


@given("a project URL on the migrated host")
def _project_url(world: dict[str, Any]) -> None:
    world["start"] = PROJECT_URL


@given("the labs Flow gallery URL")
def _gallery_url(world: dict[str, Any]) -> None:
    world["start"] = LABS_GALLERY


# ---------------------------------------------------------------------------- when


@when("Flow redirects it to its public /about landing page")
def _redirect_to_about(world: dict[str, Any]) -> None:
    world["pages"] = {
        ABOUT_URL: _BARE_HTML.format("Flow"),
        PROJECT_URL: _REDIRECT_HTML.format(ABOUT_URL),
    }
    world["error"] = asyncio.run(
        _drive(
            world["pages"],
            world["start"],
            lambda page: MigratedComposer().ensure_editor(page, PROJECT_ID, timeout_s=2.0),
        )
    )


@when("Flow serves the project page but the settings trigger never appears")
def _project_without_trigger(world: dict[str, Any]) -> None:
    world["pages"] = {PROJECT_URL: _BARE_HTML.format("editor, minus the trigger")}
    world["error"] = asyncio.run(
        _drive(
            world["pages"],
            world["start"],
            lambda page: MigratedComposer().ensure_editor(page, PROJECT_ID, timeout_s=2.0),
        )
    )


@when("Flow answers it with a NextAuth sign-in error page")
def _signin_error(world: dict[str, Any]) -> None:
    world["pages"] = {
        LABS_SIGNIN: _BARE_HTML.format("Sign in"),
        LABS_GALLERY: _REDIRECT_HTML.format(LABS_SIGNIN),
    }
    world["error"] = asyncio.run(
        _drive(
            world["pages"],
            world["start"],
            lambda page: UiAutomationTransport()._enter_editor(page),  # noqa: SLF001
        )
    )


# ---------------------------------------------------------------------------- then


@then("the failure names the landing page and the project it did not open")
def _names_landing(world: dict[str, Any]) -> None:
    error = world["error"]
    assert isinstance(error, FlowAppError), f"expected FlowAppError, got {error!r}"
    assert "/about" in str(error), str(error)
    assert PROJECT_ID in str(error), str(error)


@then("the failure is not reported as selector drift")
def _not_drift(world: dict[str, Any]) -> None:
    error = world["error"]
    assert not isinstance(error, UiSelectorDriftError), str(error)
    assert "settings-trigger-button" not in str(error), str(error)


@then("the failure does not assert why the redirect happened")
def _no_unmeasured_cause(world: dict[str, Any]) -> None:
    # #756: `gflow auth status` reports the session verified while this happens,
    # so naming a cause we have not measured would be a second wrong diagnosis.
    text = str(world["error"]).lower()
    for claim in ("expired", "signed out", "sign in again", "not authenticated"):
        assert claim not in text, f"asserts an unmeasured cause ({claim!r}): {text}"


@then("the failure says the session is signed out")
def _says_signed_out(world: dict[str, Any]) -> None:
    error = world["error"]
    assert isinstance(error, AuthExpiredError), f"expected AuthExpiredError, got {error!r}"
    assert "signin" in str(error) or "sign-in" in str(error).lower(), str(error)


@then("the failure does not blame the New project anchor")
def _not_the_cta(world: dict[str, Any]) -> None:
    assert "New project" not in str(world["error"]), str(world["error"])


@then("the failure is reported as selector drift")
def _is_drift(world: dict[str, Any]) -> None:
    error = world["error"]
    assert isinstance(error, UiSelectorDriftError), f"expected UiSelectorDriftError, got {error!r}"


@then("the failure is not flagged retryable")
def _not_retryable(world: dict[str, Any]) -> None:
    """Exit 31's class default IS retryable — correct for the crash page it was built
    for. Whether it is correct for /about could not be measured: the redirect stopped
    reproducing on `ci-probe` before the flag could be tested
    (docs/superpowers/spikes/2026-09-10-about-redirect-stability.md). This shape
    raised exit 23 before, which was already non-retryable, so the raise site must
    preserve that rather than let an exit-code change smuggle in a retry claim.
    """
    error = world["error"]
    assert isinstance(error, FlowAppError), f"expected FlowAppError, got {error!r}"
    assert is_retryable(error) is False
