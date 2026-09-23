"""Flow app-session verification — the single source of truth for
"is this profile signed in to the Flow app?".

A profile can hold Google SSO cookies (e.g. SAPISID) without holding the Flow
app's NextAuth session (`__Secure-next-auth.session-token`). Only the latter
authenticates Flow's tRPC API. This module probes the same surface
`FlowApiClient` authenticates on — the NextAuth session endpoint — so a login
is never reported successful unless a real, usable Flow session exists.

See docs/superpowers/specs/2026-05-17-issue-15-auth-verification-fix-design.md
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, cast

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError
from gflow_cli.profile_lease import ProfileLease

from .cookies import get_chrome_cookie_snapshot

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path

    from playwright.async_api import BrowserContext

logger = structlog.get_logger(__name__)

# The NextAuth session endpoint. Expected authenticated 200 body shape:
#   {"user": {"name": ..., "email": ..., "image": ...}, "expires": "..."}
# An unauthenticated request returns `200 {}`. This contract is pinned by the
# AUTHENTICATED_BODY fixture in tests/auth/test_verification.py — if Google
# changes the shape, that test fails rather than the change going silent.
SESSION_API_URL = "https://labs.google/fx/api/auth/session"

# Per-request timeout for the session probe (milliseconds).
_REQUEST_TIMEOUT_MS = 15_000
# Total fetch attempts (initial + retries) before giving up.
_MAX_ATTEMPTS = 3
# HTTP statuses worth retrying — transient server-side conditions only.
_RETRYABLE_STATUSES = frozenset({429, 503, 504})
_SESSION_HEADERS = {
    "accept": "*/*",
    "cache-control": "no-cache",
    "pragma": "no-cache",
    "referer": "https://labs.google/fx/tools/flow",
}


class FlowSessionOutcome(StrEnum):
    """Mutually-exclusive results of probing a profile for a Flow session."""

    AUTHENTICATED = "authenticated"
    GOOGLE_SESSION_ONLY = "google_session_only"
    NO_SESSION = "no_session"
    VERIFICATION_ERROR = "verification_error"
    #: The profile's `.gflow_browser_strategy` marker is missing, so the
    #: Playwright cookie reader refuses to open it (#796). Distinct from
    #: VERIFICATION_ERROR because the cause is local profile state, not the
    #: network — and telling the user to "check connectivity" sends them
    #: looking in the wrong place, on a profile a failed login just rolled back.
    PROFILE_MARKER_MISSING = "profile_marker_missing"


_DETAIL_BY_OUTCOME: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.AUTHENTICATED: "Flow app session verified.",
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: "Signed in to Google, but not to the Flow app.",
    FlowSessionOutcome.NO_SESSION: "No sign-in detected.",
    FlowSessionOutcome.VERIFICATION_ERROR: "Could not verify the Flow session.",
    FlowSessionOutcome.PROFILE_MARKER_MISSING: (
        "This profile is missing its Chrome-strategy marker."
    ),
}


@dataclass(frozen=True)
class FlowSessionStatus:
    """The verdict of a Flow-session probe.

    `detail` is a derived property — always one of the fixed strings in
    `_DETAIL_BY_OUTCOME`, never built from response, cookie, or exception
    content. Deriving it (rather than storing a free string) makes it
    structurally impossible to leak a secret through this field.
    """

    outcome: FlowSessionOutcome
    user_email: str | None
    source: str  # caller-supplied log label ("chrome"/"internal"); never from response/cookie data

    @property
    def detail(self) -> str:
        return _DETAIL_BY_OUTCOME[self.outcome]

    @property
    def authenticated(self) -> bool:
        return self.outcome is FlowSessionOutcome.AUTHENTICATED


def _validate_profile_in_home(profile_dir: Path) -> None:
    """Raise SecurityError when a profile escapes GFLOW_CLI_HOME."""
    home = get_settings().home.resolve()
    try:
        profile_dir.resolve(strict=True).relative_to(home)
    except (ValueError, OSError):
        msg = f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME ({home})."
        raise SecurityError(
            msg,
        ) from None


def evaluate_session_response(
    status_code: int,
    body: str,
    *,
    google_session: bool,
    source: str,
) -> FlowSessionStatus:
    """Map a raw /api/auth/session response to a FlowSessionStatus.

    Pure and total: no I/O, no exceptions raised or used for control flow.
    Every (status_code, body) maps to exactly one outcome. Fail-closed — only
    a 200 carrying a usable `user.email` yields AUTHENTICATED. Only `email` is
    read; `name`, `image`, and `expires` are ignored, and the parsed dict is
    never retained beyond this function.
    """

    def _result(outcome: FlowSessionOutcome, email: str | None = None) -> FlowSessionStatus:
        return FlowSessionStatus(outcome=outcome, user_email=email, source=source)

    if status_code != 200:
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    try:
        parsed: Any = json.loads(body)
    except ValueError:
        # json.JSONDecodeError is a subclass of ValueError, so this catches both.
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    if not isinstance(parsed, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    parsed_dict = cast("dict[str, Any]", parsed)
    user = parsed_dict.get("user")
    if user is None or user == {}:
        # Authenticated-shaped endpoint reachable, but no Flow session.
        if google_session:
            return _result(FlowSessionOutcome.GOOGLE_SESSION_ONLY)
        return _result(FlowSessionOutcome.NO_SESSION)

    if not isinstance(user, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    user_dict = cast("dict[str, Any]", user)
    email = user_dict.get("email")
    if isinstance(email, str) and email:
        return _result(FlowSessionOutcome.AUTHENTICATED, email)

    # `user` present but no usable email — unexpected shape (see spec §10).
    return _result(FlowSessionOutcome.VERIFICATION_ERROR)


async def _fetch_session(ctx: BrowserContext) -> tuple[int, str]:
    """Fetch /api/auth/session, retrying transient failures.

    Returns the final (status_code, body). Makes up to `_MAX_ATTEMPTS`
    attempts; an attempt is retried only on a network/timeout error or an
    HTTP status in `_RETRYABLE_STATUSES`, with exponential backoff (1s, 2s;
    capped at 8s). Re-raises the last error if no attempt produced a response.

    An explicit loop (rather than a `tenacity` decorator) is used so the final
    `(status_code, body)` survives — the caller logs the real status code as a
    durability signal (spec §10). The spec (§4.1) sanctions either form.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await ctx.request.get(SESSION_API_URL, timeout=_REQUEST_TIMEOUT_MS)
            body = await resp.text()
        # A network/timeout error is retried below, or re-raised on the final attempt.
        except Exception as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                raise
        else:
            if resp.status not in _RETRYABLE_STATUSES or attempt == _MAX_ATTEMPTS:
                return resp.status, body
        await asyncio.sleep(float(min(2 ** (attempt - 1), 8)))
    # Unreachable — the loop always returns or raises by the final attempt.
    raise last_exc or RuntimeError("session probe produced no response")


