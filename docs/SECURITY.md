# Security

## Threat model

`gflow-cli` is a single-user, local CLI. The threat model is therefore:

| Asset | Threat | Severity |
|---|---|---|
| Google session cookies (in `$GFLOW_CLI_HOME/profile_<name>/Default/Cookies`) | Theft → full access to user's Google account | **High** |
| HAR capture file (`GFLOW_CLI_HAR_PATH`, opt-in, off by default) | Contains live auth cookies + bearer tokens in plaintext — theft is equivalent to session cookie theft | **High** |
| Console/JSON output under `GFLOW_CLI_DEBUG_TRACEBACK` (opt-in, off by default) | Raw exception text may echo tokens/cookies present in error state, especially if piped to a shared/persistent system (CI logs, aggregators) | Medium–High (depends on destination) |
| Generated outputs (`$GFLOW_CLI_OUTPUT_DIR/...`) | Unwanted disclosure | Medium (depends on content) |
| Local database (`$GFLOW_CLI_HOME/gflow.db`) | Disclosure of prompt history and asset provenance | Low–Medium (depends on prompt content; use `GFLOW_CLI_HISTORY_PROMPTS=redacted` to reduce) |
| `.env` file with `GFLOW_CLI_LLM_API_KEY` | Theft → API quota theft, billing | Medium |
| Project-internal logs | Leaking prompts / asset IDs | Low |

## What we don't do

- ❌ We don't store or transmit your Google password.
- ❌ We don't ship telemetry. No phone-home, no usage stats, no remote logging by default.
- ❌ We don't write secrets to logs (verified by `_post_json` redaction tests in `tests/api/test_client.py`; reCAPTCHA tokens and bearer-style fields are scrubbed before any DEBUG-level body emission).
- ❌ We don't enable insecure TLS or skip certificate validation anywhere.

## Where secrets live

### Google session

- **Location:** `$GFLOW_CLI_HOME/profile_<name>/Default/Cookies` (a SQLite file managed by Chromium).
- **Format:** Standard Chromium cookie store, encrypted at rest by Chromium with the OS keystore (`keychain` on macOS, `Credential Manager` on Windows, `kwallet`/`gnome-keyring` on Linux).
- **Access:** OS file permissions enforce single-user access. On POSIX, `chmod 0700` is applied to the profile dir at creation time. On Windows, `gflow auth login` applies an explicit restrict-to-current-user DACL to the profile dir (`icacls`: inheritance stripped, a single owner-only ACE, children reset to inherit it) — so the protection holds even when `GFLOW_CLI_HOME` points at a location with permissive inherited ACLs, not just under `%LOCALAPPDATA%`. Profiles created by earlier versions are hardened once on their next use (marker-gated sweep at browser launch). Best-effort: an ACL failure is logged and never blocks login.
- **Lifetime:** Persists until `gflow auth logout`, manual deletion, or session invalidation by Google.

### Google account email

- **Location:** `$GFLOW_CLI_HOME/profile_<name>/.gflow_account` — one file per profile, plaintext UTF-8.
- **Content:** The verified email address of the signed-in Google account (e.g. `you@gmail.com`). Written once per `gflow auth login` by both auth strategies.
- **Sensitivity:** Low. The email is already visible to any process that can read Chromium cookies; it is not a credential. No passwords, tokens, or session artefacts are stored in this file.
- **Access:** Same OS file permissions as the profile dir (`chmod 0700` on POSIX). Visible only to the current user.

### Gemini API key (future official provider, planned v0.5+)

Not used by v0.4.0a2's reverse-engineered Flow provider. Documented here in advance of `GFLOW_CLI_PROVIDER=official`.

