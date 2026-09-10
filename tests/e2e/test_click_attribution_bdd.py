"""E2E for attributable click timeouts on the migrated composer (#776).

Binds ``tests/features/click_attribution.feature``. The Gherkin's ``@e2e`` tags become
pytest markers via pytest-bdd, so ``-m e2e`` / ``-m e2e_auth`` select this file exactly
like a hand-written e2e — see ``docs/E2E_TESTING.md`` § BDD-bound e2e.

**Why an e2e and not a unit test.** What fails in #776 is Playwright's *actionability*
gate — attached → visible → stable → receives-events → enabled. A mocked ``Page`` whose
``.click()`` is a stub cannot express "visible, enabled, and still not clickable"; it
would pass against a fix that reads the DOM at the wrong moment, which is the trap #639
set one surface over. Only a real browser can falsify this.

Each scenario therefore breaks a **different** actionability condition, in the browser,
for real:

- covered by a stacked element   → *receives-events*
- an infinite CSS transform      → *stable* (visible, enabled and hit-testable throughout)
- a pressed agent-mode chip      → #752 finding #7's predicted cause, which touches
  neither ``body{pointer-events}`` nor the hit-test

**Cost: zero.** Every Flow origin is served by Playwright route interception, so nothing
reaches Google, no credit is spent and no authenticated profile is needed — the same
harness as ``test_landing_state_diagnosis_bdd.py``.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from playwright.async_api import Route, async_playwright
from pytest_bdd import given, scenarios, then, when

from gflow_cli.api.transports.migrated_composer import MigratedComposer
from gflow_cli.errors import UiSelectorDriftError, is_retryable

scenarios("../features/click_attribution.feature")

PROJECT_ID = "e2e-click-attribution"
PROJECT_URL = f"https://flow.google.com/project/{PROJECT_ID}"
ELSEWHERE_URL = "https://flow.google.com/project/somewhere-else"

#: Planted on the covering element in the redaction scenario. Neither may ever appear in
#: a message that reaches a console, a log or a GitHub issue.
ACCOUNT_EMAIL = "e2e-victim@example.com"
SIGNED_URL = "https://lh3.googleusercontent.com/x?X-Goog-Signature=deadbeefcafe"

_STYLE = """
  body { margin: 0; font-family: sans-serif; }
  .settings-trigger-button { position: absolute; top: 100px; left: 100px;
                             width: 147px; height: 32px; }
  #cover { position: absolute; top: 0; left: 0; width: 100vw; height: 100vh;
           z-index: 9999; background: rgba(0,0,0,.01); }
  /* Never stable, never still: Playwright's stability check can never pass, while
     visibility, enabled-ness and the hit-test all read perfectly healthy. */
  @keyframes drift { from { transform: translateX(0); } to { transform: translateX(60px); } }
  .jitter { animation: drift .18s linear infinite alternate; }
"""

#: Clicking the trigger mounts the overlay `_open_pane` waits for, so the control
#: scenario exercises the whole happy path rather than just "no exception".
_OPENS_PANE_JS = """
  document.querySelector('.settings-trigger-button').addEventListener('click', () => {
    const pane = document.createElement('div');
    pane.className = 'cdk-overlay-pane';
    pane.innerHTML = "<div role='radiogroup'><div role='radio'>16:9</div></div>";
    document.body.appendChild(pane);
  });
