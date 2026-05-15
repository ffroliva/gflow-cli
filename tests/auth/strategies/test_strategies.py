from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.errors import SecurityError

# ---------------------------------------------------------------------------
# Shared mock factory
# ---------------------------------------------------------------------------


def _build_pw_mock(
    cookies: list[dict] | None = None,
    success_visible: bool = True,
) -> tuple[MagicMock, AsyncMock, MagicMock, MagicMock]:
    """Return (mock_async_playwright, mock_launch, mock_ctx, mock_page).

    Builds a fully wired Playwright async-context-manager mock that exits
    cleanly on the first poll iteration.  Playwright's API has a mix of sync
    methods (locator, get_by_text) and async methods (goto, is_visible, etc.)
    — each is set explicitly to avoid accidental TypeError on await.
    """
    if cookies is None:
        cookies = [{"name": "SAPISID", "value": "dummy"}]

    # Page: sync locator/get_by_text return MagicMocks whose async methods work.
    mock_btn = MagicMock(name="btn")
    mock_btn.is_visible = AsyncMock(return_value=False)  # skip auto-clicks
    mock_btn.get_attribute = AsyncMock(return_value="user@example.com")
    mock_btn.click = AsyncMock()

    mock_success_loc = MagicMock(name="success_loc")
    mock_success_loc.is_visible = AsyncMock(return_value=success_visible)

    mock_page = MagicMock(name="page")
    mock_page.goto = AsyncMock()
    mock_page.url = "https://labs.google/fx/tools/flow"
    mock_page.locator.return_value.first = mock_btn
    mock_page.get_by_text.return_value = mock_success_loc

    # Context: explicit AsyncMocks for all awaited methods.
    mock_ctx = MagicMock(name="ctx")
    mock_ctx.pages = [mock_page]
    mock_ctx.add_init_script = AsyncMock()
    mock_ctx.cookies = AsyncMock(return_value=cookies)
    mock_ctx.close = AsyncMock()
    mock_ctx.new_page = AsyncMock(return_value=mock_page)

    # pw object (what `async with async_playwright() as pw` binds).
    mock_pw_obj = MagicMock(name="pw")
    mock_launch = AsyncMock(return_value=mock_ctx)
    mock_pw_obj.chromium.launch_persistent_context = mock_launch

    # The async context manager returned by async_playwright().
    mock_cm = MagicMock(name="cm")
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    # The callable itself: async_playwright() → mock_cm.
    mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)

    return mock_ap, mock_launch, mock_ctx, mock_page


# ---------------------------------------------------------------------------
# RealChromeStrategy
# ---------------------------------------------------------------------------


class TestRealChromeStrategy:
    @pytest.mark.asyncio
    async def test_real_chrome_launch_flags(self, tmp_path: Path) -> None:
        """T1.4: Verify Real Chrome launches with correct stealth flags and channel."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, mock_launch, _, _ = _build_pw_mock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        _, kwargs = mock_launch.call_args
        assert kwargs["channel"] == "chrome"
        assert "--enable-automation" in kwargs.get("ignore_default_args", [])
        assert "--no-sandbox" in kwargs.get("ignore_default_args", [])
        # --disable-blink-features=AutomationControlled is REQUIRED: without it,
        # Blink sets navigator.webdriver as non-configurable before our JS runs.
        assert "--disable-blink-features=AutomationControlled" in kwargs.get("args", [])
        assert kwargs["user_data_dir"] == str(profile_dir)

    @pytest.mark.asyncio
    async def test_stealth_init_script_registered_before_page(self, tmp_path: Path) -> None:
        """Verify add_init_script is called before any page navigation (timing fix)."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, mock_launch, mock_ctx, mock_page = _build_pw_mock()
        call_order: list[str] = []

        original_add_init = mock_ctx.add_init_script
        original_goto = mock_page.goto

        async def track_add_init(*a: object, **kw: object) -> None:
            call_order.append("add_init_script")
            await original_add_init(*a, **kw)

        async def track_goto(*a: object, **kw: object) -> None:
            call_order.append("goto")
            await original_goto(*a, **kw)

        mock_ctx.add_init_script = track_add_init
        mock_page.goto = track_goto

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        assert call_order.index("add_init_script") < call_order.index("goto"), (
            "add_init_script must fire before goto() to cover the first navigation"
        )

    @pytest.mark.asyncio
    async def test_real_chrome_login_success_polling(self, tmp_path: Path) -> None:
        """T1.4: Verify it polls for SAPISID cookie and UI signal."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        # First call: no cookies. Second: has SAPISID → triggers success check.
        mock_ap, _, mock_ctx, mock_page = _build_pw_mock(cookies=[])
        mock_ctx.cookies = AsyncMock(
            side_effect=[
                [],
                [{"name": "SAPISID", "value": "found"}],
            ]
        )

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        assert mock_ctx.cookies.call_count == 2
        mock_page.get_by_text.assert_any_call("New project")

    @pytest.mark.asyncio
    async def test_real_chrome_privacy_guard(self, tmp_path: Path) -> None:
        """T1.5: Verify SecurityError when profile_dir is outside GFLOW_CLI_HOME."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        outside_dir = tmp_path / "system_chrome_profile"
        outside_dir.mkdir()

        with patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings:
            mock_settings.return_value.home = gflow_home
            with pytest.raises(SecurityError) as excinfo:
                await strategy.login(outside_dir, headless=False)

        assert "outside of GFLOW_CLI_HOME" in str(excinfo.value)


# ---------------------------------------------------------------------------
# InternalChromiumStrategy
# ---------------------------------------------------------------------------


class TestInternalChromiumStrategy:
    @pytest.mark.asyncio
    async def test_internal_chromium_standard_behavior(self, tmp_path: Path) -> None:
        """T1.4: Verify Internal Chromium uses standard Playwright (no stealth flags)."""
        strategy = InternalChromiumStrategy()
        profile_dir = tmp_path / "profile_internal"

        mock_ap, mock_launch, _, _ = _build_pw_mock()

        with (
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await strategy.login(profile_dir, headless=False)

        _, kwargs = mock_launch.call_args
        assert "channel" not in kwargs or kwargs["channel"] != "chrome"
        assert "--disable-blink-features=AutomationControlled" not in kwargs.get("args", [])
