"""Tests for bootstrap account chooser auto-selection in FlowApiClient."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.errors import FlowAccountChooserError


@pytest.mark.asyncio
async def test_bootstrap_detects_chooser_and_autoselects_account(tmp_path: Path) -> None:
    """When bootstrap hits account chooser, it clicks the row matching .gflow_account."""
    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.profile_store import ACCOUNT_FILE

    profile = tmp_path / "profile_p1"
    profile.mkdir()
    (profile / ACCOUNT_FILE).write_text("user@example.com\n", encoding="utf-8")

    client = FlowApiClient(profile_dir=profile)
    page = MagicMock()
    page.url = "https://accounts.google.com/v3/signin/accountchooser"

    # Mock locator for the email row
    account_row = AsyncMock()
    account_row.count.return_value = 1
    page.locator.return_value = account_row

    # Helper method on client or transport
    res = await client._handle_account_chooser(page, "user@example.com")
    assert res is True
    account_row.first.click.assert_awaited_once()


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
    page = MagicMock()
    page.url = "https://accounts.google.com/v3/signin/accountchooser"

    account_row = AsyncMock()
    account_row.count.return_value = 0
    page.locator.return_value = account_row

    with pytest.raises(FlowAccountChooserError) as exc_info:
        await client._handle_account_chooser(page, "recorded@example.com")

    assert "recorded@example.com" in str(exc_info.value)
    assert EXIT_CODE_MAP[FlowAccountChooserError] == 33
