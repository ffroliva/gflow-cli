# Authentication

`gflow-cli` does **not** re-implement Google OAuth. Instead it piggybacks on Playwright's persistent context: you sign in once through a real Chromium window, and the resulting cookie jar is reused by every subsequent CLI invocation as the HTTP transport's session. The actual REST calls go to `aisandbox-pa.googleapis.com` over HTTPS — Playwright's `page.request` API auto-attaches the right cookies, so there is no token to extract or refresh manually.

This page documents the full lifecycle: capture, storage, reuse, refresh, multi-account, revocation.

## Why piggyback on a browser session

| Alternative considered | Why rejected |
|---|---|
| Re-implement Google's OAuth dance | Google's web SSO involves anti-automation challenges (CAPTCHA, device verification). A community SDK can't reliably ship that. |
| Extract the bearer token from cookies and use `httpx` directly | Tokens are short-lived and tied to a refresh flow we don't have. Re-implementing the refresh is brittle. |
| Use a service-account JSON | Flow doesn't currently support service accounts on the AI Ultra/Pro consumer tier. |
| Pure stored cookie jar (no Playwright) | Some Flow endpoints set additional `x-` headers based on Chromium fingerprint; matching them by hand is fragile. |
| **Playwright persistent context (chosen)** | Captures session once, reuses indefinitely, refreshes automatically on idle, auto-attaches every header Flow expects. One-time cost: a ~150 MB Chromium download. |

## High-level flow

```text
                                          ┌──────────────────────────┐
  $ gflow auth login   ──────────────────►│  Playwright launches a   │
                                          │  HEADED Chromium window  │
                                          └────────────┬─────────────┘
                                                       │
                                                       ▼
                              ┌──────────────────────────────────────┐
                              │  User signs into Google,             │
                              │  lands on labs.google/fx/tools/flow  │
                              └────────────┬─────────────────────────┘
                                           │
                                           ▼
                              ┌──────────────────────────────────────┐
                              │  Cookies + IndexedDB persist to       │
                              │  $GFLOW_CLI_HOME/profile_<name>/       │
                              │  (Chromium user-data-dir layout)      │
                              └────────────┬─────────────────────────┘
                                           │
                                           ▼
  $ gflow image t2i ...                    (later, headless)
                                           │
                                           ▼
                              ┌──────────────────────────────────────┐
                              │  Playwright launches HEADLESS        │
                              │  Chromium with the same profile dir; │
                              │  page.request.post(...) auto-sends   │
                              │  cookies. No token plumbing needed.  │
                              └──────────────────────────────────────┘
```

## Session storage

### Default location (well-known per OS)

