"""E2E: a project create on a slow link is never reported as a retryable failure (#1037).

The #1037 spike measured a 40 s ``jHPbke`` round trip: gflow gave up at 30 s with a
retryable exit 9 while Flow made the project, so a retry made a second one. Here only
the create reply is held back -- the request reaches Flow at once, its answer is delivered
late or never -- and Flow's own projects page is the witness for how many projects exist::

    GFLOW_CLI_E2E_PROFILE=<profile> GFLOW_CLI_E2E_RUN_SLOW_LINK=1 \\
        uv run pytest -m e2e tests/e2e/test_project_create_slow_link_e2e.py -v

Cost: $0 in credits; each test creates one empty project (~1-2 min per test). Opt-in
because there is no delete route: titled projects are ``e2e-1037-*``, and the
outcome-unknown test leaves one untitled (timestamp-named) project. Remove them in Flow.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
import structlog

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports import migrated_composer as mc
from gflow_cli.config import reset_settings
from gflow_cli.errors import RpcResponseUnavailableError, UiSelectorDriftError, is_retryable
from gflow_cli.json_output import exit_code_for

pytestmark = [pytest.mark.e2e, pytest.mark.e2e_auth]

_RUN_ENV = "GFLOW_CLI_E2E_RUN_SLOW_LINK"

_IDS_JS = """
() => [...new Set([...document.querySelectorAll('a[href*="/project/"]')]
  .map(a => (a.getAttribute('href').match(/project\\/([0-9a-f-]{36})/) || [])[1])
  .filter(Boolean))]
