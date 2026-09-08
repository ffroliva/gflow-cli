# Auto-closing browser sign-in for `gflow auth login`

**Phase 0 evidence:** [`docs/superpowers/spikes/2026-09-08-g12-blocks-webdriver-not-playwright.md`](../../spikes/2026-09-08-g12-blocks-webdriver-not-playwright.md)
**Phase 2 verdict:** STOP as originally scoped → **GO on the scope below**, confidence 7/10.
Five personas: Architect CAUTION 7 · Security CAUTION 7 · Performance CAUTION 7 ·
CLI/MCP UX GO 7 · Devil's Advocate STOP 7.

## Goal

`gflow auth login` closes the browser itself once the Flow sign-in completes, instead of
instructing the user to close the window.

## Scope decisions (and why they differ from the original proposal)

1. **The Playwright driver is the DEFAULT, and the subprocess path is retained as an
   automatic fallback — not as an opt-in toggle.**

   Security and Devil's Advocate both argued for opt-in-for-one-release on N=1 evidence
   (one account, one Windows box, one IP, one Chrome build, one day). That objection was
   put to the maintainer, who decided auto-close ships as the default. Recorded here so
   the trade-off is visible rather than silently lost: **shipping this as default is a
   deliberate decision made against two personas' advice.**

   The risk those personas named is nonetheless mitigated, because fallback and opt-in
   are separable. There is **no user-facing switch**: the Playwright path is tried first,
   and the subprocess path runs automatically when Playwright cannot launch (no resolvable
   Chrome channel) or when Google rejects the browser. A Chromium-only Linux user is
   never locked out of onboarding; they simply get the old flow without being asked to
   choose. This makes the default flip safe without making the user opt in.
2. **`internal_chromium.py` is fixed first, separately.** It already auto-closes
   (`_poll_session_until_authenticated` → `finally: ctx.close()`) but ships with **no
   stealth flags** — which is the exact configuration the spike measured as BLOCKED. That
   is a likely-live G12 block, and the fix is ~4 lines.
3. **Two strategy classes are kept**, not collapsed. Performance argued YAGNI-collapse;
   Architect showed four contracts hang off the split (`name` → `source=` label
   constrained at `verification.py:86`; only chrome writes `.gflow_browser_strategy`;
   `factory.py` is a name→type registry; both classes are public exports). Shared
   launch+poll body is extracted instead.
4. **Close condition is `FlowSessionOutcome.AUTHENTICATED`**, polled from the owned
   context — never a cookie-name match. The spike used a cookie name; Security and
   Performance both showed that is the wrong oracle and that "close flushes to disk" is
   unverified by the spike's own evidence.

## Tasks

### T1 — `internal_chromium` hardening (own PR, ships first)
- [ ] Failing test: launch kwargs include both stealth flags, `no_viewport=True`,
      `chromium_sandbox=True`, and `--window-size=1920,1080`; assert **no** explicit
      `viewport=` key.
