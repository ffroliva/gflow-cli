# Issue #15 — Auth-Layer Verification Fix — Design

> **Status:** Approved design · **Date:** 2026-05-17
> **Supersedes:** `2026-05-17-i2v-uploadimage-401-bearer-auth-design.md` (the v2
> bearer-auth spec) and its plan, in full.
> **Corrects:** `2026-05-17-issue-15-root-cause-findings.md` §3 & §5 — the
> "session cookie is not flushed to disk" mechanism is **disproven** (see §2).
> **Reviewed by:** an architecture / security / regression agent council (§11).

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
  signed in.
- `auth status` — stays a lightweight file-existence check.
- CDP / `--remote-debugging-port` / `storage_state` / live-detection — the
  no-debug-port stealth ADR is kept (Approach C rejected).
- reCAPTCHA / video-generation routes — unrelated.
- A configurable verification-timeout knob — fixed sensible defaults; a knob
  can come later.

## 4. Design

Approach **A** — *honest post-close verification* — chosen because it keeps the
no-debug-port stealth ADR fully intact. Net change: **1 new file, 2 edited
files, 1 docstring touch-up.**

### 4.1 New module — `src/gflow_cli/auth/verification.py`

Single responsibility: answer "does this profile hold a usable Flow app
session?"

Constant:

- `SESSION_API_URL = "https://labs.google/fx/api/auth/session"`

`FlowSessionOutcome(str, Enum)` — four mutually-exclusive outcomes:

| Value | Meaning |
|---|---|
| `AUTHENTICATED` | `/api/auth/session` returned an authenticated user — a usable Flow session. |
| `GOOGLE_SESSION_ONLY` | No Flow session, but the Google `SAPISID` cookie is present. |
| `NO_SESSION` | No Flow session and no Google sign-in detected. |
| `VERIFICATION_ERROR` | The check could not be completed (network / timeout / non-200 / malformed). |

`FlowSessionStatus` — `@dataclass(frozen=True)`:

- `outcome: FlowSessionOutcome`
- `user_email: str | None` — from the session payload, only when `AUTHENTICATED`.
- `source: str` — originating strategy (`"chrome"` / `"internal"`), for log
  filtering.
- `detail: str` — a short, **static** human-readable string chosen from a fixed
  set keyed by `outcome`. Never built from response-body, cookie, or exception
  content.
- `authenticated` — `@property` → `outcome is FlowSessionOutcome.AUTHENTICATED`.

`evaluate_session_response(status_code: int, body: str, *, google_session: bool,
source: str) -> FlowSessionStatus` — **pure, total** function (no I/O, no
exceptions for control flow). Mapping:

- `status_code == 200` and body parses to a dict with a non-empty `user` object
  carrying an `email` → `AUTHENTICATED` (extract `user_email`; the
  `image`/profile-URL field is ignored).
- `status_code == 200` and body parses to `{}` / no `user` →
  `GOOGLE_SESSION_ONLY` if `google_session` else `NO_SESSION`.
- `status_code != 200`, or the body does not parse as JSON → `VERIFICATION_ERROR`.

This is the well-bounded testable core — exercised with zero mocks.

`async verify_flow_session(profile_dir: Path, *, channel: str | None,
source: str) -> FlowSessionStatus` — thin I/O wrapper, used by
`RealChromeStrategy`:

1. **Precondition:** `profile_dir.resolve(strict=True)` must be inside
   `get_settings().home.resolve()`; otherwise raise `SecurityError`
   (defense-in-depth — symlink-resistant).
2. Lazily `from .strategies import async_playwright` (inside the function —
   breaks a module-load circular import and preserves the test-patch seam).
3. Launch a headless `launch_persistent_context(user_data_dir=str(profile_dir),
   channel=channel, headless=True)`.
4. In `try`: read cookies → `google_session = any(c["name"] == "SAPISID")`;
   `GET SESSION_API_URL` via a page, with a **15s per-request timeout** and
   **up to 3 attempts** (initial + 2 `tenacity` retries, exponential backoff)
   on transient network / timeout errors; pass status + body to
   `evaluate_session_response`.
5. On any unrecovered exception (launch failure, retries exhausted) → return
   `FlowSessionStatus(VERIFICATION_ERROR, …)`. **Fail-closed: never returns
   `AUTHENTICATED` on uncertainty.**
