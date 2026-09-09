"""E2E for the post-migration account-chooser autoselect path (#763).

**Why this is an e2e and not one more unit test.** The defect this file pins
shipped through a fully green unit suite: the suite mocked ``page.wait_for_url``
as returning a URL string, so ``(await page.wait_for_url(...) or "")`` looked
like it could be truthy. Playwright's ``wait_for_url`` is annotated ``-> None``
and signals a miss by *raising*, so the mock encoded a contract the real API does
not have, and the inverted check beneath it — which made every *successful*
chooser click raise — was invisible to every assertion. A mock cannot falsify a
belief about the mocked thing. Only a real ``Page`` can.

So these tests drive a **real Playwright page** through the real locator engine,
a real click, a real navigation and the real ``wait_for_url``.

**Cost: zero.** Both the chooser and the Flow landing are served by Playwright
route interception, so no request reaches Google, no Flow credit is spent, and
no authenticated profile is required — see
``docs/superpowers/memory/credit-free-route-abort-verification.md``. That also
makes the test deterministic: the real Google chooser cannot be staged on demand.

The DOM here is a stand-in for Google's markup, so this proves the *mechanism*
(guard → locate → click → land), not that ``[data-email]`` is still the live
chooser's anchor. Selector drift on the real page is a separate question, and
``/gflow:live-verify`` on a signed-out account is what answers it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from playwright.async_api import Route, async_playwright

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import FlowAccountChooserError
from gflow_cli.profile_store import ACCOUNT_FILE

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

CHOOSER_URL = "https://accounts.google.com/v3/signin/accountchooser?continue=flow"
LABS_LANDING = "https://labs.google/fx/tools/flow?hl=en"
MIGRATED_LANDING = "https://flow.google.com/project/e2e-project"
ACCOUNT = "e2e-chooser@example.com"
OTHER_ACCOUNT = "someone-else@example.com"


def _chooser_html(target_href: str) -> str:
    """A chooser carrying two rows, the recorded account second.

    The decoy is first in DOM order on purpose: ``.first`` must resolve within
    the *matched* set, so a selector that over-matches would click the wrong
    account — and on a real chooser that signs in, and bills, the wrong person.
    """
    return f"""<!doctype html>
<html><body>
  <ul>
    <li><a data-email="{OTHER_ACCOUNT}" href="{LABS_LANDING}">{OTHER_ACCOUNT}</a></li>
    <li><a data-email="{ACCOUNT}" href="{target_href}">{ACCOUNT}</a></li>
  </ul>