async def _fetch_session_httpx(client: Any) -> tuple[int, str]:
    """Fetch /api/auth/session via httpx, retrying transient failures.

    Mirrors `_fetch_session` exactly — same attempt count, same retryable
    statuses, same exponential backoff — so the httpx fast path and the
    Playwright path have identical durability characteristics. A single
    transient 429/503/504 or network blip will not reject a valid login.
    """
    last_exc: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await client.get(SESSION_API_URL)
            status_code: int = resp.status_code
            body: str = resp.text
        except Exception as exc:
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                raise
        else:
            if status_code not in _RETRYABLE_STATUSES or attempt == _MAX_ATTEMPTS:
                return status_code, body
        await asyncio.sleep(float(min(2 ** (attempt - 1), 8)))
    # Unreachable — the loop always returns or raises by the final attempt.
    raise last_exc or RuntimeError("session probe produced no response")


async def fetch_flow_session_httpx(
    profile_dir: Path,
) -> tuple[int, str, bool]:
    """Read a profile's cookies and probe Flow's session endpoint with retries.

    The client created here is scoped to ``labs.google`` and is closed before
    the caller receives the response. Callers must use a separate client for
    any other host so Flow session cookies cannot cross an origin boundary.
    """
    _validate_profile_in_home(profile_dir)

    import httpx

    cookie_snapshot = await get_chrome_cookie_snapshot(profile_dir)
    async with httpx.AsyncClient(
        cookies=cookie_snapshot.httpx_cookies,
        headers=_SESSION_HEADERS,
        follow_redirects=False,
        timeout=15.0,
    ) as client:
        status_code, body = await _fetch_session_httpx(client)
    return status_code, body, cookie_snapshot.google_session


