# Design — Issue #15: i2v `uploadImage` 401 (Bearer-auth mismatch)

> **Status:** Draft for review · **Date:** 2026-05-17 · **Issue:** [#15](https://github.com/ffroliva/gflow-cli/issues/15)
> **Decision:** Implement **Approach A**; track Approaches B & C as future alternatives.

---

## 1. Problem

`gflow video i2v <image> <prompt>` fails: `POST /v1/flow/uploadImage` returns HTTP 401,
surfaced as `AuthExpiredError` (exit 3) — even seconds after a verified fresh login.
`t2i` and batch image generation work on the same session in the same window.

The 401 also produces a **misleading remediation hint** (`Run gflow auth login`),
sending users into a login loop when login was never the problem.

## 2. Root-cause investigation

Investigation was **static** (PLAN.md gates 1 & 2 + the captured sample for gate 3).
Findings, from three independent sources:

1. **Captured working request** (`samples/captured/01_upload_image.json`):
   request header `authorization: "Bearer <REDACTED_BEARER>"`. The string
   `sapisidhash` appears **nowhere** in the captured traffic.
2. **`api/transports/experimental/bearer.py`** intercepts requests to
   `aisandbox-pa.googleapis.com` looking for exactly an `Authorization: Bearer`
   header — confirming Flow's web app sends one.
3. **`FlowApiClient._post_json`** (and `_patch_json`, and the inline
   `generate_video` POST) call `page.request.post(...)` with **only a
   `content-type` header — no `Authorization`**. `page.request`
   (Playwright `APIRequestContext`) attaches session cookies but never runs
   page JS, so it omits the Bearer token the Flow app adds.

**Root cause:** REST calls to `aisandbox-pa.googleapis.com` carry no
`Authorization` header. The host rejects cookie-only requests with 401.
`t2i` works because `UiAutomationTransport` drives real UI clicks — the
page's own JS issues the fetch *with* the Bearer. `create_project` works
because it targets the `labs.google` tRPC host, which accepts cookie auth.

**Disproven hypothesis:** PLAN.md and issue #15 guessed a missing
*SAPISIDHASH* header. The captured traffic contradicts this; additionally,
`experimental/sapisidhash.py` cannot read modern encrypted SAPISID cookies
(its own docstring says so). SAPISIDHASH is a dead end for this bug.

> **Confidence:** high, but evidence is static and the captured sample is
> dated 2026-05-08. **Phase 1 (below) is a blocking live-verification gate**
> that must confirm the root cause empirically before any fix code lands.

## 3. Goals / non-goals

**Goals**
- `gflow video i2v` completes end-to-end (upload → generate → poll → download).
- All `aisandbox-pa.googleapis.com` REST routes carry valid `Authorization`.
- A 401 that survives a token re-capture is reported as a distinct,
  accurately-worded error — not `AuthExpiredError`.
- Zero regression to working paths.

**Non-goals**
- Rewriting i2v to use UI drag-and-drop upload.
- Promoting the experimental httpx transports to production.
- Proactive token-TTL refresh (reactive re-capture only; see §9).
- Touching the 403 → error mapping (tracked separately).

## 4. Design — Approach A (capture the Bearer, attach the header)

### 4.1 New module — `src/gflow_cli/api/bearer_auth.py`

Single responsibility: obtain the Flow OAuth Bearer token from a live
Playwright page. Token is held **in memory only** — never written to disk,
never logged.

```python
@dataclass(frozen=True)
class BearerToken:
    value: str
    captured_at: float

async def capture_bearer_token(page: Page, *, timeout_s: float = 30.0) -> BearerToken:
    """Observe `page`'s outgoing requests; return the first
    `Authorization: Bearer` token sent to aisandbox-pa.googleapis.com.
    Raises ApiAuthError if none appears within timeout_s."""
```

Mechanism is the proven `page.on("request")` interception from `bearer.py`,
extracted and trimmed: **no `_BearerCache`, no disk persistence, no httpx**.

### 4.2 `FlowApiClient` changes (`api/client.py`)

- Hold `self._bearer: BearerToken | None`.
- **Capture during the existing bootstrap navigation** — register the
  request observer around the bootstrap `goto`, so capture costs ~0 extra.
- Request helpers (`_post_json`, `_patch_json`, inline `generate_video`
  POST) attach `authorization: f"Bearer {token}"` **only for routes whose
  host is `aisandbox-pa.googleapis.com`** — `labs.google` tRPC calls
  (`create_project`) are left exactly as-is.
- A `_with_bearer` helper centralizes header construction so the rule lives
  in one place.
- **Reactive refresh:** on a 401 from an aisandbox-pa route, re-capture the
  token once and retry the call. A second 401 raises `ApiAuthError` (§4.3).

### 4.3 Error taxonomy (`errors.py`)

New `ApiAuthError(FlowApiError)`:
- `problem_type = "https://gflow-cli.dev/errors/api-auth"`, `title = "Flow API authorization failed"`.
- `EXIT_CODE_MAP` entry **`14`** — the next free exit code (3–13 are in use).
- Remediation hint that does **not** force a login loop — it states the API
  rejected the request's bearer token after a re-capture, and offers login
  *and* "likely a token-capture regression — file a bug" as branches.

`_raise_for_non_retryable` in `client.py`: a 401 from an aisandbox-pa route
(after re-capture+retry is exhausted) maps to `ApiAuthError`, not
`AuthExpiredError`. The 401→`AuthExpiredError` mapping is retained for any
non-aisandbox-pa 401. 403 handling is unchanged.

### 4.4 Data flow

```
FlowApiClient.__aenter__
  └─ bootstrap goto(FLOW_URL)  ──observe──▶  capture_bearer_token() ▶ self._bearer
upload_image / get_video_status / generate_video / _patch_json
  └─ page.request.{post,patch}(url, headers={content-type, authorization: Bearer …})
        ├─ 200 ▶ ok
        └─ 401 ▶ recapture_bearer() ▶ retry once ▶ 401 again ▶ raise ApiAuthError
```

### 4.5 Security

- Token lives only in `FlowApiClient._bearer` for the client's lifetime;
  dropped on `__aexit__`. No `transport_bearer.json`-style disk cache.
- Token is never logged: `_redact_for_log` already covers bodies; header
  logging (added for Phase 1) must redact `authorization`.
- A **security-review** pass (skill / `security-reviewer` agent) is a plan
  gate before merge.

## 5. Phase 1 — live verification gate (blocking)

Before any fix code is written:
1. Add opt-in outgoing-request-header logging to the request helpers
   (`authorization` value redacted to `Bearer <len=N>`).
2. Maintainer runs `gflow video i2v` once and captures a fresh working
   `uploadImage` from browser DevTools ("copy as cURL").
3. Diff: confirm the CLI request is missing `Authorization` and the working
   request carries `Bearer`. **If the diff shows anything else, stop and
   re-design.**

This gate converts the high-confidence static hypothesis into proof.

## 6. Test plan (TDD — red → green → refactor)

- **Unit** `tests/api/test_bearer_auth.py`: `capture_bearer_token` returns
  the token from a mocked `page.on("request")`; raises `ApiAuthError` when
  no Bearer is seen within the timeout.
- **Unit** `tests/api/test_client.py`: aisandbox-pa requests carry
  `Authorization: Bearer …`; `labs.google` requests do **not**; a 401
  triggers exactly one re-capture + retry; a second 401 raises `ApiAuthError`.
- **Unit** error-taxonomy tests: `ApiAuthError` → exit code 14, correct
  problem-type, remediation hint contains no blind "re-login" loop.
- **Live** `@pytest.mark.live` `tests/...`: `gflow video i2v` end-to-end on
  a real session without 401 (the permanent regression guard).
- Coverage floor unchanged: 80% overall, 90% domain/application.

## 7. Non-regression guarantees

- `create_project` / any `labs.google` tRPC call: header rule is host-scoped
  to `aisandbox-pa.googleapis.com` → untouched.
- `UiAutomationTransport` (t2i): untouched.
- Experimental transports (`bearer`/`sapisidhash`/`evaluate_fetch`): untouched.
- All 577 existing tests stay green; CI gates (ruff, format, pyright, pytest,
  hygiene) must pass.

## 8. Alternatives & future exploration

Approaches B and C are **not implemented now** but recorded for future work.
The implementation plan includes a task to add a one-line PLAN.md backlog
pointer to this section (matching the existing
`CDP Attach Transport — BACKLOG (deferred)` pattern).

- **Approach B — inline the capture in `client.py`.** Avoids a new module.
  Rejected now: `client.py` is already large and the capture logic would not
  be independently unit-testable. Revisit only if the module boundary proves
  to add no value.
- **Approach C — issue the POST inside page JS via `page.evaluate(fetch())`.**
  Would let the browser attach auth itself. Rejected now: a raw in-page
  `fetch` to aisandbox-pa does **not** automatically carry the Bearer (Flow's
  own JS networking layer adds it) — making it work means reusing Flow's
  undocumented internal client. Worth exploring if Flow ever ships a stable
  app-level fetch wrapper, or alongside the `evaluate_fetch` transport.
- **Disproven — SAPISIDHASH header** (original PLAN.md / issue guess):
  contradicted by captured traffic; kept here so it is not re-attempted.

## 9. Open questions / risks

- **Capture reliability:** Approach A assumes the bootstrap navigation
  surfaces an interceptable aisandbox-pa Bearer request. If a bare editor
  load does not, the capture step must trigger a lightweight action or fail
  loudly. **Phase 1 verifies this.**
- **Token TTL:** reactive re-capture-on-401 is the chosen strategy. If live
  testing shows mid-run expiry is common, proactive TTL refresh becomes a
  fast follow-up (the `bearer.py` `_CachedAuth.is_expired` pattern).
- **Route enumeration:** the implementation plan's first task enumerates
  every `aisandbox-pa.googleapis.com` route in `routes.py` / `client.py`
  (`uploadImage`, video generate, video status, `_patch_json` workflow PATCH).

## 10. Skills / quality gates for implementation

The implementation plan (produced next via the writing-plans skill) will
schedule: **systematic-debugging** discipline for Phase 1, **TDD** for every
unit, a **security-review** pass on token handling, an **architecture**
check on the `bearer_auth` module boundary, **code-review** before merge,
and **sub-agent orchestration** to run the independent reviews in parallel.
