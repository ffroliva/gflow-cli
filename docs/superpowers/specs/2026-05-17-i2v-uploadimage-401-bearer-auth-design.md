# Design — Issue #15: i2v `uploadImage` 401 (Bearer-auth mismatch)

> **Status:** Draft v2 (post-council-review) · **Date:** 2026-05-17 · **Issue:** [#15](https://github.com/ffroliva/gflow-cli/issues/15)
> **Decision:** Implement **Approach A**; track Approaches B & C as future alternatives.
> **Revision:** v2 incorporates a 5-dimension review council (architecture, security,
> testing, plan-readiness, independent root-cause verification).

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
3. **`FlowApiClient._post_json`**, `_patch_json`, and the inline `generate_video`
   POST all call `page.request.{post,patch}(...)` with **only a `content-type`
   header — no `Authorization`**.

**Root cause:** REST calls to `aisandbox-pa.googleapis.com` carry no
`Authorization` header. `page.request` (Playwright `APIRequestContext`) is a
**separate HTTP client that shares only the browser's cookie jar** — the Bearer
is an app-minted OAuth token that Flow's in-page JS attaches to its *own*
`fetch` calls, and an out-of-band `page.request` call never receives it. The
host rejects cookie-only requests with 401. `t2i` works because
`UiAutomationTransport` drives real UI clicks — the request is issued *by Flow's
own JS*, carrying the Bearer. `create_project` works because it targets the
`labs.google` tRPC host, which accepts cookie auth.

**Rival hypotheses considered and rejected:**
- *Expired/rotated session cookie* — rejected: `t2i` succeeds on the **same
  session in the same window**; the session is the one genuinely controlled
  variable and it is valid.
- *Wrong content-type* — rejected: the captured working request already uses
  `text/plain;charset=UTF-8`, exactly what `_post_json` sends.
- *Quota / rate limit* — rejected: the failure is 401, not 429/403.
- *SAPISIDHASH header missing* (the original PLAN.md / issue guess) —
  rejected: captured traffic carries `Bearer`, not SAPISIDHASH; additionally
  `experimental/sapisidhash.py` cannot read modern encrypted SAPISID cookies
  (its own docstring says so).

> **Confidence:** high, but evidence is static and the captured sample is
> dated 2026-05-08. The **one thing provable only by a live test** is that a
> `page.request.post` carrying *just* a Bearer (no other in-page headers) is
> *sufficient* to get 200 — Flow's JS client may also set `origin` /
> `sec-fetch-*`. **Phase 1 (§5) is a blocking live-verification gate** that
> must confirm this before any fix code lands.

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
Playwright page. A **pure module** — free functions plus a frozen
`BearerToken` value object; it holds **no `Page` state** (Page lifetime
belongs to `FlowApiClient`), so capture is unit-testable with a fake page.

```python
@dataclass(frozen=True)
class BearerToken:
    value: str

async def capture_bearer_token(page: Page, *, timeout_s: float = 30.0) -> BearerToken:
    """Observe `page`'s outgoing requests; return the first
    `Authorization: Bearer` token sent to aisandbox-pa.googleapis.com.
    Raises ApiAuthError if none appears within timeout_s."""
```

Mechanism is the proven `page.on("request")` interception from `bearer.py`,
extracted and trimmed: **no `_BearerCache`, no disk persistence, no httpx**.

**Token-handling rules (security-critical):**
- The intercepting closure holds the captured value in a single mutable
  container that is explicitly cleared in a `finally` block on any error exit,
  so the token is never reachable from a surviving exception's traceback locals.
- `capture_bearer_token` **never** passes the token value (or any
  token-derived value, e.g. its length) into an exception's arguments.

### 4.2 `FlowApiClient` changes (`api/client.py`)

- Hold `self._bearer: BearerToken | None` and `self._bearer_lock: asyncio.Lock`.
- **Capture during the existing bootstrap navigation** — register the request
  observer around the bootstrap `goto`, so capture costs ~0 extra.
- **Single header source.** A new `_with_bearer(url, base_headers) -> dict`
  helper is the **only** place the `Authorization` header is added. It attaches
  `authorization: f"Bearer {token}"` **only when the URL host is
  `aisandbox-pa.googleapis.com`**. `_post_json`, `_patch_json`, **and the
  inline `generate_video` POST** all build their headers through it — no other
  call site constructs request headers directly.
- **Single-flight re-capture.** On a 401 from an aisandbox-pa route, acquire
  `self._bearer_lock`; **re-check token freshness inside the lock** (another
  worker may have already refreshed it); if still stale, re-capture once and
  retry the call. This prevents N concurrent workers from launching N redundant
  captures under the per-worker Page pool. A second 401 raises `ApiAuthError`.

### 4.3 Error taxonomy (`errors.py`)

New `ApiAuthError(FlowApiError)`:
- `problem_type = "https://gflow-cli.dev/errors/api-auth"`, `title = "Flow API authorization failed"`.
- `EXIT_CODE_MAP` gains `ApiAuthError: 14` (codes 3–13 are in use). Placement
  among the existing `FlowApiError` subclasses is immaterial — they are
  siblings, not ancestors — but it is grouped with the other 4xx-auth entries
  to honour the file's most-specific-first convention.
- **`detail` contract:** `ApiAuthError.detail` is always exactly
  `f"HTTP {status}"`. No token value, no token-length integer, no captured
  data appears in `detail`, `route`, `instance`, or `remediation_hint`.
- Remediation hint does **not** force a login loop — it states the API
  rejected the request's bearer token after a re-capture, and offers login
  *and* "likely a token-capture regression — file a bug" as branches.

**Route discrimination.** `_raise_for_non_retryable` receives only a sanitized
route *name*, not a URL. The `ApiAuthError`-vs-`AuthExpiredError` decision is
therefore made by membership in an **enumerated set of aisandbox-pa route
names** (the set built by the §9 route-enumeration task), passed/known at the
classification site. A 401 from an aisandbox-pa route, after re-capture+retry
is exhausted, maps to `ApiAuthError`; any non-aisandbox-pa 401 keeps the
existing `AuthExpiredError` mapping. 403 handling is unchanged.

### 4.4 Data flow

```
FlowApiClient.__aenter__
  └─ bootstrap goto(FLOW_URL)  ──observe──▶  capture_bearer_token() ▶ self._bearer
upload_image / get_video_status / generate_video / _patch_json
  └─ headers = _with_bearer(url, {content-type})        # only header source
  └─ page.request.{post,patch}(url, headers=headers)
        ├─ 200 ▶ ok
        └─ 401 ▶ async with _bearer_lock:
                    if token unchanged since call started:
                        recapture_bearer()              # single-flight
                 retry once ▶ 401 again ▶ raise ApiAuthError
```

### 4.5 Security

- Token lives only in `FlowApiClient._bearer` for the client's lifetime;
  dropped on `__aexit__`. No `transport_bearer.json`-style disk cache.
- **`_redact_headers_for_log(headers) -> dict`** — a new helper, the **only**
  permitted way to log any dict that may contain an `authorization` key. It
  returns a copy with the value replaced by `Bearer <len=N>`. Every
  header-logging site, including the Phase-1 diagnostic path, must use it.
  (`_redact_for_log` covers bodies only — it does not touch headers.)
- **Threat model scope:** the in-memory-only guarantee assumes a
  **single-user host** with OS-level process isolation as the trust boundary.
  Process-memory inspection by another local user is out of scope (and is no
  worse than any browser holding the same token).
- A **security-review** pass (skill / `security-reviewer` agent) is a plan
  gate before merge.

### 4.6 Invariants (enforced by review + tests)

1. `_with_bearer` is the **sole** constructor of request headers for
   `aisandbox-pa.googleapis.com` routes.
2. Re-capture is **single-flight** — guarded by `self._bearer_lock` with a
   post-lock freshness re-check.
3. The Bearer token **never** appears in a log line, an exception argument,
   `ProblemDetails`, or on disk.
4. `ApiAuthError.detail` is a static `HTTP {status}` string.
5. The `Authorization` header is attached to aisandbox-pa hosts **only** —
   `labs.google` tRPC requests are byte-for-byte unchanged.

## 5. Phase 1 — live verification gate (blocking)

Before any fix code is written. **Owner:** the maintainer (human) runs the
live steps; the executing agent prepares the tooling and performs the diff.

1. Add **opt-in** outgoing-request-header logging to `_with_bearer`'s call
   sites, routed through `_redact_headers_for_log` (so `authorization` logs as
   `Bearer <len=N>`). This logging ships permanently behind an env flag
   (`GFLOW_CLI_LOG_REQUEST_HEADERS=1`) — useful for future transport debugging.
2. Maintainer runs `gflow video i2v` once (capturing the CLI's redacted
   outgoing headers) and captures a fresh working `uploadImage` from browser
   DevTools ("copy as cURL" — the **full** header set).
3. Diff **every header**, not just `Authorization`. Record the redacted diff
   as an evidence artifact under `tmp/issue-15-phase1/`.

### 5.1 Phase 1 outcome matrix

| Outcome | Meaning | Action |
|---|---|---|
| **PASS** | Only difference is `Authorization` (CLI missing, browser has `Bearer`) | Proceed to §4 implementation |
| **FAIL — extra header delta** | A non-`Authorization` header also differs (`origin`, `referer`, `sec-fetch-*`, …) | Re-design: extend §4.2 to copy the full required header set; revise spec before coding |
| **FAIL — auth present, still 401** | CLI already sends an `Authorization` and still 401s | This spec is **void**; open a fresh investigation issue — the root cause is elsewhere |

## 6. Test plan (TDD — red → green → refactor)

**Test fixtures** (defined once, in `tests/api/`): a `FakePage` exposing an
`on(event, callback)` that records listeners, plus a `FakeRequest` with
`url` and `headers`; a test fires a recorded listener with a `FakeRequest` to
simulate a captured Bearer — mirroring the helper style in existing
`tests/api/` tests.

Implement the three test files **in this order** (each red before its code):

1. **`tests/api/test_bearer_auth.py`** (pure module, no client dependency):
   - `capture_bearer_token` returns the token when the fake page fires a
     request carrying `Authorization: Bearer …` to aisandbox-pa.
   - raises `ApiAuthError` when no Bearer is seen — driven with
     `timeout_s=0.001` and a fake page that never fires, asserting the raise
     completes within ~1 s wall time (no real 30 s sleep in CI).
2. **Error-taxonomy tests** (pure, no Playwright):
   - `ApiAuthError` → exit code 14, correct problem-type, remediation hint
     contains no blind "re-login" loop, `detail` is `HTTP {status}` only.
   - structural uniqueness: `len(EXIT_CODE_MAP.values()) == len(set(...))`.
3. **`tests/api/test_client.py`** (depends on both):
   - aisandbox-pa requests carry `Authorization: Bearer …`.
   - **negative fixture:** a `create_project` (labs.google) call's headers
     contain **no** `authorization` key.
   - a 401 triggers **exactly one** re-capture: patch
     `bearer_auth.capture_bearer_token`, assert `call_count == 1` after the
     first 401; a second 401 raises `ApiAuthError`.
   - concurrent 401s across pooled Pages trigger **one** re-capture, not N
     (single-flight lock).
   - `structlog.testing.capture_logs`: no log line contains a raw `Bearer `
     value when an aisandbox-pa POST is made with header logging on.
- **Live** `@pytest.mark.live` `tests/live/test_i2v_live.py`: `gflow video
  i2v` end-to-end on a real session without 401 — the permanent regression
  guard (cannot run in CI; opt-in).
- Coverage floor unchanged: 80% overall, 90% domain/application.

## 7. Non-regression guarantees

- `create_project` / any `labs.google` tRPC call: header rule is host-scoped
  to `aisandbox-pa.googleapis.com` → untouched (verified by the §6 negative
  fixture).
- `UiAutomationTransport` (t2i): untouched.
- Experimental transports (`bearer`/`sapisidhash`/`evaluate_fetch`): untouched.
- All 577 existing tests stay green; CI gates (ruff, format, pyright, pytest,
  hygiene) must pass. The `main` branch is protected — this work merges via PR.

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
  undocumented internal client. Worth exploring if Flow ships a stable
  app-level fetch wrapper, or alongside the `evaluate_fetch` transport.
- **Disproven — SAPISIDHASH header** (original PLAN.md / issue guess):
  contradicted by captured traffic; kept here so it is not re-attempted.

## 9. Open questions / risks

- **Bearer alone may be insufficient.** Flow's in-page client may set more
  than the token (`origin`, `sec-fetch-*`). **Phase 1 must confirm a
  Bearer-only `page.request.post` returns 200**; if not, the §5.1 matrix
  routes to "extend to the full header set". This is the single highest
  design risk and is provable only by the live gate.
- **Capture reliability:** Approach A assumes the bootstrap navigation
  surfaces an interceptable aisandbox-pa Bearer request. If a bare editor
  load does not, the capture step must trigger a lightweight action or fail
  loudly within `timeout_s`. Phase 1 verifies this.
- **Concurrent re-capture:** N pooled workers hitting parallel 401s — mitigated
  by the single-flight `_bearer_lock` (§4.2); covered by a §6 test.
- **Token TTL:** reactive re-capture-on-401 is the chosen strategy. If live
  testing shows mid-run expiry is common, proactive TTL refresh becomes a
  fast follow-up (the `bearer.py` `_CachedAuth.is_expired` pattern).
- **Route enumeration (plan task 1):** enumerate every
  `aisandbox-pa.googleapis.com` route in `routes.py` / `client.py`
  (`uploadImage`, video generate, video status, `_patch_json` workflow PATCH).
  This set feeds both `_with_bearer`'s host check and §4.3's route
  discrimination.

## 10. Implementation phasing & quality gates

Suggested phase order for the implementation plan (writing-plans skill):

1. **Phase 1** — live verification gate (§5) — *blocking*.
2. **Phase 2** — `api/bearer_auth.py` module + `test_bearer_auth.py` (TDD).
3. **Phase 3** — `ApiAuthError` + `EXIT_CODE_MAP` + error-taxonomy tests
   (precedes client wiring, since `client.py` will import `ApiAuthError`).
4. **Phase 4** — `FlowApiClient` wiring (`_with_bearer`, single-flight
   re-capture) + `test_client.py` (TDD).
5. **Phase 5** — live E2E test, PLAN.md backlog pointer, docs/CHANGELOG.

Quality gates the plan will schedule: **systematic-debugging** discipline for
Phase 1, **TDD** for every unit, a **security-review** pass on token handling,
an **architecture** check on the `bearer_auth` boundary, **code-review**
before the PR merges, and **sub-agent orchestration** to run independent
reviews in parallel.

## 11. Review history

- **2026-05-17 — v1** drafted.
- **2026-05-17 — v2** — revised after a 5-dimension review council
  (architecture, security, testing/TDD, plan-readiness, independent
  root-cause verification). All five returned "Approve with changes" /
  "Confirmed pending live gate"; this revision incorporates every actionable
  finding (single-flight re-capture, single header source, route
  discrimination, token-leak hardening, `_redact_headers_for_log`, the Phase-1
  outcome matrix and full-header diff, the test-fixture and coverage gaps,
  and the rival-hypothesis enumeration).
