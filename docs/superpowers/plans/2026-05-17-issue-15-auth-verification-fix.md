# Issue #15 — Auth-Layer Verification Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gflow auth login` report success only when the profile holds a real, usable Flow app session — fixing issue #15, where a `SAPISID`-only check green-lights profiles that 401 on the first API call.

**Architecture:** A new `auth/verification.py` module owns one question — "is this profile signed in to the Flow app?" — answered by probing the NextAuth session endpoint (`/api/auth/session`), the same surface `FlowApiClient` authenticates on. A pure `evaluate_session_response` function holds the decision logic; an async `verify_flow_session` wrapper does the headless browser I/O. Both auth strategies adopt it for one consistent, fail-closed definition of "signed in".

**Tech Stack:** Python 3.11+, Playwright (async), `structlog`, `pytest` + `pytest-asyncio`, `uv`. Strict typing (`pyright`), `ruff` lint/format.

**Spec:** `docs/superpowers/specs/2026-05-17-issue-15-auth-verification-fix-design.md` (Rev 2).

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `src/gflow_cli/auth/verification.py` | The Flow-session verification module — enum, status dataclass, pure evaluator, async probe. | Create |
| `src/gflow_cli/auth/real_chrome.py` | `RealChromeStrategy` — opens Chrome at the Flow URL; verifies via `verify_flow_session` after close. | Modify |
| `src/gflow_cli/auth/internal_chromium.py` | `InternalChromiumStrategy` — live-polls `/api/auth/session` instead of UI-text scraping. | Modify |
| `src/gflow_cli/errors.py` | Refresh the now-misleading `AuthMissingError` docstring. | Modify |
| `tests/auth/test_verification.py` | Unit tests for `verification.py` (pure core + async probe). | Create |
| `tests/auth/strategies/test_strategies.py` | Strategy tests — updated for the new verification path. | Modify |
| `KNOWN_ISSUES.md` | Record the `/api/auth/session` endpoint as an external coupling. | Modify |

**Why this split:** `verification.py` is the single source of truth for "signed in". The pure `evaluate_session_response` is the testable decision core (zero mocks); `verify_flow_session` is the thin I/O shell. Both strategies depend on it — no duplicated, drifting definitions of success.

**Import-cycle rule:** `strategies.py` imports both strategy modules at load; `real_chrome.py` / `internal_chromium.py` import `verification.py` at the top. Therefore `verification.py` MUST NOT import `.strategies` at the top level — it imports the `async_playwright` shim lazily, inside `verify_flow_session`.

---

## Task 1: `verification.py` — the evaluation core

The pure decision logic: the outcome enum, the status dataclass, and `evaluate_session_response`. No I/O — testable with zero mocks.

**Files:**
- Create: `src/gflow_cli/auth/verification.py`
- Test: `tests/auth/test_verification.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/auth/test_verification.py`:

```python
from __future__ import annotations

import json

import pytest

from gflow_cli.auth.verification import (
    FlowSessionOutcome,
    FlowSessionStatus,
    evaluate_session_response,
)

# Representative authenticated /api/auth/session body. Sanitised — no real
# PII. Pins the endpoint contract: if Google changes the response shape, the
# AUTHENTICATED assertions below fail loudly instead of the change going silent.
AUTHENTICATED_BODY = json.dumps(
    {
        "user": {
            "name": "Test User",
            "email": "test.user@example.com",
            "image": "https://lh3.googleusercontent.com/a/fake",
        },
        "expires": "2026-06-16T08:39:21.000Z",
    }
)


class TestEvaluateSessionResponse:
    def test_authenticated_user_with_email(self) -> None:
        status = evaluate_session_response(
            200, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.authenticated is True
        assert status.user_email == "test.user@example.com"
        assert status.detail == "Flow app session verified."

    def test_empty_session_with_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY
        assert status.authenticated is False
        assert status.user_email is None

    def test_empty_session_no_google_cookie(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="chrome")
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    def test_null_user_does_not_crash(self) -> None:
        status = evaluate_session_response(
            200, '{"user": null}', google_session=False, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    @pytest.mark.parametrize(
        "body",
        [
            '{"user": {"name": "x"}}',        # user present, no email key
            '{"user": {"email": ""}}',        # empty-string email
            '{"user": ["not", "a", "dict"]}', # user is not a dict
            "[]",                              # JSON array, not an object
            '{"user":',                        # truncated JSON
            "",                                # empty body
            "   ",                             # whitespace only
            "not json at all",                 # garbage
        ],
    )
    def test_unexpected_or_malformed_body_is_verification_error(self, body: str) -> None:
        status = evaluate_session_response(200, body, google_session=True, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert status.detail == "Could not verify the Flow session."

    @pytest.mark.parametrize("status_code", [302, 401, 403, 404, 500, 503])
    def test_non_200_is_verification_error(self, status_code: int) -> None:
        # google_session is irrelevant on the error path.
        status = evaluate_session_response(
            status_code, AUTHENTICATED_BODY, google_session=True, source="chrome"
        )
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    def test_source_is_passed_through(self) -> None:
        status = evaluate_session_response(200, "{}", google_session=False, source="internal")
        assert status.source == "internal"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/test_verification.py -v`