"""


def _page(
    *,
    cover: str = "",
    chip: bool = False,
    jitter: bool = False,
    script: str = "",
) -> str:
    """A migrated-host editor stub: a real trigger, plus whatever is wrong with it."""
    chip_html = "<button class='agent-mode-chip' aria-pressed='true'>agent</button>" if chip else ""
    cls = "settings-trigger-button jitter" if jitter else "settings-trigger-button"
    return (
        f"<!doctype html><html><head><style>{_STYLE}</style></head><body>"
        f"{chip_html}"
        f"<button class='{cls}' aria-label='Settings trigger'>settings</button>"
        f"{cover}"
        f"<script>{script or _OPENS_PANE_JS}</script>"
        "</body></html>"
    )


#: A plain stacked overlay — the shape Angular CDK actually uses on this host (the
#: 2026-09-10 spike measured `body{pointer-events}` staying `auto` in 159/159 samples,
#: including while Flow's own pane was open, so the hit-test is the load-bearing
#: detector here and the body property is the labs mechanism).
_CDK_COVER = "<div id='cover' class='cdk-overlay-backdrop cdk-overlay-backdrop-showing'></div>"

#: The same cover, carrying exactly what must never be echoed back.
_LEAKY_COVER = (
    f"<div id='cover' class='cdk-overlay-backdrop' aria-label='{ACCOUNT_EMAIL}' "
    f"title='{ACCOUNT_EMAIL}'><img src='{SIGNED_URL}' alt='{ACCOUNT_EMAIL}'></div>"
)


@pytest.fixture
def world() -> dict[str, Any]:
    return {}


async def _drive(html: str, run: Any) -> BaseException | None:
    """Launch a real browser, serve `html` for every Flow URL, run `run(page)`."""
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()

            async def _handler(route: Route) -> None:
                await route.fulfill(status=200, content_type="text/html", body=html)

            await page.route("https://flow.google.com/**", _handler)
            await page.goto(PROJECT_URL, wait_until="domcontentloaded")
            try:
                await run(page)
            except BaseException as exc:  # noqa: BLE001 - the failure IS the assertion
                return exc
            return None
        finally:
            await browser.close()


def _open_pane(world: dict[str, Any]) -> None:
    world["error"] = asyncio.run(
        _drive(
            world["html"],
            lambda page: MigratedComposer()._open_pane(page),  # noqa: SLF001
        )
    )


def _text(world: dict[str, Any]) -> str:
    return str(world["error"])


# --------------------------------------------------------------------------- given


@given("a Flow project page whose settings trigger is covered")
def _covered(world: dict[str, Any]) -> None:
    world["html"] = _page(cover=_CDK_COVER)


@given("a Flow project page whose settings trigger accepts no click")
def _never_stable(world: dict[str, Any]) -> None:
    # Nothing covers it and nothing disables it — it simply never holds still.
    world["html"] = _page(jitter=True)


@given("a Flow project page whose settings trigger is clickable")
def _clickable(world: dict[str, Any]) -> None:
    world["html"] = _page()


@given("the agent-mode chip is pressed")
def _chip_pressed(world: dict[str, Any]) -> None:
    world["html"] = _page(cover=_CDK_COVER, chip=True)


@given("the covering element carries an account email and a signed media URL")
def _leaky(world: dict[str, Any]) -> None:
    world["html"] = _page(cover=_LEAKY_COVER)


@given("the page navigates away while the click is pending")
def _navigates_away(world: dict[str, Any]) -> None:
    # #722's shape: by the time the failure is diagnosed the document the click was
    # made against is gone, so the post-mortem read has nothing to answer with.
    world["html"] = _page(
        cover=_CDK_COVER,
        script=f"setTimeout(() => location.replace({ELSEWHERE_URL!r}), 400);",
    )


# ---------------------------------------------------------------------------- when


@when("the driver opens the settings pane")
def _drive_open_pane(world: dict[str, Any]) -> None:
    _open_pane(world)


# ---------------------------------------------------------------------------- then


@then("it fails with exit 23")
def _exit_23(world: dict[str, Any]) -> None:
    from gflow_cli.errors import EXIT_CODE_MAP

    error = world["error"]
    assert isinstance(error, UiSelectorDriftError), (
        f"expected UiSelectorDriftError, got {error!r} — a bare Playwright TimeoutError "
        "is exactly the #776 defect: exit 1, no locator, no cause"
    )
    assert EXIT_CODE_MAP[UiSelectorDriftError] == 23
    # A flag is a claim. Today's failure is non-retryable; the condition does not
    # reproduce, so this is PRESERVED, not measured (Bug Lane, "A flag is a claim").
    assert is_retryable(error) is False, "retryable moved as a side effect of retyping"


@then("the message names the settings trigger")
def _names_trigger(world: dict[str, Any]) -> None:
    assert ".settings-trigger-button" in _text(world), _text(world)


@then("the message names Flow's agent mode")
def _names_agent_mode(world: dict[str, Any]) -> None:
    assert "agent mode" in _text(world).lower(), _text(world)


@then("the message does not blame an overlay")
def _not_an_overlay(world: dict[str, Any]) -> None:
    # #752 finding #7: agent mode hides the trigger with a bare `hidden` and never
    # touches body{pointer-events}. Reporting it as an announcement modal sends the
    # user to dismiss something that was never there — #770's failure, repeated.
    text = _text(world).lower()
    for claim in ("announcement", "changelog", "dismiss the"):
        assert claim not in text, f"blames an overlay it did not observe ({claim!r}): {text}"


@then("the message names the covering element by tag and structural class")
def _names_occluder(world: dict[str, Any]) -> None:
    text = _text(world)
    assert "div" in text, text
    assert "cdk-overlay-backdrop" in text, text


@then("the message reports the control as visible, enabled and hit-testable")
def _reports_healthy(world: dict[str, Any]) -> None:
    text = _text(world).lower()
    for word in ("visible", "enabled", "hit-testable"):
        assert word in text, f"missing {word!r} in: {text}"


@then("the message does not name a cause it did not observe")
def _no_invented_cause(world: dict[str, Any]) -> None:
    text = _text(world).lower()
    for claim in ("agent mode", "covered by", "announcement", "changelog"):
        assert claim not in text, f"invented a cause ({claim!r}): {text}"


@then("the pane opens and nothing is raised")
def _control(world: dict[str, Any]) -> None:
    # The A/B control. Without it every assertion above would also pass against a
    # helper that raises unconditionally.
    assert world["error"] is None, f"a healthy click was rejected: {world['error']!r}"


@then("the message contains neither the account email nor the signed URL")
def _no_pii(world: dict[str, Any]) -> None:
    text = _text(world)
    assert ACCOUNT_EMAIL not in text, f"leaked the account email: {text}"
    assert "X-Goog-Signature" not in text, f"leaked a signed URL: {text}"
    assert "googleusercontent" not in text, f"leaked a media host: {text}"
