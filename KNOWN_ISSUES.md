# Known Issues

Living list of behaviour that's broken, surprising, or limited by design — alongside workarounds and a pointer to the issue / version where each is tracked or resolved.

> Pair with [CHANGELOG.md](CHANGELOG.md) (what shipped per version) and [DISCLAIMER.md](DISCLAIMER.md) (legal/scope limits).

## Conventions

- **Status: Open** — still happens in latest release. Workaround listed.
- **Status: Mitigated** — partial fix in place; full resolution tracked.
- **Status: Resolved** — fixed in version `X.Y.Z`; row kept here for searchability.

---

## Open

### Image generation returns HTTP 401 — `aisandbox-pa` generation endpoint

- **Status:** **RESOLVED in v0.7.0** — moved to [Resolved](#resolved) section
- **Severity:** ~~High~~ · **Was-affecting:** v0.6.0a6 and earlier

> **Resolution (2026-05-20, v0.7.0):** the production `ui_automation` transport
> drives the Flow web UI so Flow's own JS issues `batchGenerateImages` with
> full auth context — bypassing the 401 on the `aisandbox-pa` HTTP path
> entirely. Live-verified end-to-end on the `ffroliva` profile across four
> aspect ratios (`9:16`, `16:9`, `1:1`, `4:3`); see
> [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md). The
> 401 still hits the experimental HTTP transports
> (`evaluate_fetch` / `bearer` / `sapisidhash`) under
> `src/gflow_cli/api/transports/experimental/` — those are not the
> production path. Historical detail preserved below for searchability.

Image **generation** calls fail with HTTP 401 even on a profile that holds a
fully verified Flow session. Discovered 2026-05-17 while building the e2e test
suite, against a profile probed immediately after a successful
`gflow auth login` (`auth_flow_session_verified`, `[OK] Flow session verified`).

**What works vs. what fails — on the same freshly verified profile:**

| Operation | Endpoint | Result |
|---|---|---|
| `verify_flow_session` | `labs.google/fx/api/auth/session` | ✅ `AUTHENTICATED` |
| `FlowApiClient.health_check()` | Flow page context | ✅ `True` |
| `create_project` | `labs.google/fx/api/trpc/project.createProject` | ✅ 200 |
| **image generation** | `aisandbox-pa.googleapis.com` (private API) | ❌ **HTTP 401** |

The `evaluate_fetch` transport receives a 401 on the generation request, runs
its refresh path (`refresh_auth()` re-navigates to the Flow URL), retries once,
gets 401 again, and raises:

```
AuthExpiredError: evaluate_fetch: HTTP 401 persisted after refresh — session expired
```

from `src/gflow_cli/api/transports/experimental/evaluate_fetch.py`
(`_handle_response`). Call chain: `FlowApiClient.generate_image` /
`generate_images_batch` → `_drive_image_generation` → `transport.generate_images`
→ `_generate_images_inner` → `_handle_response`.

**Distinct from issue #15.** Issue #15 was a 401 on `create_project` caused by
the *profile* being signed in to Google but not the Flow app — fixed on the
`fix/issue-15-i2v-bearer-auth` branch by verifying the real Flow session at
login. That fix is confirmed working: `create_project` now succeeds. **This is
a different 401** — it occurs on a profile that *is* verified and *can* create
projects, specifically on the `aisandbox-pa.googleapis.com` generation
endpoint, a different surface from the `labs.google` tRPC API.

**Scope.** The 401 affects every image-generation path uniformly on the
`evaluate_fetch` transport (the live one): `test_e2e_single_image_gen` (C2,
pre-existing), `test_e2e_generate_image_without_project_id` (PR #20,
pre-existing), and the dropped `test_e2e_generate_images_batch_without_project_id`.
It is **not** caused by recent test changes — `test_transports_e2e.py` is
self-described scaffold ("Task D.1 scaffold; Task D.2 drives the real
execution") that was never run green, and PR #20's e2e tests were merged
without live execution. Whether the production CLI (`gflow image t2i` /
`gflow video i2v`) is equally affected is **unconfirmed** — it uses the same
`FlowApiClient` + transport, so it very likely is, but that has not been
observed directly and should be checked first thing.

**Experimental transports also broken.** The `bearer` and `sapisidhash`
transports (`api/transports/experimental/`) fail before generation is even
reached: `bearer` cannot intercept an OAuth token (`AuthExpiredError: bearer:
failed to intercept Bearer token from Flow page`); `sapisidhash` cascades off
the resulting profile-lock contention. These are obsolete — only
`evaluate_fetch` is viable. Issue-#15 investigation notes had already
disproven the "bearer header" hypothesis for `create_project`.

**Where to investigate.**

- The login OAuth flow *does* request the
  `https://www.googleapis.com/auth/aisandbox` scope (visible in the sign-in
  URL), so the account is authorized — the 401 points at how the credential is
  *presented* to `aisandbox-pa`, not at missing authorization.
- Capture a real generation request from `evaluate_fetch` — the exact URL,
  headers, and credential it sends — and compare with what the Flow web UI
  sends for the same action (browser DevTools network capture).
- The `aisandbox-pa.googleapis.com` host may require a Bearer token: the
  issue-#15 "bearer header" hypothesis was disproven for the `labs.google`
  tRPC `create_project` route, but may hold for this *different* Google API
  host.
- Files: `src/gflow_cli/api/transports/experimental/evaluate_fetch.py`
  (`generate_images`, `_generate_images_inner`, `_handle_response`,
  `refresh_auth`) and `src/gflow_cli/api/client.py` (`_drive_image_generation`).

**Workaround:** none known. Image generation against the live API does not
currently succeed via the e2e transport path.

---

### Browser session expires periodically — manual re-login required

- **Status:** Open · **Severity:** Medium · **Affects:** all versions · **Tracked:** N/A (architectural)

Google's web session cookies aren't permanent. They expire when:
- Long stretch of inactivity (typically months)
- You change your Google password
- You sign out from another device's session manager
- Google flags the session as suspicious (geo-jump, new device fingerprint)

When this happens, the next API call returns 401/403 and `gflow-cli` raises `AuthExpiredError`.

**Workaround:**
```bash
gflow auth login --profile <name>
```

Re-running `auth login` reuses the existing profile dir (you typically just click "Continue as <you>" on the Google account chooser). No data is lost; only the cookie jar is refreshed.

**Why we don't auto-refresh:** Google's session-refresh flow can include CAPTCHA / device verification that only a human can complete. A community SDK can't reliably automate that step. See [docs/AUTHENTICATION.md § Refresh / expiry](docs/AUTHENTICATION.md#refresh--expiry).

**Roadmap:** not scheduled. The Phase 4 hardening pass (v0.4.0a2) added typed `AuthExpiredError` + exit code `3` so scripts can branch on auth expiry deterministically. A periodic "session liveness" check + a `gflow auth refresh` command are still candidates for a later phase, but not committed to a version yet.

---

### Same profile can't be used in parallel

- **Status:** Open (by design) · **Severity:** Low · **Affects:** all versions

Chromium refuses to open two persistent contexts on the same `user-data-dir` simultaneously. So running two concurrent `gflow ...` calls with the same `--profile` will fail the second one with a "ProcessSingleton: profile is locked" error.

**Workaround:** use different profiles for parallel work.

```bash
# Terminal 1
gflow video batch ./batch-a.tsv --profile work

# Terminal 2 — different profile, same time, OK
gflow video batch ./batch-b.tsv --profile personal
```

**Roadmap:** Phase 4 (v0.4.0a2) added concurrency *inside* one `gflow video batch` process via `GFLOW_CLI_CONCURRENCY=N` (per-worker Page pool on one shared BrowserContext). Cross-process same-profile serialization is a Chromium constraint we cannot work around without rewriting the auth model — multiple shells against the same profile remains a "use different profiles" workaround.

---

### Flow's first-upload terms-of-use dialog ("Aviso") blocks the worker (worker-only)

- **Status:** Open · **Severity:** Low · **Affects:** the legacy in-tree Compiled Growth worker, NOT `gflow-cli` itself

Flow shows a one-time "Aviso" / "Notice" terms-of-use confirmation on the first image upload of a new account session. The legacy Playwright worker has to explicitly click "Concordo" / "Agree". `gflow-cli`'s API-driven path bypasses this dialog entirely (the REST endpoint already implies acceptance).

**Workaround in gflow-cli:** none needed.

**Workaround in legacy worker:** see Compiled Growth's `flow_video.py` consent-dismiss block.

---

### Flow's release-notes ("What's new") changelog popup blocks first-run UI automation

- **Status:** Mitigated · **Severity:** Medium · **Tracked:** [#26](https://github.com/ffroliva/gflow-cli/issues/26)

Google Flow ships a release-notes / "What's new" iframe (`changelogs/YYYY-MM-DD-...html`) the first time a logged-in profile visits the page after a Flow deployment. The iframe sits on top of the editor and intercepts pointer events on Flow's own controls — Playwright finds the right selector but cannot click it because the changelog is in the way. Issue [#26](https://github.com/ffroliva/gflow-cli/issues/26) confirmed the same iframe also blocks the settings menu after project navigation.

**Symptom:**
```
playwright._impl._errors.TimeoutError: Locator.click: Timeout 30000ms exceeded.
  - <iframe ... src="https://www.gstatic.com/.../changelogs/...html"></iframe> ... intercepts pointer events
  - retrying click action (57 retries, then timeout)
```

**Mitigation:** `UiAutomationTransport._dismiss_blocking_overlays(page)` detects Flow changelog iframes (`iframe[src*='/flow/changelogs/']`, `iframe[src*='/changelogs/']`) and dismisses them via a close-button selector cascade with an Escape-key fallback. Invoked after `_enter_editor` (image flow) and after `_wait_video_editor_ready` (video flow) so downstream clicks aren't intercepted. Structured logs identify what was dismissed; a debug screenshot is captured if dismissal cannot be confirmed.

**Legacy workaround (no longer required):** open Flow in Chrome with the same profile once, click the `X` on the "What's new" popup, then close Chrome cleanly.

---

### No in-CLI quota visibility

- **Status:** Open · **Severity:** Low · **Roadmap:** v0.5

`gflow-cli` doesn't yet show how many Veo / Imagen credits remain on your Ultra/Pro subscription. You can check at <https://gemini.google/subscriptions/> in the meantime.

**Roadmap:** v0.5 will surface remaining quota via `gflow auth status` once we capture the relevant Google API.

---

### Aspect-ratio support depends on the Veo / Imagen model version

- **Status:** Open · **Severity:** Low

Currently confirmed:
- Veo I2V: `9:16`, `16:9`, `1:1`
- Imagen: `1:1`, `9:16`, `16:9`, `4:3`, `3:4`

Other ratios may be silently rejected or coerced server-side. We validate in the CLI to whitelisted values to fail fast.

---

### `gflow video batch` does not skip already-completed entries

- **Status:** Open · **Severity:** Medium · **Affects:** v0.2.0a1+

If a `gflow video batch` run dies partway through (auth expiry, network blip, Ctrl-C) and you rerun the same TSV manifest, every row is re-submitted to Flow. Flow's private API does not expose a "have I generated this before?" predicate, and `gflow-cli` does not yet maintain a local manifest-of-outputs to compare against.

**Cost implication:** re-running a partially completed manifest **may consume additional Veo / Imagen credits**. We cannot guarantee that Flow de-duplicates server-side — credit accounting on a private API is not contractual.

**Workaround:** before rerunning, trim the manifest down to the rows whose `output_path` does not yet exist on disk:
```bash
awk -F'\t' 'NR==1 || (system("test -e " $NF) != 0)' manifest.tsv > manifest.remaining.tsv
gflow video batch manifest.remaining.tsv
```

**Roadmap:** under consideration for Phase 6 (operations history with a local SQLite ledger — see `PLAN.md`).

---

### REST API 401 — all `aisandbox-pa.googleapis.com` generation endpoints blocked

- **Status:** **RESOLVED in v0.7.0** — image generation live-verified end-to-end
- **Severity:** ~~High~~ · **Was-affecting:** v0.2.0a1 through v0.6.0a6

> **Resolution (2026-05-20, v0.7.0):** `UiAutomationTransport` drives the Flow
> editor so Flow's own JS issues every generation request with full auth
> context — image generation now succeeds end-to-end (see
> [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md)). Video
> T2V works at the library level via the same transport (Phase A, PR #23);
> CLI wiring (`gflow video t2v/i2v/batch`) is queued for Phase B. The HTTP
> transports under `experimental/` remain blocked by this 401 by design —
> they are not on the production path.

Even with a valid browser session (cookies present), calling Flow's REST API directly via `fetch` or `page.request` against `aisandbox-pa.googleapis.com` returns HTTP 401. This blocks **all** generation routes:

| Endpoint | Status |
|---|---|
| `flowMedia:batchGenerateImages` (image gen) | ❌ 401 |
| `video:batchAsyncGenerateVideoText` (T2V + I2V) | ❌ 401 (confirmed 2026-05-18 e2e run) |
| `flow/uploadImage` (image upload for I2V) | ❓ untested (blocked before reaching this step) |
| `video:batchCheckAsyncVideoGenerationStatus` (status poll) | ❌ 401 (confirmed 2026-05-19 — even via `page.request.post` from the authenticated browser context) |

The Phase 0 video spike (2026-05-19) confirmed the **generation** routes *do*
succeed when driven through the UI (Flow's own JS issues them) — but the
**status-poll** route 401s even from `page.request.post` inside the authed
page. Polling must therefore capture Flow's own status responses, not issue
the request directly. See the video-generation design spec §10.5.

`project.createProject` (on `labs.google/fx/api/trpc`) **does** work — it uses a different domain and auth model.

**Root cause:** Google's backend has tightened security on `aisandbox-pa.googleapis.com`, requiring a browser fingerprint, `Origin`/`Referer` headers, and reCAPTCHA token that raw script-driven requests cannot provide.

**Workaround:** Use the **UI Mimicry** approach — drive the Flow editor by clicking real buttons so the browser itself issues the generation requests with full auth context.

**Roadmap:** v0.6.0a5 will add video generation (T2V + I2V) to the `UiAutomationTransport`, making it the single transport that covers both image and video generation. I2V requires driving the Flow UI's image-upload button so the browser calls `uploadImage` with its own session cookies.

---

### Output dir is not tidied automatically

- **Status:** Open · **Severity:** Low · **By design**

`gflow-cli` never deletes from `$GFLOW_CLI_OUTPUT_DIR`. Generated assets accumulate forever unless you clean them up.

**Workaround:** schedule a cron / Task Scheduler job, e.g.:
```bash
# Delete files older than 30 days
find "$HOME/Downloads/gflow-cli" -type f -mtime +30 -delete
```

---

### `batchGenerateImages` HTTP 403 — WAF / reCAPTCHA `PUBLIC_ERROR_UNUSUAL_ACTIVITY`

- **Status:** Open · **Severity:** High (blocks affected profile until WAF score decays or profile is replaced)
- **First observed:** 2026-05-23 on profile `denon82` during `gflow image batch` runs
- **Surfaces as:** `gflow_cli.errors.WafRejectionError: WAF rejection (HTTP 403): batchGenerateImages HTTP 403 — reCAPTCHA score too low or WAF fingerprint mismatch`
- **structlog signature:** `ui_automation.batch_response_seen` with `status=403` followed by `ui_automation.batch_403_body` containing `'message': 'reCAPTCHA evaluation failed', 'status': 'PERMISSION_DENIED', 'reason': 'PUBLIC_ERROR_UNUSUAL_ACTIVITY'`

Distinct from the historical `aisandbox-pa` 401 (resolved in v0.7.0). The 403
here means Flow accepted the session as authenticated but reCAPTCHA Enterprise
scored the request as bot-like and blocked the generation call. The `denon82`
profile reproducibly 403s on `batchGenerateImages` even after a fresh
`gflow auth login --browser chrome`; the same code path on profile `ffroliva`
(re-authenticated the same day) succeeded end-to-end across one t2i + a 4-image
batch — so it is not a global incompatibility but a per-profile WAF state.

**Likely contributing factors:**
- Repeated automated runs on the same profile within a short window
- Playwright-driven Chrome leaks small fingerprint differences vs. unautomated
  Chrome that reCAPTCHA Enterprise can score
- The image-batch path issues several rapid-fire requests after the
  count-tab clicks, which the scoring may treat as a single fast burst

**Workarounds:**
1. **Use a profile with lower WAF heat** — re-test on a different Chrome-strategy
   profile (`gflow auth login --profile <new> --browser chrome`). The profile
   that has been driven by recent automation runs is usually the hottest.
2. **Let the WAF score decay** — typically hours to a day. Manually using real
   Chrome on the same account in between can help (real interactions lower
   the score).
3. **Avoid same-day repeated batch runs** on a profile after a 403 — each
   rejected request can raise the score further.

**Tracked separately from** the architectural ["first-attempt listener-miss
flake"](https://github.com/ffroliva/gflow-cli/pull/40) — that one was caused
by editor mode confusion and is resolved by PR #40. WAF 403 is a fresh, distinct
issue and not blocked by any code change in this repo.

---

### `UiAutomationTransport` selectors still partially localized — issue #24 partial

- **Status:** Mitigated · **Severity:** Medium · **Tracking:** [issue #24](https://github.com/ffroliva/gflow-cli/issues/24)

The Phase 7 multi-image-prompt work addressed the count-tab selectors:
- `_COUNT_TAB_TEXT_RE = ^(1x|x[2-4])$` only matches the digit+x format Flow
  renders identically in every locale (numbers/symbols are not translated).
- `_set_count` falls back to positional `.nth(count - 1)` when the read-back
  text is unrecognised — locale-invariant.

Still localized as of this writing:

- **`ONBOARDING_SELECTORS`** (`src/gflow_cli/api/transports/ui_automation.py:183-193`)
  — nine button-text selectors only (`Agree` / `Aceitar` / `I agree` / `Concordo`
  / `Accept` / `Create with Flow` / `Criar com o Flow` / `Get Started` /
  `Começar`). An account whose Flow renders in an unlisted language (German,
  Japanese, ...) **cannot pass onboarding**. This is the issue's stated
  priority-1 item.
- **`NEW_PROJECT_SELECTORS` localized fallbacks** + **`SUBMIT_BUTTON_SELECTORS` tail**
  — icon-first selectors lead, so these work today; the localized fallbacks
  remain as "maintenance debt and silent-failure risk" per the issue body.

**Workaround:** the account must be in a locale that matches one of the
hard-coded text selectors. For automation, prefer accounts whose Flow renders
in English or Portuguese.

---

## Mitigated

### Auth verification depends on Google's NextAuth session endpoint

- **Status:** Mitigated · **Severity:** Low (degrades fail-closed) · **Affects:** issue #15 fix onward · **Tracked:** issue #15

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

---

## Resolved

### aisandbox-pa generation 401 — bypassed by the `ui_automation` transport

- **Status:** Resolved · **Severity:** Was-High (blocked image gen via HTTP transports) · **Fixed in:** v0.7.0

The two long Open-section entries above (*Image generation returns HTTP 401* and *REST API 401 — all `aisandbox-pa.googleapis.com` generation endpoints blocked*) were closed by the same architectural change: `UiAutomationTransport` drives the Flow web UI so Flow's own JavaScript issues every generation request with the full browser auth context (cookies, reCAPTCHA, `Origin`/`Referer` headers). The 401 had affected every direct HTTP call from `evaluate_fetch` / `bearer` / `sapisidhash`; those transports now live under `src/gflow_cli/api/transports/experimental/` and are not on the production path.

End-to-end live-verified on the `ffroliva` profile across `9:16`, `16:9`, `1:1`, and `4:3` aspect ratios; see [`docs/LIVE_VERIFICATION_v0.7.0.md`](docs/LIVE_VERIFICATION_v0.7.0.md) for timing, file sizes, and exact filenames. Video T2V uses the same approach (Phase A — PR #23 — merged 2026-05-19).

---

### G12 "browser not secure" block — Google rejects automated sign-in

- **Status:** Resolved · **Severity:** Critical (blocked `gflow auth login`) · **Fixed in:** v0.6.0a2

Google's sign-in flow (`accounts.google.com/v3/signin/rejected`) detected Playwright's bundled Chromium as an automated browser and refused the login with no user-facing error.

**Root cause (timing race):** Without `--disable-blink-features=AutomationControlled`,
Blink's C++ engine sets `navigator.webdriver = true` as a non-configurable, non-writable
native property at Chrome startup — before any JavaScript (including `add_init_script`)
can run. The `Object.defineProperty` override silently fails. With the flag, the property
is never set; the JS override then works as belt-and-suspenders.

**Resolution:** `v0.6.0a2` adds `RealChromeStrategy` — a new auth strategy that launches
the system's real Google Chrome via Playwright's `channel="chrome"` with stealth flags.

```bash
# Bypass G12 block explicitly:
gflow auth login --browser chrome

# Or rely on auto-detection (default behaviour; picks real Chrome if installed):
gflow auth login
```

A cosmetic "You are using an unsupported command-line flag" notice may appear briefly in
the Chrome window — this is harmless and can be dismissed. It is the accepted trade-off
for bypassing G12.

---

### v0.1 — provider methods are stubs

- **Status:** Resolved · **Severity:** Critical (blocked usage) · **Fixed in:** v0.2.0a1

The v0.1 scaffold left `upload_image`, `start_generation`, `get_job`, `download` raising `NotImplementedError`. v0.2.0a1 wired the video routes (T2V/I2V/batch) on a new `gflow_cli.api.client.FlowApiClient` and removed the legacy `providers/` + `models` modules. v0.3.0a1 added the image routes (`gflow image upload/t2i/i2i`) on the same client.

---

## Reporting a new issue

If you hit something not listed here:

1. Search existing issues at <https://github.com/ffroliva/gflow-cli/issues>.
2. If none match, open a new issue with:
   - `gflow-cli` version (`gflow --version`)
   - Python version (`python --version`)
   - OS + version
   - Exact command that failed + full error output
   - What you expected vs. what happened
3. For **security issues**, see [docs/SECURITY.md § Reporting](docs/SECURITY.md#reporting) — email instead of public issue.
