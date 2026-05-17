from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.real_chrome import GEMINI_URL
from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError


def _status(outcome: FlowSessionOutcome, email: str | None = None) -> FlowSessionStatus:
    """Build a FlowSessionStatus for mocking verify_flow_session."""
    return FlowSessionStatus(outcome=outcome, user_email=email, source="chrome")


def _build_mock_proc() -> MagicMock:
    """Return a mock asyncio subprocess Process that exits cleanly.

    ``wait`` is async (awaited by the strategy); ``terminate`` / ``kill`` are
    synchronous on :class:`asyncio.subprocess.Process`.
    """
    mock_proc = MagicMock(name="proc")
    mock_proc.wait = AsyncMock(return_value=0)
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

        mock_proc = _build_mock_proc()
        mock_create = AsyncMock(return_value=mock_proc)
        fake_chrome = r"C:\fake\chrome.exe"
        verified = _status(FlowSessionOutcome.AUTHENTICATED, "test@example.com")

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch("gflow_cli.auth.real_chrome.find_chrome_executable", return_value=fake_chrome),
            patch("gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec", mock_create),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_session",
                AsyncMock(return_value=verified),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        args_list = mock_create.call_args.args
        assert args_list[0] == fake_chrome
        assert f"--user-data-dir={profile_dir}" in args_list
        assert "--enable-automation" not in args_list
        assert not any("--remote-debugging-port" in a for a in args_list)
        assert GEMINI_URL in args_list  # Chrome opens directly on the Flow page

    @pytest.mark.asyncio
    async def test_real_chrome_success_writes_marker(self, tmp_path: Path) -> None:
        """On an authenticated Flow session, login writes the .gflow_browser_strategy marker."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_proc = _build_mock_proc()
        verified = _status(FlowSessionOutcome.AUTHENTICATED, "test@example.com")

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=mock_proc),
            ),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_session",
                AsyncMock(return_value=verified),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        marker = profile_dir / ".gflow_browser_strategy"
        assert marker.exists()
        assert marker.read_text(encoding="utf-8") == "chrome"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "outcome",
        [
            FlowSessionOutcome.GOOGLE_SESSION_ONLY,
            FlowSessionOutcome.NO_SESSION,
            FlowSessionOutcome.VERIFICATION_ERROR,
        ],
    )
    async def test_real_chrome_unverified_raises_auth_missing(
        self, tmp_path: Path, outcome: FlowSessionOutcome
    ) -> None:
        """A non-authenticated outcome fails the login with AuthMissingError."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        gflow_home.mkdir()

        mock_proc = _build_mock_proc()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=mock_proc),
            ),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_session",
                AsyncMock(return_value=_status(outcome)),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthMissingError):
                await strategy.login(profile_dir, headless=False)

        assert not (profile_dir / ".gflow_browser_strategy").exists()

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

        async def _raise_timeout(awaitable: object, *_a: object, **_kw: object) -> None:
            # wait_for normally consumes the awaitable; close the un-awaited
            # proc.wait() coroutine so it doesn't emit a RuntimeWarning.
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise TimeoutError

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=mock_proc),
            ),
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
