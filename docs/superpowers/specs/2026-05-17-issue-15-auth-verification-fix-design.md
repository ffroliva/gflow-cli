# Issue #15 — Auth-Layer Verification Fix — Design

> **Status:** Approved design · **Date:** 2026-05-17 · **Revision:** 2
> **Supersedes:** `2026-05-17-i2v-uploadimage-401-bearer-auth-design.md` (the v2
> bearer-auth spec) and its plan, in full.
> **Corrects:** `2026-05-17-issue-15-root-cause-findings.md` §3 & §5 — the
> "session cookie is not flushed to disk" mechanism is **disproven** (see §2).
> **Reviewed by:** a design-stage architecture / security / regression council,
> then a spec-stage implementability / security / accuracy / test-strategy
> council. Both sets of findings are folded in (§11).
>
> **Rev 2 changelog:** precise `tenacity` retry contract; full
> `evaluate_session_response` decision table; enumerated `detail` literals;
> named log events; `InternalChromiumStrategy` exception handling specified;
> profile-dir `strict` divergence documented; durability hardening (§10);
> expanded test strategy (§8).

## 1. Problem

`gflow video i2v` fails at its first API call — `createProject` — with HTTP
401, and never reaches `uploadImage`.

Root cause: `gflow auth login` cannot tell a completed Flow sign-in from a
half-finished one. Its `RealChromeStrategy` gates success on the presence of
the Google **`SAPISID`** cookie. `SAPISID` is set the moment a user signs into
their *Google account* — independently of, and before, the *Flow app's* own
NextAuth sign-in that mints `__Secure-next-auth.session-token`. Any login that
signs into Google but does not carry the Flow app sign-in through to completion
is still reported `[OK] Session captured and verified`, leaving a profile that
is signed into Google but **signed out of Flow**. The Flow tRPC API
authenticates on the NextAuth session cookie, so `createProject` 401s.

`InternalChromiumStrategy` (the fallback) has the same flavour of bug, milder:
it checks `SAPISID` plus a UI-text scrape (`"New project"` / `"Your projects"`
visible) — fragile and not authoritative.

## 2. Evidence & corrected mechanism

Confirmed by live probes (`tmp/issue-15-phase2/`, exploratory, untracked):

- A **completed** Flow sign-in mints `__Secure-next-auth.session-token` as a
  **persistent** cookie (~30-day expiry) that **survives a normal Chrome close**
  and lands in the profile's on-disk cookie DB. *(probe step 1)*
- A `gflow auth login` run that did not complete the Flow app sign-in exited
  `0`, printed `verified`, yet `GET /api/auth/session` on that profile returned
  `200 {}` — signed out. *(`issue15` profile)*

