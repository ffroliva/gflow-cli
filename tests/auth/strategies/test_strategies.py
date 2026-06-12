from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.auth.real_chrome import _UNVERIFIED_HINT, _UNVERIFIED_MESSAGE, GEMINI_URL
from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.errors import (
    AuthBrowserRejectedError,
    AuthLoginTimeoutError,
    AuthMissingError,
    SecurityError,
)


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
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=verified),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        args_list = mock_create.call_args.args
        assert args_list[0] == fake_chrome
        assert f"--user-data-dir={profile_dir}" in args_list
        assert "--password-store=basic" in args_list
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
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=verified),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            await strategy.login(profile_dir, headless=False)

        marker = profile_dir / ".gflow_browser_strategy"
        assert marker.exists()
        assert marker.read_text(encoding="utf-8") == "chrome"
        account_file = profile_dir / ".gflow_account"
        assert account_file.exists(), ".gflow_account must be written on successful login"
        assert account_file.read_text(encoding="utf-8") == "test@example.com"

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
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_status(outcome)),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthMissingError) as exc_info:
                await strategy.login(profile_dir, headless=False)

        assert exc_info.value.detail == _UNVERIFIED_MESSAGE[outcome]
        assert exc_info.value.remediation_hint == _UNVERIFIED_HINT[outcome]
        assert not (profile_dir / ".gflow_browser_strategy").exists()

    @pytest.mark.asyncio
    async def test_real_chrome_preserves_preexisting_marker_on_transient_failure(
        self, tmp_path: Path
    ) -> None:
        """A re-login on a previously-verified chrome profile that hits a
        transient VERIFICATION_ERROR must NOT delete the pre-existing marker —
        otherwise channel_for_profile stops returning 'chrome' and FlowApiClient
        downgrades to bundled Chromium on a real-Chrome profile."""
        strategy = RealChromeStrategy()
        gflow_home = tmp_path / "gflow_home"
        profile_dir = gflow_home / "profile_default"
        profile_dir.mkdir(parents=True)
        # Profile was verified-chrome on a previous successful login.
        marker = profile_dir / ".gflow_browser_strategy"
        marker.write_text("chrome", encoding="utf-8")

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
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_status(FlowSessionOutcome.VERIFICATION_ERROR)),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthMissingError):
                await strategy.login(profile_dir, headless=False)

        # The marker the profile already had must survive the transient failure.
        assert marker.exists(), "pre-existing chrome marker must survive a transient failure"
        assert marker.read_text(encoding="utf-8") == "chrome"

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
        """Internal Chromium detects success via the /api/auth/session probe."""
        strategy = InternalChromiumStrategy()
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

        mock_resp = MagicMock(name="resp")
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value='{"user": {"email": "test@example.com"}}')

        mock_page = MagicMock(name="page")
        mock_page.goto = AsyncMock()
        mock_page.request.get = AsyncMock(return_value=mock_resp)

        mock_ctx = MagicMock(name="ctx")
        mock_ctx.pages = [mock_page]
        mock_ctx.cookies = AsyncMock(return_value=[{"name": "SAPISID", "value": "x"}])
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
        mock_page.request.get.assert_awaited()
        account_file = profile_dir / ".gflow_account"
        assert account_file.exists(), ".gflow_account must be written on successful login"
        assert account_file.read_text(encoding="utf-8") == "test@example.com"

    @pytest.mark.asyncio
    async def test_internal_chromium_timeout_raises(self, tmp_path: Path) -> None:
        """AuthLoginTimeoutError is raised when the session never authenticates."""
        strategy = InternalChromiumStrategy(timeout_seconds=0)
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

        mock_resp = MagicMock(name="resp")
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value="{}")

        mock_page = MagicMock(name="page")
        mock_page.goto = AsyncMock()
        mock_page.request.get = AsyncMock(return_value=mock_resp)

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

    @pytest.mark.asyncio
    async def test_internal_chromium_rejected_browser_raises_guidance(
        self,
        tmp_path: Path,
    ) -> None:
        """Google's rejected-browser page should fail fast with Chrome guidance."""
        strategy = InternalChromiumStrategy(timeout_seconds=600)
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

        mock_success_loc = MagicMock()
        mock_success_loc.is_visible = AsyncMock(return_value=False)

        mock_page = MagicMock(name="page")
        mock_page.url = "https://accounts.google.com/v3/signin/rejected?continue=flow"
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
            with pytest.raises(AuthBrowserRejectedError) as excinfo:
                await strategy.login(profile_dir, headless=False)

        assert "--browser chrome" in excinfo.value.remediation_hint
        assert "GFLOW_CLI_AUTH_BROWSER=chrome" in excinfo.value.remediation_hint
        mock_ctx.close.assert_called_once()
