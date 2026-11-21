from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from gflow_cli.auth import cookies as cookies_mod
from gflow_cli.auth.cookies import _get_chrome_cookies3, _get_chrome_cookies_playwright


def _make_profile_with_cookies(tmp_path: Path) -> Path:
    """Minimal Chrome profile dir with a Cookies file so get_cookies_path
    resolves and execution reaches the browser_cookie3 call."""
    network = tmp_path / "profile" / "Default" / "Network"
    network.mkdir(parents=True)
    (network / "Cookies").write_bytes(b"")
    return tmp_path / "profile"


def test_get_chrome_cookies3_maps_keyerror_to_permissionerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A KeyError from browser_cookie3 (Linux keyring daemon unreachable) must
    surface as PermissionError so callers fall back to the Playwright path —
    not crash with an opaque KeyError."""
    profile = _make_profile_with_cookies(tmp_path)

    def _raise_keyerror(**_: object) -> object:
        raise KeyError("encrypted_key")

    monkeypatch.setattr("browser_cookie3.chrome", _raise_keyerror)

    with pytest.raises(PermissionError, match="keyring key"):
        _get_chrome_cookies3(profile)


def test_get_chrome_cookies3_maps_browsercookieerror_to_permissionerror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Decryption failures surface as PermissionError — the signal #222
    diagnostics rely on to detect a locked password store and fall back."""
    import browser_cookie3

    profile = _make_profile_with_cookies(tmp_path)

    def _raise(**_: object) -> object:
        raise browser_cookie3.BrowserCookieError("decrypt failed")

    monkeypatch.setattr("browser_cookie3.chrome", _raise)

    with pytest.raises(PermissionError, match="decrypt"):
        _get_chrome_cookies3(profile)


# --- issue #222 / #230: /fx-scoped session token must survive the cookie read ---


@pytest.mark.asyncio
async def test_playwright_read_keeps_fx_scoped_session_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for #230: the Playwright cookie read must return the
    ``/fx``-scoped ``__Secure-next-auth.session-token``.

    Falsifiable by construction: the mocked context returns the session token
    ONLY from the full jar (``ctx.cookies()``), NOT from the path-``/`` URL
    filter (``ctx.cookies([url])``). The pre-#230 code read the URL-filtered
    ``flow_cookies`` and would drop the token (this assertion fails); the fix
    reads the full jar filtered by domain and keeps it (this assertion passes).
    """
    profile = tmp_path / "profile"
    profile.mkdir()

    full_jar = [
        {"name": "SAPISID", "value": "g", "domain": ".google.com"},
        {"name": "__Secure-next-auth.session-token", "value": "flow", "domain": "labs.google"},
    ]
    path_root_only = [{"name": "SAPISID", "value": "g", "domain": ".google.com"}]

    async def _cookies(*args: object) -> list[dict[str, str]]:
        # ctx.cookies([url]) → path=/ filtered (drops the /fx token);
        # ctx.cookies() → the full jar.
        return path_root_only if args and args[0] else full_jar

    mock_ctx = MagicMock()
    mock_ctx.cookies = AsyncMock(side_effect=_cookies)
    mock_ctx.close = AsyncMock()

    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "gflow_cli.auth.strategies.async_playwright", MagicMock(return_value=mock_cm)
    )
    monkeypatch.setattr(cookies_mod, "channel_for_profile", lambda _pd: "chrome")

    snapshot = await _get_chrome_cookies_playwright(profile)

    assert "__Secure-next-auth.session-token" in snapshot.httpx_cookies
    # SAPISID is a .google.com cookie — excluded by the labs.google domain filter.
    assert "SAPISID" not in snapshot.httpx_cookies
    # google_session is still derived from the full jar (SAPISID present there).
    assert snapshot.google_session is True


# ---------------------------------------------------------------------------
# D3 — profile-lease ownership around the Playwright cookie-read fallback
# ---------------------------------------------------------------------------


def _record_lease_events(monkeypatch: pytest.MonkeyPatch, events: list[str]) -> None:
    from gflow_cli.profile_lease import ProfileLease

    def acq(self: ProfileLease) -> ProfileLease:
        events.append("acquire")
        return self

    def rel(self: ProfileLease) -> None:
        events.append("release")

    monkeypatch.setattr(ProfileLease, "acquire", acq)
    monkeypatch.setattr(ProfileLease, "release", rel)


@pytest.mark.asyncio
async def test_playwright_cookie_read_wraps_launch_in_profile_lease(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The marker-gated Playwright cookie-read owns the profile: acquire before
    launch, release after the context closes (D3)."""
    profile = tmp_path / "profile"
    profile.mkdir()

    events: list[str] = []
    _record_lease_events(monkeypatch, events)

    mock_ctx = MagicMock()
    mock_ctx.cookies = AsyncMock(return_value=[])
    mock_ctx.close = AsyncMock()

    async def _launch(*_a: object, **_k: object) -> MagicMock:
        events.append("launch")
        return mock_ctx

    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context = AsyncMock(side_effect=_launch)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    monkeypatch.setattr(
        "gflow_cli.auth.strategies.async_playwright", MagicMock(return_value=mock_cm)
    )
    monkeypatch.setattr(cookies_mod, "channel_for_profile", lambda _pd: "chrome")

    await _get_chrome_cookies_playwright(profile)
    assert events == ["acquire", "launch", "release"]


