# Security

## Threat model

`gflow-cli` is a single-user, local CLI. The threat model is therefore:

| Asset | Threat | Severity |
|---|---|---|
| Google session cookies (in `$GFLOW_CLI_HOME/profile_<name>/Default/Cookies`) | Theft → full access to user's Google account | **High** |
| Generated outputs (`$GFLOW_CLI_OUTPUT_DIR/...`) | Unwanted disclosure | Medium (depends on content) |
| Local database (`$GFLOW_CLI_HOME/gflow.db`) | Disclosure of prompt history and asset provenance | Low–Medium (depends on prompt content; use `GFLOW_CLI_HISTORY_PROMPTS=redacted` to reduce) |
| `.env` file with `GFLOW_CLI_GEMINI_API_KEY` | Theft → API quota theft, billing | Medium |
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
- **Access:** OS file permissions enforce single-user access. On POSIX, `chmod 0700` is applied to the profile dir at creation time. On Windows, ACLs grant access only to the current user.
- **Lifetime:** Persists until `gflow auth logout`, manual deletion, or session invalidation by Google.

### Google account email

- **Location:** `$GFLOW_CLI_HOME/profile_<name>/.gflow_account` — one file per profile, plaintext UTF-8.
- **Content:** The verified email address of the signed-in Google account (e.g. `you@gmail.com`). Written once per `gflow auth login` by both auth strategies.
- **Sensitivity:** Low. The email is already visible to any process that can read Chromium cookies; it is not a credential. No passwords, tokens, or session artefacts are stored in this file.
- **Access:** Same OS file permissions as the profile dir (`chmod 0700` on POSIX). Visible only to the current user.

### Gemini API key (future official provider, planned v0.5+)

Not used by v0.4.0a2's reverse-engineered Flow provider. Documented here in advance of `GFLOW_CLI_PROVIDER=official`.

- **Location:** `$GFLOW_CLI_GEMINI_API_KEY` env var, optionally loaded from a `.env` file in the directory where you invoke `gflow` (CWD only — `$GFLOW_CLI_HOME` is not a `.env` search path; see [CONFIGURATION.md](CONFIGURATION.md)).
- **In memory:** Held only in the `Settings` dataclass, never logged.
- **In transit:** Sent only to `generativelanguage.googleapis.com` over HTTPS.
- **Rotate:** Set a new value in `.env`, restart the CLI. No persistence beyond the env var.

### Operational logs

- **Location:** stdout/stderr by default. No log file unless you redirect.
- **Content scrubbing:** Prompts, asset UUIDs, job IDs, profile names. No cookies, no tokens, no API keys.

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
| **Dependabot** | `.github/dependabot.yml` | Weekly alerts + PRs for outdated Python and Actions deps |

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
- **Exact-pinned** (`patchright==1.60.1`) so a release cannot silently move the
  browser binary underneath you.
- **Security-review-required on bump** — a Patchright version change is treated
  like a browser-binary change, NOT a routine dependabot auto-merge.
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
