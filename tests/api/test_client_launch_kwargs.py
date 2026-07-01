"""Pins the ``FlowApiClient._persistent_context_kwargs()`` seam.

The seam was extracted from the inline ``launch_persistent_context(...)`` call so
a dev-scoped recording subclass can augment the launch without any recording
concern living in core (see ``scripts/dev/_recording_client.py``). This test
proves the extraction is value-for-value behavior-preserving and keeps the
seam's contract stable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.api.transports.ui_automation import UiAutomationTransport
from gflow_cli.browser_manager import chrome_strategy_requested
from gflow_cli.errors import ConfigurationError

_MARKER = ".gflow_browser_strategy"


def test_persistent_context_kwargs_are_unchanged(tmp_path: Path) -> None:
    """The seam returns exactly the kwargs the client launched with before the
    refactor — proving the extraction changed no behavior."""
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    kwargs = client._persistent_context_kwargs()  # noqa: SLF001
    assert kwargs["user_data_dir"] == str(tmp_path)
    assert kwargs["headless"] is True
    assert kwargs["viewport"] == {"width": 1280, "height": 720}
    assert kwargs["locale"] == "en-US"
    assert kwargs["extra_http_headers"] == {"Accept-Language": "en-US,en;q=0.9"}
    assert kwargs["ignore_default_args"] == [
        "--enable-automation",
        "--no-sandbox",
    ]
    # Regression for #222: generation must pass --password-store=basic EXPLICITLY
    # in args (not merely keep it out of ignore_default_args). auth login and
    # verification seal/read the profile cookies with the *basic* store; if
    # generation lets Chrome fall back to the macOS keychain, those cookies can't
    # be decrypted -> logged-out -> 401 on createProject. Every other launch site
    # passes the flag; this shared context must too. (Unit-level proxy: the real
    # failure only reproduces on a headed Chrome on macOS.)
    assert "--password-store=basic" not in kwargs["ignore_default_args"]
    assert "--password-store=basic" in kwargs["args"]
    assert kwargs["args"] == [
        "--password-store=basic",
        "--disable-blink-features=AutomationControlled",
        "--disable-dev-shm-usage",
    ]
    # channel is profile-derived; a marker-less tmp_path has no
    # .gflow_browser_strategy file, so channel_for_profile() returns None.
    assert kwargs["channel"] is None


@pytest.mark.asyncio
async def test_ui_automation_setup_passes_disable_dev_shm_usage(tmp_path: Path) -> None:
    """setup() must pass --disable-dev-shm-usage in args to launch_persistent_context."""
    fake_page = MagicMock()
    fake_page.goto = AsyncMock()
    fake_page.add_init_script = AsyncMock()

    fake_ctx = MagicMock()
    fake_ctx.pages = [fake_page]
    fake_ctx.add_init_script = AsyncMock()

    fake_chromium = MagicMock()
    fake_chromium.launch_persistent_context = AsyncMock(return_value=fake_ctx)

    fake_pw = MagicMock()
    fake_pw.chromium = fake_chromium

    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=fake_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_async_playwright = MagicMock(return_value=mock_cm)

    with patch("gflow_cli.api.transports.ui_automation.async_playwright", mock_async_playwright):
        transport = UiAutomationTransport()
        await transport.setup(profile_dir=tmp_path)

    _call_kwargs = fake_chromium.launch_persistent_context.call_args
    args_passed = _call_kwargs.kwargs.get(
        "args",
        _call_kwargs.args[1] if len(_call_kwargs.args) > 1 else [],
    )
    assert "--disable-dev-shm-usage" in args_passed


# --- issue #222: chrome-strategy downgrade guard --------------------------------


def test_chrome_strategy_requested_reads_marker(tmp_path: Path) -> None:
    """chrome_strategy_requested is True only when the marker says 'chrome'."""
    assert chrome_strategy_requested(tmp_path) is False  # no marker
    (tmp_path / _MARKER).write_text("chrome", encoding="utf-8")
    assert chrome_strategy_requested(tmp_path) is True
    (tmp_path / _MARKER).write_text("internal-chromium", encoding="utf-8")
    assert chrome_strategy_requested(tmp_path) is False


def test_guard_raises_on_macos_when_chrome_strategy_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """marker=chrome but channel=None on macOS → fatal (bundled Chromium can't
    decrypt real-Chrome cookies via the per-app Keychain key)."""
    (tmp_path / _MARKER).write_text("chrome", encoding="utf-8")
    # Construct BEFORE patching sys.platform: construction resolves dirs via
    # platformdirs, whose backend is sensitive to sys.platform. The guard path
    # itself does not touch platformdirs, so patch only around the call.
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    with pytest.raises(ConfigurationError, match="chrome"):
        client._log_and_guard_launch({"channel": None})  # noqa: SLF001


def test_guard_warns_not_raises_off_macos(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Same downgrade off macOS is non-fatal (e.g. Windows DPAPI cookie key is
    per-user, so bundled Chromium can still decrypt) — must not raise, but MUST
    still log the ``client.chrome_strategy_downgraded`` warning so the silent
    downgrade is visible to operators."""
    import structlog

    (tmp_path / _MARKER).write_text("chrome", encoding="utf-8")
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    monkeypatch.setattr(sys, "platform", "win32")
    cap = structlog.testing.LogCapture()
    with patch("gflow_cli.api.client.logger", structlog.wrap_logger(None, processors=[cap])):
        client._log_and_guard_launch({"channel": None})  # noqa: SLF001  (no raise)
    assert any(e["event"] == "client.chrome_strategy_downgraded" for e in cap.entries)