async def verify_flow_session(
    profile_dir: Path,
    *,
    channel: str | None = "chrome",
    source: str = "chrome",
) -> FlowSessionStatus:
    """Headlessly probe `profile_dir` for a usable Flow app session.

    NOTE: since PR #168, `verify_flow_profile` is the production entry point
    (`RealChromeStrategy.login` calls it): it reads cookies straight from
    Chrome's SQLite store via `browser_cookie3` and only launches Playwright
    when that decryption fails. This function is the original full-Playwright
    probe, retained for the tests and as a standalone verification primitive.

    Launches a headless persistent context on the profile, reads cookies, and
    calls the NextAuth session endpoint. Fail-closed: any failure — boundary
    violation aside — yields VERIFICATION_ERROR, never AUTHENTICATED.

    Precondition: `profile_dir` must resolve inside GFLOW_CLI_HOME. The check
    uses `strict=True` (the directory exists by the time verification runs);
    `RealChromeStrategy.login`'s own pre-`mkdir` check deliberately stays
    `strict=False` — see the design spec §4.2.
    """
    _validate_profile_in_home(profile_dir)

    # Lazy import — a top-level `from .strategies import ...` would create the
    # cycle strategies -> real_chrome -> verification -> strategies.
    from .strategies import async_playwright

    status_code: int
    body: str
    try:
        from gflow_cli.browser_manager import ensure_profile_engine_compatible

        # Own the profile for this headless probe context (D3). Lease is the
        # OUTER context so it releases only after the driver stops. Contention
        # raises ProfileLockedError before Chrome launches; the fail-closed
        # wrapper below maps it (like any probe failure) to VERIFICATION_ERROR.
        async with ProfileLease(profile_dir):
            # #477 guard AFTER the lease (a pre-wait check would validate a
            # 'Last Version' the holder rewrites as it releases): the probe
            # must not trigger downgrade cleanup either. Inside the
            # fail-closed wrapper, so the refusal maps to VERIFICATION_ERROR.
            ensure_profile_engine_compatible(profile_dir, channel)
            async with async_playwright() as pw:
                ctx = await pw.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    channel=channel,
                    headless=True,
                    args=["--password-store=basic"],
                )
                try:
                    cookies = await ctx.cookies()
                    google_session = any(c.get("name") == "SAPISID" for c in cookies)
                    status_code, body = await _fetch_session(ctx)
                finally:
                    await ctx.close()
    # Fail-closed: any failure here yields VERIFICATION_ERROR, never AUTHENTICATED.
    except Exception as exc:
        logger.warning("auth_flow_session_probe_error", source=source, error=type(exc).__name__)
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.VERIFICATION_ERROR,
            user_email=None,
            source=source,
        )

    result = evaluate_session_response(
        status_code,
        body,
        google_session=google_session,
        source=source,
    )
    if result.outcome is FlowSessionOutcome.VERIFICATION_ERROR:
        # Observable durability signal — distinguishes a moved/changed endpoint
        # from a flaky link. The status code is safe to log; the body is not.
        logger.warning(
            "auth_flow_session_unexpected_response",
            source=source,
            status_code=status_code,
        )
    return result


#: The migrated-host fallback probe. `myaccount` is the oracle because it is
#: server-side: a revoked session is redirected off it, which a client-rendered page
#: cannot attest to.
_MYACCOUNT_ORIGIN = "https://myaccount.google.com"
#: Any domain, not just gmail.com — an `@gmail.com`-only pattern declined every
#: Google Workspace account (dev@axelate.io, user@mycompany.com, user@googlemail.com
#: all failed to match), leaving #791 open for them with no signal it had refused.
#:
#: Split in two, and anchored on the `@`, because one combined pattern was
#: quadratic (#852). `[\w.+-]+@…` retries from every start position and rescans
#: its run before failing to find an `@`, so an unbroken run of characters that
#: class accepts costs O(n²) — measured 12.1 s for 40 000 of them, and the class
#: covers the whole URL-safe base64 alphabet, which a Google page is full of.
#: Leading with the literal `@` lets CPython's `re` use its literal-prefix fast
#: search, so the work becomes proportional to the number of `@` in the document.
_DOMAIN_RE = re.compile(r"@[\w.-]+\.[A-Za-z]{2,}")
#: The non-alphanumeric half of `[\w.+-]`. Python's `\w` is exactly
#: "`str.isalnum()` or underscore" (its own docs say so), so the two together
#: reproduce that class character for character.
#:
#: Spelled out rather than left as a regex because `[\w.+-]+\Z` is itself
#: super-linear (Sonar python:S8786) — the window below bounds it in practice,
#: but a reader cannot see that from the pattern, and neither can a scanner. A
#: backwards scan is O(1) per character and visibly so.
_LOCAL_PART_PUNCT = "_.+-"
#: RFC 5321 caps a local part at 64 octets. It is also what keeps the scan
#: linear: the window is constant, so each `@` costs the same regardless of the
#: page. ponytail: a longer local part is truncated rather than dropped — raise
#: this if a real address is ever found past it.
_MAX_LOCAL_PART = 64
_MYACCOUNT_URL = f"{_MYACCOUNT_ORIGIN}/?hl=en"
#: 15 s was measured too tight: the probe answers in 2.6-7.2 s idle but timed out under
#: browser contention during a 10-profile sweep.
_MIGRATED_PROBE_TIMEOUT_MS = 30_000


