# Known Issues

Living list of behaviour that's broken, surprising, or limited by design — alongside workarounds and a pointer to the issue / version where each is tracked or resolved.

> Pair with [CHANGELOG.md](CHANGELOG.md) (what shipped per version) and [DISCLAIMER.md](DISCLAIMER.md) (legal/scope limits).

## Conventions

- **Status: Open** — still happens in latest release. Workaround listed.
- **Status: Mitigated** — partial fix in place; full resolution tracked.
- **Status: Resolved** — fixed in version `X.Y.Z`; row kept here for searchability.

---

## Open

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

_(none yet)_

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

---

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