def test_guard_ok_when_channel_resolved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """channel resolved to 'chrome' → no downgrade → no raise, even on macOS."""
    (tmp_path / _MARKER).write_text("chrome", encoding="utf-8")
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    monkeypatch.setattr(sys, "platform", "darwin")
    client._log_and_guard_launch({"channel": "chrome"})  # noqa: SLF001  (no raise)


def test_persistent_context_launch_event_fields(tmp_path: Path) -> None:
    """The launch diagnostic emits the fields needed to remotely root-cause #222:
    channel, the cookie-db path/presence (H2 location discriminator), platform,
    and the ``--password-store=basic`` flag (cookie-store symmetry). A regression
    that drops or renames one of these would otherwise ship green."""
    import structlog

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    kwargs = {
        "channel": None,
        "args": ["--password-store=basic", "--disable-dev-shm-usage"],
        "ignore_default_args": ["--enable-automation"],
    }
    cap = structlog.testing.LogCapture()
    # No marker -> chrome_strategy_requested is False, so the guard neither raises
    # nor warns: this isolates the launch event itself.
    with patch("gflow_cli.api.client.logger", structlog.wrap_logger(None, processors=[cap])):
        client._log_and_guard_launch(kwargs)  # noqa: SLF001
    ev = next(e for e in cap.entries if e["event"] == "client.persistent_context_launch")
    assert ev["channel"] is None
    assert ev["chrome_strategy_requested"] is False
    assert ev["password_store_basic"] is True
    assert ev["launch_args"] == kwargs["args"]
    assert ev["user_data_dir"] == str(tmp_path)
    assert ev["platform"] == sys.platform
    assert "cookies_db_present" in ev
    assert "cookies_db_path" in ev


# --- issue #222: persistent-context cookie-state diagnostic --------------------
#
# These pin the load-vs-send discriminator: the launched context's own cookie
# jar tells us whether the Flow session cookie was loaded (vs a server-side 401).
# A fresh LogCapture-wrapped logger is injected so no cached structlog config
# bleeds in (see auto-memory: structlog cache-logger-off-for-tests).


def _capture_cookie_state(client: FlowApiClient) -> dict:
    import asyncio

    import structlog

    cap = structlog.testing.LogCapture()
    with patch("gflow_cli.api.client.logger", structlog.wrap_logger(None, processors=[cap])):
        asyncio.run(client._ensure_context_session_cookie())  # noqa: SLF001
    return dict(next(e for e in cap.entries if e["event"] == "client.context_cookie_state"))


def _client_with_cookies(tmp_path: Path, cookies: list[dict]) -> FlowApiClient:
    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(return_value=cookies)
    client._context = ctx  # noqa: SLF001
    return client


def test_context_cookie_state_present_and_unexpired(tmp_path: Path) -> None:
    """Flow session cookie present + future expiry → present=True, expired=False."""
    client = _client_with_cookies(
        tmp_path,
        [
            {"name": "__Secure-next-auth.session-token", "expires": 9_999_999_999.0},
            {"name": "SAPISID", "expires": -1},
        ],
    )
    ev = _capture_cookie_state(client)
    assert ev["flow_session_cookie_present"] is True
    assert ev["flow_session_cookie_expired"] is False
    assert ev["google_sapisid_present"] is True
    assert ev["context_cookie_count"] == 2


def test_context_cookie_state_flags_expired(tmp_path: Path) -> None:
    """A past expiry on the Flow session cookie → expired=True."""
    client = _client_with_cookies(
        tmp_path,
        [{"name": "__Secure-next-auth.session-token", "expires": 1_000.0}],
    )
    ev = _capture_cookie_state(client)
    assert ev["flow_session_cookie_present"] is True
    assert ev["flow_session_cookie_expired"] is True


def test_context_cookie_state_absent(tmp_path: Path) -> None:
    """No Flow session cookie in the jar → present=False (the cookie-LOAD failure
    signal we expect if the persistent context can't decrypt/find it)."""
    client = _client_with_cookies(tmp_path, [{"name": "SAPISID", "expires": -1}])
    ev = _capture_cookie_state(client)
    assert ev["flow_session_cookie_present"] is False
    assert ev["flow_session_cookie_expired"] is False
    assert ev["google_sapisid_present"] is True


