"""Tests for bootstrap account chooser auto-selection in FlowApiClient."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    page.wait_for_url = AsyncMock(return_value="https://flow.google.com/project/p1")

    res = await client._handle_account_chooser(page, "user@example.com")
    assert res is True
    # The exact row selector is used, and it is clicked
    assert page.locator.call_args[0][0] == '[data-email="user@example.com"]'
    row.first.click.assert_awaited_once()
    page.wait_for_url.assert_awaited_once_with("**/project/**", timeout=30_000)


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
        await client._handle_account_chooser(page, "recorded@example.com")

    assert "recorded@example.com" in str(exc_info.value)
    assert EXIT_CODE_MAP[FlowAccountChooserError] == 38
    page.get_by_text.assert_called_once_with("recorded@example.com", exact=True)


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
        await client._handle_account_chooser(page, "an@corp.com")
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
    page.wait_for_url = AsyncMock(
        return_value="https://accounts.google.com/v3/signin/accountchooser"
    )

    with pytest.raises(FlowAccountChooserError) as exc_info:
        await client._handle_account_chooser(page, "user@example.com")
    assert "did not reach the Flow editor" in str(exc_info.value)


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

    res = await client._handle_account_chooser(page, "user@example.com")
    assert res is False
    page.locator.assert_not_called()
