"""Tests for bootstrap account chooser auto-selection in FlowApiClient."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from gflow_cli.errors import FlowAccountChooserError


def _chooser_page(url: str, row_count: int) -> tuple[MagicMock, AsyncMock]:
    """Build a chooser page whose data-email row has the given count.

    The exact-row locator is the first ``page.locator`` call; the exact-text
    fallback uses ``page.get_by_text``. ``wait_for_url`` defaults to an
    un-awaited MagicMock — tests that reach it must override it.
    """
    page = MagicMock()
    page.url = url
    row = AsyncMock()
    row.count = AsyncMock(return_value=row_count)
    row.first = AsyncMock()
    page.locator.return_value = row
    return page, row


@pytest.mark.asyncio
async def test_bootstrap_detects_chooser_and_autoselects_account(tmp_path: Path) -> None:
    """When bootstrap hits account chooser, it clicks the row matching .gflow_account."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page(
        "https://accounts.google.com/v3/signin/accountchooser?continue=flow.google.com",
        row_count=1,
    )
    # `Page.wait_for_url` is annotated `-> None`: it returns nothing and signals a miss
    # by raising. A mock that returns a URL string encodes a contract Playwright does
    # not have, and it hid an inverted check that made every successful click raise.
    page.wait_for_url = AsyncMock(return_value=None)

    res = await client._handle_account_chooser(page)
    assert res is True
    # The exact row selector is used, and it is clicked. The trailing `i` is the CSS
    # case-insensitivity flag: `--account` compares with `.lower()`, so the row match
    # must too, or a case variant passes the assert and then misses its row.
    assert page.locator.call_args[0][0] == '[data-email="user@example.com" i]'
    row.first.click.assert_awaited_once()
    page.wait_for_url.assert_awaited_once()
    # The landing predicate accepts BOTH Flow cohorts and rejects the chooser itself,
    # so it cannot be satisfied by simply still being on accounts.google.com.
    predicate = page.wait_for_url.call_args[0][0]
    assert predicate("https://labs.google/fx/tools/flow?hl=en") is True
    assert predicate("https://flow.google.com/project/p1") is True
    assert predicate("https://accounts.google.com/v3/signin/accountchooser") is False
    assert page.wait_for_url.call_args[1]["timeout"] == 30_000


@pytest.mark.asyncio
async def test_bootstrap_chooser_absent_account_raises_flow_account_chooser_error(
    tmp_path: Path,
) -> None:
    """When recorded account is not found on chooser, FlowAccountChooserError is raised."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.errors import EXIT_CODE_MAP
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("recorded@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page(
        "https://accounts.google.com/v3/signin/accountchooser?continue=flow.google.com",
        row_count=0,
    )
    # The exact-text fallback also matches nothing.
    page.get_by_text = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))

    with pytest.raises(FlowAccountChooserError) as exc_info:
        await client._handle_account_chooser(page)

    assert "recorded@example.com" in str(exc_info.value)
    assert EXIT_CODE_MAP[FlowAccountChooserError] == 38
    # The fallback is an ANCHORED case-insensitive pattern, not `exact=True`. Assert the
    # anchoring behaviourally rather than by repr: an unanchored relaxation would match
    # the chooser's "Sign out of <email>" row, and clicking that signs the operator out
    # instead of in — the precise hazard `exact=True` was there to prevent.
    page.get_by_text.assert_called_once()
    pattern = page.get_by_text.call_args[0][0]
    assert pattern.search("RECORDED@example.com"), "must match a case variant"
    assert not pattern.search("Sign out of recorded@example.com"), "must refuse a superset"
    assert not pattern.search("Remove recorded@example.com"), "must refuse a superset"


@pytest.mark.asyncio
async def test_bootstrap_chooser_exact_match_never_clicks_superset_account(
    tmp_path: Path,
) -> None:
    """A superset address on the chooser must not be clicked (billing safety).

    Regression for the substring-match defect: with ``an@corp.com`` recorded and
    only ``ryan@corp.com`` present, neither the exact data-email row nor the
    exact-text fallback may match — the handler must raise, never click.
    """
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("an@corp.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page(
        "https://accounts.google.com/v3/signin/accountchooser",
        row_count=0,
    )
    page.get_by_text = MagicMock(return_value=MagicMock(count=AsyncMock(return_value=0)))

    with pytest.raises(FlowAccountChooserError):
        await client._handle_account_chooser(page)
    row.first.click.assert_not_awaited()
    page.wait_for_url.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_chooser_click_no_editor_raises_flow_account_chooser_error(
    tmp_path: Path,
) -> None:
    """Click-through that never reaches the editor raises FlowAccountChooserError."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page(
        "https://accounts.google.com/v3/signin/accountchooser",
        row_count=1,
    )
    # A landing that never happens is a Playwright TimeoutError out of wait_for_url —
    # the real failure signal, not a returned URL.
    page.wait_for_url = AsyncMock(side_effect=PlaywrightTimeoutError("timed out"))

    with pytest.raises(FlowAccountChooserError) as exc_info:
        await client._handle_account_chooser(page)
    assert "did not reach Flow" in str(exc_info.value)
    # The Playwright timeout is chained, not swallowed, so the bundle keeps the cause.
    assert isinstance(exc_info.value.__cause__, PlaywrightTimeoutError)


@pytest.mark.asyncio
async def test_bootstrap_rejected_browser_hop_is_not_a_chooser(tmp_path: Path) -> None:
    """The bot-rejection hop must surface as its own error, never a missing account."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page("https://accounts.google.com/v3/signin/rejected", row_count=0)

    res = await client._handle_account_chooser(page)
    assert res is False
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_chooser_non_string_url_is_not_a_chooser(
    tmp_path: Path,
) -> None:
    """A mocked page whose url is not a string must not raise (probe totality)."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page = MagicMock()
    page.url = MagicMock(name="mock.url")

    assert await client._handle_account_chooser(page) is False
    page.locator.assert_not_called()


@pytest.mark.asyncio
async def test_bootstrap_chooser_landing_timeout_names_where_the_page_landed(
    tmp_path: Path,
) -> None:
    """A landing timeout must report the URL the click actually left us on.

    The sibling raise above interpolates the chooser URL; this branch shipped
    without it and fired live on 2026-09-09 saying only "did not reach Flow
    within 30s". `flow_host_kind` is a host-only match that accepts every known
    Flow landing (including `/about`), so a timeout means the session is still on
    a Google surface — and *which* surface is the whole diagnosis: a password
    challenge needs a human, a consent screen needs a click, and an unchanged
    chooser URL means our click never navigated at all. Without the URL those
    are one indistinguishable exit 38.
    """
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page, row = _chooser_page(
        "https://accounts.google.com/v3/signin/accountchooser",
        row_count=1,
    )
    interstitial = "https://accounts.google.com/signin/v2/challenge/pwd"

    async def _click_moves_to_interstitial(*_args: object, **_kwargs: object) -> None:
        page.url = interstitial

    row.first.click = AsyncMock(side_effect=_click_moves_to_interstitial)
    page.wait_for_url = AsyncMock(side_effect=PlaywrightTimeoutError("timed out"))

    with pytest.raises(FlowAccountChooserError) as exc_info:
        await client._handle_account_chooser(page)

    # The CURRENT url, not the chooser url captured on entry: reporting the entry
    # url would claim "still on the chooser" for a click that did navigate.
    assert interstitial in str(exc_info.value)