6. `finally`: `await ctx.close()` — fully awaited, so the profile's SQLite lock
   is released before the function returns.

`verify_flow_session` is **side-effect-free** beyond the browser probe — it
writes no files and emits no marker; callers own those side effects.

### 4.2 `src/gflow_cli/auth/real_chrome.py` — edit

- Append the Flow URL (the existing `GEMINI_URL` constant) to `chrome_args` so
  Chrome opens directly on the Flow page.
- Rewrite the console guidance: state explicitly that signing into the Google
  account is **not** sufficient — the user must continue until the working Flow
  editor (prompt box / their projects) is loaded, *then* close Chrome.
- Replace the inline post-close verification block (the direct `async_playwright`
  import, `launch_persistent_context`, `cookies()`, `has_sapisid` check) with a
  single call: `status = await verify_flow_session(profile_dir,
  channel="chrome", source="chrome")`. This runs **after** `proc.wait()` returns
  (the login Chrome has exited and released the profile lock — sequencing
  preserved).
  - `status.authenticated` → emit structlog `auth_flow_session_verified`
    (`outcome`, `source`, `user_email`); **write the `.gflow_browser_strategy`
    marker** (`"chrome"`); print `[OK] Flow session verified (<email>).`
  - else → emit a structlog warning; raise `AuthMissingError` (exit code 8) with
    an `outcome`-tailored message + `remediation_hint` (see §6).

> The `.gflow_browser_strategy` marker write is **load-bearing** —
> `browser_manager.channel_for_profile` reads it; dropping it makes
> `FlowApiClient` open a Chrome-130+ profile with bundled Chromium and fail. It
> is preserved in the success branch.

### 4.3 `src/gflow_cli/auth/internal_chromium.py` — edit

`InternalChromiumStrategy` already polls *live* (it controls the browser via
Playwright and auto-detects success without waiting for a close). Change only
*what* it checks:

- In the poll loop, replace the `SAPISID` + `get_by_text("New project")` /
  `("Your projects")` test with: `resp = await
  page.request.get(SESSION_API_URL)`, then `evaluate_session_response(
  resp.status, await resp.text(), google_session=<SAPISID present>,
  source="internal")`; success when `outcome is AUTHENTICATED`.
- Query `/api/auth/session` at a modest cadence (every ~3s, not every 1s) to
  avoid hammering the endpoint.
- The existing timeout / "closed before verified" branches stay, raising
  `AuthLoginTimeoutError`; their wording is refreshed to mention reaching the
  Flow editor.
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
       │           → headless context → GET /api/auth/session
       │           → evaluate_session_response
       │     → AUTHENTICATED ? write marker + log + success
       │                     : raise AuthMissingError (exit 8)
       └─ InternalChromiumStrategy:
             launch bundled Chromium (Playwright-controlled) at the Flow URL
             → live poll: GET /api/auth/session → evaluate_session_response
             → AUTHENTICATED ? success
                             : (timeout / closed) raise AuthLoginTimeoutError
