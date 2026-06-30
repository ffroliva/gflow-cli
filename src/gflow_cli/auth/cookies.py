"""Chrome cookie extraction helpers for Flow auth verification."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.browser_manager import channel_for_profile
from gflow_cli.errors import SecurityError
from gflow_cli.paths import get_cookies_path

if TYPE_CHECKING:
    from pathlib import Path

logger = structlog.get_logger(__name__)

_FLOW_COOKIE_DOMAIN = "labs.google"
_FLOW_COOKIE_URL = f"https://{_FLOW_COOKIE_DOMAIN}"
_GOOGLE_SESSION_COOKIE = "SAPISID"


@dataclass(frozen=True)
class ChromeCookieSnapshot:
    """Cookies needed for the Flow session probe.

    `httpx_cookies` is intentionally limited to cookies scoped to labs.google.
    `google_session` is derived from the full local jar so verification can
    still distinguish "Google signed in, Flow app not signed in" without
    sending broader Google cookies to labs.google.
    """

    httpx_cookies: dict[str, str]
    google_session: bool


def _cookie_field(cookie: object, field: str) -> object:
    if isinstance(cookie, Mapping):
        cookie_mapping = cast("Mapping[str, object]", cookie)
        return cookie_mapping.get(field)
    return getattr(cookie, field, None)


def _is_flow_cookie(cookie: Any) -> bool:
    domain = _cookie_field(cookie, "domain")
    if not isinstance(domain, str):
        return False
    normalized = domain.lstrip(".").lower()
    return normalized == _FLOW_COOKIE_DOMAIN or normalized.endswith(f".{_FLOW_COOKIE_DOMAIN}")


def _name_value_cookies(cookies: Iterable[object], *, flow_only: bool) -> dict[str, str]:
    result: dict[str, str] = {}
    for cookie in cookies:
        if flow_only and not _is_flow_cookie(cookie):
            continue
        name = _cookie_field(cookie, "name")
        value = _cookie_field(cookie, "value")
        if isinstance(name, str) and name and value is not None:
            result[name] = str(value)
    return result


def _has_google_session_cookie(cookies: Iterable[object]) -> bool:
    return any(_cookie_field(cookie, "name") == _GOOGLE_SESSION_COOKIE for cookie in cookies)


def _get_chrome_cookies3(profile_dir: Path) -> ChromeCookieSnapshot:
    """Fast path: extract cookies directly from the SQLite store via browser_cookie3.

    Raises `PermissionError` on browser-cookie3 decryption failures so callers
    can fall back to the Playwright browser path. Other errors are not masked.

    On Windows, `browser-cookie3` may raise `RuntimeError` (specifically
    ``'Failed to decrypt the cipher text with DPAPI'``) when the DPAPI master
    key is inaccessible — e.g. running as a different user or in a sandboxed
    environment. This is treated the same as `BrowserCookieError` and triggers
    the Playwright fallback.

    On Linux, `browser-cookie3` reads the encryption key from the system keyring
    (secretstorage / kwallet), not from `Local State`. Passing a ``key_file`` on
    Linux can trigger the Windows DPAPI code path and raise ``KeyError:
    'encrypted_key'`` when ``Local State`` lacks an ``os_crypt.encrypted_key``
    entry (which is always the case on Linux). We therefore only pass
    ``key_file`` on Windows. Additionally, if the Linux keyring daemon is not
    reachable (e.g. right after Chrome exits, before the D-Bus session bus has
    settled), browser_cookie3 may raise ``KeyError`` from its ``__methods_map``
    lookup — we treat this as a decryption failure and fall back to Playwright.
    """
    import sys

    import browser_cookie3  # pyright: ignore[reportMissingTypeStubs]

    try:
        cookies_file = get_cookies_path(profile_dir)
    except FileNotFoundError:
        return ChromeCookieSnapshot(httpx_cookies={}, google_session=False)

    # Only pass key_file on Windows — browser_cookie3 only uses it on win32.
    # On Linux/macOS the key comes from the system keyring; passing Local State
    # there can hit the Windows DPAPI branch and raise KeyError.
    key_file = profile_dir.joinpath("Local State") if sys.platform == "win32" else None

    try:
        cookies = list(
            browser_cookie3.chrome(  # pyright: ignore[reportUnknownMemberType]
                cookie_file=cookies_file,
                key_file=key_file if (key_file and key_file.exists()) else None,
            )
        )
    except browser_cookie3.BrowserCookieError as exc:
        raise PermissionError(f"Failed to decrypt Chrome cookies: {exc}") from exc
    except RuntimeError as exc:
        # browser-cookie3 raises RuntimeError('Failed to decrypt the cipher text with DPAPI')
        # from _crypt_unprotect_data on Windows when the DPAPI master key is unavailable.
        # Treat it identically to BrowserCookieError so the Playwright fallback is used.
        if "dpapi" in str(exc).lower():
            raise PermissionError(f"Failed to decrypt Chrome cookies (DPAPI): {exc}") from exc
        raise
    except KeyError as exc:
        # On Linux, browser_cookie3 raises KeyError when the system keyring daemon
        # (secretstorage / kwallet) is unreachable or lacks the expected key entry.
        # This can happen transiently right after Chrome exits before the D-Bus
        # session has settled, or in minimal desktop environments without a keyring.
        # Treat it as a decryption failure so the Playwright fallback runs.
        raise PermissionError(f"Failed to read Chrome keyring key: {exc}") from exc

    return ChromeCookieSnapshot(
        httpx_cookies=_name_value_cookies(cookies, flow_only=True),
        google_session=_has_google_session_cookie(cookies),
    )


async def _get_chrome_cookies_playwright(profile_dir: Path) -> ChromeCookieSnapshot:
    """Slow path: extract cookies through a marker-gated Chrome Playwright context."""
    channel = channel_for_profile(profile_dir)
    if channel != "chrome":
        msg = (
            "Chrome-strategy marker missing for Playwright fallback. "
            "Re-run `gflow auth login --browser chrome` to rewrite the profile marker."
        )
        raise SecurityError(
            msg,
        )

    # Lazy import avoids strategies -> real_chrome -> verification import cycles.
    from .strategies import async_playwright

    async with async_playwright() as pw:
        ctx = await pw.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel=channel,
            headless=True,
            args=["--password-store=basic"],
        )
        try:
            all_cookies = await ctx.cookies()
            flow_cookies = await ctx.cookies([_FLOW_COOKIE_URL])
        finally:
            await ctx.close()

    return ChromeCookieSnapshot(
        httpx_cookies=_name_value_cookies(flow_cookies, flow_only=False),
        google_session=_has_google_session_cookie(all_cookies),
    )


async def get_chrome_cookie_snapshot(profile_dir: Path) -> ChromeCookieSnapshot:
    """Return the Chrome cookies needed by the Flow session probe.

    Strategy:
    1. browser_cookie3 direct SQLite read, with httpx cookies filtered to labs.google.
    2. marker-gated Playwright fallback when browser-cookie3 reports decryption failure.
    """
    try:
        return _get_chrome_cookies3(profile_dir=profile_dir)
    except PermissionError:
        logger.info("cookie_decryption_failed_falling_back_to_playwright", profile=str(profile_dir))
        return await _get_chrome_cookies_playwright(profile_dir=profile_dir)