Expected: FAIL at collection — `ModuleNotFoundError: No module named 'gflow_cli.auth.verification'`.

- [ ] **Step 3: Write the minimal implementation**

Create `src/gflow_cli/auth/verification.py`:

```python
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

import json
from dataclasses import dataclass
from enum import Enum

# The NextAuth session endpoint. Expected authenticated 200 body shape:
#   {"user": {"name": ..., "email": ..., "image": ...}, "expires": "..."}
# An unauthenticated request returns `200 {}`. This contract is pinned by the
# AUTHENTICATED_BODY fixture in tests/auth/test_verification.py — if Google
# changes the shape, that test fails rather than the change going silent.
SESSION_API_URL = "https://labs.google/fx/api/auth/session"


class FlowSessionOutcome(str, Enum):
    """Mutually-exclusive results of probing a profile for a Flow session."""

    AUTHENTICATED = "authenticated"
    GOOGLE_SESSION_ONLY = "google_session_only"
    NO_SESSION = "no_session"
    VERIFICATION_ERROR = "verification_error"


_DETAIL_BY_OUTCOME: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.AUTHENTICATED: "Flow app session verified.",
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: "Signed in to Google, but not to the Flow app.",
    FlowSessionOutcome.NO_SESSION: "No sign-in detected.",
    FlowSessionOutcome.VERIFICATION_ERROR: "Could not verify the Flow session.",
}


@dataclass(frozen=True)
class FlowSessionStatus:
    """The verdict of a Flow-session probe.

    `detail` is a derived property — always one of the four fixed strings in
    `_DETAIL_BY_OUTCOME`, never built from response, cookie, or exception
    content. Deriving it (rather than storing a free string) makes it
    structurally impossible to leak a secret through this field.
    """

    outcome: FlowSessionOutcome
    user_email: str | None
    source: str

    @property
    def detail(self) -> str:
        return _DETAIL_BY_OUTCOME[self.outcome]

    @property
    def authenticated(self) -> bool:
        return self.outcome is FlowSessionOutcome.AUTHENTICATED


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
        parsed = json.loads(body)
    except (json.JSONDecodeError, ValueError):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    if not isinstance(parsed, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    user = parsed.get("user")
    if user is None or user == {}:
        # Authenticated-shaped endpoint reachable, but no Flow session.
        if google_session:
            return _result(FlowSessionOutcome.GOOGLE_SESSION_ONLY)
        return _result(FlowSessionOutcome.NO_SESSION)

    if not isinstance(user, dict):
        return _result(FlowSessionOutcome.VERIFICATION_ERROR)

    email = user.get("email")
    if isinstance(email, str) and email:
        return _result(FlowSessionOutcome.AUTHENTICATED, email)

    # `user` present but no usable email — unexpected shape (see spec §10).
    return _result(FlowSessionOutcome.VERIFICATION_ERROR)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_verification.py -v`
Expected: PASS — all `TestEvaluateSessionResponse` cases green.

- [ ] **Step 5: Lint, type-check, and commit**

```bash
uv run ruff check src/gflow_cli/auth/verification.py tests/auth/test_verification.py
uv run ruff format src/gflow_cli/auth/verification.py tests/auth/test_verification.py
uv run pyright src/gflow_cli/auth/verification.py
git add src/gflow_cli/auth/verification.py tests/auth/test_verification.py
git commit -m "feat(auth): add Flow-session evaluation core (issue #15)"
```

---

## Task 2: `verification.py` — the `verify_flow_session` probe

The async I/O wrapper: launches a headless context on the profile, fetches `/api/auth/session` with bounded retries, and delegates the verdict to `evaluate_session_response`. Fail-closed.

**Files:**
- Modify: `src/gflow_cli/auth/verification.py`
- Test: `tests/auth/test_verification.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/auth/test_verification.py` — and extend the top imports:

Change the import block at the top of the file from:

```python
from gflow_cli.auth.verification import (
    FlowSessionOutcome,
    FlowSessionStatus,
    evaluate_session_response,
)
```

to:

```python
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from gflow_cli.auth.verification import (
    FlowSessionOutcome,
    FlowSessionStatus,
    evaluate_session_response,
    verify_flow_session,
)
from gflow_cli.errors import SecurityError
```

Append at the end of the file:

```python
# ---------------------------------------------------------------------------
# verify_flow_session — async headless probe
# ---------------------------------------------------------------------------


def _build_verify_mock(
    *,
    cookies: list[dict] | None = None,
    response_status: int = 200,
    response_body: str = "{}",
    get_side_effect: object = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_async_playwright, mock_ctx) for verify_flow_session.

    Mocks the headless persistent context: ctx.cookies(), ctx.request.get()
    (an APIResponse-like object with `.status` and async `.text()`), and
    ctx.close(). Patch target for the shim is gflow_cli.auth.strategies.
    """
    if cookies is None:
        cookies = [{"name": "SAPISID", "value": "x"}]

    mock_resp = MagicMock(name="resp")
    mock_resp.status = response_status
    mock_resp.text = AsyncMock(return_value=response_body)

    mock_request = MagicMock(name="request")
    if get_side_effect is not None:
        mock_request.get = AsyncMock(side_effect=get_side_effect)
    else:
        mock_request.get = AsyncMock(return_value=mock_resp)

    mock_ctx = MagicMock(name="ctx")
    mock_ctx.cookies = AsyncMock(return_value=cookies)
    mock_ctx.request = mock_request
    mock_ctx.close = AsyncMock()

    mock_pw_obj = MagicMock(name="pw")
    mock_pw_obj.chromium.launch_persistent_context = AsyncMock(return_value=mock_ctx)

    mock_cm = MagicMock(name="cm")
    mock_cm.__aenter__ = AsyncMock(return_value=mock_pw_obj)
    mock_cm.__aexit__ = AsyncMock(return_value=False)

    mock_ap = MagicMock(name="async_playwright", return_value=mock_cm)
    return mock_ap, mock_ctx


class TestVerifyFlowSession:
    @pytest.fixture
    def gflow_home(self, tmp_path: Path) -> Path:
        home = tmp_path / "gflow_home"
        home.mkdir()
        return home

    @pytest.mark.asyncio
    async def test_authenticated_profile(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, mock_ctx = _build_verify_mock(response_body=AUTHENTICATED_BODY)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, channel="chrome", source="chrome")
        assert status.outcome is FlowSessionOutcome.AUTHENTICATED
        assert status.user_email == "test.user@example.com"
        mock_ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_google_session_only(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock(
            cookies=[{"name": "SAPISID", "value": "x"}], response_body="{}"
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.GOOGLE_SESSION_ONLY

    @pytest.mark.asyncio
    async def test_no_session(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock(cookies=[], response_body="{}")
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.NO_SESSION

    @pytest.mark.asyncio
    async def test_launch_failure_is_verification_error(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, _ = _build_verify_mock()
        mock_ap.return_value.__aenter__.return_value.chromium.launch_persistent_context = (
            AsyncMock(side_effect=RuntimeError("launch failed"))
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR

    @pytest.mark.asyncio
    async def test_transient_errors_exhaust_to_verification_error(
        self, gflow_home: Path
    ) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # Every fetch attempt raises a network error -> retries exhausted.
        mock_ap, mock_ctx = _build_verify_mock(
            get_side_effect=[RuntimeError("net::ERR")] * 3
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 3

    @pytest.mark.asyncio
    async def test_retryable_status_retried_then_returned(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # HTTP 503 every time -> retried 3x, then evaluated as VERIFICATION_ERROR.
        mock_ap, mock_ctx = _build_verify_mock(response_status=503)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 3

    @pytest.mark.asyncio
    async def test_non_retryable_status_not_retried(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        # HTTP 401 -> not retried; one attempt, then VERIFICATION_ERROR.
        mock_ap, mock_ctx = _build_verify_mock(response_status=401)
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            status = await verify_flow_session(profile, source="chrome")
        assert status.outcome is FlowSessionOutcome.VERIFICATION_ERROR
        assert mock_ctx.request.get.await_count == 1

    @pytest.mark.asyncio
    async def test_ctx_closed_on_error_path(self, gflow_home: Path) -> None:
        profile = gflow_home / "profile_default"
        profile.mkdir()
        mock_ap, mock_ctx = _build_verify_mock(
            get_side_effect=[RuntimeError("net::ERR")] * 3
        )
        with (
            patch("gflow_cli.auth.verification.get_settings") as mock_settings,
            patch("gflow_cli.auth.strategies.async_playwright", mock_ap),
            patch("asyncio.sleep", AsyncMock()),
        ):
            mock_settings.return_value.home = gflow_home
            await verify_flow_session(profile, source="chrome")
        mock_ctx.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_profile_outside_home_raises_security_error(self, gflow_home: Path) -> None:
        outside = gflow_home.parent / "outside_profile"
        outside.mkdir()
        with patch("gflow_cli.auth.verification.get_settings") as mock_settings:
            mock_settings.return_value.home = gflow_home
            with pytest.raises(SecurityError):
                await verify_flow_session(outside, source="chrome")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/test_verification.py::TestVerifyFlowSession -v`
Expected: FAIL at collection — `ImportError: cannot import name 'verify_flow_session'`.

- [ ] **Step 3: Write the minimal implementation**