</body></html>"""


async def _serve(page: object, chooser_html: str) -> None:
    """Route the chooser and both Flow cohorts to local HTML — nothing leaves the box."""

    async def _chooser(route: Route) -> None:
        await route.fulfill(status=200, content_type="text/html", body=chooser_html)

    async def _flow(route: Route) -> None:
        await route.fulfill(
            status=200,
            content_type="text/html",
            body="<!doctype html><html><body>flow</body></html>",
        )

    await page.route("https://accounts.google.com/**", _chooser)  # type: ignore[attr-defined]
    await page.route("https://labs.google/**", _flow)  # type: ignore[attr-defined]
    await page.route("https://flow.google.com/**", _flow)  # type: ignore[attr-defined]


@pytest.mark.parametrize("landing", [LABS_LANDING, MIGRATED_LANDING], ids=["labs", "migrated"])
async def test_e2e_chooser_autoselect_lands_on_either_cohort(tmp_path: Path, landing: str) -> None:
    """A real click on the recorded row returns True and leaves the chooser.

    Parameterised over both cohorts because the landing predicate is the part
    this fix changed: a ``**/project/**`` glob describes only the migrated
    origin, while the labs bootstrap URL has no ``/project/`` segment at all.
    Whichever host an account resolves to, leaving the chooser must count.

    ``account_email`` is deliberately NOT passed, so this drives the branch
    production actually uses — the ``.gflow_account`` read — which every unit
    test bypasses by passing the address in.
    """
    profile = tmp_path / "profile_e2e"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text(f"{ACCOUNT}\n", encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, _chooser_html(landing))
            await page.goto(CHOOSER_URL, wait_until="domcontentloaded")

            client = FlowApiClient(profile_dir=profile)
            assert await client._handle_account_chooser(page) is True

            # Landed on a Flow host, and specifically the one the row pointed at.
            assert page.url.startswith(landing.split("?")[0]), (
                f"expected to land on {landing}, still at {page.url}"
            )
            assert "accounts.google.com" not in page.url
        finally:
            await browser.close()


async def test_e2e_chooser_click_that_never_lands_raises_exit_38(tmp_path: Path) -> None:
    """A click that does not leave the chooser raises FlowAccountChooserError.

    The row's href is a same-page anchor, so the click is real and lands
    nowhere. That makes ``wait_for_url`` raise a genuine Playwright
    ``TimeoutError`` — the failure signal the code must catch and translate.
    Before the fix this branch was unreachable for the opposite reason: the
    check was inverted, so it fired on success and the real timeout escaped
    uncaught as a generic exit 1, which is the #763 symptom itself.

    Costs the handler's full 30 s wait; that is the price of proving the real
    timeout rather than a mocked one.
    """
    profile = tmp_path / "profile_e2e"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text(f"{ACCOUNT}\n", encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, _chooser_html("#stay-put"))
            await page.goto(CHOOSER_URL, wait_until="domcontentloaded")

            client = FlowApiClient(profile_dir=profile)
            with pytest.raises(FlowAccountChooserError) as exc_info:
                await client._handle_account_chooser(page)

            assert ACCOUNT in str(exc_info.value)
            assert "did not reach Flow" in str(exc_info.value)
        finally:
            await browser.close()


async def test_e2e_chooser_absent_row_raises_before_any_click(tmp_path: Path) -> None:
    """A recorded account with no row raises without clicking anything.

    The wrong-account hazard is the reason the selector is an exact
    ``[data-email=]`` match: this chooser offers a different address, and a
    substring or text-engine fallback that matched it would sign in — and bill —
    the wrong person. Nothing here may be clickable.
    """
    profile = tmp_path / "profile_e2e"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("not-on-this-chooser@example.com\n", encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, _chooser_html(LABS_LANDING))
            await page.goto(CHOOSER_URL, wait_until="domcontentloaded")

            client = FlowApiClient(profile_dir=profile)
            with pytest.raises(FlowAccountChooserError) as exc_info:
                await client._handle_account_chooser(page)

            assert "not-on-this-chooser@example.com" in str(exc_info.value)
            # Still on the chooser: no row was clicked, so no wrong account was picked.
            assert "accounts.google.com" in page.url
        finally:
            await browser.close()


async def test_e2e_chooser_matches_recorded_account_case_insensitively(tmp_path: Path) -> None:
    """A case variant of the recorded address still selects the row.

    `gflow auth login --account` compares case-insensitively (`cli.py`:
    ``actual_account.lower() != account.strip().lower()``), but the row match is
    a CSS attribute selector and an exact-text fallback, both case-SENSITIVE, and
    ``read_account_file`` normalises nothing. So an address recorded in one case
    and rendered by Google in another passes the `--account` assertion and then
    misses the row — surfacing as "recorded account was not found among
    selectable accounts", exit 38, telling the operator to re-login while the
    account sits right there on the chooser. That is the exact false negative
    this feature exists to remove.

    Only a real locator engine can settle this: CSS attribute matching is
    case-sensitive by default and case-insensitive only with the `i` flag, which
    no mock can model. Zero credits — route interception, as above.
    """
    profile = tmp_path / "profile_e2e"
    profile.mkdir()
    # Chooser renders ACCOUNT lowercase; the profile records a case variant.
    recorded = "E2E-Chooser@Example.com"
    assert recorded.lower() == ACCOUNT, "variant must differ only by case"
    (profile / ACCOUNT_FILE).write_text(f"{recorded}\n", encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, _chooser_html(MIGRATED_LANDING))
            await page.goto(CHOOSER_URL, wait_until="domcontentloaded")

            client = FlowApiClient(profile_dir=profile)
            assert await client._handle_account_chooser(page) is True
            assert "accounts.google.com" not in page.url
        finally:
            await browser.close()


async def test_e2e_chooser_case_insensitive_match_still_refuses_a_superset_row(
    tmp_path: Path,
) -> None:
    """Relaxing case must not relax the anti-substring discipline.

    The chooser's loose surfaces ("Remove <email>", "Sign out of <email>") are
    why the match is exact. A case-insensitive match implemented with an
    unanchored regex would start selecting those, and clicking "Sign out of" on
    a real chooser signs the operator out instead of in. Here the ONLY row
    carrying the address is a superset string, so a correct implementation finds
    no exact row and raises rather than clicking it.
    """
    profile = tmp_path / "profile_e2e"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text(f"{ACCOUNT}\n", encoding="utf-8")

    superset_only = f"""<!doctype html>
<html><body>
  <ul><li><a href="{MIGRATED_LANDING}">Sign out of {ACCOUNT.upper()}</a></li></ul>
</body></html>"""

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await (await browser.new_context()).new_page()
            await _serve(page, superset_only)
            await page.goto(CHOOSER_URL, wait_until="domcontentloaded")

            client = FlowApiClient(profile_dir=profile)
            with pytest.raises(FlowAccountChooserError) as exc_info:
                await client._handle_account_chooser(page)
            assert "not found among selectable accounts" in str(exc_info.value)
        finally:
            await browser.close()
