# Security

## Threat model

`flow-cli` is a single-user, local CLI. The threat model is therefore:

| Asset | Threat | Severity |
|---|---|---|
| Google session cookies (in `$FLOW_CLI_HOME/profile_<name>/Default/Cookies`) | Theft → full access to user's Google account | **High** |
| Generated outputs (`$FLOW_CLI_OUTPUT_DIR/...`) | Unwanted disclosure | Medium (depends on content) |
| `.env` file with `FLOW_CLI_GEMINI_API_KEY` | Theft → API quota theft, billing | Medium |
| Project-internal logs | Leaking prompts / asset IDs | Low |

## What we don't do

- ❌ We don't store or transmit your Google password.
- ❌ We don't ship telemetry. No phone-home, no usage stats, no remote logging by default.
- ❌ We don't write secrets to logs (verified by tests in `tests/security/`).
- ❌ We don't enable insecure TLS or skip certificate validation anywhere.

## Where secrets live

### Google session

- **Location:** `$FLOW_CLI_HOME/profile_<name>/Default/Cookies` (a SQLite file managed by Chromium).
- **Format:** Standard Chromium cookie store, encrypted at rest by Chromium with the OS keystore (`keychain` on macOS, `Credential Manager` on Windows, `kwallet`/`gnome-keyring` on Linux).
- **Access:** OS file permissions enforce single-user access. On POSIX, `chmod 0700` is applied to the profile dir at creation time. On Windows, ACLs grant access only to the current user.
- **Lifetime:** Persists until `gflow auth logout` (planned v0.2), manual deletion, or session invalidation by Google.

### Gemini API key (Phase 2+)

- **Location:** `$FLOW_CLI_GEMINI_API_KEY` env var, optionally loaded from a `.env` file in `$CWD` or `$FLOW_CLI_HOME`.
- **In memory:** Held only in the `Settings` dataclass, never logged.
- **In transit:** Sent only to `generativelanguage.googleapis.com` over HTTPS.
- **Rotate:** Set a new value in `.env`, restart the CLI. No persistence beyond the env var.

### Operational logs

- **Location:** stdout/stderr by default. No log file unless you redirect.
- **Content scrubbing:** Prompts, asset UUIDs, job IDs, profile names. No cookies, no tokens, no API keys.

## Hardening checklist

For users on shared / multi-user / production-adjacent machines:

- [ ] **Enable full-disk encryption** (FileVault on macOS, BitLocker on Windows, LUKS on Linux). Protects session cookies if the machine is lost.
- [ ] **Use a dedicated Google account** for `flow-cli` automation if your main account has sensitive data (Gmail, Drive, etc.). Compromising the session compromises the *whole* Google account, not just Flow.
- [ ] **Set `FLOW_CLI_HOME` to a non-default path** if you want the session away from the standard `LOCALAPPDATA` / `~/.local/share` location for any reason (auditability, separate volumes).
- [ ] **Use `--profile sandbox`** for short-lived experiments. Easy to delete (`rm -rf $FLOW_CLI_HOME/profile_sandbox`) without disturbing your main profile.
- [ ] **Rotate sessions monthly** by signing out of Google → re-running `gflow auth login`. Limits blast radius of an unnoticed session theft.
- [ ] **Pin a flow-cli version** in production (`uv tool install flow-cli==0.2.1`) and review release diffs before upgrading.
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

These are belt-and-braces protection for the case where a user puts profiles inside the repo dir. **Default profile location is outside the repo** (`$LOCALAPPDATA/flow-cli/...`, `~/.local/share/flow-cli/...`). The `.gitignore` is the second line of defence.

## TLS / network

- All API calls use HTTPS to:
  - `https://aisandbox-pa.googleapis.com` (Flow REST surface)
  - `https://labs.google` (project create + asset URL redirects)
  - `https://generativelanguage.googleapis.com` (planned, official provider)
- No HTTP fallback, no `verify=False`, no custom CA bundles. Standard system trust store.
- Playwright's bundled Chromium handles cert pinning for Google domains as a real Chrome would.

## Dependencies

Audited with `pip-audit` in CI on every push. Major dependency surface:

- `playwright` — Microsoft, mature, security-reviewed, large user base.
- `httpx` — Encode/Anyio, security-reviewed, used by FastAPI and many others.
- `click` — Pallets, decade-old, stable.
- `rich` — Textualize, mature.
- `pydantic` / `pydantic-settings` — Pydantic, used by FastAPI ecosystem.
- `structlog` — Hynek Schlawack, mature.

No transitive dep with known CVEs at the time of v0.1.0 scaffold.

## Reporting

| Issue type | How |
|---|---|
| **Security vulnerability** (RCE, auth bypass, secret leak in logs/output) | Email <ffroliva@gmail.com> with `flow-cli SECURITY` in the subject. **Do not** open a public GitHub issue. PGP key available on request. |
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
