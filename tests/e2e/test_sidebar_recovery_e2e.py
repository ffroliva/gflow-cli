"""Live e2e — recover the classic composer from Flow's expanded chat sidebar (#493).

**Zero credits** (``e2e_auth``): drives the composer's mode controls and reads the
DOM. Never types a prompt, never clicks Generate.

## What this pins

Expanding Flow's chat sidebar removes the classic composer **entirely** — no
``crop_*`` settings trigger *and* no Agent pill. That single state produces both
symptoms reported in #493, and because no agentic indicator is on screen either,
the cohort detector matches nothing, so the run fails as ``UiSelectorDriftError``
(exit 23) rather than the retryable agentic error (exit 25).

Recovery hinged on ``SIDEBAR_CLOSE_SELECTOR``, scoped to the sidebar's
``edit_square`` ("new session") affordance. On a cohort whose sidebar lacks that
ligature the close button is never found, the sidebar never closes, and **every**
run fails. ``ensure_media_mode`` now falls back to an unscoped close, reached only
from that stuck state.

The second test is the important one: it neuters the scoped selector to
reproduce the reporter's cohort on OUR account, so the fallback is exercised for
real rather than assumed. Unit tests cover the same shapes against a fake page
(``tests/api/transports/test_mode_control.py``), but only a real editor proves
the sidebar actually removes both affordances.

Run:
    GFLOW_CLI_E2E_PROFILE=<profile> pytest -m e2e_auth \
        tests/e2e/test_sidebar_recovery_e2e.py
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gflow_cli.api.transports import mode_control
from gflow_cli.api.transports.mode_control import AGENT_TOGGLE_SELECTOR, CROP_SELECTORS
from tests.e2e.conftest import skip_on_migrated_host

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_EXPAND_SELECTOR = "button:has(i.google-symbols:text-is('expand_content'))"


async def _crop_count(page: Any) -> int:
    return sum([await page.locator(sel).count() for sel in CROP_SELECTORS])


def _any_project_id(profile_dir: Path) -> str:
    """A real project id for the active profile, from the real catalog.

    Derived from ``profile_dir`` rather than ``get_settings()``: the root
    ``_isolate_settings`` fixture redirects ``GFLOW_CLI_DB_PATH`` to a temp path,
    and ``sqlite3.connect`` would silently CREATE an empty database there — which
    surfaces as "no such table: assets" rather than a clean skip.

    Skips rather than inventing an id: a bogus project would fail for a reason
    unrelated to what this test pins.
    """
    import os
    import sqlite3

    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE", "").strip()
    db = profile_dir.parent / "gflow.db"
    if not db.exists():
        pytest.skip(f"no catalog at {db} to resolve a project from")
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        row = con.execute(
            "SELECT flow_project_id FROM assets WHERE profile_name = ? "
            "AND flow_project_id IS NOT NULL LIMIT 1",
            (profile,),
        ).fetchone()
    except sqlite3.OperationalError as exc:  # unmigrated / unexpected schema
        pytest.skip(f"catalog unusable for project lookup: {exc}")
    finally:
        con.close()
    if not row:
        pytest.skip(f"no catalogued project for profile {profile!r} to open an editor in")
    return str(row[0])


async def _open_editor(client: Any, project_id: str) -> Any:
    from gflow_cli.api import routes

    ctx = client._context  # noqa: SLF001
    assert ctx is not None
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()
    await page.goto(
        # #587: the ACCOUNT's locale, never a hardcoded segment — Flow serves
        # whatever it is asked for, so "en" quietly tests the wrong thing on a
        # redirected account.
        routes.project_editor_url(client._account_locale, project_id),  # noqa: SLF001
        wait_until="domcontentloaded",
        timeout=60_000,
    )
    # #593: this test drives a raw page off the client, so it passes through none of
    # the transport's dismissal boundaries. An announcement modal here blocks every
    # control below with a bare TimeoutError — and this test is `e2e_auth`, the
    # nightly canary's default tier.
    await client.transport._dismiss_blocking_overlays(page)  # noqa: SLF001
    for _ in range(30):
        if await page.locator("button").count() > 8:
            break
        await page.wait_for_timeout(1000)
    # The composer mounts after `load`; absorb that before probing.
    await mode_control.ensure_media_mode(page, allow_reload=True)
    return page


async def _drive_into_sidebar_state(page: Any) -> bool:
    """Turn Agent mode on and expand the sidebar. Returns whether it took."""
    toggle = page.locator(AGENT_TOGGLE_SELECTOR).first
    if await toggle.count() == 0:
        return False
    if await toggle.get_attribute("aria-pressed") == "false":
        await toggle.click(timeout=4000)
        await page.wait_for_timeout(1500)
    expand = page.locator(_EXPAND_SELECTOR).first
    if await expand.count() == 0:
        return False
    await expand.click(timeout=4000)
    await page.wait_for_timeout(2000)
    return True


@pytest.mark.asyncio
@skip_on_migrated_host
async def test_expanded_sidebar_hides_both_affordances_and_is_recoverable(
    e2e_profile_dir: Path,
) -> None:
    """The #493 fingerprint is real, and production recovers from it."""
    from gflow_cli.api.client import FlowApiClient

    async with FlowApiClient(profile_dir=e2e_profile_dir, headless=False) as client:
        page = await _open_editor(client, _any_project_id(e2e_profile_dir))
        if not await _drive_into_sidebar_state(page):
            pytest.skip("composer did not expose the Agent toggle / expand affordance")

        # The #493 fingerprint: BOTH affordances gone at once.
        assert await _crop_count(page) == 0, "expected the classic panel to be gone"
        assert await page.locator(AGENT_TOGGLE_SELECTOR).count() == 0, (
            "expected the in-composer Agent pill to be gone"
        )

        await mode_control.ensure_media_mode(page, allow_reload=True)
        await page.wait_for_timeout(1500)
        assert await _crop_count(page) > 0, "ensure_media_mode failed to restore classic"


@pytest.mark.asyncio
@skip_on_migrated_host
async def test_recovers_when_the_scoped_close_selector_misses(
    e2e_profile_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduce the reporter's cohort on our account.

    Neutering the ``edit_square``-scoped selector simulates a sidebar that does
    not carry that ligature — the condition that made #493 unrecoverable. The
    unscoped fallback must still bring the composer back.
    """
    from gflow_cli.api.client import FlowApiClient

    async with FlowApiClient(profile_dir=e2e_profile_dir, headless=False) as client:
        page = await _open_editor(client, _any_project_id(e2e_profile_dir))
        if not await _drive_into_sidebar_state(page):
            pytest.skip("composer did not expose the Agent toggle / expand affordance")

        monkeypatch.setattr(mode_control, "SIDEBAR_CLOSE_SELECTOR", "button#gflow-never-matches")
        assert await page.locator(mode_control.SIDEBAR_CLOSE_SELECTOR).count() == 0

        await mode_control.ensure_media_mode(page, allow_reload=True)
        await page.wait_for_timeout(1500)
        assert await _crop_count(page) > 0, (
            "the #493 fallback did not recover the composer when the scoped close selector missed"
        )
