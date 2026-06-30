from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.auth.cookies import _get_chrome_cookies3


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