@pytest.mark.asyncio
async def test_playwright_read_on_a_profile_held_by_chrome_is_a_profile_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#1059: Chrome's 'already in use' refusal is ProfileLockedError, not a raw error."""
    from gflow_cli.errors import ProfileLockedError

    mock_pw = MagicMock()
    mock_pw.chromium.launch_persistent_context = AsyncMock(
        side_effect=RuntimeError(
            "Opening in existing browser session. This usually means that the profile "
            "is already in use by another instance of Chromium."
        )
    )
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw)
    mock_cm.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr(
        "gflow_cli.auth.strategies.async_playwright", MagicMock(return_value=mock_cm)
    )
    monkeypatch.setattr(cookies_mod, "channel_for_profile", lambda _pd: "chrome")

    with pytest.raises(ProfileLockedError):
        await _get_chrome_cookies_playwright(_make_profile_with_cookies(tmp_path))


# --- macOS: browser_cookie3 asks the Keychain, then cannot decrypt anyway ---


@pytest.mark.asyncio
async def test_snapshot_on_macos_skips_browser_cookie3_keychain_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On macOS the browser_cookie3 read fetches "Chrome Safe Storage" from the
    Keychain (a password prompt per call) and still fails to decrypt the jar,
    so every call ends in the Playwright read. Go there directly."""
    profile = _make_profile_with_cookies(tmp_path)
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)
    direct = MagicMock(side_effect=AssertionError("browser_cookie3 read on macOS"))
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies3", direct)
    expected = cookies_mod.ChromeCookieSnapshot(httpx_cookies={"a": "b"}, google_session=True)
    browser = AsyncMock(return_value=expected)
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    assert await cookies_mod.get_chrome_cookie_snapshot(profile) is expected
    direct.assert_not_called()
    browser.assert_awaited_once_with(profile_dir=profile)


@pytest.mark.asyncio
async def test_snapshot_off_macos_still_tries_browser_cookie3_first(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _make_profile_with_cookies(tmp_path)
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", False)
    expected = cookies_mod.ChromeCookieSnapshot(httpx_cookies={}, google_session=False)
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies3", MagicMock(return_value=expected))
    browser = AsyncMock()
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    assert await cookies_mod.get_chrome_cookie_snapshot(profile) is expected
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_on_macos_keeps_a_corrupt_jar_away_from_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#796 on the macOS route: a jar that is not SQLite is reported as such,
    and Chrome (which would raze it) never opens the profile."""
    import sqlite3

    profile = _make_profile_with_cookies(tmp_path)
    (profile / "Default" / "Network" / "Cookies").write_bytes(b"x" * 32)
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)
    browser = AsyncMock()
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    with pytest.raises(sqlite3.DatabaseError):
        await cookies_mod.get_chrome_cookie_snapshot(profile)
    browser.assert_not_awaited()


def _write_cookie_db(path: Path) -> None:
    import sqlite3

    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT)")
    conn.execute("INSERT INTO cookies VALUES ('labs.google', 'a', 'b')")
    conn.commit()
    conn.close()


@pytest.mark.asyncio
async def test_snapshot_on_macos_keeps_a_jar_with_a_corrupt_body_away_from_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#796: a valid SQLite header over a malformed body is still corrupt. The
    browser_cookie3 read raised before Chrome launched; the macOS route must too."""
    import sqlite3

    profile = _make_profile_with_cookies(tmp_path)
    jar = profile / "Default" / "Network" / "Cookies"
    _write_cookie_db(jar)
    data = bytearray(jar.read_bytes())
    data[100:] = b"\xff" * (len(data) - 100)  # keep the header, wreck every page
    jar.write_bytes(bytes(data))
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)
    browser = AsyncMock()
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    with pytest.raises(sqlite3.DatabaseError):
        await cookies_mod.get_chrome_cookie_snapshot(profile)
    browser.assert_not_awaited()


@pytest.mark.asyncio
async def test_snapshot_on_macos_reads_a_valid_jar_through_chrome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = _make_profile_with_cookies(tmp_path)
    _write_cookie_db(profile / "Default" / "Network" / "Cookies")
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)
    expected = cookies_mod.ChromeCookieSnapshot(httpx_cookies={"a": "b"}, google_session=True)
    browser = AsyncMock(return_value=expected)
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    assert await cookies_mod.get_chrome_cookie_snapshot(profile) is expected


@pytest.mark.asyncio
async def test_snapshot_on_macos_falls_back_to_chrome_when_the_jar_cannot_be_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unreadable jar fell back to the Playwright read before; it must not
    surface as a verification error ("check network") on macOS."""
    profile = _make_profile_with_cookies(tmp_path)
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)

    def _denied(*_args: object, **_kwargs: object) -> object:
        raise PermissionError("denied")

    monkeypatch.setattr("builtins.open", _denied)
    expected = cookies_mod.ChromeCookieSnapshot(httpx_cookies={}, google_session=False)
    browser = AsyncMock(return_value=expected)
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    assert await cookies_mod.get_chrome_cookie_snapshot(profile) is expected
    browser.assert_awaited_once_with(profile_dir=profile)


@pytest.mark.asyncio
async def test_snapshot_on_macos_without_a_jar_is_an_empty_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "profile"
    (profile / "Default").mkdir(parents=True)
    monkeypatch.setattr(cookies_mod, "_SKIP_DIRECT_READ", True)
    browser = AsyncMock()
    monkeypatch.setattr(cookies_mod, "_get_chrome_cookies_playwright", browser)

    snapshot = await cookies_mod.get_chrome_cookie_snapshot(profile)

    assert snapshot == cookies_mod.ChromeCookieSnapshot(httpx_cookies={}, google_session=False)
    browser.assert_not_awaited()
