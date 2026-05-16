from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.errors import SecurityError

# ---------------------------------------------------------------------------
# Shared mock factory — CDP "attach" pattern (v0.6.0a2)
# ---------------------------------------------------------------------------


def _build_pw_mock(
    cookies: list[dict] | None = None,
    success_visible: bool = True,
) -> tuple[MagicMock, AsyncMock, MagicMock, MagicMock]:
    """Return (mock_async_playwright, mock_connect_cdp, mock_ctx, mock_page).

    Builds a mock for the subprocess+CDP approach used by RealChromeStrategy:
      1. subprocess.Popen launches Chrome
      2. pw.chromium.connect_over_cdp(url) → browser
      3. browser.contexts[0] → ctx
      4. ctx.add_init_script, ctx.cookies, etc.
    """
    if cookies is None:
        cookies = [{"name": "SAPISID", "value": "dummy"}]

    # Page mock
    mock_btn = MagicMock(name="btn")
    mock_btn.is_visible = AsyncMock(return_value=False)
    mock_btn.get_attribute = AsyncMock(return_value="user@example.com")
    mock_btn.click = AsyncMock()

    mock_success_loc = MagicMock(name="success_loc")
    mock_success_loc.is_visible = AsyncMock(return_value=success_visible)

    mock_page = MagicMock(name="page")
    mock_page.goto = AsyncMock()
    mock_page.url = "https://labs.google/fx/tools/flow"
    mock_page.locator.return_value.first = mock_btn
    mock_page.get_by_text.return_value = mock_success_loc

    # Context mock
    mock_ctx = MagicMock(name="ctx")
    mock_ctx.pages = [mock_page]
    mock_ctx.add_init_script = AsyncMock()
    mock_ctx.cookies = AsyncMock(return_value=cookies)
    mock_ctx.close = AsyncMock()
    mock_ctx.new_page = AsyncMock(return_value=mock_page)

    # Browser mock returned by connect_over_cdp
    mock_browser = MagicMock(name="browser")
    mock_browser.contexts = [mock_ctx]
    mock_browser.close = AsyncMock()

    # pw.chromium.connect_over_cdp
    mock_connect_cdp = AsyncMock(return_value=mock_browser)

    # pw object
    mock_pw_obj = MagicMock(name="pw")
    mock_pw_obj.chromium.connect_over_cdp = mock_connect_cdp

    # async with async_playwright() as pw
    mock_cm = MagicMock(name="cm")
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)

    return mock_ap, mock_connect_cdp, mock_ctx, mock_page


def _build_mock_proc() -> MagicMock:
    """Return a mock subprocess.Popen instance."""
    mock_proc = MagicMock(name="proc")
    mock_proc.terminate = MagicMock()
    mock_proc.wait = MagicMock()
    mock_proc.kill = MagicMock()
    return mock_proc


# ---------------------------------------------------------------------------
# RealChromeStrategy
# ---------------------------------------------------------------------------


class TestRealChromeStrategy:
    @pytest.mark.asyncio
    async def test_real_chrome_launch_flags(self, tmp_path: Path) -> None:
        """Verify Chrome is launched via subprocess with CDP port, no --enable-automation."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, _, _, _ = _build_pw_mock()
        mock_proc = _build_mock_proc()
        fake_chrome = r"C:\fake\chrome.exe"

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch("gflow_cli.auth.real_chrome.find_chrome_executable", return_value=fake_chrome),
            patch("gflow_cli.auth.real_chrome.get_free_port", return_value=12345),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        args_list = mock_popen.call_args[0][0]
        assert args_list[0] == fake_chrome
        assert "--remote-debugging-port=12345" in args_list
        assert f"--user-data-dir={profile_dir}" in args_list
        # No automation-triggering flag — the core stealth guarantee
        assert "--enable-automation" not in args_list

    @pytest.mark.asyncio
    async def test_stealth_init_script_registered_before_page(self, tmp_path: Path) -> None:
        """Verify add_init_script is called before any page navigation (timing fix)."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, _, mock_ctx, mock_page = _build_pw_mock()
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

        mock_proc = _build_mock_proc()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch("gflow_cli.auth.real_chrome.get_free_port", return_value=12345),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        assert "add_init_script" in call_order
        assert "goto" in call_order
        assert call_order.index("add_init_script") < call_order.index("goto"), (
            "add_init_script must fire before goto() to cover the first navigation"
        )

    @pytest.mark.asyncio
    async def test_real_chrome_login_success_polling(self, tmp_path: Path) -> None:
        """Verify it polls for SAPISID cookie and UI signal, then exits cleanly."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, _, mock_ctx, mock_page = _build_pw_mock(cookies=[])
        # First call: no cookies. Second: has SAPISID → triggers success check.
        mock_ctx.cookies = AsyncMock(
            side_effect=[
                [],
                [{"name": "SAPISID", "value": "found"}],
            ]
        )
        mock_proc = _build_mock_proc()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.async_playwright", mock_ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch("gflow_cli.auth.real_chrome.get_free_port", return_value=12345),
            patch("subprocess.Popen", return_value=mock_proc),
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

        # Internal Chromium uses launch_persistent_context, not CDP
        mock_success_loc = MagicMock()
        mock_success_loc.is_visible = AsyncMock(return_value=True)

        mock_page = MagicMock(name="page")
        mock_page.goto = AsyncMock()
        mock_page.get_by_text.return_value = mock_success_loc

        mock_ctx = MagicMock(name="ctx")
        mock_ctx.pages = [mock_page]
        mock_ctx.cookies = AsyncMock(return_value=[{"name": "SAPISID", "value": "dummy"}])
        mock_ctx.close = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        mock_pw_obj = MagicMock(name="pw")
        mock_launch_pctx = AsyncMock(return_value=mock_ctx)
        mock_pw_obj.chromium.launch_persistent_context = mock_launch_pctx

        mock_cm = MagicMock(name="cm")
        mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)

        with (
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            await strategy.login(profile_dir, headless=False)

        _, kwargs = mock_launch_pctx.call_args
        assert "channel" not in kwargs or kwargs["channel"] != "chrome"
        assert "--disable-blink-features=AutomationControlled" not in kwargs.get("args", [])