def find_emails(body: str) -> list[str]:
    """Every address in `body`, in document order, in one linear pass.

    Behaviourally the same as the single pattern it replaces for anything that
    looks like an address; see `_DOMAIN_RE` for why that pattern could not stay.
    """
    found: list[str] = []
    consumed = 0  # end of the last address emitted — matches never overlap
    for match in _DOMAIN_RE.finditer(body):
        at = match.start()
        # `findall` resumed scanning at the end of its previous match, so a local
        # part could never reach back into one. Two `@` within 64 characters of
        # each other is the only shape where that shows, and fuzzing against the
        # old pattern is what turned it up: 2 differences in 4 000 random bodies.
        floor = max(consumed, at - _MAX_LOCAL_PART)
        start = at
        while start > floor and (body[start - 1].isalnum() or body[start - 1] in _LOCAL_PART_PUNCT):
            start -= 1
        if start < at:  # an `@` with no local part in front of it is not an address
            found.append(body[start:at] + match.group())
            consumed = match.end()
    return found


def has_migrated_app_session(cookies: Iterable[Mapping[str, Any]]) -> bool:
    """Both halves of a migrated Flow session are present in this jar.

    The `.google.com` SSO cookie alone means "signed in to Google"; the
    `flow.google.com` app-session cookie alone means nothing without it. Only
    the pair says an account has a session on the host that serves the app.

    This is a NECESSARY condition, never a sufficient one — a cookie on disk
    outlives a password change or a "sign out of all devices". Everything that
    decides authentication goes on to ask a server (`_verify_migrated_host_fallback`
    below). What the pair IS good for on its own is deciding when to stop
    *waiting*: the labs oracle never answers for these accounts, so without some
    other signal the login poll runs to its deadline (#849).
    """
    names = {(c.get("name"), c.get("domain", "")) for c in cookies}
    has_sso = any(n == "SAPISID" and "google.com" in d for n, d in names)
    has_flow_osid = any(n in ("__Secure-OSID", "OSID") and "flow.google.com" in d for n, d in names)
    return has_sso and has_flow_osid


def _origin_of(url: str) -> str:
    """Scheme + host only — never log a URL with a query string from an auth page."""
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme else "(unparseable)"