In `src/gflow_cli/auth/verification.py`, replace the import block:

```python
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
```

with:

```python
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from gflow_cli.config import get_settings
from gflow_cli.errors import SecurityError

if TYPE_CHECKING:
    from playwright.async_api import BrowserContext

logger = structlog.get_logger(__name__)
```

Then add, just below `SESSION_API_URL`:

```python
# Per-request timeout for the session probe (milliseconds).
_REQUEST_TIMEOUT_MS = 15_000
# Total fetch attempts (initial + retries) before giving up.
_MAX_ATTEMPTS = 3
# HTTP statuses worth retrying — transient server-side conditions only.
_RETRYABLE_STATUSES = frozenset({429, 503, 504})
```

And append at the end of the file:

```python
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
        except Exception as exc:  # noqa: BLE001 - retried below, or re-raised
            last_exc = exc
            if attempt == _MAX_ATTEMPTS:
                raise
        else:
            if resp.status not in _RETRYABLE_STATUSES or attempt == _MAX_ATTEMPTS:
                return resp.status, body
        await asyncio.sleep(float(min(2 ** (attempt - 1), 8)))
    # Unreachable — the loop always returns or raises by the final attempt.
    raise last_exc or RuntimeError("session probe produced no response")


async def verify_flow_session(
    profile_dir: Path,
    *,
    channel: str = "chrome",
    source: str = "chrome",
) -> FlowSessionStatus:
    """Headlessly probe `profile_dir` for a usable Flow app session.

    Launches a headless persistent context on the profile, reads cookies, and
    calls the NextAuth session endpoint. Fail-closed: any failure — boundary
    violation aside — yields VERIFICATION_ERROR, never AUTHENTICATED.

    Precondition: `profile_dir` must resolve inside GFLOW_CLI_HOME. The check
    uses `strict=True` (the directory exists by the time verification runs);
    `RealChromeStrategy.login`'s own pre-`mkdir` check deliberately stays
    `strict=False` — see the design spec §4.2.
    """
    home = get_settings().home.resolve()
    try:
        profile_dir.resolve(strict=True).relative_to(home)
    except (ValueError, OSError):
        raise SecurityError(
            f"Profile directory {profile_dir} is outside of GFLOW_CLI_HOME ({home})."
        ) from None

    # Lazy import — a top-level `from .strategies import ...` would create the
    # cycle strategies -> real_chrome -> verification -> strategies.
    from .strategies import async_playwright

    status_code: int
    body: str
    try:
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel=channel,
                headless=True,
            )
            try:
                cookies = await ctx.cookies()
                google_session = any(c.get("name") == "SAPISID" for c in cookies)
                status_code, body = await _fetch_session(ctx)
            finally:
                await ctx.close()
    except Exception as exc:  # noqa: BLE001 - fail-closed: any failure -> VERIFICATION_ERROR
        logger.warning(
            "auth_flow_session_probe_error", source=source, error=type(exc).__name__
        )
        return FlowSessionStatus(
            outcome=FlowSessionOutcome.VERIFICATION_ERROR, user_email=None, source=source
        )

    result = evaluate_session_response(
        status_code, body, google_session=google_session, source=source
    )
    if result.outcome is FlowSessionOutcome.VERIFICATION_ERROR:
        # Observable durability signal — distinguishes a moved/changed endpoint
        # from a flaky link. The status code is safe to log; the body is not.
        logger.warning(
            "auth_flow_session_unexpected_response", source=source, status_code=status_code
        )
    return result
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/test_verification.py -v`
Expected: PASS — `TestEvaluateSessionResponse` and `TestVerifyFlowSession` all green.

- [ ] **Step 5: Lint, type-check, and commit**

```bash
uv run ruff check src/gflow_cli/auth/verification.py tests/auth/test_verification.py
uv run ruff format src/gflow_cli/auth/verification.py tests/auth/test_verification.py
uv run pyright src/gflow_cli/auth/verification.py
git add src/gflow_cli/auth/verification.py tests/auth/test_verification.py
git commit -m "feat(auth): add verify_flow_session headless probe (issue #15)"
```

---

## Task 3: Refresh the `AuthMissingError` docstring

`AuthMissingError`'s docstring describes a `SapisidhashTransport` scenario that no longer reflects how the error is used. Docstring-only — no behaviour change.

**Files:**
- Modify: `src/gflow_cli/errors.py:266-274`

- [ ] **Step 1: Edit the docstring and remediation text**

In `src/gflow_cli/errors.py`, replace:

```python
class AuthMissingError(GFlowError):
    """Raised when a strategy lacks a required prerequisite credential
    (e.g. SAPISID cookie missing from profile dir for SapisidhashTransport)."""

    problem_type = "https://gflow-cli.dev/errors/auth-missing"
    title = "Authentication credential missing"
    _default_remediation = (
        "A required credential (e.g. SAPISID cookie) is absent from the profile. "
        "Run `gflow auth login --profile <name>` to capture a fresh session."
    )
```

