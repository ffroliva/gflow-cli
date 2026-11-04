"""Flow itself can attest a workspace when the unrelated My Account probe times out."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.auth.verification import _verify_migrated_workspace


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