def test_context_cookie_state_swallows_probe_error(tmp_path: Path) -> None:
    """Contract: the diagnostic is pure observability — if ``context.cookies()``
    raises (e.g. a closing context), it must NOT propagate (it is awaited inside
    ``_enter_setup``, so a raise would abort the whole generation launch). It logs
    ``client.context_cookie_probe_error`` and emits no ``context_cookie_state``."""
    import asyncio

    import structlog

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(side_effect=PermissionError("locked"))
    client._context = ctx  # noqa: SLF001
    cap = structlog.testing.LogCapture()
    with patch("gflow_cli.api.client.logger", structlog.wrap_logger(None, processors=[cap])):
        asyncio.run(client._ensure_context_session_cookie())  # noqa: SLF001  (must not raise)
    events = [e["event"] for e in cap.entries]
    assert "client.context_cookie_probe_error" in events
    assert "client.context_cookie_state" not in events


# --- issue #222: pre-read seed of the session cookie (macOS decrypt failure) ----


def test_context_seeds_session_when_absent_and_preread_present(tmp_path: Path) -> None:
    """#222: when the headed context loaded NO session cookie (macOS can't decrypt
    the on-disk store) but the pre-launch snapshot has it, seed it into the context."""
    import asyncio

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(return_value=[])  # headed context loaded nothing
    ctx.add_cookies = AsyncMock()
    client._context = ctx  # noqa: SLF001
    client._preread_flow_cookies = {  # noqa: SLF001
        "__Secure-next-auth.session-token": "tok",
        "__Host-next-auth.csrf-token": "csrf",
    }
    asyncio.run(client._ensure_context_session_cookie())  # noqa: SLF001
    ctx.add_cookies.assert_awaited_once()
    seeded = ctx.add_cookies.await_args.args[0]
    assert any(c["name"] == "__Secure-next-auth.session-token" for c in seeded)
    assert all(c["url"] == "https://labs.google" for c in seeded)


def test_context_no_seed_when_session_present(tmp_path: Path) -> None:
    """#222: if the context already loaded the session cookie, do NOT seed."""
    import asyncio

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(
        return_value=[{"name": "__Secure-next-auth.session-token", "expires": -1}]
    )
    ctx.add_cookies = AsyncMock()
    client._context = ctx  # noqa: SLF001
    client._preread_flow_cookies = {"__Secure-next-auth.session-token": "tok"}  # noqa: SLF001
    asyncio.run(client._ensure_context_session_cookie())  # noqa: SLF001
    ctx.add_cookies.assert_not_awaited()


def test_context_no_seed_when_preread_empty(tmp_path: Path) -> None:
    """#222: session absent AND no pre-read → nothing to seed, logs unavailable."""
    import asyncio

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    ctx = MagicMock()
    ctx.cookies = AsyncMock(return_value=[])
    ctx.add_cookies = AsyncMock()
    client._context = ctx  # noqa: SLF001
    # _preread_flow_cookies defaults to {} — nothing captured pre-launch.
    asyncio.run(client._ensure_context_session_cookie())  # noqa: SLF001
    ctx.add_cookies.assert_not_awaited()


# --- issue #222: pre-launch snapshot capture (_preread_flow_session_cookies) ---


def test_preread_flow_session_cookies_populates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#222: the pre-launch capture stores the snapshot's flow cookies into
    _preread_flow_cookies, ready for _ensure_context_session_cookie to seed."""
    import asyncio

    from gflow_cli.auth.cookies import ChromeCookieSnapshot

    client = FlowApiClient(profile_dir=tmp_path, headless=True)
    snap = ChromeCookieSnapshot(
        httpx_cookies={"__Secure-next-auth.session-token": "tok", "other": "v"},
        google_session=True,
    )

    async def _fake_snapshot(_profile_dir: Path) -> ChromeCookieSnapshot:
        return snap

    monkeypatch.setattr("gflow_cli.auth.cookies.get_chrome_cookie_snapshot", _fake_snapshot)
    asyncio.run(client._preread_flow_session_cookies())  # noqa: SLF001
    assert client._preread_flow_cookies == {  # noqa: SLF001
        "__Secure-next-auth.session-token": "tok",
        "other": "v",
    }


def test_preread_flow_session_cookies_best_effort(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#222: a failing snapshot read must never break launch — pre-read stays {}."""
    import asyncio

    client = FlowApiClient(profile_dir=tmp_path, headless=True)

    async def _boom(_profile_dir: Path) -> object:
        raise PermissionError("cannot decrypt")

    monkeypatch.setattr("gflow_cli.auth.cookies.get_chrome_cookie_snapshot", _boom)
    asyncio.run(client._preread_flow_session_cookies())  # noqa: SLF001  (must not raise)
    assert client._preread_flow_cookies == {}  # noqa: SLF001