with:

```python
class AuthMissingError(GFlowError):
    """Raised when a profile lacks a usable session for the requested action.

    Covers both a wholly absent session and the issue-#15 case: a profile
    signed in to Google but not to the Flow app (no NextAuth session). The
    raising site supplies a message and `remediation_hint` describing which.
    """

    problem_type = "https://gflow-cli.dev/errors/auth-missing"
    title = "Authentication credential missing"
    _default_remediation = (
        "No usable Flow session was found in the profile. "
        "Run `gflow auth login --profile <name>` and complete the Flow sign-in."
    )
```

- [ ] **Step 2: Verify nothing broke**

Run: `uv run pyright src/gflow_cli/errors.py && uv run pytest tests/ -q -k errors`
Expected: pyright clean; any error-taxonomy tests still PASS (no behaviour changed).

- [ ] **Step 3: Commit**

```bash
git add src/gflow_cli/errors.py
git commit -m "docs(errors): refresh AuthMissingError docstring for issue #15"
```

---

## Task 4: `RealChromeStrategy` — verify the Flow app session

Open Chrome at the Flow URL, clarify the guidance, and replace the post-close `SAPISID` check with `verify_flow_session`.

**Files:**
- Modify: `src/gflow_cli/auth/real_chrome.py`
- Test: `tests/auth/strategies/test_strategies.py`

- [ ] **Step 1: Update the strategy tests (failing)**

In `tests/auth/strategies/test_strategies.py`:

(a) Replace the import block at the top:

```python
from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.errors import AuthLoginTimeoutError, SecurityError
```

with:

```python
from gflow_cli.auth.strategies import InternalChromiumStrategy, RealChromeStrategy
from gflow_cli.auth.verification import FlowSessionOutcome, FlowSessionStatus
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError
```

(b) Delete the `_build_verify_pw_mock` helper entirely (lines ~16-43). `RealChromeStrategy`'s verification is now `verify_flow_session`, which the strategy tests mock directly — the Playwright-level mock plumbing for verification lives in `tests/auth/test_verification.py`.

(c) Add this helper next to `_build_mock_proc`:

```python
def _status(outcome: FlowSessionOutcome, email: str | None = None) -> FlowSessionStatus:
    """Build a FlowSessionStatus for mocking verify_flow_session."""
    return FlowSessionStatus(outcome=outcome, user_email=email, source="chrome")
```

(d) Replace `test_real_chrome_launch_flags` — same assertions, but mock
`verify_flow_session` instead of Playwright:

```python
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
```

(e) Replace `test_real_chrome_success_verified_via_sapisid` with:

```python
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
```

`test_real_chrome_privacy_guard` and `test_real_chrome_timeout_raises` are
**unchanged** — both raise before verification is reached, so they neither
need nor reference `verify_flow_session`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/strategies/test_strategies.py::TestRealChromeStrategy -v`
Expected: FAIL — `verify_flow_session` is not yet importable in `real_chrome`, and the marker/`AuthMissingError` behaviour does not exist yet.

- [ ] **Step 3: Edit `real_chrome.py`**

(a) Replace the import line:

```python
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError

from .base import AuthStrategy
```

with:

```python
from gflow_cli.errors import AuthLoginTimeoutError, AuthMissingError, SecurityError

from .base import AuthStrategy
from .verification import FlowSessionOutcome, verify_flow_session
```

(b) Add these module-level dicts just below the `GEMINI_URL = ...` line:

```python
# User-facing guidance per non-authenticated verification outcome (issue #15).
_UNVERIFIED_MESSAGE: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: (
        "Signed in to your Google account, but the Flow app sign-in wasn't completed."
    ),
    FlowSessionOutcome.NO_SESSION: "No sign-in detected.",
    FlowSessionOutcome.VERIFICATION_ERROR: (
        "Could not verify the Flow session — this is often a network problem."
    ),
}
_UNVERIFIED_HINT: dict[FlowSessionOutcome, str] = {
    FlowSessionOutcome.GOOGLE_SESSION_ONLY: (
        "Re-run `gflow auth login` and continue until the Flow editor "
        "(the prompt box / your projects) loads before closing Chrome."
    ),
    FlowSessionOutcome.NO_SESSION: (
        "Re-run `gflow auth login`, sign in to Google, and continue until "
        "the Flow editor loads."
    ),
    FlowSessionOutcome.VERIFICATION_ERROR: (
        "Check your connection and re-run `gflow auth login`."
    ),
}
```

(c) Replace the `chrome_args` list (currently lines ~86-93):

```python
        chrome_args = [
            chrome_exe,
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,800",
            # No --remote-debugging-port: zero automation surface.
        ]