This **corrects** the phase-1 findings doc: the session-token is **not** a
session-scoped cookie and **is not** lost on browser close. The capture
mechanism (the passive-capture pattern's post-close cookie read) is mechanically
sound. **The defect is the verification step**, which checks the wrong cookie —
and a UX that does not reliably land users on a completed Flow sign-in.

## 3. Goal & non-goals

**Goal:** `gflow auth login` must never report success unless the profile holds
a real, usable Flow app session — verified against the same surface
`FlowApiClient` authenticates on.

**In scope:**

- A new, self-contained verification module.
- `RealChromeStrategy` and `InternalChromiumStrategy` both adopt it (one
  consistent definition of "signed in").
- Both strategies open the browser at the Flow URL with guidance that
  distinguishes Google sign-in from Flow sign-in.
- An honest, actionable failure when the Flow sign-in was not completed.

**Non-goals (YAGNI / explicitly out of scope):**

- `FlowApiClient` — unchanged; it already works once a profile is genuinely
  signed in (verified: it loads the session purely via `launch_persistent_context`
  on the profile dir, no cookie/token parsing).
- `auth status` — stays a lightweight file-existence check.
- CDP / `--remote-debugging-port` / `storage_state` / live-detection — the
  no-debug-port stealth ADR is kept (Approach C rejected).
- reCAPTCHA / video-generation routes — unrelated.
- A configurable verification-timeout knob — fixed sensible defaults; a knob
  can come later.

## 4. Design

Approach **A** — *honest post-close verification* — chosen because it keeps the
no-debug-port stealth ADR fully intact. Net change: **1 new file, 2 edited
files, 1 docstring touch-up**, plus a test fixture and a `KNOWN_ISSUES.md` note.

### 4.1 New module — `src/gflow_cli/auth/verification.py`

Single responsibility: answer "does this profile hold a usable Flow app
session?"

**Import rule:** `verification.py` has **no top-level import of `.strategies`**.
The cycle `strategies.py → real_chrome.py → verification.py → strategies.py`
would trigger at module load; the `async_playwright` shim is imported **lazily,
inside `verify_flow_session`**.

Constant:

- `SESSION_API_URL = "https://labs.google/fx/api/auth/session"`

`FlowSessionOutcome(str, Enum)` — four mutually-exclusive outcomes:

| Value | Meaning |
|---|---|
| `AUTHENTICATED` | `/api/auth/session` returned an authenticated user — a usable Flow session. |
| `GOOGLE_SESSION_ONLY` | No Flow session, but the Google `SAPISID` cookie is present. |
| `NO_SESSION` | No Flow session and no Google sign-in detected. |
| `VERIFICATION_ERROR` | The check could not be completed or returned an unexpected shape. |

`FlowSessionStatus` — `@dataclass(frozen=True)`:

- `outcome: FlowSessionOutcome`
- `user_email: str | None` — from the session payload, only when `AUTHENTICATED`.
- `source: str` — originating strategy (`"chrome"` / `"internal"`), for log
  filtering.
- `detail: str` — a short, **static** human-readable label, one of a fixed set
  keyed by `outcome` (below). Never built from response-body, cookie, or
  exception content.
- `authenticated` — `@property` → `outcome is FlowSessionOutcome.AUTHENTICATED`.

The four `detail` literals (the only permitted values):

| Outcome | `detail` |
|---|---|
| `AUTHENTICATED` | `"Flow app session verified."` |
| `GOOGLE_SESSION_ONLY` | `"Signed in to Google, but not to the Flow app."` |
| `NO_SESSION` | `"No sign-in detected."` |
| `VERIFICATION_ERROR` | `"Could not verify the Flow session."` |

#### `evaluate_session_response`

`evaluate_session_response(status_code: int, body: str, *, google_session: bool,
source: str) -> FlowSessionStatus` — **pure, total** function: no I/O, no
exceptions raised or used for control flow; every `(status_code, body)` maps to
exactly one outcome. Decision table (first match wins):

| Condition | Outcome |
|---|---|
| `status_code != 200` | `VERIFICATION_ERROR` |
| `200`, `body` is not a JSON **object** (malformed, partial, empty, whitespace, JSON array, JSON scalar) | `VERIFICATION_ERROR` |
| `200`, JSON object, `user` key absent / `null` / `{}` | `GOOGLE_SESSION_ONLY` if `google_session` else `NO_SESSION` |
| `200`, JSON object, `user` is a dict with a non-empty string `email` | `AUTHENTICATED` (`user_email` = that email) |
| `200`, JSON object, `user` present but malformed (not a dict, or no non-empty string `email`) | `VERIFICATION_ERROR` (unexpected shape — see §10) |

Only `user.email` is read; `user.name`, `user.image`, and `expires` are
ignored. The parsed JSON dict is **never** attached to any exception object or
log field — only the extracted scalar `email` is retained beyond the parsing
call site.

#### `verify_flow_session`

`async verify_flow_session(profile_dir: Path, *, channel: str = "chrome",
source: str = "chrome") -> FlowSessionStatus` — the I/O wrapper used by
`RealChromeStrategy`:

1. **Boundary recheck.** `profile_dir.resolve(strict=True)` must be inside
   `get_settings().home.resolve()`; otherwise raise `SecurityError`. `strict=True`
   is safe and correct here — the profile dir already exists by the time this
   runs (see §4.2 for why `RealChromeStrategy.login`'s own pre-`mkdir` check
   deliberately stays `strict=False`).
2. Lazily `from .strategies import async_playwright` **inside the function**.
3. Launch a headless `launch_persistent_context(user_data_dir=str(profile_dir),
   channel=channel, headless=True)`.
4. In `try`: read cookies → `google_session = any(c["name"] == "SAPISID")`; fetch
   the session via `ctx.request.get(SESSION_API_URL, timeout=15000)` (15 s
   per-request timeout; no extra page is created). **Retry contract:** up to **3
   attempts total**, exponential backoff (~1 s → 2 s, cap 8 s). An attempt is
   retried **only** if the fetch raises a Playwright network/timeout error **or**
   returns HTTP status ∈ `{429, 503, 504}`. Any other non-200 (401/403/404/500/
   3xx/…) is **not** retried — it is passed straight to `evaluate_session_response`
   (→ `VERIFICATION_ERROR`). The plan may implement this as a `tenacity`-decorated
   inner `_fetch_session` helper or an explicit loop.
5. Pass the final `(status_code, body)` to `evaluate_session_response`. If no
   response was ever obtained (retries exhausted, launch failure, unexpected
   exception) → return `FlowSessionStatus(VERIFICATION_ERROR, …)`. **Fail-closed:
   `verify_flow_session` never returns `AUTHENTICATED` on uncertainty.**
   When the `VERIFICATION_ERROR` is caused by a definite non-200 response, log
   the `status_code` (never the body) at WARNING — see §10.
6. `finally`: `await ctx.close()` — fully awaited, so the profile's SQLite lock
   is released before the function returns, on both the success and error paths.

`verify_flow_session` is **side-effect-free** beyond the browser probe and that
one WARNING log — it writes no files and emits no marker; callers own those
side effects.

### 4.2 `src/gflow_cli/auth/real_chrome.py` — edit

- Append the Flow URL (the existing `GEMINI_URL` constant) to `chrome_args` so
  Chrome opens directly on the Flow page.
- Rewrite the console guidance: state explicitly that signing into the Google
  account is **not** sufficient — the user must continue until the working Flow
  editor (prompt box / their projects) is loaded, *then* close Chrome.
- **Profile-dir boundary check — deliberate `strict` divergence.**
  `RealChromeStrategy.login`'s existing pre-`mkdir` boundary check stays
  `resolve(strict=False)`. It **must** run before `mkdir` — if `mkdir` ran first,
  a path-traversal profile name (`../../evil`) would have its directory created
  *outside* `GFLOW_CLI_HOME` before being rejected. `strict=False` is **not**
  weaker against symlink attacks: `Path.resolve()` resolves existing symlinks
  regardless of the `strict` flag; `strict` only controls whether resolution
  errors on a *non-existent* final component. The deeper `strict=True` check
  lives in `verify_flow_session` (§4.1 step 1), which runs after the directory
  exists. This divergence is intentional.
- Replace the inline post-close verification block (the direct `async_playwright`
  import, `launch_persistent_context`, `cookies()`, `has_sapisid` check) with a
  single call: `status = await verify_flow_session(profile_dir,
  channel="chrome", source="chrome")`. This runs **after** `proc.wait()` returns
  (the login Chrome has exited and released the profile lock — sequencing
  preserved).
  - `status.authenticated` → emit structlog **`auth_flow_session_verified`**
    (`outcome`, `source`, `user_email`); **write the `.gflow_browser_strategy`
    marker** (`"chrome"`); print `[OK] Flow session verified (<email>).`
  - else → emit structlog WARNING **`auth_flow_session_unverified`** (`outcome`,
    `source`); raise `AuthMissingError` (exit code 8) with an `outcome`-tailored
    message + `remediation_hint` (see §6).
- The old structlog events `auth_login_success_verified` and
  `auth_login_no_cookies` are **replaced** by `auth_flow_session_verified` /
  `auth_flow_session_unverified`.

> The `.gflow_browser_strategy` marker write is **load-bearing** —
> `browser_manager.channel_for_profile` reads it; dropping it makes
> `FlowApiClient` open a Chrome-130+ profile with bundled Chromium and fail. It
> is preserved in the success branch.

### 4.3 `src/gflow_cli/auth/internal_chromium.py` — edit

`InternalChromiumStrategy` already polls *live* (it controls the browser via
Playwright and auto-detects success without waiting for a close). Change only
*what* it checks:

- In the poll loop, replace the `SAPISID` + `get_by_text("New project")` /
  `("Your projects")` test with: `resp = await page.request.get(SESSION_API_URL,
  timeout=15000)`, then `evaluate_session_response(resp.status, await
  resp.text(), google_session=<SAPISID present>, source="internal")`; success
  when `outcome is AUTHENTICATED`.
- Query `/api/auth/session` on a modest cadence — `asyncio.sleep(3)` between
  polls (not every 1 s) — within the existing time-budget `while` loop;
  otherwise the loop structure is unchanged.
- **Non-`AUTHENTICATED` outcomes mid-poll — including `VERIFICATION_ERROR` —
  are not surfaced distinctly: the loop keeps polling until the time budget is
  exhausted** (the user may still be completing sign-in). On budget exhaustion
  or browser close the existing `AuthLoginTimeoutError` is raised; its wording
  is refreshed to mention reaching the Flow editor.
- **Replace the loop's bare `except Exception: break`** with differentiated
  handling: re-raise `asyncio.CancelledError`; treat Playwright
  "target/context/browser closed" errors as the browser-closed signal (break to
  the closed-handling branch); log any other unexpected exception at WARNING
  before breaking.
- Internal does not write `.gflow_browser_strategy` (absence ⇒ bundled
  Chromium ⇒ correct) — unchanged.

### 4.4 `src/gflow_cli/errors.py` — edit

No class or behaviour change. `AuthMissingError` is reused (it already maps to
**exit code 8** and carries `remediation_hint`). Only its docstring is
refreshed — it currently describes a `SAPISID` / `SapisidhashTransport`
scenario that no longer reflects how the error is used.

## 5. Data flow

```
gflow auth login
  └─ auth.login() → factory.create(mode) → strategy.login(profile_dir, headless)
       ├─ RealChromeStrategy:
       │     launch system Chrome (raw subprocess, no debug port) at the Flow URL
       │     → user signs in & closes Chrome → proc.wait()
       │     → verify_flow_session(channel="chrome", source="chrome")
       │           → headless context → ctx.request.get(/api/auth/session)
       │           → evaluate_session_response
       │     → AUTHENTICATED ? write marker + log + success
       │                     : raise AuthMissingError (exit 8)
       └─ InternalChromiumStrategy:
             launch bundled Chromium (Playwright-controlled) at the Flow URL
             → live poll (~3s): page.request.get(/api/auth/session)
                                 → evaluate_session_response
             → AUTHENTICATED ? success
                             : (budget exhausted / closed) raise AuthLoginTimeoutError
```

`FlowApiClient` later opens the profile and authenticates on the now-guaranteed
`__Secure-next-auth.session-token` — no change.

## 6. Error handling & fail-closed semantics

Only `FlowSessionOutcome.AUTHENTICATED` green-lights a login. Every other
outcome fails the login — verification never reports success under uncertainty.

`VERIFICATION_ERROR` is a **distinct fail-closed outcome**, not conflated with
`NO_SESSION`: a network timeout is not evidence that the user is signed out. It
produces a connectivity-flavoured remediation rather than a "you didn't sign in"
message, and only after the retry budget is spent — so a *correct* login on a
slow link is not falsely failed without recourse.

`AuthMissingError` messages (`real_chrome.py` call site), by `outcome`:

- `GOOGLE_SESSION_ONLY` → "Signed in to your Google account, but the Flow app
  sign-in wasn't completed. Re-run `gflow auth login` and continue until the
  Flow editor (the prompt box / your projects) loads before closing Chrome."
- `NO_SESSION` → "No sign-in detected. Re-run `gflow auth login`, sign in to
  Google, and continue until the Flow editor loads."
- `VERIFICATION_ERROR` → "Could not verify the Flow session — this is often a
  network problem. Check your connection and re-run `gflow auth login`."

**Strategy asymmetry (deliberate).** `RealChromeStrategy` verifies *after* the
browser closes, so it surfaces the `outcome`-tailored `AuthMissingError` above.
`InternalChromiumStrategy` verifies *live* and simply keeps polling until it
either sees `AUTHENTICATED` or the time budget runs out — its failure surface is
the existing `AuthLoginTimeoutError`. Both are fail-closed; only the message
shape differs.

## 7. Security considerations

Folded in from the council security reviews (must-fix items):

- **No secret in logs or `detail`.** `__Secure-next-auth.session-token` and all
  raw cookie values are never logged. `FlowSessionStatus.detail` is one of the
  four static literals in §4.1 — never interpolated from response-body, cookie,
  or exception content. The raw `/api/auth/session` body is never logged or
  stored, at any level. The parsed JSON dict is never attached to an exception
  object; only the scalar `email` survives the parsing call site.
- **Minimal payload extraction.** `evaluate_session_response` reads only
  `user.email`; `user.name`, `user.image` (a signed CDN URL), and `expires` are
  discarded.
- **`user_email`** may be logged at INFO on success (the user's own email;
  consistent with the prior `auth_login_success_verified` event) — never inside
  a body dump or at DEBUG.
- **Fail-closed.** Any uncertainty → `VERIFICATION_ERROR`; never
  `AUTHENTICATED`. Non-200 and malformed/unexpected bodies are uncertainty, not
  "unauthenticated". `evaluate_session_response`'s total, exception-free design
  means no exceptional path can bypass the outcome enum.
- **No `storage_state`.** Deliberately not used — it would serialise the session
  token into a plaintext JSON file. The session stays inside Chrome's own cookie
  store, encrypted at rest by the OS (DPAPI on Windows). **Scope of that
  protection:** DPAPI protects the cookie store against processes running under
  a *different* Windows user account; it does not protect against code running
  in the *same* user session — consistent with `docs/SECURITY.md`'s single-user
  local-CLI threat model. No new credential file is written.
- **Profile-dir boundary.** `verify_flow_session` re-validates `profile_dir`
  with `resolve(strict=True)` against `GFLOW_CLI_HOME`, raising `SecurityError`.
  `RealChromeStrategy.login`'s own pre-`mkdir` check stays `strict=False` for the
  reason given in §4.2 — `resolve()` follows symlinks regardless of `strict`, so
  this is not a weakening.
- **No new untrusted surface.** `SESSION_API_URL` is a hard-coded constant — no
  SSRF surface; TLS via Chromium's stack (same trust surface as `FlowApiClient`).

## 8. Testing strategy (TDD — red → green → refactor)

**Wave 1 — `evaluate_session_response` (pure, zero mocks).** One case per
decision-table row, plus boundary cases the audit surfaced:

- `200` + authenticated `user` with `email` → `AUTHENTICATED`, email extracted;
  `.authenticated` property is `True`.
- `200` + `{}` , `google_session=True` → `GOOGLE_SESSION_ONLY`.
- `200` + `{}` , `google_session=False` → `NO_SESSION`.
- `200` + `{"user": null}` → `GOOGLE_SESSION_ONLY` / `NO_SESSION` per
  `google_session` (must not crash).
- `200` + `{"user": {"name": "x"}}` (no `email`) → `VERIFICATION_ERROR`.
- `200` + `{"user": {"email": ""}}` (empty email) → `VERIFICATION_ERROR`.
- `200` + JSON array `"[]"` → `VERIFICATION_ERROR`.
- `200` + malformed / partial JSON, empty string, whitespace-only → `VERIFICATION_ERROR`.
- non-200 (401 / 500 / 302) → `VERIFICATION_ERROR`, regardless of `google_session`.
- `source` argument is passed through to `FlowSessionStatus.source`.
- An **endpoint-contract fixture** capturing the real authenticated `200` body
  shape (see §10) is used here, so a future Google shape change fails this test.

**Wave 2 — `verify_flow_session` (mock the `.strategies` `async_playwright`
shim).** The correct patch target is `gflow_cli.auth.strategies.async_playwright`
(where the lazy import resolves) — **not** `playwright.async_api.async_playwright`.
The mock must stub `ctx.request.get` returning an object with `.status` and an
async `.text()`:

- authenticated probe → `AUTHENTICATED`.
- unauthenticated probe, SAPISID present / absent → `GOOGLE_SESSION_ONLY` / `NO_SESSION`.
- retryable error (timeout, then 503) retried up to 3 attempts → exhausted →
  `VERIFICATION_ERROR`.
- non-retryable non-200 (401) → not retried → `VERIFICATION_ERROR` in one attempt.
- `launch_persistent_context` raises → `VERIFICATION_ERROR`.
- `ctx.close()` is awaited in `finally` on both success and error paths.
- `profile_dir` outside `GFLOW_CLI_HOME` → `SecurityError`.

**Wave 3 — strategy integration** (`tests/auth/strategies/test_strategies.py`):

- **Rework** the shared `_build_verify_pw_mock` helper: patch
  `gflow_cli.auth.strategies.async_playwright`, and stub `request.get` (with
  configurable status/body) instead of `goto`.
- **Update + rename** `test_real_chrome_success_verified_via_sapisid` →
  `..._via_flow_session`, repointed to the `/api/auth/session` flow.
- **Add** `test_real_chrome_writes_gflow_browser_strategy_on_success` — asserts
  the marker file contains `"chrome"` (the load-bearing regression guard; lives
  here because the *write* is a `RealChromeStrategy.login` side effect).
- **Add** parametrized tests: each non-`AUTHENTICATED` outcome
  (`GOOGLE_SESSION_ONLY`, `NO_SESSION`, `VERIFICATION_ERROR`) raises
  `AuthMissingError` with the matching §6 message.
- **Update** the internal-chromium tests (`test_internal_chromium_standard_behavior`,
  `test_internal_chromium_timeout_raises`): replace the `get_by_text` + `SAPISID`
  mocks with a `page.request.get` mock; confirm a timeout still raises
  `AuthLoginTimeoutError` and `ctx.close()` is still called.
- **Must NOT change:** `test_real_chrome_launch_flags`,
  `test_real_chrome_privacy_guard`, `test_real_chrome_timeout_raises`.

**Wave 4 — BDD.** `tests/test_browser_manager.py` and the auth `.feature` files
**must NOT change**; before relying on that, **verify** the feature step
definitions / `conftest.py` do not assert on the renamed structlog event
strings. Step mocks are updated to produce a verifiable session where needed.

Coverage: ≥80% overall maintained; `verification.py` targeted near-100% (the
pure core makes this cheap).

## 9. Regression guardrails (what must not break)

From the council regression review:

- **`.gflow_browser_strategy` marker** — must still be written on a successful
  real-chrome login (HIGH risk; covered by the new Wave-3 test).
- **Exit code 8** — verification failure must raise `AuthMissingError` (a
  `GFlowError` subclass), not fall through to the catch-all exit 1.
- **`FlowApiClient`** — confirmed unchanged; a profile from the fixed flow is a
  strict superset of today's.
- **Sequencing** — `verify_flow_session` runs only after the login Chrome
  process has exited (`proc.wait()`), so there is no SQLite-lock contention; its
  own context is fully closed (`await ctx.close()` in `finally`) before return.
- **Slow-network correctness** — the retry budget + `VERIFICATION_ERROR`
  classification ensure a correct login on a flaky link is not falsely failed.
- **`InternalChromiumStrategy`** — `ctx.close()` is still reached on the timeout
  path after the polling replacement.
- structlog event renames are safe **once Wave-4 confirms** no test/step
  definition asserts on the old event strings.

## 10. Durability & known limitations

**Durability against Google hardening Flow.** The design depends on two external
Google surfaces. Its fail-closed posture means every plausible change degrades
gracefully — it never yields a silent false success:

- *Endpoint path moved / removed* → non-200 or non-JSON → `VERIFICATION_ERROR` →
  honest, recoverable error.
- *Response shape changed* (`user` nesting / `email` renamed) → falls to
  `VERIFICATION_ERROR` or a non-`AUTHENTICATED` outcome — annoying but never a
  false positive.
- *`SAPISID` renamed* → only changes which remediation message is shown, never
  the authenticated decision.
- *`__Secure-next-auth.session-token` renamed* → not referenced by name in the
  verification logic; the session is checked transitively via the endpoint —
  the design's biggest durability strength.

Hardening folded in to make this brittleness **observable** rather than silent:

1. **Endpoint-contract fixture.** A unit-test fixture (Wave 1) captures the real
   authenticated `200` body shape, and `verification.py` carries a code comment
   documenting the expected contract. A Google shape change then surfaces as a
   *failing test*, not a field of bug reports.
2. **Observable failures.** On a `VERIFICATION_ERROR` caused by a non-200, the
   `status_code` is logged at WARNING (never the body) — distinguishing a moved
   endpoint (a CLI bug) from a flaky link (the user's network).
3. **Documented coupling.** `KNOWN_ISSUES.md` gets an entry recording the
   `/api/auth/session` endpoint and the `SAPISID` cookie name as external
   couplings, so the next maintainer knows where to look when Google changes
   Flow's auth. (The probes under `tmp/` are untracked and will not survive.)

**Known limitation.** In environments without an OS keystore (some CI / Docker
containers), Chrome cannot decrypt its cookie store, so verification may always
fail. Such environments should use `InternalChromiumStrategy`
(`--browser internal`), whose bundled Chromium profile does not depend on
DPAPI / keychain. This is documented, not fixed.

## 11. Council review

This design was reviewed in two rounds of parallel agent review before approval:

- **Design stage** — architecture, security, and regression agents validated
  Approach A, the module decomposition, and the blast radius.
- **Spec stage** — implementability, security, codebase-accuracy, and
  test-strategy agents audited this written spec. The accuracy audit confirmed
  all 17 codebase claims; the other three produced the refinements folded into
  Rev 2. One security suggestion (upgrading `RealChromeStrategy.login`'s check to
  `strict=True`) was **partially rejected** with technical reasoning — see §4.2.

## 12. References

- `docs/superpowers/specs/2026-05-17-issue-15-root-cause-findings.md` —
  investigation evidence (mechanism in §3 / §5 corrected here).
- `docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md`
  — superseded v2 spec.
- `docs/superpowers/2026-05-17-issue-15-handover.md` — prior handover.
- `KNOWN_ISSUES.md` — to receive the external-endpoint coupling note (§10).
- Probes: `tmp/issue-15-phase2/` (exploratory, untracked).
