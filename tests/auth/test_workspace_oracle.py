"""Flow itself can attest a workspace when the unrelated My Account probe times out."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.auth.verification import _verify_migrated_workspace, record_account


def page_mock(url="https://flow.google.com/", ready=True, status=200):
    page = MagicMock()
    page.url = url
    page.goto = AsyncMock(return_value=MagicMock(status=status))
    anchor = MagicMock()
    anchor.wait_for = AsyncMock(side_effect=None if ready else TimeoutError())
    anchor.is_visible = AsyncMock(return_value=ready)
    label = MagicMock()
    label.count = AsyncMock(return_value=1)
    label.get_attribute = AsyncMock(return_value="Google Account: Test (a@example.com)")
    page.locator.side_effect = lambda selector: MagicMock(
        first=label if "SignOutOptions" in selector else anchor
    )
    return page


@pytest.mark.asyncio
async def test_workspace_survives_myaccount_network_failure():
    page = page_mock()
    ctx = MagicMock(new_page=AsyncMock(return_value=page))
    status = await _verify_migrated_workspace(ctx, "test")
    assert status is not None and status.authenticated
    assert status.user_email == "a@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url,ready,status",
    [
        ("https://flow.google.com/about", True, 200),
        ("https://accounts.google.com/challenge/pwd", True, 200),
        ("https://flow.google.com.evil.test/", True, 200),
        ("https://flow.google.com/", False, 200),
        ("https://flow.google.com/", True, 503),
    ],
)
async def test_html_or_redirect_or_missing_workspace_is_not_authenticated(url, ready, status):
    page = page_mock(url, ready, status)
    ctx = MagicMock(new_page=AsyncMock(return_value=page))
    assert await _verify_migrated_workspace(ctx, "test") is None


@pytest.mark.asyncio
async def test_a_workspace_without_a_readable_address_is_authenticated_with_no_email():
    page = page_mock()
    label = page.locator("a[href*=SignOutOptions]").first
    label.get_attribute = AsyncMock(return_value="Google Account")
    ctx = MagicMock(new_page=AsyncMock(return_value=page))
    status = await _verify_migrated_workspace(ctx, "test")
    assert status is not None and status.authenticated
    assert status.user_email is None


def test_record_account_writes_the_address(tmp_path):
    record_account(tmp_path, "a@example.com")
    assert (tmp_path / ".gflow_account").read_text(encoding="utf-8") == "a@example.com"


def test_no_address_clears_a_stale_account_rather_than_keeping_the_old_one(tmp_path):
    (tmp_path / ".gflow_account").write_text("old@example.com", encoding="utf-8")
    record_account(tmp_path, None)
    assert not (tmp_path / ".gflow_account").exists()
    record_account(tmp_path, None)  # nothing to clear is not an error
