# Authentication

`gflow-cli` does **not** re-implement Google OAuth. Instead it piggybacks on Playwright's persistent context: you sign in once through a real Chromium window, and the resulting cookie jar is reused by every subsequent CLI invocation as the HTTP transport's session. The actual REST calls go to `aisandbox-pa.googleapis.com` over HTTPS — Playwright's `page.request` API auto-attaches the right cookies, so there is no token to extract or refresh manually.

This page documents the full lifecycle: capture, storage, reuse, refresh, multi-account, revocation.

## Why piggyback on a browser session

| Alternative considered | Why rejected |
|---|---|
| Re-implement Google's OAuth dance | Google's web SSO involves anti-automation challenges (CAPTCHA, device verification). A community SDK can't reliably ship that. |
| Extract the bearer token from cookies and use `httpx` directly | Tokens are short-lived and tied to a refresh flow we don't have. Re-implementing the refresh is brittle. |
| Use a service-account JSON | Flow doesn't currently support service accounts on the consumer tier. |
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
  $ gflow image t2i ...                    (later, headed by default)
                                           │
                                           ▼
                              ┌──────────────────────────────────────┐
                              │  Playwright launches HEADED Chrome   │
                              │  with the same profile dir (the      │
                              │  production default — reCAPTCHA      │
                              │  Enterprise rejects headless outright│
                              │  with a 403); page.request.post(...) │
                              │  auto-sends cookies. No token         │
                              │  plumbing needed.                     │
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
profile_your-name/
├── Default/
│   ├── Cookies              ← SQLite DB of cookies (incl. Google session)
│   ├── IndexedDB/           ← Flow's per-account state
│   ├── Local Storage/       ← Some Flow client config
│   └── Preferences          ← Chrome-level settings
├── .gflow_account           ← signed-in Google email (written by auth login)
├── .gflow_browser_strategy  ← "chrome" or "internal" (transport selector)
├── BrowserMetrics-spare.pma
└── (lots of other Chromium files we don't care about)
```

`.gflow_account` contains the plain email address (`your.name@gmail.com`) and is
written by `gflow auth login` immediately after the session is verified. It is the
source of truth for "which Google account is this profile signed into?", surfaced by
`gflow auth list` and `profile_store.list_profiles()`.

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

  Default  Name          Google account          Session   Last used (UTC)        Profile dir
    ●      default       you@gmail.com            present   2026-05-09 14:42:18    /home/you/.local/share/gflow-cli/profile_default
           work          you@work.example.com     present   2026-05-08 09:11:02    /home/you/.local/share/gflow-cli/profile_work
           experiments   unknown                  missing   -                      /home/you/.local/share/gflow-cli/profile_experiments

Use `gflow auth use <name>` to set the default profile.
Use `gflow auth login --profile <name>` to add or refresh a profile.
```

### `gflow auth login`

Opens a headed browser, navigates to `https://labs.google/fx/tools/flow?hl=en`, and waits
for you to sign in. The CLI automatically detects success and persists the session to disk.

```bash
gflow auth login                   # default profile, auto browser
gflow auth login --profile work    # named profile (creates if missing)
gflow auth login --browser chrome  # force real Chrome (bypasses G12 block)
```

Re-running this command refreshes an expired session: it reuses the existing profile dir,
so you typically just have to click "Continue as <you>" on the Google account chooser.

#### First-run auto-naming

When no `--profile` flag is given and no profiles exist yet, `gflow auth login`
creates a profile with a temporary name of `default`. Once the session is verified
and the signed-in email is known, it **automatically renames** the profile to the
local-part of the email address (e.g. `profile_default` → `profile_your-name`) and
updates `config.toml`'s `default_profile` pointer atomically.

The local-part is sanitized to a filesystem-safe name: any character outside
letters, digits, `-`, and `_` is replaced with `-`. So `your.name@gmail.com`
becomes `profile_your.name-oliva` and `user+flow@gmail.com` becomes `profile_user-flow`.
If nothing usable remains, the profile keeps the name `default`.

```text
$ gflow auth login
Launching real Chrome...
[OK] Flow session verified (your.name@gmail.com).
Session saved. Profile dir: …/profile_your-name
Renamed profile from default to ffroliva (derived from Google account).
Set ffroliva as default profile.
```

If a profile named after the email local-part already exists, the rename is skipped
and the profile keeps the name `default`.

#### `--browser [auto|chrome|internal]`

| Value | Browser used | When to use |
|---|---|---|
| `auto` (default) | Real Chrome if installed; falls back to internal | First choice for most users |
| `chrome` | System Google Chrome (**Passive Capture**) | Required to bypass "G12" blocks |
| `internal` | Playwright's bundled Chromium | Fallback when Chrome isn't installed |

Override with the env var: `GFLOW_CLI_AUTH_BROWSER=chrome gflow auth login`

**Why `chrome` bypasses bot detection:** Playwright's default automation mode exposes
`navigator.webdriver = true` as a non-configurable native property. Google detects this
and redirects to `/v3/signin/rejected` (the "G12 block"). The `chrome` strategy
implements **Passive Capture**: it launches your real system Chrome as a 100% standard
process without any automation flags or debugging ports. You log in manually, close
the window, and `gflow` extracts the verified session from the profile.

**Privacy guard:** The `chrome` strategy strictly refuses to use any profile directory
outside `GFLOW_CLI_HOME`. This protects your primary system Chrome profile from
accidental interference or data corruption.

### `gflow auth status`

Reports where the profile lives, then **proves the session**: it probes the Flow session endpoint with the profile's cookies (no browser, no credits) and exits `0` only when the session is verified — `1` otherwise. Handy for scripts: `gflow auth status && gflow run ...`.

```bash
$ gflow auth status
  profile: /home/you/.local/share/gflow-cli/profile_default
  exists: True
  cookies_present: True
  cookies_path: /home/you/.local/share/gflow-cli/profile_default/Default/Cookies
  browser_engine: playwright
Probing Flow session (may take up to ~45s on a slow network)...
Flow session verified as you@example.com.
```

> Note: `cookies_present: True` only confirms the file exists. The final verdict line is the live probe result: a dead session prints a `gflow auth login` remediation hint and exits 1; a probe that cannot reach the endpoint (offline, 5xx) also exits 1 but suggests checking connectivity instead of re-logging in.

### `gflow auth list`

Same output as bare `gflow auth` when profiles exist — useful when you want the table even from a script (no auto-login fallback).

```text
$ gflow auth list

Profiles in /home/you/.local/share/gflow-cli

  Default  Name       Google account          Session   Last used (UTC)        Profile dir
    ●      ffroliva   your.name@gmail.com       present   2026-05-28 10:14:22    …/profile_your-name
           work       alice@work.example.com   present   2026-05-27 08:30:01    …/profile_work
           old        unknown                  missing   -                      …/profile_old
```

The **Google account** column shows the email address from `.gflow_account`. Profiles
created before develop post-v0.9.1 (before this feature shipped) will show `unknown`
until `gflow auth login` is re-run against them — the file is written on every login.

`--json` includes `google_account` per entry:

```bash
$ gflow auth list --json | jq '.[] | {name, google_account}'
{"name": "ffroliva", "google_account": "your.name@gmail.com"}
{"name": "work",     "google_account": "alice@work.example.com"}
{"name": "old",      "google_account": null}
```

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

## Profile naming

### Convention

Use a short, stable identifier derived from the Google account you're signing in with.
The auto-rename on first login handles this for you: if you run `gflow auth login`
without `--profile`, the profile ends up named after your email local-part automatically.

For subsequent accounts, pass `--profile` explicitly:

```bash
gflow auth login --profile alice        # alice@example.com
gflow auth login --profile bob-work     # bob@work.example.com
gflow auth login --profile personal     # or any name you prefer
```

### Why avoid `default`

A profile named `default` is opaque — you can't tell which Google account it holds,
what locale it uses, or whether it's still valid just from the name. The auto-rename
handles the common case; if you find yourself with an old `default` profile you'd
like to rename, use:

```bash
gflow auth use default     # confirm it's the one you want to rename
gflow auth login           # re-runs login against the default; auto-renames on success
```

Or rename it manually:

```bash
# Verify the email first
gflow auth list

# Then re-login under a meaningful name
gflow auth login --profile ffroliva
gflow auth logout --profile default --yes   # delete the old one
```

### One Google account, multiple profiles

Each `gflow auth login --profile <name>` creates an independent Chromium user-data-dir.
You can have multiple profiles pointing at the same Google account — they each hold their
own cookie jar and Flow project history. This is intentional: it lets you isolate
experiments, run a "clean" profile for testing, or keep a production profile separate
from a sandbox one.

`gflow auth list` surfaces the Google account for each profile, making duplicates visible:

```
Default  Name           Google account           Session
  ●      ffroliva       your.name@gmail.com        present
         sandbox        your.name@gmail.com        present   ← same account, different dir
         work           alice@work.example.com    present
```

gflow does **not** enforce uniqueness across profiles — two profiles pointing at the same
account is valid and supported. The data layer (`gflow data`) keys records by
`profile_name`, so each profile builds its own independent generation history.

## Multiple accounts

Run any command with `--profile <name>` to use a different session:

```bash
gflow auth login --profile personal
gflow auth login --profile client-a
gflow auth login --profile client-b

gflow image t2i "test"          --profile personal
gflow image t2i "client work"   --profile client-a
```

Each profile is fully isolated (its own cookies, its own Flow project history). **Different profiles run fully in parallel** — each acquires its own cross-process `ProfileLease` on its own canonical directory, so concurrent `gflow` calls across profiles never contend. **The same profile can only ever have one active holder.** A cross-process advisory lock (`ProfileLease`, kernel `flock`/`msvcrt.locking`) enforces this fail-fast: a second call against an already-leased profile — whether it's another `gflow` invocation, the `gflow serve` daemon, or an MCP tool call — is rejected immediately with `ProfileLockedError` (exit code 11); it never blocks or waits for the lease to free up.

For automated multi-account work: a per-profile Page pool (`GFLOW_CLI_CONCURRENCY=N`, 1–16, shipped in v0.4.0a2) opens N Playwright Pages on one shared BrowserContext within a single profile, but no current CLI command drives more than one generation concurrently through it — the one caller that used to (a manifest-driven video batch runner) never worked and was removed. Cross-profile parallel runs remain "one shell per profile"; same-profile contention is a fast, typed rejection rather than a Chromium crash (see [KNOWN_ISSUES § Same profile can't be used in parallel](../KNOWN_ISSUES.md#same-profile-cant-be-used-in-parallel)).

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

## Session verification (cookie-store fast path)

Since v0.17.0, profile verification (run by `gflow auth login --browser chrome` after
capture) is handled by `gflow_cli.auth.verification.verify_flow_profile`, which probes
Flow's `/fx/api/auth/session` endpoint **directly from the Chrome cookie store** via
`browser_cookie3` + `httpx` — no browser launch needed (contributed in PR #168 by
@3mora2). Two hardening behaviors:

- **Encrypted/locked store fallback** — if the cookie store can't be read (e.g.
  Windows DPAPI decryption fails, or the store is locked by a running Chrome), the
  verifier falls back to a marker-gated Playwright probe. The chrome marker is written
  before verification so the fallback can find the profile; on failure only a
  *speculative* marker write is rolled back — a previously-verified profile survives a
  transient probe failure.
- **Transient-error retry** — HTTP 429/503/504 and network errors retry with
  exponential backoff before the verifier gives up.

The outcome is the same `AUTHENTICATED` / no-session decision documented under
[`gflow auth status`](#gflow-auth-status); only the transport is faster. Cookie
extraction lives in `gflow_cli.auth.cookies`.

## Threat model & limits

| Threat | Mitigation |
|---|---|
| Session file leaked to a public repo | `.gitignore` excludes profile dirs at every layer. Recommended belt-and-braces: keep the gflow-cli home outside the repo (default location via `platformdirs` already does this) and run `git status` before any commit. Automatic in-repo detection is on the backlog (not yet scheduled). |
| Multi-user shared machine | Profiles live under each user's home dir; OS file permissions (`0700` on POSIX, ACL on Windows) prevent cross-user reads by default. |
| `gflow-cli` itself becomes malicious | The package is open-source under MIT; pin a version (`uv tool install gflow-cli==0.55.0`) and review release diffs before upgrading. |
| Stolen laptop | Anyone with disk access has your session. Use full-disk encryption (FileVault, BitLocker, LUKS). Consider a dedicated `--profile sandbox` for short-lived experiments. |
| Sharing a profile between machines | Technically works (copy the profile dir), but Google may flag the device-fingerprint mismatch as suspicious. Re-login on the new machine instead. |

For deeper guidance see [SECURITY.md](SECURITY.md).

## FAQ

**Q: Can I use `gflow-cli` without a browser at all?**
A: No — not for `auth login`, and not for generation either. `auth login` needs Chromium so you can solve any 2FA/CAPTCHA challenge interactively. Generation calls also launch a real, **headed** Chrome window by default (`GFLOW_CLI_HEADLESS=false`) — the `ui_automation` transport is gflow-cli's only production transport, and reCAPTCHA Enterprise rejects headless Chromium with an immediate 403, so there is no way to run production generation on a display-less machine without a virtual display (e.g. Xvfb) or a workstation. Plan for that when choosing where to run gflow-cli long-term; see [CONFIGURATION.md § `GFLOW_CLI_HEADLESS`](CONFIGURATION.md#gflow_cli_headless).

**Q: Does this support Google Workspace SSO?**
A: Yes — sign in normally during `auth login`. Whatever your IdP flow looks like in the browser, that's what you'll go through. The captured cookies are the same.

**Q: What about MFA / passkeys?**
A: Handled by the browser during `auth login`. You'll do the MFA challenge once; subsequent CLI calls reuse the cookies.

**Q: How do I rotate a session?**
A: Sign out of your Google account from the browser (any browser), then run `gflow auth login` again. The old cookies are now invalid; new ones replace them.