async def _verify_migrated_host_fallback(
    profile_dir: Path, source: str
) -> FlowSessionStatus | None:
    """ai4u delta (2026-09-12) — migrated-host session oracle.

    For accounts Google has moved to flow.google.com, the labs.google NextAuth
    session is never minted (the labs app hands off immediately; observed:
    47 cookies, zero on labs.google, but OSID/__Secure-OSID present on
    .flow.google.com and SAPISID/SID on .google.com). The labs oracle then
    reports GOOGLE_SESSION_ONLY although the workspace is fully usable —
    gflow's own migrated-host driver authenticates via exactly these cookies
    (spike 2026-09-05-migrated-host-wire-protocol: ".google.com SSO cookies
    already in the profile authenticate the host").

    Fail-closed: upgrades GOOGLE_SESSION_ONLY to AUTHENTICATED only when BOTH
    the .google.com SSO cookie (SAPISID) AND the flow.google.com app session
    cookie (__Secure-OSID/OSID) are present, AND the account email resolves
    from myaccount.google.com. Anything else returns None (caller keeps the
    original outcome). Never touches other outcomes.
    """
    from .strategies import async_playwright

    try:
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=True,
                args=["--password-store=basic"],
            )
            try:
                if not has_migrated_app_session(await ctx.cookies()):
                    return None
                resp = await ctx.request.get(_MYACCOUNT_URL, timeout=_MIGRATED_PROBE_TIMEOUT_MS)
                page_body = await resp.text()
                final_url = str(resp.url)
            finally:
                await ctx.close()
    except Exception as exc:
        logger.warning(
            "auth_migrated_fallback_probe_error", source=source, error=type(exc).__name__
        )
        return None

    # THE auth signal, and it is server-attested: a dead or revoked session cannot stay
    # on myaccount — Google redirects it to the sign-in page. Checking where we landed
    # is therefore the decision; the address below is only a label.
    #
    # The address must NOT be the decision. It used to be, via an `@gmail.com`-only
    # regex, which silently declined every Google Workspace account (measured:
    # `dev@axelate.io`, `user@mycompany.com`, even `user@googlemail.com` all failed to
    # match) — so #791 stayed open for them with no signal that the fallback had
    # refused. Widening that regex alone would have been worse: any address on a
    # signed-out page would then read as proof of a session.
    if not final_url.startswith(_MYACCOUNT_ORIGIN):
        logger.warning(
            "auth_migrated_fallback_not_signed_in", source=source, landed=_origin_of(final_url)
        )
        return None

    # Most frequent, not first. Measured on a live myaccount response (1.28 MB,
    # 2026-09-16): 9 matches, all 9 the account's own address, 0 competing candidates —
    # so first-match happened to be right. It is right by luck, though: one support or
    # noreply address rendered above the account's would silently relabel the user.
    # Counting costs nothing and removes the coin flip. Ties keep document order, so
    # the single-candidate case is unchanged.
    found = find_emails(page_body)
    email = Counter(found).most_common(1)[0][0] if found else None
    return FlowSessionStatus(
        outcome=FlowSessionOutcome.AUTHENTICATED,
        # Absent when the page shape changes — the session is still proven by the URL,
        # so a missing label must not cost the user their login.
        user_email=email,
        source=source,
    )


async def verify_flow_profile(
    profile_dir: Path,
    *,
    source: str = "chrome",
) -> FlowSessionStatus:
    """Probe `profile_dir` for a usable Flow app session via the fast httpx path.

    Reads Chrome cookies directly from the SQLite store using browser_cookie3
    (falling back to a marker-gated Playwright context on decryption failure),
    then calls the NextAuth session endpoint with up to `_MAX_ATTEMPTS` attempts.
    Fail-closed: any failure yields VERIFICATION_ERROR, never AUTHENTICATED.

    ai4u delta (2026-09-12): when the labs oracle yields GOOGLE_SESSION_ONLY,
    a migrated-host fallback probe runs (see _verify_migrated_host_fallback).
    """
    _validate_profile_in_home(profile_dir)

    status_code: int
    body: str
    try:
        status_code, body, google_session = await fetch_flow_session_httpx(profile_dir)

    # #796: the marker gate is local profile state, not a network fault. It was
    # flattened into VERIFICATION_ERROR, whose remediation says "check network
    # connectivity" — wrong advice, and specifically wrong on a profile whose
    # marker a failed login had just rolled back (real_chrome.py:433-434).
    # `_validate_profile_in_home` raises SecurityError too, but above the try, so
    # a path violation still propagates instead of being classified here.
    except SecurityError:
        logger.warning("auth_profile_marker_missing", source=source)
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.PROFILE_MARKER_MISSING,
            user_email=None,
            source=source,
        )

    # Fail-closed: any failure here yields VERIFICATION_ERROR, never AUTHENTICATED.
    except Exception as exc:
        logger.warning("auth_flow_session_probe_error", source=source, error=type(exc).__name__)
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.VERIFICATION_ERROR,
            user_email=None,
            source=source,
        )

    result = evaluate_session_response(
        status_code,
        body,
        google_session=google_session,
        source=source,
    )
    if result.outcome is FlowSessionOutcome.VERIFICATION_ERROR:
        # Observable durability signal — distinguishes a moved/changed endpoint
        # from a flaky link. The status code is safe to log; the body is not.
        logger.warning(
            "auth_flow_session_unexpected_response",
            source=source,
            status_code=status_code,
        )
    if result.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY:
        # ai4u delta (2026-09-12): migrated-host fallback — see
        # _verify_migrated_host_fallback docstring. Fail-closed.
        fallback = await _verify_migrated_host_fallback(profile_dir, source)
        if fallback is not None:
            logger.warning(
                "auth_migrated_host_fallback_authenticated",
                source=source,
                detail="labs NextAuth absent; flow.google.com OSID session + SSO cookies present",
            )
            return fallback
    return result