```

`FlowApiClient` later opens the profile and authenticates on the now-guaranteed
`__Secure-next-auth.session-token` — no change.

## 6. Error handling & fail-closed semantics

Only `FlowSessionOutcome.AUTHENTICATED` green-lights a login. Every other
outcome fails the login — the verification never reports success under
uncertainty.

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

## 7. Security considerations

Folded in from the council's security review (must-fix items):

- **No secret in logs or `detail`.** `__Secure-next-auth.session-token` and all
  raw cookie values are never logged. `FlowSessionStatus.detail` is a static
  string from a fixed set — never interpolated from response-body, cookie, or
  exception content. The raw `/api/auth/session` body is never logged or stored,
  at any level.
- **Minimal payload extraction.** `evaluate_session_response` reads only the
  keys it needs (`user.email`); the profile-image URL is discarded.
- **`user_email`** may be logged at INFO on success (the user's own email;
  consistent with the existing `auth_login_success_verified` event) — never
  inside a body dump.
- **Fail-closed.** Any uncertainty → `VERIFICATION_ERROR`; never
  `AUTHENTICATED`. Non-200 / malformed body are uncertainty, not
  "unauthenticated".
- **No `storage_state`.** Deliberately not used — it would serialise the session
  token into a plaintext JSON file. The session stays inside Chrome's own cookie
  store (encrypted at rest by the OS — DPAPI on Windows). No new credential file
  is written.
- **Profile-dir boundary.** `verify_flow_session` re-validates `profile_dir`
  with `resolve(strict=True)` against `GFLOW_CLI_HOME`, raising `SecurityError`
  — symlink-resistant defense-in-depth.
- **No new untrusted surface.** `SESSION_API_URL` is a hard-coded constant — no
  SSRF surface; TLS via Chromium's stack.

## 8. Testing strategy (TDD — red → green → refactor)

`evaluate_session_response` — **pure unit tests, zero mocks:**

- `200` + authenticated `user` body → `AUTHENTICATED`, email extracted.
- `200` + `{}`, `google_session=True` → `GOOGLE_SESSION_ONLY`.
- `200` + `{}`, `google_session=False` → `NO_SESSION`.
- non-200 (401 / 500 / 302) → `VERIFICATION_ERROR`.
- malformed / non-JSON body → `VERIFICATION_ERROR`.

`verify_flow_session` — **mock the `.strategies` `async_playwright` shim:**

- authenticated probe → `AUTHENTICATED`.
- unauthenticated probe → `GOOGLE_SESSION_ONLY` / `NO_SESSION`.
- transient error → retried → exhausted → `VERIFICATION_ERROR`.
- launch failure → `VERIFICATION_ERROR`.
- `ctx.close()` awaited in `finally` even on the error path.
- profile dir outside `GFLOW_CLI_HOME` → `SecurityError`.

Strategy tests — `tests/auth/strategies/test_strategies.py`:

- **Update** (behaviour legitimately changed): the real-chrome "success verified
  via SAPISID" test and its verify-probe mock helper; the internal-chromium
  standard + timeout tests (they mock `get_by_text` + SAPISID — repoint to
  `/api/auth/session`).
- **Must NOT change:** `test_real_chrome_launch_flags` (launch flags),
  `test_real_chrome_privacy_guard` (`SecurityError` boundary),
  `test_real_chrome_timeout_raises` (`AuthLoginTimeoutError`).

Regression — `tests/test_browser_manager.py`:

- **Must NOT change**, and **add** a test asserting a successful real-chrome
  login still writes `.gflow_browser_strategy`.

BDD — `tests/**/*.feature` for auth login / status: **must NOT change**; step
mocks updated to produce a verifiable session where needed.

Coverage: ≥80% overall maintained; `verification.py` targeted near-100% (the
pure core makes this cheap).

## 9. Regression guardrails (what must not break)

From the council's regression review:

- **`.gflow_browser_strategy` marker** — must still be written on a successful
  real-chrome login (HIGH risk; covered by a new test).
- **Exit code 8** — verification failure must raise `AuthMissingError` (a
  `GFlowError` subclass), not fall through to the catch-all exit 1.
- **`FlowApiClient`** — confirmed unchanged; a profile from the fixed flow is a
  strict superset of today's.
- **Sequencing** — `verify_flow_session` runs only after the login Chrome
  process has exited (`proc.wait()`), so there is no SQLite-lock contention.
- **Slow-network correctness** — the retry budget + `VERIFICATION_ERROR`
  classification ensure a correct login on a flaky link is not falsely failed.
- structlog event renames are safe (no code or test asserts on the event
  strings).

## 10. Known limitation

In environments without an OS keystore (some CI / Docker containers), Chrome
cannot decrypt its cookie store, so verification may always fail. Such
environments should use `InternalChromiumStrategy` (`--browser internal`), whose
bundled Chromium profile does not depend on DPAPI / keychain. This is
documented, not fixed.

## 11. Council review

This design was reviewed by three parallel agents before approval:

- **Architecture** — verified single-responsibility, module decomposition, the
  circular-import fix, and `AuthMissingError` reuse.
- **Security** — verified credential handling, the fail-closed posture, and the
  no-`storage_state` decision; its must-fix items are folded into §7.
- **Regression** — verified the blast radius; its guardrails are folded into §9
  and §8.

## 12. References

- `docs/superpowers/specs/2026-05-17-issue-15-root-cause-findings.md` —
  investigation evidence (mechanism in §3 / §5 corrected here).
- `docs/superpowers/specs/2026-05-17-i2v-uploadimage-401-bearer-auth-design.md`
  — superseded v2 spec.
- `docs/superpowers/2026-05-17-issue-15-handover.md` — prior handover.
- Probes: `tmp/issue-15-phase2/` (exploratory, untracked).
