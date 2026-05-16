from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.errors import AuthLoginTimeoutError, SecurityError

# ---------------------------------------------------------------------------
# Shared Playwright mock factory — verification-probe (launch_persistent_context)
# ---------------------------------------------------------------------------


def _build_verify_pw_mock(
    cookies: list[dict] | None = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Return (mock_async_playwright, mock_ctx, mock_page) for the headless verification probe.

    RealChromeStrategy (Passive Capture) uses launch_persistent_context(channel="chrome")
    after the user closes Chrome to verify the persisted cookies.
    """
    if cookies is None:
        cookies = [{"name": "SAPISID", "value": "dummy"}]

    mock_page = MagicMock(name="page")
    mock_page.goto = AsyncMock()

    mock_ctx = MagicMock(name="ctx")
    mock_ctx.pages = [mock_page]
    mock_ctx.cookies = AsyncMock(return_value=cookies)
    mock_ctx.close = AsyncMock()

    mock_pw_obj = MagicMock(name="pw")
    mock_pw_obj.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)

    mock_cm = MagicMock(name="cm")
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)
    return mock_ap, mock_ctx, mock_page


def _build_mock_proc() -> MagicMock:
    """Return a mock subprocess.Popen instance that exits cleanly."""
    mock_proc = MagicMock(name="proc")
    mock_proc.wait = MagicMock(return_value=0)
    mock_proc.terminate = MagicMock()
    mock_proc.kill = MagicMock()
    return mock_proc


# ---------------------------------------------------------------------------
# RealChromeStrategy — Passive Capture (v0.6.0a3)
# ---------------------------------------------------------------------------


class TestRealChromeStrategy:
    @pytest.mark.asyncio
    async def test_real_chrome_launch_flags(self, tmp_path: Path) -> None:
        """Verify Chrome launches WITHOUT --remote-debugging-port or --enable-automation."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, _, _ = _build_verify_pw_mock()
        mock_proc = _build_mock_proc()
        fake_chrome = r"C:\fake\chrome.exe"

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.find_chrome_executable", return_value=fake_chrome),
            patch("subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("playwright.async_api.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        args_list = mock_popen.call_args[0][0]
        assert args_list[0] == fake_chrome
        assert f"--user-data-dir={profile_dir}" in args_list
        assert "--enable-automation" not in args_list
        assert not any("--remote-debugging-port" in a for a in args_list)

    @pytest.mark.asyncio
    async def test_real_chrome_success_verified_via_sapisid(self, tmp_path: Path) -> None:
        """After proc.wait(), verify headless probe detects SAPISID and exits cleanly."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_ap, mock_ctx, _ = _build_verify_pw_mock(cookies=[{"name": "SAPISID", "value": "abc"}])
        mock_proc = _build_mock_proc()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("playwright.async_api.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        mock_ctx.cookies.assert_called_once()
        mock_ctx.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_real_chrome_privacy_guard(self, tmp_path: Path) -> None:
        """Verify SecurityError when profile_dir is outside GFLOW_CLI_HOME."""
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

    @pytest.mark.asyncio
    async def test_real_chrome_timeout_raises(self, tmp_path: Path) -> None:
        """AuthLoginTimeoutError raised when asyncio.wait_for times out.

        Mocks asyncio.wait_for to raise asyncio.TimeoutError, simulating the
        case where the user never closes Chrome within timeout_seconds.
        """
        strategy = RealChromeStrategy(timeout_seconds=0)
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_proc = _build_mock_proc()

        async def _raise_timeout(*_a: object, **_kw: object) -> None:
            raise TimeoutError

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch("subprocess.Popen", return_value=mock_proc),
            patch("gflow_cli.auth.real_chrome.asyncio.wait_for", side_effect=_raise_timeout),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthLoginTimeoutError) as excinfo:
                await strategy.login(profile_dir, headless=False)

        assert "0s" in str(excinfo.value)
        mock_proc.terminate.assert_called_once()


# ---------------------------------------------------------------------------
# InternalChromiumStrategy
# ---------------------------------------------------------------------------


class TestInternalChromiumStrategy:
    @pytest.mark.asyncio
    async def test_internal_chromium_standard_behavior(self, tmp_path: Path) -> None:
        """Verify Internal Chromium uses standard Playwright (no stealth flags)."""
        strategy = InternalChromiumStrategy()
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

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
            patch("gflow_cli.auth.internal_chromium.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        _, kwargs = mock_launch_pctx.call_args
        assert "channel" not in kwargs or kwargs["channel"] != "chrome"
        assert "--disable-blink-features=AutomationControlled" not in kwargs.get("args", [])

    @pytest.mark.asyncio
    async def test_internal_chromium_timeout_raises(self, tmp_path: Path) -> None:
        """Verify AuthLoginTimeoutError is raised when polling loop exceeds deadline."""
        strategy = InternalChromiumStrategy(timeout_seconds=0)
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

        mock_success_loc = MagicMock()
        mock_success_loc.is_visible = AsyncMock(return_value=False)

        mock_page = MagicMock(name="page")
        mock_page.goto = AsyncMock()
        mock_page.get_by_text.return_value = mock_success_loc

        mock_ctx = MagicMock(name="ctx")
        mock_ctx.pages = [mock_page]
        mock_ctx.cookies = AsyncMock(return_value=[])
        mock_ctx.close = AsyncMock()
        mock_ctx.new_page = AsyncMock(return_value=mock_page)

        mock_pw_obj = MagicMock(name="pw")
        mock_pw_obj.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)

        mock_cm = MagicMock(name="cm")
        mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
        mock_cm.__aexit__ = AsyncMock(return_value=False)
        mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)

        with (
            patch("gflow_cli.auth.internal_chromium.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthLoginTimeoutError) as excinfo:
                await strategy.login(profile_dir, headless=False)

        assert "0s" in str(excinfo.value)
        mock_ctx.close.assert_called_once()
