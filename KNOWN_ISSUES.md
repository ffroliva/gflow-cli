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

**Roadmap:** v0.4 will add a periodic background "session liveness" check that warns ~24h before expected expiry, and a `gflow auth refresh` command that opens the browser only if needed.

---

### Same profile can't be used in parallel

- **Status:** Open (by design) · **Severity:** Low · **Affects:** all versions

Chromium refuses to open two persistent contexts on the same `user-data-dir` simultaneously. So running two concurrent `gflow ...` calls with the same `--profile` will fail the second one with a "ProcessSingleton: profile is locked" error.

**Workaround:** use different profiles for parallel work.

```bash
# Terminal 1
gflow image batch ./batch-a.tsv --profile work

# Terminal 2 — different profile, same time, OK
gflow image batch ./batch-b.tsv --profile personal
```

**Roadmap:** v0.4's per-account pool will queue same-profile calls automatically.

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
