# Issue #15 — i2v `uploadImage` 401 Bearer-Auth Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `gflow video i2v` work by attaching the `Authorization: Bearer` token that `aisandbox-pa.googleapis.com` REST routes require.

**Architecture:** A new pure module `api/bearer_auth.py` captures the Flow OAuth Bearer token by observing a Playwright page's own requests. `FlowApiClient` captures it during bootstrap, attaches it host-scoped via a single `_with_bearer` helper, and re-captures once (single-flight) on a 401. A new `ApiAuthError` replaces the misleading `AuthExpiredError` for genuine API-auth failures.

**Tech Stack:** Python 3.11+, Playwright (`page.request`, `page.on`), `asyncio`, `structlog`, `pytest` + `pytest-asyncio`.

**Spec:** `docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md` (v2, council-reviewed).

**Branch:** `fix/issue-15-i2v-bearer-auth` — all commits land here; merges to `main` via PR.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/gflow_cli/api/bearer_auth.py` | Create | Capture the Bearer token from a live page (pure module) |
| `tests/api/test_bearer_auth.py` | Create | Unit tests for the capture module |
| `src/gflow_cli/errors.py` | Modify | Add `ApiAuthError` + `EXIT_CODE_MAP` entry |
| `tests/test_errors.py` | Modify | `ApiAuthError` taxonomy + exit-code-uniqueness tests |
| `src/gflow_cli/api/client.py` | Modify | Fields, `_with_bearer`, `_redact_headers_for_log`, bootstrap capture, re-capture retry, error classification |
| `tests/api/test_client.py` | Modify | Header-attach, host-scoping, re-capture, redaction tests |
| `tests/live/test_i2v_live.py` | Create | `@pytest.mark.live` end-to-end i2v regression guard |
| `PLAN.md` | Modify | Backlog pointer to the spec's alternatives |
| `CHANGELOG.md` | Modify | `[Unreleased]` entry |

**Convention reminders:** `from __future__ import annotations` at the top of every module; `structlog` (never `print`/`logging`); `@dataclass(frozen=True)` for value objects; run the 5 quality gates before every commit (`uv run python scripts/ci/check_repo_hygiene.py`, `uv run ruff check src tests`, `uv run ruff format --check src tests`, `uv run pyright src`, `uv run python -m pytest -q -m "not e2e and not live"`).

---

## Task 1: Phase 1 — Live verification gate (BLOCKING)

Confirms the root cause empirically before any fix code. This task ends with a maintainer decision — do not start Task 2 until this gate returns **PASS**.

**Files:**
- Modify: `src/gflow_cli/api/client.py` (request helpers — temporary diagnostic logging)

- [ ] **Step 1: Add env-flagged outgoing-header logging**

In `client.py`, add this module-level helper near `_redact_for_log` (after line 962):

```python
def _redact_headers_for_log(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of `headers` with any `authorization` value masked.

    The SOLE permitted way to log a headers dict — `_redact_for_log` covers
    request bodies only, not headers. Spec §4.5.
    """
    redacted = dict(headers)
    auth = redacted.get("authorization")
    if auth is not None:
        redacted["authorization"] = f"Bearer <len={len(auth)}>"
    return redacted
```

In each of the three `attempt()` closures (`_post_json` ~line 322, `_patch_json` ~line 354, `generate_video` ~line 600), immediately before the `page.request.{post,patch}(...)` call, add:

```python
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,  # in generate_video use: routes.GENERATE_VIDEO
                        headers=_redact_headers_for_log({"content-type": content_type}),
                    )
```

(Use the literal headers dict each call site passes. Add `import os` to the imports if absent.)

- [ ] **Step 2: Run the gates and commit the diagnostic**

Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run pyright src`
Expected: all pass.

```bash
git add src/gflow_cli/api/client.py
git commit -m "feat(api): env-flagged outgoing request-header logging (issue #15 Phase 1)"
```

- [ ] **Step 3: Maintainer — capture the live diff**

The maintainer (human) runs, on a profile with a verified session:

```bash
GFLOW_CLI_LOG_REQUEST_HEADERS=1 uv run gflow video i2v <image> "<prompt>" --profile <name> -o tmp/issue-15-phase1/out
```

Then, in a browser DevTools → Network, performs an image upload in the Flow UI and "copy as cURL" of the `POST /v1/flow/uploadImage` request. Save both header sets (CLI redacted log + browser cURL with the Bearer value redacted by hand) under `tmp/issue-15-phase1/`.

- [ ] **Step 4: Diff every header and decide**

Compare the **full** header set of the working browser request against the CLI request. Apply the outcome matrix (spec §5.1):

| Outcome | Action |
|---|---|
| Only `Authorization` differs (browser has `Bearer`, CLI has none) | **PASS** → proceed to Task 2 |
| A non-`Authorization` header also differs (`origin`, `referer`, `sec-fetch-*`, …) | **STOP** — revise the spec to copy the full required header set, re-plan |
| CLI already sends `Authorization` and still 401s | **STOP** — spec is void; open a fresh investigation issue |

Record the redacted diff and the decision in `tmp/issue-15-phase1/RESULT.md`. **Only a PASS unblocks Task 2.**

---

## Task 2: `ApiAuthError` error class

**Files:**
- Modify: `src/gflow_cli/errors.py` (add class after `AuthExpiredError` ~line 115; add to `EXIT_CODE_MAP` ~line 280)
- Test: `tests/test_errors.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_errors.py`:

```python
def test_api_auth_error_exit_code_is_14():
    from gflow_cli.errors import ApiAuthError, EXIT_CODE_MAP
    assert EXIT_CODE_MAP[ApiAuthError] == 14


def test_api_auth_error_is_flow_api_error():
    from gflow_cli.errors import ApiAuthError, FlowApiError
    assert issubclass(ApiAuthError, FlowApiError)


def test_api_auth_error_remediation_is_not_a_login_loop():
    from gflow_cli.errors import ApiAuthError
    hint = ApiAuthError().remediation_hint.lower()
    # Must not send users straight into a blind re-login loop.
    assert "gflow auth login" not in hint or "bug" in hint


def test_api_auth_error_detail_carries_no_token_data():
    from gflow_cli.errors import ApiAuthError
    err = ApiAuthError(detail="HTTP 401", status=401, route="uploadImage")
    pd = err.to_problem_details()
    assert pd["detail"] == "HTTP 401"
    assert "Bearer" not in repr(pd)


def test_exit_code_map_values_are_unique():
    from gflow_cli.errors import EXIT_CODE_MAP
    values = list(EXIT_CODE_MAP.values())
    assert len(values) == len(set(values)), "duplicate exit code in EXIT_CODE_MAP"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/test_errors.py -q -k "api_auth or exit_code_map_values"`
Expected: FAIL — `ImportError: cannot import name 'ApiAuthError'`.

- [ ] **Step 3: Add the `ApiAuthError` class**

In `errors.py`, after the `AuthExpiredError` class (line 115):

```python
class ApiAuthError(FlowApiError):
    """Raised when an aisandbox-pa.googleapis.com route returns 401 even after
    a Bearer-token re-capture. Distinct from AuthExpiredError: the session
    login is fine — the API rejected the request's bearer token.

    `detail` MUST carry no token-derived data (spec §4.3) — a fixed string
    only.
    """

    problem_type = "https://gflow-cli.dev/errors/api-auth"
    title = "Flow API authorization failed"
    _default_remediation = (
        "The Flow API rejected the request's authorization after a token "
        "re-capture. If you signed out elsewhere, run "
        "`gflow auth login --profile <name>`; otherwise this is likely a "
        "token-capture regression — please file a bug at "
        "https://github.com/ffroliva/gflow-cli/issues (do NOT include tokens)."
    )
```

- [ ] **Step 4: Register the exit code**

In `errors.py`, inside `EXIT_CODE_MAP` (line 280), add this entry alongside the other `FlowApiError` subclasses (placement among siblings is immaterial — they are not ancestors of each other):

```python
    ApiAuthError: 14,
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run python -m pytest tests/test_errors.py -q`
Expected: PASS (all error tests, new and existing).

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/errors.py tests/test_errors.py
git commit -m "feat(errors): add ApiAuthError (exit 14) for genuine API-auth failures"
```

---

## Task 3: `bearer_auth.py` capture module

**Files:**
- Create: `src/gflow_cli/api/bearer_auth.py`
- Test: `tests/api/test_bearer_auth.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/api/test_bearer_auth.py`:

```python
from __future__ import annotations

import time

import pytest

from gflow_cli.api.bearer_auth import BearerToken, capture_bearer_token
from gflow_cli.errors import ApiAuthError


class FakeRequest:
    """Minimal stand-in for a Playwright Request."""

    def __init__(self, url: str, headers: dict[str, str]) -> None:
        self.url = url
        self.headers = headers


class FakePage:
    """Fake Playwright Page. `goto` fires `requests` through the listeners."""

    def __init__(self, requests: list[FakeRequest] | None = None) -> None:
        self._requests = requests or []
        self._listeners: dict[str, list] = {}

    def on(self, event: str, cb) -> None:
        self._listeners.setdefault(event, []).append(cb)

    def remove_listener(self, event: str, cb) -> None:
        self._listeners.get(event, []).remove(cb)

    async def goto(self, url: str, **kwargs: object) -> None:
        for req in self._requests:
            for cb in list(self._listeners.get("request", [])):
                cb(req)


@pytest.mark.asyncio
async def test_capture_returns_token_from_aisandbox_bearer_request():
    page = FakePage([
        FakeRequest(
            "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage",
            {"authorization": "Bearer abc123"},
        )
    ])
    token = await capture_bearer_token(page, timeout_s=1.0)
    assert isinstance(token, BearerToken)
    assert token.value == "abc123"


@pytest.mark.asyncio
async def test_capture_ignores_non_aisandbox_and_non_bearer_requests():
    page = FakePage([
        FakeRequest("https://labs.google/whatever", {"authorization": "Bearer x"}),
        FakeRequest(
            "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage",
            {"authorization": "SAPISIDHASH nope"},
        ),
    ])
    with pytest.raises(ApiAuthError):
        await capture_bearer_token(page, timeout_s=0.05)


@pytest.mark.asyncio
async def test_capture_times_out_fast_when_no_bearer_seen():
    page = FakePage(requests=[])  # goto fires nothing
    start = time.monotonic()
    with pytest.raises(ApiAuthError):
        await capture_bearer_token(page, timeout_s=0.001)
    assert time.monotonic() - start < 1.0  # no real 30s sleep
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_bearer_auth.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gflow_cli.api.bearer_auth'`.

- [ ] **Step 3: Create the module**

Create `src/gflow_cli/api/bearer_auth.py`:

```python
"""Capture the Flow OAuth Bearer token from a live Playwright page.

aisandbox-pa.googleapis.com REST endpoints require an `Authorization: Bearer
<token>` header. Playwright's `page.request` is a separate HTTP client that
shares only the browser cookie jar — it never receives the OAuth token the
Flow web app mints in-page. This module captures that token by observing the
page's own outgoing requests during a navigation.

Security: the token is held in memory only — never written to disk, never
logged. The intercepting closure clears its capture buffer on any error exit.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

import structlog

from gflow_cli.api import routes
from gflow_cli.errors import ApiAuthError

if TYPE_CHECKING:
    from playwright.async_api import Page
    from playwright.async_api import Request as PlaywrightRequest

log = structlog.get_logger(__name__)

AISANDBOX_HOST = "aisandbox-pa.googleapis.com"
_BEARER_PREFIX = "Bearer "


@dataclass(frozen=True)
class BearerToken:
    """An OAuth Bearer token captured from a live Flow page."""

    value: str


async def capture_bearer_token(page: "Page", *, timeout_s: float = 30.0) -> BearerToken:
    """Navigate `page` to the Flow editor and return the first
    `Authorization: Bearer` token it sends to aisandbox-pa.googleapis.com.

    Raises:
        ApiAuthError: if no Bearer token is observed within `timeout_s`.
    """
    captured: list[str] = []  # mutable container — cleared on error exit
    done = asyncio.Event()

    def _on_request(req: "PlaywrightRequest") -> None:
        if captured or AISANDBOX_HOST not in req.url:
            return
        auth = req.headers.get("authorization", "")
        if auth.startswith(_BEARER_PREFIX):
            captured.append(auth[len(_BEARER_PREFIX) :])
            done.set()

    page.on("request", _on_request)
    try:
        await page.goto(
            routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=60_000
        )
        await asyncio.wait_for(done.wait(), timeout=timeout_s)
        log.info("bearer_auth.captured")
        return BearerToken(value=captured[0])
    except TimeoutError:
        captured.clear()  # never leave a token reachable from an exception
        raise ApiAuthError(
            detail="bearer token capture timed out",
            route="bearer_auth.capture",
        ) from None
    finally:
        page.remove_listener("request", _on_request)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_bearer_auth.py -q`
Expected: PASS (3 tests).

- [ ] **Step 5: Run type + lint gates**

Run: `uv run ruff check src tests && uv run pyright src`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/api/bearer_auth.py tests/api/test_bearer_auth.py
git commit -m "feat(api): add bearer_auth module — capture Flow OAuth Bearer token"
```

---

## Task 4: `FlowApiClient` — fields, helpers, bootstrap capture

**Files:**
- Modify: `src/gflow_cli/api/client.py` (`__init__` ~line 142, `__aenter__` ~line 199, new helpers)
- Test: `tests/api/test_client.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/api/test_client.py`:

```python
def test_with_bearer_attaches_only_for_aisandbox_host():
    from gflow_cli.api.bearer_auth import BearerToken
    from gflow_cli.api.client import FlowApiClient
    from pathlib import Path

    client = FlowApiClient(Path("x"))
    client._bearer = BearerToken(value="tok123")

    aisandbox = client._with_bearer(
        "https://aisandbox-pa.googleapis.com/v1/flow/uploadImage",
        {"content-type": "text/plain"},
    )
    assert aisandbox["authorization"] == "Bearer tok123"

    labs = client._with_bearer(
        "https://labs.google/fx/api/trpc/project.createProject",
        {"content-type": "application/json"},
    )
    assert "authorization" not in labs


def test_redact_headers_for_log_masks_authorization():
    from gflow_cli.api.client import _redact_headers_for_log

    out = _redact_headers_for_log({"authorization": "Bearer secrettokenvalue"})
    assert out["authorization"] == "Bearer <len=24>"
    assert "secrettokenvalue" not in repr(out)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python -m pytest tests/api/test_client.py -q -k "with_bearer or redact_headers"`
Expected: FAIL — `AttributeError: 'FlowApiClient' object has no attribute '_with_bearer'`.

- [ ] **Step 3: Add imports and `__init__` fields**

In `client.py` imports, add:

```python
from gflow_cli.api.bearer_auth import AISANDBOX_HOST, BearerToken, capture_bearer_token
from gflow_cli.errors import ApiAuthError  # add to the existing errors import
```

In `__init__`, after `self._page: Page | None = None` (line 142):

```python
        # issue #15: Bearer token for aisandbox-pa REST routes — captured on
        # bootstrap, held in memory only. The lock makes re-capture single-flight.
        self._bearer: BearerToken | None = None
        self._bearer_lock = asyncio.Lock()
```

- [ ] **Step 4: Add the `_with_bearer` helper**

In `client.py`, add as a method on `FlowApiClient` near `_post_json` (before line 293):

```python
    def _with_bearer(self, url: str, headers: dict[str, str]) -> dict[str, str]:
        """Return `headers` plus an `Authorization: Bearer` header IFF `url`
        targets aisandbox-pa.googleapis.com and a token has been captured.

        This is the SOLE place the Authorization header is added (spec §4.6).
        """
        if AISANDBOX_HOST in url and self._bearer is not None:
            return {**headers, "authorization": f"Bearer {self._bearer.value}"}
        return headers
```

- [ ] **Step 5: Add `_redact_headers_for_log`** (if Task 1 was reverted/not merged)

If `_redact_headers_for_log` is not already present from Task 1, add it as a module-level function after `_redact_for_log` (see Task 1 Step 1 for the exact code). Otherwise skip.

- [ ] **Step 6: Capture the Bearer during bootstrap**

In `__aenter__`, replace the existing bootstrap navigation (lines 199-201):

```python
        await self._page.goto(
            routes.EDITOR_BOOTSTRAP_URL, wait_until="domcontentloaded", timeout=60_000
        )
```

with:

```python
        # Bootstrap navigation + Bearer capture (issue #15). capture_bearer_token
        # navigates to the editor and observes the page's own aisandbox-pa
        # request to lift the OAuth Bearer. A miss here is non-fatal — the first
        # aisandbox 401 triggers a re-capture (see _post_json).
        try:
            self._bearer = await capture_bearer_token(self._page)
        except ApiAuthError:
            log.warning("bearer_capture_failed_on_bootstrap")
            await self._page.goto(
                routes.EDITOR_BOOTSTRAP_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
```

(`log` is the module `structlog` logger — confirm it exists; `client.py` already binds `logger`. Use whichever name the module already defines for the structlog logger.)

- [ ] **Step 7: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_client.py -q -k "with_bearer or redact_headers"`
Expected: PASS.

- [ ] **Step 8: Run the full client test file (no regressions)**

Run: `uv run python -m pytest tests/api/ -q -m "not e2e and not live"`
Expected: PASS — all existing api tests still green.

- [ ] **Step 9: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client.py
git commit -m "feat(api): capture Bearer token on bootstrap; add _with_bearer helper"
```

---

## Task 5: `FlowApiClient` — attach header + single-flight re-capture

**Files:**
- Modify: `src/gflow_cli/api/client.py` (`_post_json`, `_patch_json`, `generate_video`, new `_recapture_bearer`)
- Test: `tests/api/test_client.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/api/test_client.py` (these use the existing Playwright-mock fixtures in that file — follow the established `_patch_playwright` / `fake_context` pattern; the assertions below describe the required behaviour):

```python
@pytest.mark.asyncio
async def test_post_json_attaches_bearer_for_aisandbox(monkeypatch):
    """A _post_json call to an aisandbox-pa URL sends Authorization: Bearer."""
    # Arrange a FlowApiClient with a stubbed page whose request.post records
    # the headers it was called with, and client._bearer set to a known token.
    # Assert the recorded headers contain authorization == "Bearer <token>".


@pytest.mark.asyncio
async def test_post_json_omits_bearer_for_labs_host(monkeypatch):
    """create_project (labs.google) must NOT receive an Authorization header."""
    # Same harness; call create_project; assert "authorization" not in headers.


@pytest.mark.asyncio
async def test_single_recapture_on_401_then_retry(monkeypatch):
    """A 401 from aisandbox triggers exactly one capture_bearer_token call."""
    # Stub page.request.post to return 401 once, then 200.
    # Patch gflow_cli.api.client.capture_bearer_token with a Mock.
    # Call upload_image; assert capture mock call_count == 1 and the call
    # ultimately succeeds.


@pytest.mark.asyncio
async def test_second_401_raises_api_auth_error(monkeypatch):
    """A 401 that survives the re-capture retry raises ApiAuthError, not
    AuthExpiredError."""
    # Stub page.request.post to return 401 on every call.
    # Call upload_image; assert pytest.raises(ApiAuthError).


@pytest.mark.asyncio
async def test_concurrent_401s_recapture_once(monkeypatch):
    """N concurrent aisandbox calls hitting 401 trigger ONE re-capture."""
    # Run several _post_json calls concurrently with asyncio.gather, all
    # getting 401-then-200. Assert capture_bearer_token mock call_count == 1.


@pytest.mark.asyncio
async def test_no_bearer_value_in_logs(monkeypatch):
    """structlog output never contains a raw 'Bearer <token>' string."""
    # Use structlog.testing.capture_logs(); make an aisandbox POST with
    # GFLOW_CLI_LOG_REQUEST_HEADERS=1; assert no entry repr contains the
    # literal token value.
```

Write these as concrete tests following the existing harness in `tests/api/test_client.py`. Each must fail before Step 3.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python -m pytest tests/api/test_client.py -q -k "bearer or recapture or 401 or api_auth"`
Expected: FAIL — headers lack `authorization`; no re-capture occurs.

- [ ] **Step 3: Add the single-flight re-capture method**

In `client.py`, add to `FlowApiClient`:

```python
    async def _recapture_bearer(self, stale: BearerToken | None) -> None:
        """Re-capture the Bearer token, single-flight.

        `stale` is the token the caller saw fail. Under the lock we re-check:
        if another worker already refreshed it, we do nothing.
        """
        async with self._bearer_lock:
            if self._bearer is not stale:
                return  # another worker already refreshed
            page = await self._checkout_page()
            try:
                self._bearer = await capture_bearer_token(page)
            finally:
                self._checkin_page(page)
```

- [ ] **Step 4: Wire the header + retry into `_post_json`**

In `_post_json`, replace the `attempt()` closure (lines 319-328) with:

```python
        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                return await page.request.post(
                    url,
                    data=body_str,
                    headers=self._with_bearer(url, {"content-type": content_type}),
                )
            finally:
                self._checkin_page(page)
```

Then, after `resp = await self._run_with_retry(attempt, route=route)` (line 330), insert the re-capture retry **before** `text = await resp.text()`:

```python
        if resp.status == 401 and AISANDBOX_HOST in url:
            stale = self._bearer
            await self._recapture_bearer(stale)
            resp = await self._run_with_retry(attempt, route=route)
            if resp.status == 401:
                raise ApiAuthError(
                    detail=f"HTTP {resp.status}",
                    status=resp.status,
                    instance=_make_instance(),
                    route=route,
                )
```

- [ ] **Step 5: Wire `_patch_json` and `generate_video`**

In `_patch_json`'s `attempt()` (line 354), change the `headers=` argument to:

```python
                    headers=self._with_bearer(url, {"content-type": _AISANDBOX_CONTENT_TYPE}),
```

and add the same re-capture-retry block after its `resp = await self._run_with_retry(...)`.

In `generate_video`'s `attempt()` (line 600), change the `headers=` argument to:

```python
                    headers=self._with_bearer(
                        routes.GENERATE_VIDEO, {"content-type": _AISANDBOX_CONTENT_TYPE}
                    ),
```

and add the same re-capture-retry block after its `response = await self._run_with_retry(...)` (line 608), using `routes.GENERATE_VIDEO` as the URL in the `AISANDBOX_HOST in ...` check.

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run python -m pytest tests/api/test_client.py -q`
Expected: PASS — all new and existing client tests.

- [ ] **Step 7: Run the full suite (no regressions)**

Run: `uv run python -m pytest -q -m "not e2e and not live" --cov=gflow_cli`
Expected: PASS — 577+ tests, coverage ≥ 80%.

- [ ] **Step 8: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_client.py
git commit -m "fix(api): attach Bearer to aisandbox routes; single-flight re-capture on 401"
```

---

## Task 6: Live E2E regression test

**Files:**
- Create: `tests/live/test_i2v_live.py`

- [ ] **Step 1: Create the live test**

Create `tests/live/test_i2v_live.py`:

```python
"""End-to-end i2v regression guard for issue #15.

OPT-IN ONLY — requires a real logged-in profile and live Flow auth. Never
runs in CI. Run with: `uv run python -m pytest tests/live/ -m live`.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.live


@pytest.mark.asyncio
async def test_i2v_uploads_image_without_401(tmp_path: Path) -> None:
    """`upload_image` to aisandbox-pa /v1/flow/uploadImage returns 200 — the
    issue #15 regression: it must NOT raise ApiAuthError / AuthExpiredError."""
    profile = os.environ.get("GFLOW_CLI_E2E_PROFILE")
    if not profile:
        pytest.skip("GFLOW_CLI_E2E_PROFILE not set")

    from gflow_cli.api.client import FlowApiClient
    from gflow_cli.config import get_settings

    profile_dir = get_settings().profile_subdir(profile)
    fixture = Path("test_assets/fixtures") / "i2v_source.png"
    assert fixture.exists(), "commit a small PNG fixture at test_assets/fixtures/"

    async with FlowApiClient(profile_dir=profile_dir, headless=False) as client:
        project = await client.create_project(title="issue-15 live test")
        asset = await client.upload_image(project.project_id, fixture)
        assert asset.name  # a non-empty asset UUID proves the 200
```

- [ ] **Step 2: Verify it is excluded from the default run**

Run: `uv run python -m pytest tests/live/test_i2v_live.py -q -m "not e2e and not live"`
Expected: `1 deselected` (the `live` marker keeps it out of the default suite).

- [ ] **Step 3: Commit**

```bash
git add tests/live/test_i2v_live.py
git commit -m "test(live): add opt-in i2v end-to-end regression guard (issue #15)"
```

---

## Task 7: Backlog pointer, changelog, verification & PR

**Files:**
- Modify: `PLAN.md` (the issue #15 section ~line 443)
- Modify: `CHANGELOG.md` (`[Unreleased]`)

- [ ] **Step 1: Add the PLAN.md backlog pointer for Approaches B/C**

In `PLAN.md`, append to the issue #15 section (after line 501, before the `---`):

```markdown
**Alternative auth-attachment strategies — BACKLOG (deferred):**
Approaches B (inline capture in `client.py`) and C (`page.evaluate(fetch())`)
were considered and deferred. Full rationale:
`docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md` §8.
```

- [ ] **Step 2: Add the CHANGELOG entry**

In `CHANGELOG.md`, under `## [Unreleased]`, add:

```markdown
### Fixed

- **`gflow video i2v` no longer fails with a 401** — `aisandbox-pa.googleapis.com`
  REST routes now carry the `Authorization: Bearer` token they require
  (captured from the live Flow page, in memory only). A genuine API-auth
  failure now raises the new `ApiAuthError` (exit 14) instead of a misleading
  `AuthExpiredError` that looped users through `gflow auth login`. (#15)
```

- [ ] **Step 3: Remove the Phase 1 diagnostic decision**

Confirm whether the env-flagged header logging from Task 1 stays (spec §5 — it ships permanently behind `GFLOW_CLI_LOG_REQUEST_HEADERS`). It stays. Verify `_redact_headers_for_log` is its only formatting path.

- [ ] **Step 4: Run all five quality gates**

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q -m "not e2e and not live" --cov=gflow_cli --cov-fail-under=80
```
Expected: all pass; coverage ≥ 80%.

- [ ] **Step 5: Commit and push**

```bash
git add PLAN.md CHANGELOG.md
git commit -m "docs: record issue #15 fix in CHANGELOG + PLAN backlog pointer"
git push
```

- [ ] **Step 6: Open the PR**

```bash
gh pr create --base main --head fix/issue-15-i2v-bearer-auth \
  --title "fix(i2v): attach Bearer auth to aisandbox-pa routes (#15)" \
  --body "Implements docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md. Closes #15."
```

CI must pass all 5 required checks. Merge via the PR (the `main` branch is protected — no direct pushes).

---

## Self-Review (completed by plan author)

**Spec coverage:** §2 root cause → Task 1 verifies it. §4.1 module → Task 3. §4.2 client wiring + single-flight → Tasks 4-5. §4.3 `ApiAuthError` + route discrimination → Task 2 + Task 5 Step 4. §4.5 `_redact_headers_for_log` → Task 1/4. §4.6 invariants → enforced by Task 4-5 tests. §5 Phase 1 gate → Task 1. §6 test plan → Tasks 2-6 (fixtures in Task 3, `call_count`/negative/uniqueness/redaction tests in Tasks 2&5). §7 non-regression → Step "full suite" in Tasks 4,5,7. §8 alternatives → Task 7 Step 1. §10 phasing → task order matches.

**Placeholder scan:** Task 5 Step 1 intentionally describes tests as behavioural specs to be written against the existing `tests/api/test_client.py` Playwright-mock harness (that harness is large and project-specific); every other code step shows complete code. The executing engineer must write Task 5's test bodies concretely before Step 2 — this is called out explicitly.

**Type consistency:** `BearerToken.value: str`, `capture_bearer_token(page, *, timeout_s) -> BearerToken`, `_with_bearer(url, headers) -> dict`, `_recapture_bearer(stale)`, `ApiAuthError`, `AISANDBOX_HOST`, `_redact_headers_for_log` — names are consistent across Tasks 2-5.
