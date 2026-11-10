"""The wire's cookie read: domain-scoped, explicit jar, never the Playwright or VSS fallback.

SCENARIO rows 5, 6, 9, 19, 20 (#1229). Spike E1: while Chrome runs, the jar is locked and
browser_cookie3 walks on to its shadow-copy (VSS) method; that must never run here.
"""

from __future__ import annotations

import http.cookiejar
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from gflow_cli.auth import cookies
from gflow_cli.auth.cookies import CookieStoreUnreadable, read_cookies_for
from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError


def _cookie(name: str, domain: str, *, path: str = "/", expires: int | None = None) -> Any:
    return http.cookiejar.Cookie(
        0,
        name,
        f"fake-{name}-{domain}",
        None,
        False,
        domain,
        True,
        domain.startswith("."),
        path,
        True,
        True,
        expires,
        False,
        None,
        None,
        {},
    )


@pytest.fixture
def profile() -> Path:
    path = get_settings().home / "profiles" / "profile_x"
    (path / "Default" / "Network").mkdir(parents=True)
    (path / "Default" / "Network" / "Cookies").write_bytes(b"")
    (path / "Local State").write_text("{}", encoding="utf-8")
    return path


@pytest.fixture
def jar(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace browser_cookie3.chrome with a spy over the spike's inventory shape."""
    import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]

    calls: list[dict[str, Any]] = []
    inventory = [
        _cookie("SID", ".google.com"),
        _cookie("__Secure-3PSID", ".google.com"),
        _cookie("OSID", "flow.google.com"),
        _cookie("_ga_X2GNH8R5NS", "flow.google.com"),
        _cookie("_ga_X2GNH8R5NS", ".flow.google.com"),  # same name, two domains: both sent
        _cookie("OSID", "accounts.google.com"),  # host-only elsewhere: never sent
        _cookie("OSID", "myaccount.google.com"),
        _cookie("LSID", "accounts.google.com"),
        _cookie("__Secure-next-auth.session-token", "labs.google"),
        _cookie("SCOPED", ".google.com", path="/fx"),  # a path that "/" does not match
        _cookie("OLD", ".google.com", expires=1),  # expired
        _cookie("EVIL", ".notgoogle.com"),
        _cookie("EVIL2", "evilflow.google.com"),
    ]

    def chrome(**kwargs: Any) -> list[Any]:
        calls.append(kwargs)
        return inventory

    monkeypatch.setattr(browser_cookie3, "chrome", chrome)
    return calls


def test_keeps_exactly_what_a_browser_sends_to_flow(
    profile: Path, jar: list[dict[str, Any]]
) -> None:
    pairs = read_cookies_for(profile)

    assert sorted(pairs) == sorted(
        [
            ("SID", "fake-SID-.google.com"),
            ("__Secure-3PSID", "fake-__Secure-3PSID-.google.com"),
            ("OSID", "fake-OSID-flow.google.com"),
            ("_ga_X2GNH8R5NS", "fake-_ga_X2GNH8R5NS-flow.google.com"),
            ("_ga_X2GNH8R5NS", "fake-_ga_X2GNH8R5NS-.flow.google.com"),
        ]
    )


def test_the_jar_is_always_named_explicitly(profile: Path, jar: list[dict[str, Any]]) -> None:
    """Row 20: `cookie_file=None` would read the user's own personal Chrome jar."""
    read_cookies_for(profile)

    (call,) = jar
    assert call["cookie_file"] == profile / "Default" / "Network" / "Cookies"
    if sys.platform == "win32":
        assert call["key_file"] == profile / "Local State"


def test_a_profile_outside_home_is_refused_before_any_read(
    tmp_path: Path, jar: list[dict[str, Any]]
) -> None:
    """Row 19."""
    outside = tmp_path / "elsewhere"
    (outside / "Default").mkdir(parents=True)
    (outside / "Default" / "Cookies").write_bytes(b"")

    with pytest.raises(SecurityError):
        read_cookies_for(outside)
    assert jar == []


def test_a_missing_cookie_file_is_unreadable(jar: list[dict[str, Any]]) -> None:
    """Row 9: a profile that never logged in."""
    empty = get_settings().home / "profiles" / "profile_new"
    empty.mkdir(parents=True)

    with pytest.raises(CookieStoreUnreadable):
        read_cookies_for(empty)
    assert jar == []


def test_a_missing_local_state_on_windows_is_unreadable(
    profile: Path, jar: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """key_file=None on win32 makes browser_cookie3 use the user's own Chrome key."""
    monkeypatch.setattr(sys, "platform", "win32")
    (profile / "Local State").unlink()

    with pytest.raises(CookieStoreUnreadable):
        read_cookies_for(profile)
    assert jar == []


def test_a_locked_jar_is_unreadable_before_browser_cookie3_runs(
    profile: Path, jar: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Row 5: Chrome holds the jar exclusively (errno 13 / WinError 32 in spike E1)."""

    def locked(*_a: Any, **_k: Any) -> Any:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(cookies, "open", locked, raising=False)

    with pytest.raises(CookieStoreUnreadable):
        read_cookies_for(profile)
    assert jar == []


@pytest.mark.parametrize(
    "error",
    [
        RuntimeError("Failed to decrypt the cipher text with DPAPI"),
        KeyError("encrypted_key"),
        ValueError("v20 app-bound"),
        PermissionError("This operation requires admin."),
        OSError("unable to open database file"),
    ],
)
def test_every_browser_cookie3_failure_is_unreadable(
    profile: Path, monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    """Row 6, and the Playwright fallback is never reached."""
    import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]

    def chrome(**_k: Any) -> Any:
        raise error

    async def playwright(_p: Path) -> Any:
        raise AssertionError("the wire must never launch Chrome to read cookies")

    monkeypatch.setattr(browser_cookie3, "chrome", chrome)
    monkeypatch.setattr(cookies, "_get_chrome_cookies_playwright", playwright)

    with pytest.raises(CookieStoreUnreadable) as exc:
        read_cookies_for(profile)
    assert str(exc.value) == type(error).__name__  # the class name, never the message


def test_the_shadow_copy_fallback_never_runs(
    profile: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Spike E1: past the pre-check, Chrome can still grab the jar. browser_cookie3 then
    tries read-only, then a copy, then a VSS snapshot. Run its REAL connection cascade on
    a jar it cannot open as a database and prove the snapshot is never attempted."""
    import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]

    snapshots: list[Any] = []
    fake_vss = types.SimpleNamespace(shadow_copy=lambda *a: snapshots.append(a))
    monkeypatch.setattr(browser_cookie3, "shadowcopy", fake_vss)
    (profile / "Default" / "Network" / "Cookies").write_bytes(b"not a sqlite database" * 64)

    def chrome(**kwargs: Any) -> Any:
        conn = browser_cookie3._DatabaseConnetion(kwargs["cookie_file"])  # pyright: ignore
        try:
            return conn.get_connection()
        finally:
            conn.close()

    monkeypatch.setattr(browser_cookie3, "chrome", chrome)

    with pytest.raises(CookieStoreUnreadable):
        read_cookies_for(profile)
    assert snapshots == []
    assert browser_cookie3.shadowcopy is fake_vss  # restored for every other caller
