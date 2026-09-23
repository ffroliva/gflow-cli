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
from gflow_cli.errors import (
    AuthExpiredError,
    FlowAccessUnavailableError,
    FlowAppError,
    UiSelectorDriftError,
    is_retryable,
)

scenarios("../features/landing_state_diagnosis.feature")

PROJECT_ID = "e2e-landing-project"
PROJECT_URL = f"https://flow.google.com/project/{PROJECT_ID}"
ABOUT_URL = "https://flow.google.com/about"
LABS_GALLERY = "https://labs.google/fx/tools/flow?hl=en"
LABS_SIGNIN = "https://labs.google/fx/api/auth/signin?error=Callback"
LABS_PROJECT = "https://labs.google/fx/tools/flow/project/" + PROJECT_ID
UNAVAILABLE_URL = "https://flow.google.com/unavailable"

# The measured shape, 2026-09-15, from a real account with no Flow entitlement
# (spike: scripts/dev/spike_flow_unavailable_signal.py). Flow's own Angular shell
# renders and routes to a dedicated component — the app IS loaded, it simply has
# nothing to show this account. The <h2> and the support link are reproduced so a
# selector that keys on prose instead of the component would still find prose to
# key on, and would still be wrong.
_UNAVAILABLE_HTML = """<!doctype html><html><body>
<aisandbox-root>
  <main>
    <flow-banner></flow-banner>
    <router-outlet></router-outlet>
    <flow-pinhole-unavailable-screen>
      <h2>It looks like you don't have access to Flow.</h2>
      <a href="https://support.google.com/flow/answer/16353333">here</a>
      <a href="https://labs.google/flow/tv">Flow TV</a>
    </flow-pinhole-unavailable-screen>
  </main>
</aisandbox-root>
</body></html>"""

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


@given("a project URL on the labs host")
def _labs_project_url(world: dict[str, Any]) -> None:
    world["start"] = LABS_PROJECT


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


@when("Flow answers it with the unavailable screen")
def _unavailable_screen(world: dict[str, Any]) -> None:
    """The hop is client-side on purpose.

    Measured: `https://flow.google.com/` answers **200** and Angular routes to the
    unavailable screen afterwards — there is no HTTP 3xx to key on. Reproducing it
    as a script rather than a redirect is what lets this test fail the #639 way if
    the check is ever moved ahead of the readiness wait.
    """
    world["pages"] = {
        UNAVAILABLE_URL: _UNAVAILABLE_HTML,
        PROJECT_URL: _REDIRECT_HTML.format(UNAVAILABLE_URL),
    }
    world["error"] = asyncio.run(
        _drive(
            world["pages"],
            world["start"],
            lambda page: MigratedComposer().ensure_editor(page, PROJECT_ID, timeout_s=2.0),
        )
    )


@when("Flow answers the labs editor with the unavailable screen")
def _labs_unavailable(world: dict[str, Any]) -> None:
    """The labs arm, which `_enter_editor`'s guard cannot reach.

    With a project id, `_enter_editor` navigates and returns with no readiness gate, so
    the first place that can ask "can this account reach Flow at all?" is the mode
    switch. Driving `_switch_to_image_mode` directly is what makes this a test of the
    guard rather than of the navigation above it.
    """
    world["pages"] = {LABS_PROJECT: _UNAVAILABLE_HTML}
    world["error"] = asyncio.run(
        _drive(
            world["pages"],
            world["start"],
            lambda page: UiAutomationTransport._switch_to_image_mode(page),  # noqa: SLF001
        )
    )


# ---------------------------------------------------------------------------- then


@then("the failure says this account cannot reach Flow")
def _says_no_access(world: dict[str, Any]) -> None:
    error = world["error"]
    assert isinstance(error, FlowAccessUnavailableError), (
        f"expected FlowAccessUnavailableError, got {error!r}"
    )
    text = f"{error} {error.remediation_hint or ''}".casefold()
    assert "subscription" in text or "google ai" in text, (
        "must name the requirement the user can act on; Flow needs Google AI "
        f"Plus/Pro/Ultra (or a qualifying Workspace plan): {text}"
    )


@then("the failure does not tell the user to sign in again")
def _no_relogin_loop(world: dict[str, Any]) -> None:
    """The defect this scenario exists for.

    Every wrong answer this state produced pointed back at sign-in. Measured
    2026-09-15 on a real unentitled account: `gflow auth login` exited 8 with "the
    Flow app sign-in wasn't completed. Re-run ... until the Flow editor loads", and
    `gflow image t2i` without a project exited 3 with "Authentication expired -> Run
    `gflow auth login`". Both instruct a retry that can never terminate, because the
    editor does not exist for this account at any price short of a subscription. On
    the route this scenario covers the old answer was different and worse (exit 23,
    "file a frontend bug"), so the assertion is the union: nothing here may send the
    user back to a login.
    """
    text = str(world["error"]).casefold()
    hint = (getattr(world["error"], "remediation_hint", "") or "").casefold()
    for claim in ("auth login", "sign in again", "expired", "re-run"):
        assert claim not in text, f"sends the user back into the login loop ({claim!r}): {text}"
        assert claim not in hint, f"sends the user back into the login loop ({claim!r}): {hint}"


@then("the failure is terminal, not retryable")
def _terminal(world: dict[str, Any]) -> None:
    """A missing subscription is a state, not a blip.

    Decided by evidence rather than by class default, per the Bug Lane's "a flag is
    a claim": the screen renders on every visit to that account, and no retry can
    grant access that was never purchased.
    """
    error = world["error"]
    assert isinstance(error, FlowAccessUnavailableError), f"got {error!r}"
    assert is_retryable(error) is False


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