- **Location:** `$GFLOW_CLI_LLM_API_KEY` env var, optionally loaded from a `.env` file in the directory where you invoke `gflow` or from `$GFLOW_CLI_HOME/.env` (CWD wins on conflicts; see [CONFIGURATION.md](CONFIGURATION.md#env-loading)). Treat BOTH locations as secret-bearing files: keep `$GFLOW_CLI_HOME/.env` user-readable only and out of shared images/backups.
- **In memory:** Held only in the `Settings` dataclass, never logged.
- **In transit:** Sent only to `generativelanguage.googleapis.com` over HTTPS.
- **Rotate:** Set a new value in `.env`, restart the CLI. No persistence beyond the env var.

### Operational logs

- **Location:** stdout/stderr by default. No log file unless you redirect.
- **Content scrubbing:** Prompts, asset UUIDs, job IDs, profile names. No cookies, no tokens, no API keys.
- The structured `error_unhandled` telemetry event is **always** SHA-256-hashed, regardless of any debug flag below — this guarantee is unconditional.

### Automatic incident bundles (`GFLOW_CLI_INCIDENT_CAPTURE`, default on)

- **Location:** `<GFLOW_CLI_HOME>/incidents/` only. Never uploaded, never
  auto-attached to bug reports; remote error surfaces (MCP/HTTP/worker) see
  an opaque `{id, capture_status}` — never the local path, artifact names,
  profile paths, or lock paths.
- **Two sensitivity tiers.** The automatic JSON artifacts are built from an
  explicit allowlist: structural DOM signals, host *categories* + canonical
  routes (query strings stripped, unknown hosts reduced to `other`), status
  codes, and text *lengths/categories* — no prompts, tokens, cookies,
  headers, bodies, signed URLs, raw titles, or raw error/console text, and
  no unsalted hashes of low-entropy values (equality inside one command uses
  a random per-command HMAC key that is never persisted). The
  `sensitive/screenshot.png` tier CAN show your account identity, prompts,
  and media — the manifest marks it `sensitive` and every operator surface
  says review-before-sharing.
- **Access:** POSIX directories are created `0700` and files `0600` from
  first creation (not post-write chmod). On Windows there is no POSIX mode
  bit — protection relies on the inherited per-user ACLs of
  `%LOCALAPPDATA%`; gflow does not claim `chmod` creates a restrictive DACL
  there.
- **Bounded:** ≤100 network records, ≤100 console records, ≤50 page errors,
  ≤3 bundles per command, ≤50 complete bundles / 250 MiB retained. Retention
  validates schema + ownership before deleting anything and never follows
  symlinks/junctions or touches unknown directories.
- **Raw HAR is never enabled or copied** by the incident recorder — it stays
  a separate, explicit opt-in (below).

### HAR capture (`GFLOW_CLI_HAR_PATH`, opt-in)

- **Location:** wherever you point the env var. Not created unless explicitly set.
- **Content:** full Playwright network traffic for the session — every request/response, including headers and cookies. This means live Flow session cookies and bearer tokens are written to the file in plaintext.
- **Access:** chmod'd `0600` on POSIX after Playwright finishes writing it (best-effort; no-op on Windows, which has no equivalent POSIX permission bit).
- **Handling:** treat exactly like a session-cookie leak (see "I committed a session by mistake" below) if a HAR file is ever shared, committed, or uploaded anywhere. Never attach one to a public bug report.

### Debug tracebacks (`GFLOW_CLI_DEBUG_TRACEBACK`, opt-in)

- **Effect:** unhandled (non-typed) exceptions print their real message + full traceback — to the console, and under `--json`, into the payload's `error.detail`/`error.traceback` fields — instead of the default generic placeholder.
- **Risk:** the real exception text may contain tokens/cookies present in exception state. `--json` output under this flag is a materially higher-risk surface than the console: a human watches the console live, but `--json` is designed to be piped into CI logs, log aggregators, and webhooks that persist or forward it unreviewed. Never pipe `--json` output under this flag to a shared/persistent system without redacting it first.
- **Unaffected:** the structured telemetry event (`error_unhandled`) stays hashed regardless of this setting — only what the operator/caller sees changes.

## Local data layer

`gflow-cli` maintains a local SQLite database at `<GFLOW_CLI_HOME>/gflow.db` (default) that records provenance for every new image and video operation.

### What is stored

| Field | Stored? |
|---|---|
| Profile name | Yes |
| Flow project ID, media ID, workflow ID, operation ID | Yes |
| Local file paths or cloud URIs of downloaded assets | Yes |
| Prompt text | Yes (default) — set `GFLOW_CLI_HISTORY_PROMPTS=redacted` to store hash only |
| Prompt SHA-256 hash | Yes (always) |
| Asset metadata: model, aspect ratio, dimensions, seed, timestamps | Yes |
| Signed CDN URLs | **Never** — stripped by `redact_metadata` before DB write |
| reCAPTCHA tokens | **Never** — stripped by `redact_metadata` before DB write |
| Authorization headers and cookies | **Never** — stripped by `redact_metadata` before DB write |

### Privacy controls

- **`GFLOW_CLI_HISTORY_PROMPTS=redacted`** — store only the SHA-256 hash of the prompt, never the plain text. Useful when prompts contain sensitive or confidential content.
- The database is local-only. No database cloud sync, no telemetry upload. It lives on your filesystem and never leaves the machine unless you explicitly copy it.
- `GFLOW_CLI_DB_PATH` lets you redirect the database to any path (e.g. an encrypted volume). A fresh path creates an empty database automatically.

### Redaction guarantee

The `OperationRecorder.redact_metadata` method explicitly strips `signedUrl`, `cdnUrl`, `token`, `recaptchaToken`, `authorization`, and `cookie` keys (case-insensitive, including nested structures) before any data reaches the database. This is covered by unit tests in `tests/data/test_recorder.py`.

### Database file permissions

The database is created with standard OS file permissions. On POSIX systems this means it is readable by the current user (mode `0600` is not enforced — use filesystem-level controls if you need strict isolation). On Windows, ACLs follow the `GFLOW_CLI_HOME` directory defaults. Use full-disk encryption (FileVault / BitLocker / LUKS) if you store sensitive prompts and need at-rest protection.

## Cloud storage

When [`GFLOW_CLI_STORAGE_URI`](EXTERNAL_STORAGE.md) is set, generated asset
bytes are uploaded to the configured S3/GCS/MinIO bucket instead of local asset
files. This is explicit user configuration, not telemetry.

Security responsibilities for cloud storage:

- Keep AWS/GCS credentials out of Git and shell history. Prefer the provider's
  normal credential chain or short-lived environment variables.
- Lock bucket public access unless you intentionally need public outputs.
- Configure encryption, retention, lifecycle, and audit logging at the bucket
  layer. `gflow-cli` does not manage provider-side IAM policy.
- Treat object names and `cloud_uri` values as metadata. The local SQLite
  catalog stores those URIs so `gflow data media <media-id>` can find outputs.

`gflow-cli` still redacts Flow signed CDN URLs before writing metadata. Bucket
URIs are not signed Flow URLs; they are the durable output locations you asked
the CLI to write.

## CI / Repository security controls (v0.6.0a5+)

The following controls are active on this repository to prevent accidental leakage of personal data, session artefacts, or credentials:

| Control | Where | What it catches |
|---|---|---|
| **GitHub Secret Scanning + Push Protection** | GitHub Settings → Code security | OAuth tokens, API keys, Google credentials — blocked server-side before the commit lands |
| **`gitleaks` secret scan** | CI job `secrets-scan` (runs first, never skippable) | Entropy-based + regex detection of secrets across the full diff |
| **`detect-secrets` baseline** | `.pre-commit-config.yaml` + `.secrets.baseline` | Catches high-entropy strings and keyword patterns at commit time |
| **Repo hygiene script** | CI step + pre-commit | Blocks tracked images (`*.jpg/jpeg`), CDP lock files, test_assets output dirs, hardcoded Windows paths in any `.py` file |
| **`.gitignore` hardening** | `.gitignore` | Last-resort catch-all for untracked files |
| **CODEOWNERS** | `.github/CODEOWNERS` | Ensures security-sensitive files (auth, CI, hygiene gate) always request maintainer review |
| **Dependabot** | `.github/dependabot.yml` | Weekly alerts + PRs for outdated Python and Actions deps. Routine minor/patch bumps are **grouped** into one PR per ecosystem; majors open individually, and security updates stay ungrouped so an advisory fix does not wait for the weekly batch |
| **Label sync** | `.github/workflows/labels.yml` | Keeps the labels `dependabot.yml` references (`dependencies`, `python`, `github-actions`) actually present — Dependabot can apply a label but never create one, and a missing label silently drops the label from every bump PR |

### Known residual risk: git history

Commit `369fd1e` (2026-05-16) pushed artefacts that have since been removed from HEAD via `git rm --cached`. The data exposed was:

- Windows username (`ffrol`) and Google profile name (`denon82`) in script source files
- A CDP browser lock file (contained browser PID and port — no auth tokens)
- AI-generated JPG images (no PII)
- Flow UI element dumps in JSON (no auth tokens, UI text only)

**These commits remain in git history.** Any existing clone of the repo contains them. A `git filter-repo` history rewrite was decided against (fix-forward, see ADR #3 in `PLAN.md`) to avoid breaking forks and existing clones. The exposed data is PII (name, profile name) but not credentials — no Google tokens, passwords, or API keys were committed.

**To fully purge the history** (if your risk posture requires it):
```bash
# Install: pip install git-filter-repo
git filter-repo --path denon82/ --invert-paths --force
git filter-repo --path-glob 'test_assets/smoke_*/' --invert-paths --force
git filter-repo --path-glob 'test_assets/debug_*/' --invert-paths --force
git push --force --all
# Notify all forks and ask them to re-clone.
```
Note: GitHub's fork network means forks created before this date may still hold the original objects. History rewrite does not remove data from existing forks.



For users on shared / multi-user / production-adjacent machines:

- [ ] **Enable full-disk encryption** (FileVault on macOS, BitLocker on Windows, LUKS on Linux). Protects session cookies if the machine is lost.
- [ ] **Use a dedicated Google account** for `gflow-cli` automation if your main account has sensitive data (Gmail, Drive, etc.). Compromising the session compromises the *whole* Google account, not just Flow.
- [ ] **Set `GFLOW_CLI_HOME` to a non-default path** if you want the session away from the standard `LOCALAPPDATA` / `~/.local/share` location for any reason (auditability, separate volumes).
- [ ] **Use `--profile sandbox`** for short-lived experiments. Easy to delete (`rm -rf $GFLOW_CLI_HOME/profile_sandbox`) without disturbing your main profile.
- [ ] **Rotate sessions monthly** by signing out of Google → re-running `gflow auth login`. Limits blast radius of an unnoticed session theft.
- [ ] **Pin a gflow-cli version** in production (`uv tool install gflow-cli==0.5.0a1`) and review release diffs before upgrading.
- [ ] **Keep the package up-to-date for security fixes.** Subscribe to GitHub Releases for `ffroliva/gflow-cli`.
- [ ] **Scan your repo for accidentally-committed profiles** before pushing: `git ls-files | grep -E "profile_|cookies\.json|\.env$"`.

## "I committed a session by mistake"

If a profile dir or `.env` containing real secrets ever lands in a Git repo:

1. **Rotate immediately** — go to <https://myaccount.google.com/security> → "Your devices" → Sign out the leaked session. Then `gflow auth login` to mint fresh cookies. Treat any Gemini API keys in the same `.env` as compromised; revoke and rotate at <https://aistudio.google.com/apikey>.
2. **Purge from history** — `git rm` is insufficient; the secret remains in the Git object store. Use `git filter-repo`:
   ```bash
   git filter-repo --path-glob 'profile_*/' --invert-paths --force
   git filter-repo --path '.env' --invert-paths --force
   git push --force --all
   ```
3. **Tell collaborators** they need to re-clone. Old clones still hold the leaked secret.
4. **If the repo is public**, assume the secret is permanently compromised (search engines and tools like GitHub's secret scanner index quickly). Step 1 is your only mitigation.

## `.gitignore` rules in this repo

The repository's [`.gitignore`](../.gitignore) excludes:

```
auth/                    # any project-local auth dir
profile_*/               # any Chromium profile (regardless of name)
*.cookies.json           # exported cookie jars
storage_state.json       # Playwright storage_state output
secrets.json             # generic secrets file (commonly used by other tools)
*.env                    # any .env file (the .env.template is committed; .env is not)
```

These are belt-and-braces protection for the case where a user puts profiles inside the repo dir. **Default profile location is outside the repo** (`$LOCALAPPDATA/gflow-cli/...`, `~/.local/share/gflow-cli/...`). The `.gitignore` is the second line of defence.

## TLS / network

- All API calls use HTTPS to:
  - `https://aisandbox-pa.googleapis.com` (Flow REST surface)
  - `https://labs.google` (project create + asset URL redirects)
  - `https://generativelanguage.googleapis.com` (planned, official provider)
- No HTTP fallback, no `verify=False`, no custom CA bundles. Standard system trust store.
- Playwright's bundled Chromium handles cert pinning for Google domains as a real Chrome would.

## Dependencies

Audited with `pip-audit` in CI on every push. Major dependency surface:

- `playwright` — Microsoft, mature, security-reviewed, large user base. **Also the HTTP transport** (`page.request.post`) — auto-attaches Google session cookies; no separate `httpx`/`requests` runtime dep.
- `click` — Pallets, decade-old, stable.
- `rich` — Textualize, mature.
- `pydantic` / `pydantic-settings` — Pydantic, used by FastAPI ecosystem.
- `structlog` — Hynek Schlawack, mature.
- `tenacity` — Mature retry-helper, used widely in async-Python ecosystems.

No transitive dep with known CVEs at the time of v0.1.0 scaffold.

### Optional `patchright` engine — elevated supply-chain trust

The `gflow-cli[patchright]` extra (opt-in via `GFLOW_CLI_BROWSER_ENGINE=patchright`)
installs **Patchright**, a single-maintainer package that ships a **patched
Chromium driver**. Because that driver *is* the browser process that loads your
real Google session — it has direct access to session cookies, the OAuth Bearer
token, and SAPISID — it carries a higher blast radius than an ordinary Python
dependency: a compromised release could exfiltrate the full Google session.

Controls:

- **Optional-only** — never in the base `dependencies`; the default install pulls
  neither the package nor its driver.
- **Exact-pinned** (`patchright==1.61.2`) so a release cannot silently move the
  browser binary underneath you.
- **Security-review-required on bump** — a Patchright version change is treated
  like a browser-binary change, NOT a routine dependabot auto-merge. The exact
  pin alone does **not** enforce this: Dependabot's `uv` ecosystem rewrites a
  constraint standing in an update's way rather than respecting it (PR #465 did
  exactly that to playwright's upper bound). The enforcement is the explicit
  `ignore` entry in `.github/dependabot.yml`, guarded by
  `tests/test_playwright_pin.py::test_dependabot_ignores_driver_engine_bumps`.
- Default engine is `playwright` (Microsoft, security-reviewed); patchright is an
  experiment you opt into per the trade-off above.

## Reporting

| Issue type | How |
|---|---|
| **Security vulnerability** (RCE, auth bypass, secret leak in logs/output) | Email <ffroliva@gmail.com> with `gflow-cli SECURITY` in the subject. **Do not** open a public GitHub issue. PGP key available on request. |
| **Suspected supply-chain compromise** | Email + open a private GitHub Security Advisory at <https://github.com/ffroliva/gflow-cli/security/advisories/new>. |
| **Functional bug** (something just broke) | Public issue at <https://github.com/ffroliva/gflow-cli/issues> — include error output, OS, Python version. |
| **Documentation issue** (this page is wrong / unclear) | PR welcome. |

Acknowledgement target: **48 hours** for security reports. Initial fix or mitigation: **7 days** for high/critical, best-effort for medium/low.

## Disclosures

None to date. This section will list public CVEs, advisories, and patched versions as they happen.

## See also

- [DISCLAIMER](../DISCLAIMER.md) — legal scope & takedown policy
- [AUTHENTICATION](AUTHENTICATION.md) — full auth lifecycle
- [CONFIGURATION](CONFIGURATION.md) — secret-bearing env vars