- [ ] Add `--disable-blink-features=AutomationControlled`,
      `ignore_default_args=["--enable-automation"]`, `chromium_sandbox=True`,
      `no_viewport=True`; replace `viewport={1920,1080}` with `--window-size=1920,1080`
      in `args` (keeps the #315 geometry rationale, drops the emulation that pushes
      Google's sign-in form off-screen).
- [ ] Keep `--password-store=basic` (cross-module load-bearing, `client.py:552-560`).

### T2 — Factory gate (blocking precondition for T3)
- [ ] Failing test: Chromium-only Linux + `--browser auto` → `InternalChromiumStrategy`;
      `--browser chrome` → `ConfigurationError` exit 11 (matches `auth_login.feature:26-31`).
- [ ] Promote `_is_playwright_chrome_channel_available` to public API; gate
      `factory.py` on it when the Playwright driver is selected.
- [ ] Fix its `CHROME_BINARY` false positive (`browser_manager.py:148-150`): Playwright's
      `channel="chrome"` ignores `CHROME_BINARY`; only `executable_path=` honours it.

### T3 — Playwright auth driver (default, with automatic fallback)
- [ ] Failing test: with a resolvable Chrome channel, `gflow auth login --browser chrome`
      takes the Playwright path and closes the browser itself.
- [ ] Failing test: with **no** resolvable Chrome channel, the same command silently
      falls back to the subprocess path and still completes — no user-facing choice, no
      new flag, no error.
- [ ] Failing test: `AuthBrowserRejectedError` on the Playwright path triggers one
      automatic subprocess retry rather than surfacing exit 14 to the user.
- [ ] Extract shared launch-kwargs + session-poll helper used by both strategies.
- [ ] Detection: poll `SESSION_API_URL` from the owned context until
      `FlowSessionOutcome.AUTHENTICATED`; monotonic deadline; break on browser-closed.
- [ ] **Manual close routes to `verify_flow_profile`, never to exit 12.** A user who
      closes the window themselves must not see a red error on a successful login.
- [ ] Port `_is_google_rejected_browser_page` → `AuthBrowserRejectedError` (exit 14) to
      this path, and assert `navigator.webdriver is False` once at launch, so a future
      Chrome that ignores the flag fails loudly instead of as a 600 s timeout.
- [ ] Teardown via `close_context_bounded` + `run_teardown_step` (`api/_engine.py`), in
      `pw → lease` unwind order. Not a bare `finally`.
- [ ] No new CLI option and no new env var — so no MCP parity work is owed
      (`auth login` is an explicit exemption, `tests/mcp/test_cli_parity.py:91`, and the
      reason still holds). Record that rather than re-deriving it.

### T4 — Preserve the two regression guards
- [ ] `CancelledError` during detection closes the context, stops the driver, and
      releases the lease **in that order** (assert order via a shared list, as
      `tests/api/test_concurrency.py:284-299` does).
- [ ] Timeout raises `AuthLoginTimeoutError` after the same three steps.
  These replace `test_await_chrome_close_cancellation_terminates_and_reaps` and
  `test_login_cancellation_releases_lease_and_reaps_chrome`, whose subject code the
  Playwright path bypasses. The guarantees must survive even though the code does not.

### T5 — Copy and observability
- [ ] Rewrite `_print_login_instructions` step 4 and the `PASSIVE AUTHENTICATION` header.
- [ ] `_UNVERIFIED_HINT[GOOGLE_SESSION_ONLY]` — drop "before closing Chrome".
- [ ] Timeout message: "not detected within Ns", not "you timed out".
- [ ] `auth_passive_capture_started` → `auth_login_started`; add
      `auth_login_session_detected(strategy, elapsed_s)`,
      `auth_login_browser_closed_by_user(strategy)`, `auth_login_launch_failed(strategy, error)`.
- [ ] **Never log `page.url`** — OAuth `state`/`code_challenge` live there and
      `data/redaction.py` matches neither. Test: no `accounts.google.com` substring in
      any emitted log event.

### T6 — Docs (own PR for the pure-doc part)
- [ ] `KNOWN_ISSUES.md` G12 entry: it currently describes a Playwright `channel="chrome"`
      `RealChromeStrategy` that has never existed, and its "unsupported command-line flag"
      note becomes false once `chromium_sandbox=True` removes `--no-sandbox`.
- [ ] `docs/ARCHITECTURE.md` (Passive Capture / `proc.wait()`), `docs/AUTHENTICATION.md`,
      `docs/USER_GUIDE.md`.
- [ ] **Website-only, hand-edit, the mirror gate cannot see these:**
      `website/docs/onboarding.md`, `website/docs/onboarding-mockup.html`.
- [ ] Supersede `docs/superpowers/memory/real-browser-auth-mandatory.md` — it asserts
      "Never write a flow that expects interactive Google sign-in inside a
      Playwright-driven browser — it cannot work", which the spike refutes.
- [ ] Do **not** edit `docs/LIVE_VERIFICATION_v0.54.0.md` — a dated historical record.

## Verification

- **No e2e test can cover this**, and that is a named blocker, not an omission: login
  requires a human typing a Google password. `tests/e2e/` has 39 tests, all consuming an
  already-authenticated profile. The instrument that discharges it is
  `scripts/dev/spike_playwright_chrome_login.py`, plus a `/gflow:live-verify` run.
- **Required live runs before merge:** (a) auto-close fires end to end; (b) manual close
  mid-login still verifies and does **not** exit 12; (c) `--browser auto` on this machine
  still selects and completes the default subprocess path unchanged.

## Follow-up owed after this ships (not blocking, but do not lose it)

Because the default flip ships on N=1 evidence against Security's advice, the evidence
gap it named stays open and should be closed after release, not forgotten:

- Re-run the `stealth` arm on a second profile and one non-Windows host.
- Re-run the 20-generation WAF baseline (`docs/superpowers/spikes/2026-07-09-camoufox-waf-403.md`,
  currently 0/20 403s) on a profile authenticated by the **new** path. That baseline was
  measured on a subprocess-authenticated profile and does not transfer automatically.
- If either regresses, the automatic-fallback seam built in T3 is the rollback lever —
  invert its preference order rather than reverting the release.

## Explicitly out of scope

- `headless=True` on the Playwright path — unmeasured; refuse it rather than ship it.
- `#480 --import-from-browser` — blocked on Windows by App-Bound Encryption (verified:
  `app_bound_encrypted_key` present in this machine's Chrome `Local State`).