```

with:

```python
        chrome_args = [
            chrome_exe,
            f"--user-data-dir={profile_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            "--window-size=1280,800",
            # No --remote-debugging-port: zero automation surface.
            GEMINI_URL,  # open straight on the Flow sign-in page
        ]
```

(d) Replace the guidance block (currently lines ~108-114, the four `_console.print`
steps inside `if not headless:`):

```python
            _console.print("1. A Google Chrome window will open.")
            _console.print(f"2. Sign in at: [bold]{GEMINI_URL}[/bold]")
            _console.print("3. Complete sign-in until you reach the Flow editor.")
            _console.print(
                "4. [bold yellow]CLOSE THE BROWSER[/bold yellow] when done — "
                "gflow will verify your session automatically."
            )
```

with:

```python
            _console.print("1. A Google Chrome window opens at the Flow sign-in page.")
            _console.print("2. Sign in with your Google account.")
            _console.print(
                "3. [bold yellow]Keep going until the Flow editor itself loads[/bold yellow] "
                "— the prompt box and your projects."
            )
            _console.print(
                "   Signing in to Google is NOT enough; gflow needs a completed "
                "Flow app sign-in."
            )
            _console.print(
                "4. Then [bold yellow]CLOSE THE BROWSER[/bold yellow] — gflow verifies "
                "the Flow session automatically."
            )
```

(e) Replace the entire post-close verification block (currently lines ~146-177,
from `_console.print("\n[bold green]Browser closed...` through the end of the
method):

```python
        _console.print("\n[bold green]Browser closed.[/bold green] Verifying session...")

        # Headless probe: read persisted cookies from the isolated profile dir.
        # channel="chrome" uses the system Chrome binary so verification avoids
        # Playwright's own automation flags (belt-and-suspenders stealth).
        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=True,
            )
            try:
                cookies = await ctx.cookies()
                has_sapisid = any(c.get("name") == "SAPISID" for c in cookies)

                if has_sapisid:
                    logger.info("auth_login_success_verified", strategy=self.name)
                    # Write strategy marker before any output that might fail on
                    # narrow Windows codepages — FlowApiClient reads this to select
                    # the matching Chrome channel for launch_persistent_context.
                    (profile_dir / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")
                    _console.print("[green][OK] Session captured and verified.[/green]")
                else:
                    logger.warning("auth_login_no_cookies", strategy=self.name)
                    raise AuthMissingError(
                        "No session cookies found after sign-in. "
                        "Did you complete the sign-in before closing Chrome?"
                    )
            finally:
                await ctx.close()
```

with:

```python
        _console.print("\n[bold green]Browser closed.[/bold green] Verifying Flow session...")

        # Verify the real Flow app session — not just the Google SSO cookie.
        status = await verify_flow_session(profile_dir, channel="chrome", source=self.name)

        if status.authenticated:
            logger.info(
                "auth_flow_session_verified",
                strategy=self.name,
                source=status.source,
                user_email=status.user_email,
            )
            # Marker read by browser_manager.channel_for_profile so FlowApiClient
            # selects the system Chrome channel. Load-bearing — must persist here.
            (profile_dir / ".gflow_browser_strategy").write_text("chrome", encoding="utf-8")
            _console.print(
                f"[green][OK] Flow session verified ({status.user_email}).[/green]"
            )
        else:
            logger.warning(
                "auth_flow_session_unverified",
                strategy=self.name,
                outcome=status.outcome.value,
            )
            raise AuthMissingError(
                _UNVERIFIED_MESSAGE[status.outcome],
                remediation_hint=_UNVERIFIED_HINT[status.outcome],
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/strategies/test_strategies.py::TestRealChromeStrategy -v`
Expected: PASS — all `TestRealChromeStrategy` tests green.

- [ ] **Step 5: Lint, type-check, and commit**

```bash
uv run ruff check src/gflow_cli/auth/real_chrome.py tests/auth/strategies/test_strategies.py
uv run ruff format src/gflow_cli/auth/real_chrome.py tests/auth/strategies/test_strategies.py
uv run pyright src/gflow_cli/auth/real_chrome.py
git add src/gflow_cli/auth/real_chrome.py tests/auth/strategies/test_strategies.py
git commit -m "fix(auth): real-chrome verifies the Flow app session (issue #15)"
```

---

## Task 5: `InternalChromiumStrategy` — live-poll `/api/auth/session`

Swap the fragile `SAPISID` + UI-text check for the authoritative live check; harden the loop's exception handling.

**Files:**
- Modify: `src/gflow_cli/auth/internal_chromium.py`
- Test: `tests/auth/strategies/test_strategies.py`

- [ ] **Step 1: Update the internal-chromium tests (failing)**

In `tests/auth/strategies/test_strategies.py`, replace `test_internal_chromium_standard_behavior` and `test_internal_chromium_timeout_raises` with versions that mock `page.request.get` instead of `page.get_by_text`:

