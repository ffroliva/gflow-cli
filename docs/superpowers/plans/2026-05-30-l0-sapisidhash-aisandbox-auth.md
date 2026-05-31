# L0 — SAPISIDHASH auth for aisandbox-pa REST Implementation Plan

> ⚠️ **SUPERSEDED 2026-05-31** by [`2026-05-31-l0-bearer-pivot.md`](2026-05-31-l0-bearer-pivot.md).
> The SAPISIDHASH hypothesis was **disproven by live verification**: `aisandbox-pa`
> authenticates with `Authorization: Bearer ya29.<oauth>` (fetched from
> `GET /fx/api/auth/session`), not SAPISIDHASH. This plan is kept only as a record of
> the investigation — **do not implement it.** See the bearer-pivot plan for the shipped design.

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `page.request` POST/PATCH/GET calls to `aisandbox-pa.googleapis.com` authenticate (fixing the HTTP 401 of Issue #15) by attaching an `Authorization: SAPISIDHASH <ts>_<sha1>` header computed from the live browser session's `SAPISID` cookie.

**Architecture:** `FlowApiClient._post_json`/`_patch_json` already fire through an authenticated Playwright `page.request`, which shares the browser's cookie jar but does **not** add the `Authorization` header Google's first-party web app computes in JS. We compute it ourselves: read `SAPISID` from the **live Playwright context** (`page.context.cookies()` — returns HttpOnly values decrypted, sidestepping the Windows DPAPI-encrypted on-disk cookie that `sapisidhash.py:read_sapisid_from_profile` cannot read), reuse the already-correct `compute_sapisidhash()` helper, and merge the header **only for aisandbox-pa URLs** (BFF `labs.google` calls keep working on cookies alone). On 401 we re-read `SAPISID` once (cookie rotation) and retry, then raise a distinct `AisandboxAuthError`.

**Tech Stack:** Python 3.13, Playwright async, `httpx`-free (uses `page.request`), `hashlib.sha1`, `structlog`, pytest. Windows test runner: `.venv\Scripts\python.exe -m pytest` (per `[[windows-dev-quirks]]`; `uv run pytest` is broken on this machine).

---

## Investigation gates (DONE — recorded here so the executor doesn't repeat them)

1. **`compute_sapisidhash` exists and is correct** — `src/gflow_cli/api/transports/experimental/sapisidhash.py:53`, returns `<ts>_<sha1("<ts> <SAPISID> <origin>")>`, already unit-tested. **Reuse it.**
2. **`_post_json` attaches no auth today** — only `{"content-type": ...}` (`client.py:402-405`). Same for `_patch_json` (`:444-447`).
3. **Source of SAPISID must be the live context, not disk** — `read_sapisid_from_profile` raises `AuthMissingError` on Windows because Chromium 80+ stores `SAPISID` DPAPI-encrypted (HIGH #8 in that file). `page.context.cookies()` returns the decrypted value from the running browser. The empirical 200-vs-401 confirmation is **Task 7** (Chrome HAR exports redact the `Authorization` header value, so it can only be confirmed live).

---

## File Structure

- **Create** `src/gflow_cli/api/_sapisidhash.py` — neutral home for the pure `compute_sapisidhash()` helper (avoids a core→`transports.experimental` import; keeps it DRY).
- **Modify** `src/gflow_cli/api/transports/experimental/sapisidhash.py` — import `compute_sapisidhash` from the new module instead of defining it.
- **Modify** `src/gflow_cli/errors.py` — add `AisandboxAuthError(AuthExpiredError)` (inherits exit code 3; distinct class + remediation).
- **Modify** `src/gflow_cli/api/client.py` — add `_is_aisandbox_url`, `_read_sapisid_from_context`, `_ensure_sapisid`, `_aisandbox_auth_headers`; wire them into `_post_json` and `_patch_json` with a 401 refresh-retry.
- **Create** `tests/api/test_sapisidhash_helper.py`, `tests/api/test_aisandbox_auth_headers.py`, `tests/api/test_post_json_aisandbox_auth.py` — unit + integration.
- **Create** `tests/e2e/test_aisandbox_auth_live.py` — opt-in live smoke (credit-free).

---

## Task 1: Extract `compute_sapisidhash` into a neutral module (DRY)

**Files:**
- Create: `src/gflow_cli/api/_sapisidhash.py`
- Modify: `src/gflow_cli/api/transports/experimental/sapisidhash.py:53-57` (remove def, import instead)
- Test: `tests/api/test_sapisidhash_helper.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_sapisidhash_helper.py
import hashlib

import pytest

from gflow_cli.api._sapisidhash import compute_sapisidhash


@pytest.mark.unit
def test_compute_sapisidhash_matches_google_convention():
    ts, sapisid, origin = 1700000000, "FAKE_SAPISID", "https://labs.google"
    expected_digest = hashlib.sha1(
        f"{ts} {sapisid} {origin}".encode()
    ).hexdigest()
    assert compute_sapisidhash(timestamp=ts, sapisid=sapisid, origin=origin) == (
        f"{ts}_{expected_digest}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_sapisidhash_helper.py -v`
Expected: FAIL — `ModuleNotFoundError: gflow_cli.api._sapisidhash`

- [ ] **Step 3: Create the neutral module**

```python
# src/gflow_cli/api/_sapisidhash.py
"""Pure SAPISIDHASH computation — shared by the client and the S3 transport."""

from __future__ import annotations

import hashlib


def compute_sapisidhash(*, timestamp: int, sapisid: str, origin: str) -> str:
    """Return ``<timestamp>_<sha1("<timestamp> <sapisid> <origin>")>``.

    Google's first-party web-app authentication scheme for its private APIs.
    """
    payload = f"{timestamp} {sapisid} {origin}".encode()
    digest = hashlib.sha1(payload).hexdigest()  # noqa: S324 — Google's scheme mandates SHA-1
    return f"{timestamp}_{digest}"
```

- [ ] **Step 4: Point the experimental transport at the new module**

In `src/gflow_cli/api/transports/experimental/sapisidhash.py`, delete the local `def compute_sapisidhash(...)` (lines 53-57) and add to the imports block:

```python
from gflow_cli.api._sapisidhash import compute_sapisidhash
```

- [ ] **Step 5: Run helper test + the existing sapisidhash transport tests**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_sapisidhash_helper.py tests/api/transports -k sapisidhash -v`
Expected: PASS (the moved function still satisfies the transport's existing tests).

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/api/_sapisidhash.py src/gflow_cli/api/transports/experimental/sapisidhash.py tests/api/test_sapisidhash_helper.py
git commit -m "refactor(api): extract compute_sapisidhash into neutral _sapisidhash module"
```

---

## Task 2: Add the distinct `AisandboxAuthError`

**Files:**
- Modify: `src/gflow_cli/errors.py` (add class after `AuthExpiredError` ~line 138)
- Test: `tests/api/test_aisandbox_auth_error.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_aisandbox_auth_error.py
import pytest

from gflow_cli.errors import (
    EXIT_CODE_MAP,
    AisandboxAuthError,
    AuthExpiredError,
    exit_code_for,
)


@pytest.mark.unit
def test_aisandbox_auth_error_is_distinct_but_inherits_exit_code_3():
    err = AisandboxAuthError("create_scene returned 401 after SAPISID refresh")
    # Distinct, catchable class
    assert isinstance(err, AisandboxAuthError)
    assert issubclass(AisandboxAuthError, AuthExpiredError)
    # Inherits AuthExpiredError's exit code (3) via the isinstance walk
    assert exit_code_for(err) == 3
    # Has its own remediation, not the generic one
    assert "SAPISID" in err.remediation_hint
    # No standalone EXIT_CODE_MAP entry needed (inherits parent's)
    assert AisandboxAuthError not in EXIT_CODE_MAP
```

> Note: if the helper is named differently than `exit_code_for`, use the actual lookup in `cli.py:178` / `errors.py:423` region. Confirm the name with `grep -n "def exit_code_for\|EXIT_CODE_MAP\[" src/gflow_cli/errors.py src/gflow_cli/cli.py` before writing Step 3.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_aisandbox_auth_error.py -v`
Expected: FAIL — `ImportError: cannot import name 'AisandboxAuthError'`

- [ ] **Step 3: Add the error class** (in `errors.py`, immediately after `AuthExpiredError`, ~line 138)

```python
class AisandboxAuthError(AuthExpiredError):
    """aisandbox-pa REST returned 401 even after a fresh SAPISIDHASH.

    Distinct from the generic AuthExpiredError so callers (and the scene
    feature) can catch the aisandbox-specific auth failure, while still
    mapping to exit code 3 via the EXIT_CODE_MAP isinstance walk.
    """

    problem_type = "https://gflow-cli.dev/errors/aisandbox-auth"
    title = "aisandbox-pa authentication failed"
    _default_remediation = (
        "SAPISID cookie missing, expired, or unreadable. "
        "Re-run `gflow auth login --profile <name>` and retry."
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_aisandbox_auth_error.py -v`
Expected: PASS

- [ ] **Step 5: Run the EXIT_CODE_MAP ordering invariant test** (guard against `[[exit-code-map-ordering-invariant-test-pitfall]]`)

Run: `.venv\Scripts\python.exe -m pytest tests -k "exit_code_map" -v`
Expected: PASS (subclass with no own entry must not break the most-specific-first invariant).

- [ ] **Step 6: Commit**

```bash
git add src/gflow_cli/errors.py tests/api/test_aisandbox_auth_error.py
git commit -m "feat(errors): add AisandboxAuthError (distinct 401 class, exit 3)"
```

---

## Task 3: SAPISID-from-live-context + header builder on the client

**Files:**
- Modify: `src/gflow_cli/api/client.py` (add helpers + `self._sapisid` field in `__init__` ~line 147)
- Test: `tests/api/test_aisandbox_auth_headers.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/api/test_aisandbox_auth_headers.py
import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AuthMissingError


def _make_client() -> FlowApiClient:
    # Construct without entering the async context (no browser launched).
    return FlowApiClient.__new__(FlowApiClient)


@pytest.mark.unit
def test_is_aisandbox_url_discriminates_host():
    c = _make_client()
    assert c._is_aisandbox_url("https://aisandbox-pa.googleapis.com/v1/flow/x")
    assert not c._is_aisandbox_url("https://labs.google/fx/api/trpc/project.createProject")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_builds_authorization(monkeypatch):
    c = _make_client()
    c._sapisid = None
    # Inject the SAPISID read so no real browser is needed.
    async def fake_read():
        return "FAKE_SAPISID"
    monkeypatch.setattr(c, "_read_sapisid_from_context", fake_read)
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)

    headers = await c._aisandbox_auth_headers()
    assert headers["authorization"].startswith("SAPISIDHASH 1700000000_")
    assert headers["origin"] == "https://labs.google"
    # SAPISID was cached
    assert c._sapisid == "FAKE_SAPISID"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_aisandbox_auth_headers_raises_when_sapisid_absent(monkeypatch):
    c = _make_client()
    c._sapisid = None
    async def fake_read():
        raise AuthMissingError("no SAPISID")
    monkeypatch.setattr(c, "_read_sapisid_from_context", fake_read)
    with pytest.raises(AuthMissingError):
        await c._aisandbox_auth_headers()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_aisandbox_auth_headers.py -v`
Expected: FAIL — `AttributeError: '_is_aisandbox_url'` / `'_aisandbox_auth_headers'`

- [ ] **Step 3: Add the field + helpers** (in `client.py`)

Add to `__init__` near `self.transport = None` (~line 147):

```python
        self._sapisid: str | None = None
```

Add these methods to `FlowApiClient` (near `_post_json`, and add `from gflow_cli.api._sapisidhash import compute_sapisidhash` + `_AISANDBOX_HOST = "aisandbox-pa.googleapis.com"` + `_SAPISID_ORIGIN = "https://labs.google"` at module scope):

```python
    @staticmethod
    def _is_aisandbox_url(url: str) -> bool:
        """True for aisandbox-pa REST URLs, which require SAPISIDHASH auth.

        BFF (labs.google) URLs authenticate on cookies alone — never matched.
        """
        return _AISANDBOX_HOST in url

    async def _read_sapisid_from_context(self) -> str:
        """Read the SAPISID cookie from the LIVE browser context.

        Playwright returns HttpOnly cookie values decrypted from the running
        browser — unlike reading the on-disk SQLite DB, which fails on
        Windows (DPAPI-encrypted, see transports/experimental/sapisidhash.py).
        """
        page = await self._checkout_page()
        try:
            cookies = await page.context.cookies("https://www.google.com")
        finally:
            self._checkin_page(page)
        for cookie in cookies:
            if cookie.get("name") == "SAPISID" and cookie.get("value"):
                return str(cookie["value"])
        msg = (
            "SAPISID cookie not present in the browser session. "
            "Run `gflow auth login --profile <name>`."
        )
        raise AuthMissingError(msg)

    async def _ensure_sapisid(self) -> str:
        """Lazily read + cache SAPISID for the session."""
        if self._sapisid is None:
            self._sapisid = await self._read_sapisid_from_context()
        return self._sapisid

    async def _aisandbox_auth_headers(self) -> dict[str, str]:
        """Build the SAPISIDHASH Authorization header for an aisandbox call.

        Timestamp is recomputed every call (freshness signal); SAPISID is
        long-lived and cached. NEVER log the returned values.
        """
        sapisid = await self._ensure_sapisid()
        ts = int(time.time())
        hash_value = compute_sapisidhash(
            timestamp=ts, sapisid=sapisid, origin=_SAPISID_ORIGIN
        )
        return {
            "authorization": f"SAPISIDHASH {hash_value}",
            "origin": _SAPISID_ORIGIN,
        }
```

Confirm `time` and `AuthMissingError` are imported in `client.py` (add if missing: `import time`, `from gflow_cli.errors import AuthMissingError`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_aisandbox_auth_headers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_aisandbox_auth_headers.py
git commit -m "feat(api): read SAPISID from live context + build SAPISIDHASH headers"
```

---

## Task 4: Wire auth + 401 refresh-retry into `_post_json`

**Files:**
- Modify: `src/gflow_cli/api/client.py:367-422` (`_post_json`)
- Test: `tests/api/test_post_json_aisandbox_auth.py`

- [ ] **Step 1: Write the failing test** (fake `page.request` captures headers; simulates 401-then-200)

```python
# tests/api/test_post_json_aisandbox_auth.py
import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.errors import AisandboxAuthError


class _FakeResp:
    def __init__(self, status, body="{}"):
        self.status = status
        self._body = body
    async def text(self):
        return self._body


class _FakeRequest:
    def __init__(self, statuses):
        self.statuses = list(statuses)
        self.calls = []
    async def post(self, url, *, data, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        return _FakeResp(self.statuses.pop(0))


class _FakePage:
    def __init__(self, statuses):
        self.request = _FakeRequest(statuses)
        self.context = None


def _client_with_page(page, sapisid="FAKE_SAPISID"):
    c = FlowApiClient.__new__(FlowApiClient)
    c._sapisid = sapisid
    async def checkout():
        return page
    c._checkout_page = checkout                      # type: ignore[method-assign]
    c._checkin_page = lambda p: None                 # type: ignore[method-assign]
    return c


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_attaches_sapisidhash_for_aisandbox(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json("https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes", {"workflowIds": []})
    sent = page.request.calls[0]["headers"]
    assert sent["authorization"].startswith("SAPISIDHASH 1700000000_")


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_does_not_attach_auth_for_bff():
    page = _FakePage([200])
    c = _client_with_page(page)
    await c._post_json(
        "https://labs.google/fx/api/trpc/project.createProject",
        {"json": {}},
        content_type="application/json",
    )
    assert "authorization" not in page.request.calls[0]["headers"]


@pytest.mark.integration
@pytest.mark.asyncio
async def test_post_json_refreshes_sapisid_on_401_then_raises(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([401, 401])  # 401, refresh, still 401
    c = _client_with_page(page)
    refreshed = {"n": 0}
    async def fake_read():
        refreshed["n"] += 1
        return "ROTATED_SAPISID"
    c._read_sapisid_from_context = fake_read          # type: ignore[method-assign]
    with pytest.raises(AisandboxAuthError):
        await c._post_json("https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes", {"workflowIds": []})
    assert refreshed["n"] == 1  # re-read exactly once
    assert len(page.request.calls) == 2  # original + one retry
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_post_json_aisandbox_auth.py -v`
Expected: FAIL — auth header not attached / `AisandboxAuthError` not raised.

- [ ] **Step 3: Rewrite `_post_json`'s `attempt()` to merge auth + add a 401 refresh-retry**

Replace the body of `_post_json` (keep the signature) so the `attempt()` closure builds headers including aisandbox auth, and add a single 401 refresh-retry around `_run_with_retry`:

```python
        route = route_name or url
        is_aisandbox = self._is_aisandbox_url(url)

        async def attempt() -> Any:
            page = await self._checkout_page()
            try:
                headers = {"content-type": content_type}
                if is_aisandbox:
                    headers.update(await self._aisandbox_auth_headers())
                if os.environ.get("GFLOW_CLI_LOG_REQUEST_HEADERS") == "1":
                    logger.info(
                        "request_headers",
                        url=url,
                        headers=_redact_headers_for_log(headers),
                    )
                return await page.request.post(url, data=body_str, headers=headers)
            finally:
                self._checkin_page(page)

        resp = await self._run_with_retry(attempt, route=route)
        if is_aisandbox and resp.status == 401:
            # Cookie may have rotated mid-session — re-read once and retry.
            self._sapisid = None
            await self._ensure_sapisid()
            resp = await self._run_with_retry(attempt, route=route)
            if resp.status == 401:
                raise AisandboxAuthError(
                    detail="aisandbox-pa returned 401 after SAPISID refresh",
                    status=401,
                    instance=_make_instance(),
                    route=route,
                )

        text = await resp.text()
        _raise_for_non_retryable(resp, text, route=route)
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise WireFormatError(
                detail=f"non-JSON response: {text[:200]}",
                status=resp.status,
                instance=_make_instance(),
                route=route,
                discovery=_build_wire_format_discovery(resp, text, route),
            ) from e
```

Ensure `AisandboxAuthError` is imported in `client.py`. Verify `_redact_headers_for_log` redacts `authorization` (it should — confirm with the redaction test in Task 6).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_post_json_aisandbox_auth.py -v`
Expected: PASS (all three)

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_post_json_aisandbox_auth.py
git commit -m "feat(api): attach SAPISIDHASH to aisandbox _post_json + 401 refresh-retry"
```

---

## Task 5: Same wiring for `_patch_json` (commit step uses PATCH)

**Files:**
- Modify: `src/gflow_cli/api/client.py:424-458` (`_patch_json`)
- Test: `tests/api/test_post_json_aisandbox_auth.py` (add a PATCH case)

- [ ] **Step 1: Add the failing test**

```python
class _FakeRequestPatch(_FakeRequest):
    async def patch(self, url, *, data, headers):
        self.calls.append({"url": url, "headers": dict(headers)})
        return _FakeResp(self.statuses.pop(0))


@pytest.mark.integration
@pytest.mark.asyncio
async def test_patch_json_attaches_sapisidhash_for_aisandbox(monkeypatch):
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    page = _FakePage([200])
    page.request = _FakeRequestPatch([200])
    c = _client_with_page(page)
    await c._patch_json(
        "https://aisandbox-pa.googleapis.com/v1/flowWorkflows/wf-1",
        {"workflow": {"name": "wf-1"}, "updateMask": "metadata.primaryMediaId"},
    )
    assert page.request.calls[0]["headers"]["authorization"].startswith("SAPISIDHASH ")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_post_json_aisandbox_auth.py::test_patch_json_attaches_sapisidhash_for_aisandbox -v`
Expected: FAIL — no authorization header.

- [ ] **Step 3: Apply the same header-merge to `_patch_json`'s `attempt()`**

Mirror Task 4 inside `_patch_json`: compute `is_aisandbox = self._is_aisandbox_url(url)`, merge `await self._aisandbox_auth_headers()` into the headers when aisandbox, and add the identical 401 refresh-retry → `AisandboxAuthError` block around its `_run_with_retry`.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_post_json_aisandbox_auth.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/gflow_cli/api/client.py tests/api/test_post_json_aisandbox_auth.py
git commit -m "feat(api): attach SAPISIDHASH to aisandbox _patch_json + 401 refresh-retry"
```

---

## Task 6: Secret-redaction guard (Critical — docs/SECURITY.md)

**Files:**
- Test: `tests/api/test_sapisidhash_redaction.py`
- Modify (only if the test fails): `src/gflow_cli/api/client.py` redaction helper

- [ ] **Step 1: Write the failing test** (drive a real `_post_json` with `GFLOW_CLI_LOG_REQUEST_HEADERS=1` and assert no secret leaks)

```python
# tests/api/test_sapisidhash_redaction.py
import pytest
import structlog

from tests.api.test_post_json_aisandbox_auth import _FakePage, _client_with_page


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sapisidhash_and_sapisid_never_logged(monkeypatch, caplog):
    monkeypatch.setenv("GFLOW_CLI_LOG_REQUEST_HEADERS", "1")
    monkeypatch.setattr("gflow_cli.api.client.time.time", lambda: 1700000000.0)
    structlog.contextvars.clear_contextvars()
    page = _FakePage([200])
    c = _client_with_page(page, sapisid="SUPER_SECRET_SAPISID")
    with caplog.at_level("INFO"):
        await c._post_json("https://aisandbox-pa.googleapis.com/v1/flow/projects/p/scenes", {"workflowIds": []})
    blob = caplog.text
    assert "SUPER_SECRET_SAPISID" not in blob
    # The computed hash must also be redacted, not just the raw cookie.
    assert "1700000000_" not in blob
```

> If `caplog` doesn't capture structlog output in this repo, use the existing `LogCapture` fixture pattern (`[[structlog-cache-logger-off-for-tests]]` — `cache_logger_on_first_use=False` is required).

- [ ] **Step 2: Run to verify it fails or passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_sapisidhash_redaction.py -v`
Expected: If `_redact_headers_for_log` already strips `authorization` → PASS. If it leaks → FAIL.

- [ ] **Step 3: If failing, harden `_redact_headers_for_log`**

Ensure the redactor lower-cases header names and replaces `authorization` (and any `cookie`) values with `"<redacted>"` before logging. Add `authorization` to its denylist if absent.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/api/test_sapisidhash_redaction.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/api/test_sapisidhash_redaction.py src/gflow_cli/api/client.py
git commit -m "test(api): assert SAPISID/SAPISIDHASH never reach logs"
```

---

## Task 7: Live smoke — confirm the fix against the real API (opt-in, credit-free)

This is the empirical Issue #15 confirmation that the HAR could not provide (redacted headers). It exercises the **original 401 symptom**: a REST `uploadImage` (image asset upload — no generation credit) must now return success with the SAPISIDHASH header.

**Files:**
- Create: `tests/e2e/test_aisandbox_auth_live.py`

- [ ] **Step 1: Write the opt-in live test**

```python
# tests/e2e/test_aisandbox_auth_live.py
import os
from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient

pytestmark = pytest.mark.e2e

REASON = "set GFLOW_CLI_E2E_RUN_AUTH=1 + a verified profile to run the live aisandbox auth smoke"


@pytest.mark.skipif(os.environ.get("GFLOW_CLI_E2E_RUN_AUTH") != "1", reason=REASON)
@pytest.mark.asyncio
async def test_rest_upload_image_authenticates_after_sapisidhash(tmp_path):
    """Issue #15: REST uploadImage previously 401'd; with SAPISIDHASH it must 200.

    Credit-free — uploading an image asset does not spend a generation credit.
    """
    img = tmp_path / "smoke.png"
    img.write_bytes(
        bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de")
        + b"\x00" * 16
    )
    async with FlowApiClient() as client:
        project = await client.create_project(title="L0 auth smoke")
        asset = await client.upload_image(project.project_id, img)
        assert asset.asset_id  # no AisandboxAuthError => SAPISIDHASH accepted
```

- [ ] **Step 2: Run it opt-in against a verified profile**

Run: `set GFLOW_CLI_E2E_RUN_AUTH=1 && .venv\Scripts\python.exe -m pytest tests/e2e/test_aisandbox_auth_live.py -v`
Expected: PASS — `upload_image` returns an asset id (no 401 / `AisandboxAuthError`). This is the green light that L1 (`create_scene`) will authenticate.

> If it still 401s: the SAPISIDHASH origin or the cookie host filter is wrong. Re-run with `GFLOW_CLI_LOG_REQUEST_HEADERS=1`, diff the (redacted-safe) header set against a browser DevTools "Copy as cURL" of a working `uploadImage`, and adjust `_SAPISID_ORIGIN` / the cookie query URL. Do NOT proceed to L1 until this is green.

- [ ] **Step 3: Commit**

```bash
git add tests/e2e/test_aisandbox_auth_live.py
git commit -m "test(e2e): opt-in live smoke proving aisandbox SAPISIDHASH auth (Issue #15)"
```

---

## Task 8: Full scoped check + close-out

- [ ] **Step 1: Run the scoped suite + lint/format**

Run: `.venv\Scripts\python.exe -m pytest tests/api -v` then `/gflow:check`
Expected: all green; ruff/black/isort clean. (Do NOT run the full unscoped suite — `[[full-test-suite-ooms]]`.)

- [ ] **Step 2: Update KNOWN_ISSUES.md** — mark Issue #15 resolved by this L0 work; note the fix is in `_post_json`/`_patch_json` for all aisandbox-pa routes, SAPISID read from the live context (not disk).

- [ ] **Step 3: Commit**

```bash
git add KNOWN_ISSUES.md
git commit -m "docs(known-issues): resolve #15 — SAPISIDHASH wired for aisandbox REST"
```

---

## Self-Review

**Spec coverage (vs §6 L0 + §8 mitigations of the design spec):**
- "Wire SAPISIDHASH into `_post_json`/`_patch_json` for aisandbox routes" → Tasks 4, 5. ✓
- "Complete Issue #15's 3 investigation gates first" → done + recorded at top; empirical gate = Task 7. ✓
- "Map aisandbox 401 to a distinct error (not bare AuthExpiredError)" → Task 2 (`AisandboxAuthError`). ✓
- "Secret redaction of SAPISIDHASH/SAPISID/Authorization" → Task 6. ✓
- "Credit-free auth spike (create_scene → 200)" → approximated credit-free via `uploadImage` (Task 7); the literal `create_scene` 200 lands at the start of L1. ✓
- Windows DPAPI-encrypted cookie blocker → solved by reading from the live context (Task 3), not disk. ✓

**Placeholder scan:** no TBD/TODO; every code step carries real code; commands have expected output. The only deferred note is Task 2's "confirm `exit_code_for` name" — a 1-line grep, not a placeholder. ✓

**Type consistency:** `_is_aisandbox_url`, `_read_sapisid_from_context`, `_ensure_sapisid`, `_aisandbox_auth_headers`, `_sapisid`, `AisandboxAuthError`, `compute_sapisidhash(timestamp=, sapisid=, origin=)` used identically across Tasks 1–7. ✓

**Out of scope (next plans):** `gflow scene` compose (L1), video upload (L2). This plan stops at "aisandbox REST authenticates."