The session is **always stored outside the project tree** in a stable, user-local directory. `gflow-cli` resolves the path via [`platformdirs`](https://github.com/platformdirs/platformdirs) — same conventions used by `pip`, `poetry`, `uv`, `httpx`, etc.

| OS | Default profile dir |
|---|---|
| Windows | `%LOCALAPPDATA%\gflow-cli\profile_<name>\`  (e.g. `C:\Users\<you>\AppData\Local\gflow-cli\profile_default\`) |
| macOS | `~/Library/Application Support/gflow-cli/profile_<name>/` |
| Linux (XDG) | `$XDG_DATA_HOME/gflow-cli/profile_<name>/` (typically `~/.local/share/gflow-cli/profile_<name>/`) |

> ⚠️ **The session is NOT stored in the OS temp dir** (`/tmp`, `%TEMP%`). OS temp dirs get periodically reaped (boot-time cleanups, `tmpwatch`, `cleanmgr`), which would force you to re-login every reboot. We use the persistent user-data-dir instead — same place a regular Chromium profile lives.

### Override

Set `GFLOW_CLI_HOME` to put profiles anywhere you want:

```bash
# Linux/macOS
export GFLOW_CLI_HOME=/secure-volume/gflow-cli

# Windows (PowerShell)
$env:GFLOW_CLI_HOME = "D:\gflow-cli"
```

Resulting profile dir becomes `$GFLOW_CLI_HOME/profile_<name>/`.

### What's actually inside a profile dir

A profile dir is a full Chromium user-data-dir. The interesting files for `gflow-cli`:

```
profile_default/
├── Default/
│   ├── Cookies              ← SQLite DB of cookies (incl. Google session)
│   ├── IndexedDB/           ← Flow's per-account state
│   ├── Local Storage/       ← Some Flow client config
│   └── Preferences          ← Chrome-level settings
├── BrowserMetrics-spare.pma
└── (lots of other Chromium files we don't care about)
```

**Treat this directory as a secret.** It contains active Google session credentials. See [SECURITY.md](SECURITY.md) for hardening.

### Why it must be `.gitignored`

Even though profiles live outside the repo by default, three scenarios can put them inside:

1. A user sets `GFLOW_CLI_HOME=.` from inside the repo (e.g. for a sandboxed dev session).
2. A test fixture writes a temporary profile to `tests/fixtures/profile_*/`.
3. Someone clones the repo into a path that already has a `gflow-cli/` folder with a profile.

`.gitignore` covers all three by excluding `auth/`, `profile_*/`, `*.cookies.json`, `storage_state.json`, `secrets.json` at the repository root. Never disable these rules. If you accidentally `git add` a profile, see [SECURITY § "I committed a session by mistake"](SECURITY.md#i-committed-a-session-by-mistake).

## Commands

### `gflow auth` (no subcommand)

The bare command does the right thing based on current state:

- **No profiles yet** → automatically launches `gflow auth login` to create one.
- **One or more profiles** → prints the inventory table (profile names, session present, last used, default marker, full path).

```text
$ gflow auth

Profiles in /home/you/.local/share/gflow-cli

  Default  Name       Session   Last used (UTC)        Profile dir
    ●      default    present   2026-05-09 14:42:18    /home/you/.local/share/gflow-cli/profile_default
           work       present   2026-05-08 09:11:02    /home/you/.local/share/gflow-cli/profile_work
           experiments missing  -                      /home/you/.local/share/gflow-cli/profile_experiments

Use `gflow auth use <name>` to set the default profile.
Use `gflow auth login --profile <name>` to add or refresh a profile.
```

### `gflow auth login`

Opens a headed Chromium, navigates to `https://labs.google/fx/tools/flow?hl=en`, and waits for you to sign in. The browser window must be closed within **10 minutes total** (the underlying `wait_for_event("close", timeout=600_000)`); after that the command times out. When the window is closed, the captured session is persisted to disk.

```bash
gflow auth login                  # default profile
gflow auth login --profile work   # named profile (creates if missing)
```

Re-running this command refreshes an expired session: it reuses the existing profile dir, so you typically just have to click "Continue as <you>" on the Google account chooser.

### `gflow auth status`

Reports whether a profile exists, where it lives, and whether the cookies file is present.

```bash
$ gflow auth status
Profile 'default' is configured.
  profile: /home/you/.local/share/gflow-cli/profile_default
  exists: True
  cookies_present: True
  cookies_path: /home/you/.local/share/gflow-cli/profile_default/Default/Cookies
```

> Note: `cookies_present: True` only confirms the file exists — not that the session is still valid with Google. The first real API call (e.g. `gflow image t2i`) is the actual probe. If Google has invalidated the session, the call will fail with an authentication error and you'll be prompted to re-run `auth login`.

### `gflow auth list`

Same output as bare `gflow auth` when profiles exist — useful when you want the table even from a script (no auto-login fallback).

### `gflow auth use <name>`

Sets `<name>` as the default profile. Persisted to `$GFLOW_CLI_HOME/config.toml`.

```bash
gflow auth use work
# Default profile set to work
# Persisted in /home/you/.local/share/gflow-cli/config.toml
```

After this, every command without `--profile` and without `GFLOW_CLI_PROFILE` resolves to `work`.

### Default profile resolution

Precedence (highest first):

1. **CLI flag** `--profile <name>`
2. **Env var** `GFLOW_CLI_PROFILE`
3. **`config.toml`** `default_profile` (set by `gflow auth use`)
4. **Auto** — if exactly one profile exists, it becomes the de-facto default.
5. **Fail** with a friendly error listing the available profiles, if 2+ profiles exist and none of (1)-(3) is set.

The first successful `gflow auth login` automatically sets the new profile as default (so a single-account user never sees "no default" friction).

### `gflow auth logout`

Deletes the profile dir and clears it as default if it was set. Confirms before destroying state — pass `--yes` to skip the prompt for scripts.

```bash
gflow auth logout                     # uses resolved default
gflow auth logout --profile work
gflow auth logout --profile work --yes  # no confirmation
```

## Multiple accounts

Run any command with `--profile <name>` to use a different session:

```bash
gflow auth login --profile personal
gflow auth login --profile client-a
gflow auth login --profile client-b

gflow image t2i "test"          --profile personal
gflow image t2i "client work"   --profile client-a
```

Each profile is fully isolated (its own cookies, its own Flow project history). You can run multiple `gflow` calls concurrently across profiles since each launches its own Chromium context — but **never run two concurrent calls against the same profile**, because Chromium will refuse to open a second persistent context on a locked user-data-dir.

For automated multi-account batching: concurrency *within* one profile shipped in v0.4.0a2 — set `GFLOW_CLI_CONCURRENCY=N` (1–16) and `gflow video batch` fans out across N Playwright Pages on one shared BrowserContext. Cross-profile parallel batches are still "one shell per profile" (Chromium per-profile lock; see [KNOWN_ISSUES § Same profile can't be used in parallel](../KNOWN_ISSUES.md#same-profile-cant-be-used-in-parallel)).

## Refresh / expiry

Google sessions don't have a fixed lifetime; they expire when:

- You change your Google password.
- You remove the device from your account at <https://myaccount.google.com/device-activity>.
- A long stretch of inactivity passes (typically months).
- You explicitly sign out from another device's session manager.

When this happens, the next REST call returns 401/403 and `gflow-cli` raises `AuthExpiredError` with a remediation hint:

```text
ERROR: Auth expired for profile 'default'.
Run: gflow auth login --profile default
```

Re-running `auth login` refreshes the cookies in place — no other state is lost.

## Threat model & limits

| Threat | Mitigation |
|---|---|
| Session file leaked to a public repo | `.gitignore` excludes profile dirs at every layer. Recommended belt-and-braces: keep the gflow-cli home outside the repo (default location via `platformdirs` already does this) and run `git status` before any commit. Automatic in-repo detection is on the backlog (not yet scheduled). |
| Multi-user shared machine | Profiles live under each user's home dir; OS file permissions (`0700` on POSIX, ACL on Windows) prevent cross-user reads by default. |
| `gflow-cli` itself becomes malicious | The package is open-source under MIT; pin a version (`uv tool install gflow-cli==0.5.0a1`) and review release diffs before upgrading. |
| Stolen laptop | Anyone with disk access has your session. Use full-disk encryption (FileVault, BitLocker, LUKS). Consider a dedicated `--profile sandbox` for short-lived experiments. |
| Sharing a profile between machines | Technically works (copy the profile dir), but Google may flag the device-fingerprint mismatch as suspicious. Re-login on the new machine instead. |

For deeper guidance see [SECURITY.md](SECURITY.md).

## FAQ

**Q: Can I use `gflow-cli` without a browser at all?**
A: Not for `auth login` — that step needs Chromium so you can solve any 2FA/CAPTCHA challenge interactively. After login, all generation calls run headless. Plan to use a workstation for the one-time auth, then copy the profile dir to a headless server (re-running `auth login` there is recommended though, see threat model above).

**Q: Does this support Google Workspace SSO?**
A: Yes — sign in normally during `auth login`. Whatever your IdP flow looks like in the browser, that's what you'll go through. The captured cookies are the same.

**Q: What about MFA / passkeys?**
A: Handled by the browser during `auth login`. You'll do the MFA challenge once; subsequent CLI calls reuse the cookies.

**Q: How do I rotate a session?**
A: Sign out of your Google account from the browser (any browser), then run `gflow auth login` again. The old cookies are now invalid; new ones replace them.
