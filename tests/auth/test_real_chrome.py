"""RealChromeStrategy — cancellation safety + chrome-arg hygiene (Task D4).

Companion to ``tests/auth/strategies/test_strategies.py`` (the success/marker/
lease-ordering suite). This file focuses on D4's two additions:

* the duplicate Flow-URL positional is gone (Chrome opens ONE Flow tab), and
* a cancellation while waiting for the user to close Chrome terminates + reaps
  the child and releases the profile lease (nothing orphaned).

...plus the auto-close Playwright driver (default path) and the two teardown
guarantees it must reproduce, since it bypasses the subprocess code the two
original guards cover.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError
from structlog.testing import capture_logs

from gflow_cli.auth.internal_chromium import GOOGLE_REJECTED_BROWSER_ROUTE
from gflow_cli.auth.real_chrome import (
    _UNVERIFIED_HINT,
    GEMINI_URL,
    RealChromeStrategy,
    _await_chrome_close,
    _build_chrome_args,
    _print_login_instructions,
)
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.errors import AuthLoginTimeoutError

AUTHENTICATED_BODY = '{"user": {"email": "test@example.com"}}'


def _authenticated_status() -> FlowSessionStatus:
    return FlowSessionStatus(
        outcome=FlowSessionOutcome.AUTHENTICATED,
        user_email="test@example.com",
        source="chrome",
    )


def _build_fake_playwright(
    *,
    session_body: str = AUTHENTICATED_BODY,
    page_url: str = "https://labs.google/fx/tools/flow",
    webdriver: bool = False,
    launch_error: Exception | None = None,
    poll_error: Exception | None = None,
    order: list[str] | None = None,
    on_poll: Any = None,
) -> tuple[MagicMock, MagicMock, MagicMock]:
    """Build (async_playwright_factory, pw, ctx) doubles for the owned-browser path."""
    resp = MagicMock(name="resp")
    resp.status = 200
    resp.text = AsyncMock(return_value=session_body)

    page = MagicMock(name="page")
    page.url = page_url
    page.goto = AsyncMock()
    page.evaluate = AsyncMock(return_value=webdriver)
    page.request.get = AsyncMock(return_value=resp, side_effect=poll_error)

    async def _cookies() -> list[dict[str, str]]:
        if on_poll is not None:
            await on_poll()
        return [{"name": "SAPISID", "value": "x"}]

    ctx = MagicMock(name="ctx")
    ctx.pages = [page]
    ctx.cookies = AsyncMock(side_effect=_cookies)
    ctx.new_page = AsyncMock(return_value=page)

    async def _close() -> None:
        if order is not None:
            order.append("close_context")

    ctx.close = AsyncMock(side_effect=_close)

    pw = MagicMock(name="pw")
    pw.chromium.launch_persistent_context = AsyncMock(
        return_value=ctx,
        side_effect=launch_error,
    )

    async def _aexit(*_a: object) -> bool:
        if order is not None:
            order.append("stop_driver")
        return False

    cm = MagicMock(name="cm")
    cm.__aenter__ = AsyncMock(return_value=pw)
    cm.__aexit__ = AsyncMock(side_effect=_aexit)
    return MagicMock(name="async_playwright", return_value=cm), pw, ctx


def _record_lease_events(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    from gflow_cli.profile_lease import ProfileLease

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)


# ---------------------------------------------------------------------------
# _build_chrome_args — Flow URL appears exactly once (D4: dup positional gone)
# ---------------------------------------------------------------------------


def test_build_chrome_args_opens_flow_once() -> None:
    args = _build_chrome_args(r"C:\fake\chrome.exe", Path("prof"), headless=False)
    assert args.count(GEMINI_URL) == 1, "Flow URL must be passed exactly once"
    assert args[-1] == GEMINI_URL, "the Flow URL should be the trailing positional"


def test_build_chrome_args_headless_opens_flow_once() -> None:
    args = _build_chrome_args(r"C:\fake\chrome.exe", Path("prof"), headless=True)
    assert args.count(GEMINI_URL) == 1
    assert "--headless=new" in args


# ---------------------------------------------------------------------------
# _await_chrome_close — cancellation terminates + reaps the child
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_await_chrome_close_cancellation_terminates_and_reaps() -> None:
    """A cancel while waiting for Chrome to close must terminate + reap the
    child (so it can't be orphaned holding the profile lock) and re-raise."""
    entered = asyncio.Event()
    closed = asyncio.Event()

    proc = MagicMock(name="proc")

    async def _wait() -> int:
        entered.set()
        await closed.wait()  # released by terminate() below
        return 0

    proc.wait = _wait
    proc.terminate = MagicMock(side_effect=lambda: closed.set())
    proc.kill = MagicMock()

    task = asyncio.create_task(_await_chrome_close(proc, timeout_seconds=600))
    await asyncio.wait_for(entered.wait(), timeout=1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    proc.terminate.assert_called_once()
    proc.kill.assert_not_called()  # exited within the reap grace after terminate


# ---------------------------------------------------------------------------
# login — cancellation releases the profile lease (chrome reaped first)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_login_cancellation_releases_lease_and_reaps_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cancelling login while passive-capture Chrome runs must terminate the
    child and release the profile lease via the enclosing ``async with
    ProfileLease`` (chrome dead BEFORE the profile is freed)."""
    from gflow_cli.profile_lease import ProfileLease

    events: list[str] = []

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)

    strategy = RealChromeStrategy()
    gflow_home = tmp_path / "gflow_home"
    profile_dir = gflow_home / "profile_default"
    gflow_home.mkdir()

    entered = asyncio.Event()
    closed = asyncio.Event()
    proc = MagicMock(name="proc")

    async def _wait() -> int:
        entered.set()
        await closed.wait()
        return 0

    proc.wait = _wait
    proc.terminate = MagicMock(side_effect=lambda: closed.set())
    proc.kill = MagicMock()

    with (
        patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
        # This guard covers the RETAINED subprocess path — force it.
        patch(
            "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
            return_value=False,
        ),
        patch(
            "gflow_cli.auth.real_chrome.find_chrome_executable",
            return_value=r"C:\fake\chrome.exe",
        ),
        patch(
            "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
            AsyncMock(return_value=proc),
        ),
        # verify_flow_profile must never run — the cancel lands before it.
        patch(
            "gflow_cli.auth.real_chrome.verify_flow_profile",
            AsyncMock(side_effect=AssertionError("verification must not run after cancel")),
        ),
    ):
        mock_settings.return_value.home = gflow_home
        task = asyncio.create_task(strategy.login(profile_dir, headless=False))
        await asyncio.wait_for(entered.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    proc.terminate.assert_called_once()
    # Lease acquired around Chrome, then released on the cancellation path.
    assert events == ["acquire", "release"]


# ---------------------------------------------------------------------------
# Playwright auto-close driver (default path)
# ---------------------------------------------------------------------------


class TestPlaywrightAutoClose:
    """The owned-browser path: gflow closes Chrome itself once Flow signs in."""

    @staticmethod
    def _home(tmp_path: Path) -> tuple[Path, Path]:
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        return gflow_home, gflow_home / "profile_default"

    @pytest.mark.asyncio
    async def test_default_path_owns_and_closes_the_browser(self, tmp_path: Path) -> None:
        """Chrome channel resolvable -> Playwright drives the login and closes it."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, pw, ctx = _build_fake_playwright()
        subprocess_exec = AsyncMock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch("gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec", subprocess_exec),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            patch("gflow_cli.auth.real_chrome.asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        subprocess_exec.assert_not_awaited()
        ctx.close.assert_awaited()
        kwargs = pw.chromium.launch_persistent_context.call_args.kwargs
        assert kwargs["user_data_dir"] == str(profile_dir)
        assert kwargs["channel"] == "chrome"
        assert kwargs["headless"] is False
        assert kwargs["no_viewport"] is True
        assert kwargs["chromium_sandbox"] is True
        assert kwargs["ignore_default_args"] == ["--enable-automation"]
        assert "viewport" not in kwargs
        launch_args = kwargs["args"]
        assert "--disable-blink-features=AutomationControlled" in launch_args
        assert "--window-size=1920,1080" in launch_args
        assert "--password-store=basic" in launch_args
        # The durability check still runs, outside the lease, exactly as before.
        assert (profile_dir / ".gflow_browser_strategy").read_text(encoding="utf-8") == "chrome"
        assert (profile_dir / ".gflow_account").read_text(encoding="utf-8") == "test@example.com"

    @pytest.mark.asyncio
    async def test_no_chrome_channel_falls_back_to_subprocess(self, tmp_path: Path) -> None:
        """No resolvable channel -> the old subprocess flow runs, silently."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, pw, _ctx = _build_fake_playwright()
        proc = MagicMock(name="proc")
        proc.wait = AsyncMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=False,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ) as subprocess_exec,
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        subprocess_exec.assert_awaited_once()
        pw.chromium.launch_persistent_context.assert_not_awaited()
        fallbacks = [e for e in logs if e.get("event") == "auth_login_subprocess_fallback"]
        assert len(fallbacks) == 1
        assert fallbacks[0]["reason"] == "channel_unavailable"

    @pytest.mark.asyncio
    async def test_headless_never_reaches_the_owned_browser(self, tmp_path: Path) -> None:
        """headless=True takes the subprocess path even when the channel resolves.

        Every arm of the 2026-09-08 spike was headed, so a headless Playwright sign-in is
        unmeasured against Google's gate. The subprocess path's ``--headless=new`` branch
        predates this change and is the measured option.
        """
        gflow_home, profile_dir = self._home(tmp_path)
        ap, pw, _ctx = _build_fake_playwright()
        proc = MagicMock(name="proc")
        proc.wait = AsyncMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ) as subprocess_exec,
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=True)

        subprocess_exec.assert_awaited_once()
        pw.chromium.launch_persistent_context.assert_not_awaited()
        fallbacks = [e for e in logs if e.get("event") == "auth_login_subprocess_fallback"]
        assert len(fallbacks) == 1
        assert fallbacks[0]["reason"] == "headless"

    @pytest.mark.asyncio
    async def test_google_rejection_falls_back_to_subprocess_once(self, tmp_path: Path) -> None:
        """Google's rejected-browser page must not surface exit 14 to the user."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, pw, ctx = _build_fake_playwright(
            page_url=f"https://{GOOGLE_REJECTED_BROWSER_ROUTE}?continue=flow",
        )
        proc = MagicMock(name="proc")
        proc.wait = AsyncMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ) as subprocess_exec,
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        pw.chromium.launch_persistent_context.assert_awaited_once()
        ctx.close.assert_awaited()  # the rejected window is closed, not left open
        subprocess_exec.assert_awaited_once()
        fallbacks = [e for e in logs if e.get("event") == "auth_login_subprocess_fallback"]
        assert [e["reason"] for e in fallbacks] == ["browser_rejected"]

    @pytest.mark.asyncio
    async def test_launch_failure_falls_back_to_subprocess(self, tmp_path: Path) -> None:
        gflow_home, profile_dir = self._home(tmp_path)
        ap, _pw, _ctx = _build_fake_playwright(launch_error=PlaywrightError("no chrome"))
        proc = MagicMock(name="proc")
        proc.wait = AsyncMock(return_value=0)
        proc.terminate = MagicMock()
        proc.kill = MagicMock()

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.find_chrome_executable",
                return_value=r"C:\fake\chrome.exe",
            ),
            patch(
                "gflow_cli.auth.real_chrome.asyncio.create_subprocess_exec",
                AsyncMock(return_value=proc),
            ) as subprocess_exec,
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        subprocess_exec.assert_awaited_once()
        failures = [e for e in logs if e.get("event") == "auth_login_launch_failed"]
        assert len(failures) == 1
        assert failures[0]["error"] == "Error"
        fallbacks = [e for e in logs if e.get("event") == "auth_login_subprocess_fallback"]
        assert [e["reason"] for e in fallbacks] == ["launch_failed"]

    @pytest.mark.asyncio
    async def test_manual_close_is_not_an_error(self, tmp_path: Path) -> None:
        """Three releases told users to close the window themselves. Doing so on a
        login that actually succeeded must NOT produce a red error — it falls
        through to verify_flow_profile, which is the authority either way."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, _pw, _ctx = _build_fake_playwright(poll_error=PlaywrightError("Target closed"))

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            patch("gflow_cli.auth.real_chrome.asyncio.sleep", AsyncMock()),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        assert (profile_dir / ".gflow_account").read_text(encoding="utf-8") == "test@example.com"
        assert [e for e in logs if e.get("event") == "auth_login_browser_closed_by_user"]

    @pytest.mark.asyncio
    async def test_timeout_with_window_open_raises(self, tmp_path: Path) -> None:
        gflow_home, profile_dir = self._home(tmp_path)
        ap, _pw, ctx = _build_fake_playwright(session_body="{}")

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(side_effect=AssertionError("verification must not run on timeout")),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthLoginTimeoutError) as excinfo:
                await RealChromeStrategy(timeout_seconds=0).login(profile_dir, headless=False)

        assert "not detected within 0s" in str(excinfo.value)
        assert "GFLOW_CLI_AUTH_LOGIN_TIMEOUT" in (excinfo.value.remediation_hint or "")
        ctx.close.assert_awaited()

    @pytest.mark.asyncio
    async def test_timeout_teardown_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Timeout closes the context, stops the driver, then releases the lease."""
        gflow_home, profile_dir = self._home(tmp_path)
        order: list[str] = []
        _record_lease_events(monkeypatch, order)
        ap, _pw, _ctx = _build_fake_playwright(session_body="{}", order=order)

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
        ):
            mock_settings.return_value.home = gflow_home
            with pytest.raises(AuthLoginTimeoutError):
                await RealChromeStrategy(timeout_seconds=0).login(profile_dir, headless=False)

        assert order == ["acquire", "close_context", "stop_driver", "release"]

    @pytest.mark.asyncio
    async def test_cancellation_teardown_order(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Ctrl-C mid-login: close context -> stop driver -> release lease, in that
        order. Ported from the subprocess guard — an orphaned browser keeps the
        profile's SQLite lock and therefore the ProfileLease (fixed twice)."""
        gflow_home, profile_dir = self._home(tmp_path)
        order: list[str] = []
        _record_lease_events(monkeypatch, order)
        polling = asyncio.Event()
        forever = asyncio.Event()

        async def _block() -> None:
            polling.set()
            await forever.wait()

        ap, _pw, _ctx = _build_fake_playwright(order=order, on_poll=_block)

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(side_effect=AssertionError("verification must not run after cancel")),
            ),
        ):
            mock_settings.return_value.home = gflow_home
            task = asyncio.create_task(RealChromeStrategy().login(profile_dir, headless=False))
            await asyncio.wait_for(polling.wait(), timeout=1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        assert order == ["acquire", "close_context", "stop_driver", "release"]

    @pytest.mark.asyncio
    async def test_webdriver_exposed_is_logged(self, tmp_path: Path) -> None:
        """A future Chrome that ignores the stealth flag must fail loudly, not as
        a 600s timeout telling the user to sign in faster."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, _pw, _ctx = _build_fake_playwright(webdriver=True)

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            patch("gflow_cli.auth.real_chrome.asyncio.sleep", AsyncMock()),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        assert [e for e in logs if e.get("event") == "auth_login_webdriver_exposed"]

    @pytest.mark.asyncio
    async def test_never_logs_a_google_url(self, tmp_path: Path) -> None:
        """OAuth `state` / `code_challenge` live in these URLs and data/redaction.py
        matches neither — so no page URL may ever reach a log event."""
        gflow_home, profile_dir = self._home(tmp_path)
        ap, _pw, _ctx = _build_fake_playwright(
            page_url=(
                "https://accounts.google.com/v3/signin/identifier"
                "?state=SECRETSTATE&code_challenge=SECRETCHALLENGE"
            ),
        )

        with (
            patch("gflow_cli.auth.real_chrome.get_settings") as mock_settings,
            patch(
                "gflow_cli.auth.real_chrome.is_playwright_chrome_channel_available",
                return_value=True,
            ),
            patch("gflow_cli.auth.strategies.async_playwright", ap),
            patch(
                "gflow_cli.auth.real_chrome.verify_flow_profile",
                AsyncMock(return_value=_authenticated_status()),
            ),
            patch("gflow_cli.auth.real_chrome.asyncio.sleep", AsyncMock()),
            capture_logs() as logs,
        ):
            mock_settings.return_value.home = gflow_home
            await RealChromeStrategy().login(profile_dir, headless=False)

        blob = repr(logs)
        assert "accounts.google.com" not in blob
        assert "SECRETSTATE" not in blob
        assert "SECRETCHALLENGE" not in blob
        # The rename must be observable: the old event name is gone.
        assert [e for e in logs if e.get("event") == "auth_login_started"]
        assert not [e for e in logs if e.get("event") == "auth_passive_capture_started"]
        detected = [e for e in logs if e.get("event") == "auth_login_session_detected"]
        assert len(detected) == 1
        assert detected[0]["strategy"] == "chrome"
        assert "elapsed_s" in detected[0]


# ---------------------------------------------------------------------------
# T5 — copy
# ---------------------------------------------------------------------------


def test_login_instructions_say_gflow_closes_chrome(capsys: pytest.CaptureFixture[str]) -> None:
    _print_login_instructions()
    out = capsys.readouterr().out
    assert "BROWSER SIGN-IN" in out
    assert "PASSIVE AUTHENTICATION" not in out
    assert "closes" in out.lower()


def test_google_session_only_hint_drops_close_chrome() -> None:
    assert "before closing Chrome" not in _UNVERIFIED_HINT[FlowSessionOutcome.GOOGLE_SESSION_ONLY]