"""


@pytest.fixture(autouse=True)
def _opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.environ.get(_RUN_ENV, "0").strip() != "1":
        pytest.skip(f"{_RUN_ENV} != 1 -- each run leaves an empty project in Flow")
    monkeypatch.delenv("GFLOW_CLI_FLOW_HOST", raising=False)
    reset_settings()


# A transparent layer above the whole page: the FAB stays visible, the click cannot land.
_COVER_JS = """
setInterval(() => {
  if (!document.body || document.getElementById('e2e-cover')) return;
  const d = document.createElement('div');
  d.id = 'e2e-cover';
  d.style.cssText = 'position:fixed;inset:0;z-index:2147483647;background:transparent';
  document.body.appendChild(d);
}, 100);
"""


def _title() -> str:
    return f"e2e-1037-{uuid.uuid4().hex[:6]}"


async def _project_ids(page: Any) -> set[str]:
    await page.goto(mc.MIGRATED_ROOT_URL, wait_until="domcontentloaded", timeout=90_000)
    await page.locator(mc.NEW_PROJECT_BUTTON).first.wait_for(state="visible", timeout=60_000)
    await page.wait_for_timeout(4000)  # the grid fills after the FAB renders
    return set(await page.evaluate(_IDS_JS))


async def _hold_create_reply(client: FlowApiClient, *, delay_s: float, deliver: bool) -> None:
    """Send the create to Flow at once; hand its reply to the page late, or never."""

    async def hold(route: Any, request: Any) -> None:
        if mc.url_rpcid(str(request.url)) != mc.CREATE_PROJECT_RPC:
            await route.continue_()
            return
        response = await route.fetch()  # Flow has the request now: the project is made
        await asyncio.sleep(delay_s)
        if deliver:
            await route.fulfill(response=response)
        else:
            await route.abort()

    assert client._context is not None  # noqa: SLF001
    await client._context.route("**/batchexecute*", hold)  # noqa: SLF001


@pytest.fixture
async def client(e2e_profile_dir: Path) -> AsyncIterator[FlowApiClient]:
    async with FlowApiClient(profile_dir=e2e_profile_dir) as c:
        yield c


async def test_a_40s_create_reply_is_a_success_on_the_client_path(client: FlowApiClient) -> None:
    """What the reporter hit: a 40 s create. The client path every CLI/MCP command takes
    now waits long enough to read the reply."""
    page = await client._checkout_page()  # noqa: SLF001
    try:
        before = await _project_ids(page)
    finally:
        client._checkin_page(page)  # noqa: SLF001
    await _hold_create_reply(client, delay_s=40.0, deliver=True)
    title = _title()

    info = await client.create_project(title=title)

    assert mc.UUID_RE.fullmatch(info.project_id), info
    assert info.title == title
    await client._context.unroute("**/batchexecute*")  # type: ignore[union-attr]  # noqa: SLF001
    page = await client._checkout_page()  # noqa: SLF001
    try:
        assert await _project_ids(page) - before == {info.project_id}
    finally:
        client._checkin_page(page)  # noqa: SLF001


async def test_a_reply_past_the_ceiling_is_recovered_from_the_navigation(
    client: FlowApiClient, install_log_capture: structlog.testing.LogCapture
) -> None:
    """The timeout arm: the reply lands after the ceiling, the page then navigates to the
    new project, and that navigation -- not a second click -- yields the id."""
    page = await client._checkout_page()  # noqa: SLF001
    try:
        before = await _project_ids(page)
        await _hold_create_reply(client, delay_s=12.0, deliver=True)
        title = _title()

        info = await mc.create_project(page, title, timeout_s=10.0)

        assert mc.UUID_RE.fullmatch(info.project_id), info
        assert info.title == title
        events = install_log_capture.entries
        recovered = [e for e in events if e["event"] == "migrated.project_create_recovered"]
        assert recovered and recovered[0]["after"] == "TransportTimeoutError", recovered
        await page.context.unroute("**/batchexecute*")
        assert await _project_ids(page) - before == {info.project_id}
    finally:
        client._checkin_page(page)  # noqa: SLF001


async def test_no_reply_and_no_navigation_is_outcome_unknown_not_retryable(
    client: FlowApiClient,
) -> None:
    """The page never learns the create happened. Flow made the project anyway, which is
    exactly why the answer must be exit 7 and not retryable."""
    page = await client._checkout_page()  # noqa: SLF001
    try:
        before = await _project_ids(page)
        await _hold_create_reply(client, delay_s=0.0, deliver=False)

        with pytest.raises(RpcResponseUnavailableError) as exc:
            await mc.create_project(page, _title(), timeout_s=10.0)

        assert exit_code_for(exc.value) == 7
        assert is_retryable(exc.value) is False
        assert "--project" in exc.value.remediation_hint
        await page.context.unroute("**/batchexecute*")
        assert len(await _project_ids(page) - before) == 1  # made, though gflow never saw it
    finally:
        client._checkin_page(page)  # noqa: SLF001


async def test_a_covered_new_project_button_is_drift_and_creates_nothing(
    client: FlowApiClient,
) -> None:
    """The first telemetry week's top crash: the FAB renders but something covers it, so
    the click times out. No create request left the page, so nothing was made: exit 23,
    charged nothing. A fresh page carries the cover and is closed, so no pooled page does."""
    assert client._context is not None  # noqa: SLF001
    page = await client._context.new_page()  # noqa: SLF001
    try:
        before = await _project_ids(page)
        await page.add_init_script(_COVER_JS)

        with pytest.raises(UiSelectorDriftError, match="new-project button") as exc:
            await mc.create_project(page, _title(), timeout_s=30.0)

        assert exit_code_for(exc.value) == 23
        assert exc.value.charged == "no"
        assert "it is covered by div" in str(exc.value)  # #1557: the occluder is named
    finally:
        await page.close()
    page = await client._checkout_page()  # noqa: SLF001
    try:
        assert await _project_ids(page) == before  # the witness: no project was made
    finally:
        client._checkin_page(page)  # noqa: SLF001


# #1557: Flow's bonus-credit promo rendered AFTER the page settled and its backdrop took the
# Create click. The promotion is time-limited, so this stands in for it: a backdrop over the
# whole page and a `flow-g1-promo-dialog` with a `button.close-button` that removes both,
# added after a delay on the projects page only.
_PROMO_JS = """
const tick = setInterval(() => {
  if (!document.body || location.pathname.includes('/project/')) return;
  if (!document.querySelector("flow-projects-page button:has(mat-icon)")) return;
  clearInterval(tick);
  const back = document.createElement('div');
  back.style.cssText = 'position:fixed;inset:0;z-index:2147483646;background:transparent';
  const dialog = document.createElement('flow-g1-promo-dialog');
  dialog.style.cssText = 'position:fixed;top:40%;left:40%;z-index:2147483647;display:block';
  const close = document.createElement('button');
  close.className = 'close-button';
  close.textContent = 'x';
  close.onclick = () => { back.remove(); dialog.remove(); };
  dialog.appendChild(close);
  document.body.append(back, dialog);
}, 50);
"""


async def test_a_late_promo_dialog_over_the_page_is_closed_and_the_project_is_created(
    client: FlowApiClient, install_log_capture: structlog.testing.LogCapture
) -> None:
    """A stand-in promo appears over the new-project button after the page settles. The
    locator handler closes it, and the create goes through (one empty project is made)."""
    assert client._context is not None  # noqa: SLF001
    page = await client._context.new_page()  # noqa: SLF001
    try:
        before = await _project_ids(page)
        await page.add_init_script(_PROMO_JS)
        title = _title()

        info = await mc.create_project(page, title, timeout_s=60.0)

        assert mc.UUID_RE.fullmatch(info.project_id), info
        assert info.title == title
        events = [e["event"] for e in install_log_capture.entries]
        assert "migrated.promo_dismissed" in events, events
        assert await _project_ids(page) - before == {info.project_id}
    finally:
        await page.close()