```python
    @pytest.mark.asyncio
    async def test_internal_chromium_standard_behavior(self, tmp_path: Path) -> None:
        """Internal Chromium detects success via the /api/auth/session probe."""
        strategy = InternalChromiumStrategy()
        gflow_home = tmp_path / "gflow_home"
        gflow_home.mkdir()
        profile_dir = gflow_home / "profile_internal"

        mock_resp = MagicMock(name="resp")
        mock_resp.status = 200
        mock_resp.text = AsyncMock(
            return_value='{"user": {"email": "test@example.com"}}'
        )

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
        mock_page.request.get.assert_awaited()

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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/auth/strategies/test_strategies.py::TestInternalChromiumStrategy -v`
Expected: FAIL — `internal_chromium.py` still calls `get_by_text`, never `page.request.get`, so `mock_page.request.get.assert_awaited()` fails.

- [ ] **Step 3: Edit `internal_chromium.py`**

(a) Replace the import block:

```python
from gflow_cli.config import get_settings
from gflow_cli.errors import AuthLoginTimeoutError, SecurityError

from .base import AuthStrategy
```

with:

```python
from playwright.async_api import Error as PlaywrightError

from gflow_cli.config import get_settings
from gflow_cli.errors import AuthLoginTimeoutError, SecurityError

from .base import AuthStrategy
from .verification import SESSION_API_URL, FlowSessionOutcome, evaluate_session_response
```

(b) Replace the polling block — currently the `while` loop with the
`has_sapisid` + `get_by_text` check (lines ~67-107, from the `timeout_at = ...`
line through the `if not success:` raise):

```python
                # Polling for success (SAPISID cookie + UI signal).
                timeout_at = asyncio.get_running_loop().time() + self._timeout_seconds
                success = False

                while asyncio.get_running_loop().time() < timeout_at:
                    try:
                        cookies = await ctx.cookies()
                        has_sapisid = any(c.get("name") == "SAPISID" for c in cookies)

                        if has_sapisid:
                            # Final confirmation via UI signal
                            if (
                                await page.get_by_text("New project").is_visible()
                                or await page.get_by_text("Your projects").is_visible()
                            ):
                                logger.info("auth_login_success_detected", strategy=self.name)
                                success = True
                                break
                    except Exception:
                        # Browser or context is gone — exit loop without success
                        break

                    await asyncio.sleep(1)
                else:
                    raise AuthLoginTimeoutError(
                        f"Sign-in not completed within {self._timeout_seconds}s.",
                        remediation_hint=(
                            "Run `gflow auth login` again and complete sign-in promptly. "
                            f"Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT to a higher value if needed "
                            f"(current: {self._timeout_seconds}s)."
                        ),
                    )

                if not success:
                    raise AuthLoginTimeoutError(
                        "Browser closed before authentication was verified.",
                        remediation_hint=(
                            "Complete the full sign-in flow before closing the browser. "
                            "Run `gflow auth login` to try again."
                        ),
                    )
```

with:

```python
                # Poll the NextAuth session endpoint live until the Flow app
                # sign-in completes. Non-AUTHENTICATED outcomes (including a
                # transient VERIFICATION_ERROR) just mean "keep waiting" — the
                # user may still be signing in.
                timeout_at = asyncio.get_running_loop().time() + self._timeout_seconds
                success = False

                while asyncio.get_running_loop().time() < timeout_at:
                    try:
                        cookies = await ctx.cookies()
                        google_session = any(c.get("name") == "SAPISID" for c in cookies)
                        resp = await page.request.get(SESSION_API_URL, timeout=15_000)
                        status = evaluate_session_response(
                            resp.status,
                            await resp.text(),
                            google_session=google_session,
                            source=self.name,
                        )
                        if status.outcome is FlowSessionOutcome.AUTHENTICATED:
                            logger.info(
                                "auth_flow_session_verified",
                                strategy=self.name,
                                source=status.source,
                                user_email=status.user_email,
                            )
                            success = True
                            break
                    except asyncio.CancelledError:
                        raise
                    except PlaywrightError:
                        # Browser / page / context closed — stop polling.
                        break
                    except Exception as exc:  # noqa: BLE001 - unexpected; log and stop
                        logger.warning(
                            "auth_flow_session_poll_error",
                            strategy=self.name,
                            error=type(exc).__name__,
                        )
                        break

                    await asyncio.sleep(3)
                else:
                    raise AuthLoginTimeoutError(
                        f"Flow sign-in not completed within {self._timeout_seconds}s.",
                        remediation_hint=(
                            "Run `gflow auth login` again and continue until the Flow "
                            "editor loads. Set GFLOW_CLI_AUTH_LOGIN_TIMEOUT higher if "
                            f"needed (current: {self._timeout_seconds}s)."
                        ),
                    )

                if not success:
                    raise AuthLoginTimeoutError(
                        "Browser closed before the Flow editor sign-in was verified.",
                        remediation_hint=(
                            "Complete the Flow sign-in — until the editor loads — "
                            "before closing the browser. Run `gflow auth login` to retry."
                        ),
                    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/auth/strategies/test_strategies.py -v`
