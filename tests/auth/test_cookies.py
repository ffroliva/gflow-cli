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
