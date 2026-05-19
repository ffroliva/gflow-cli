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

- **Status:** Open · **Severity:** High (blocks image generation in the e2e path; production-CLI impact unconfirmed) · **Affects:** v0.6.0a6 · **Tracked:** N/A — needs a dedicated issue

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

### REST API 401 unauthorized on project creation

- **Status:** Open (Mitigated) · **Severity:** High · **Affects:** v0.2.0a1+ · **Fixed in:** v0.6.0a5 (planned)

Even with a valid browser session (cookies present), calling Flow's REST API directly via `fetch` or `page.request` (e.g., `project.createProject`) may return HTTP 401. This blocks the CLI's standard "pre-flight" sequence for generations.

**Root cause:** Google's backend has tightened security on its private trpc/REST endpoints, likely requiring specific headers (`Origin`, `Referer`) or a more complete browser fingerprint that raw script-driven requests lack.

**Workaround:** Use the **UI Mimicry** approach (used by the `scripts/smoke_worker_style.py` diagnostic). This strategy performs actions by clicking real buttons in the Flow editor instead of making raw REST calls.

**Roadmap:** v0.6.0a5 will refactor the `ui_automation` transport to handle its own project creation via the UI, bypassing the REST-based `create_project` blocker entirely for image generation.

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