Expected: PASS — all `TestRealChromeStrategy` and `TestInternalChromiumStrategy` tests green.

- [ ] **Step 5: Lint, type-check, and commit**

```bash
uv run ruff check src/gflow_cli/auth/internal_chromium.py tests/auth/strategies/test_strategies.py
uv run ruff format src/gflow_cli/auth/internal_chromium.py tests/auth/strategies/test_strategies.py
uv run pyright src/gflow_cli/auth/internal_chromium.py
git add src/gflow_cli/auth/internal_chromium.py tests/auth/strategies/test_strategies.py
git commit -m "fix(auth): internal-chromium verifies via /api/auth/session (issue #15)"
```

---

## Task 6: Record the external endpoint coupling in `KNOWN_ISSUES.md`

The fix depends on Google's `/api/auth/session` endpoint. Record it so a future maintainer knows where to look if Google changes Flow's auth.

**Files:**
- Modify: `KNOWN_ISSUES.md`

- [ ] **Step 1: Append the coupling note**

Open `KNOWN_ISSUES.md` and append the following entry, matching the file's
existing heading/format style (use the same heading level as other entries):

```markdown
### Auth verification depends on Google's NextAuth session endpoint

`gflow auth login` verifies a real Flow sign-in by calling
`https://labs.google/fx/api/auth/session` (see `src/gflow_cli/auth/verification.py`)
and by checking for the Google `SAPISID` cookie. These are **external Google
surfaces** — if Google changes the endpoint path, the response shape, or the
cookie names, verification degrades **fail-closed**: it reports
`VERIFICATION_ERROR` (an honest "could not verify") rather than a false
success. The expected authenticated response shape is pinned by the
`AUTHENTICATED_BODY` fixture in `tests/auth/test_verification.py` — a Google
change surfaces there as a failing test. Start any investigation of a sudden
`gflow auth login` verification failure at that fixture and `verification.py`.
```

- [ ] **Step 2: Commit**

```bash
git add KNOWN_ISSUES.md
git commit -m "docs: record the Flow session-endpoint coupling in KNOWN_ISSUES"
```

---

## Task 7: Full verification gate

Run the project's full quality gate and confirm no regressions — including the BDD scenarios.

**Files:** none modified (verification only).

- [ ] **Step 1: Confirm no test or step definition references the removed log events**

Run: `git grep -n "auth_login_success_verified\|auth_login_no_cookies\|auth_login_success_detected" tests/ ; echo "exit:$?"`
Expected: `exit:1` (no matches). If any match is found in a `.feature` step
definition or `conftest.py`, update that assertion to the new event names
(`auth_flow_session_verified` / `auth_flow_session_unverified`) and re-run.

- [ ] **Step 2: Run the full quality gate**

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run pytest -q --cov=gflow_cli
```

Expected: hygiene gate passes; ruff lint + format clean; pyright reports no
errors; the full test suite passes with overall coverage ≥ 80%.

- [ ] **Step 3: Confirm the auth BDD scenarios pass**

Run: `uv run pytest tests/ -q -k "auth"`
Expected: PASS — all auth unit and BDD scenarios green.

- [ ] **Step 4: Update the changelog**

Add a line under `CHANGELOG.md` `[Unreleased]` (the change is user-visible):

```markdown
### Fixed
- `gflow auth login` now verifies a real Flow app session before reporting
  success — fixes issue #15, where a Google-only sign-in was wrongly accepted
  and later failed with HTTP 401.
```

- [ ] **Step 5: Commit**

```bash
git add CHANGELOG.md
git commit -m "docs: changelog entry for issue #15 auth-verification fix"
```

---

## Notes on spec reconciliation

Two intentional, minor refinements of the spec made while planning — both
strengthen the design and are flagged here for the plan review:

1. **`test_real_chrome_launch_flags` patch seam.** Spec §8 lists it under "must
   NOT change". Its *assertions* are unchanged, but because `real_chrome.py` no
   longer drives Playwright directly, its mock target moves from
   `playwright.async_api.async_playwright` to a direct mock of
   `verify_flow_session`. This is mechanical plumbing, not a behaviour change.
2. **`FlowSessionStatus.detail` is a derived property**, not a stored field
   (spec §4.1 lists it as a field). Deriving it from `outcome` makes it
   structurally impossible to set `detail` to anything outside the fixed set —
   a strict strengthening of the spec's "never interpolated" security rule.
3. **`_fetch_session` uses an explicit retry loop**, not a `tenacity` decorator.
   The spec §4.1 explicitly sanctions either; the loop is chosen so the final
   `(status_code, body)` is preserved for the durability WARNING log (spec §10).
