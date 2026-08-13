# Live Verification — v0.55.0

> Evidence ledger for the user-facing changes in v0.55.0 (Tier-1 quick wins
> #471–#476 + the subscription-claim docs correction). All verification below
> was **credit-free** — none of these features touch a Veo/Imagen generation
> path. Host: Windows 11, real Chrome profile `ffroliva`, 2026-08-13.

## 1. `gflow auth status` proves the Flow session (#471, PR #486)

Run live on this host against the real profile and the real Flow session
endpoint (`labs.google/fx/api/auth/session`, cookie-jar probe, no browser):

```
  profile: C:\Users\ffrol\AppData\Local\ffroliva\gflow-cli\profile_ffroliva
  exists: True
  cookies_present: True
  browser_engine: playwright
Probing Flow session (may take up to ~45s on a slow network)...
Flow session verified as ffroliva@gmail.com.
```

| Layer | Check | Result |
|---|---|---|
| Exit code | `0` on verified session (the new contract) | **PASS** |
| Identity | Verified account email printed (matches profile) | **PASS** |
| Wire | Live NextAuth session endpoint answered 200 with `user.email` | **PASS** |
| Fail-closed | VERIFICATION_ERROR/GOOGLE_SESSION_ONLY → exit 1 pinned by 6 unit tests + 2 BDD scenarios (mocked probe) | **PASS** |

## 2. `gflow mcp setup` (#475, PR #491)

Run end-to-end with the real CLI binary against a **scratch copy** of this
host's real `%APPDATA%\Claude\claude_desktop_config.json` (APPDATA redirected;
the real config untouched):

```
gflow MCP server configured for claude-desktop.
  config: <scratch>\Claude\claude_desktop_config.json
LIVE-RC=0
gflow entry: {'command': 'gflow', 'args': ['mcp', 'run']}
```

| Layer | Check | Result |
|---|---|---|
| Exit code | 0 | **PASS** |
| Artifact | Valid JSON config with the documented server entry | **PASS** |
| Non-destructive | Pre-existing keys preserved; pristine `.gflow-backup`; second run = no-op ("Already configured") — pinned by 20 tests | **PASS** |

## 3. Windows profile DACL hardening (#472, PR #488)

The two-step `icacls` idiom was **derived empirically on this host** (the
naive single-call `/inheritance:r … /t` form left files unreadable —
PermissionError on `Cookies`), then pinned by `test_real_icacls_round_trip`,
which executes the real `icacls.exe` on every Windows host including CI's
windows lane:

| Layer | Check | Result |
|---|---|---|
| ACL | Dir shows exactly one owner ACE `<user>:(OI)(CI)(F)` after hardening | **PASS** (manual icacls listing during the spike) |
| Usability | Pre-existing `Cookies` readable, new files writable afterwards | **PASS** (real-icacls test, this host + CI) |
| Fail-open | icacls failure logs `auth_profile_acl_failed` and never blocks login | **PASS** (unit) |

## 4. Suite-verified (no live-Flow surface — reason recorded)

- **Incident bug-report template (#476, PR #487):** the recorder pipeline is
  fully browser-free at test time (fake pages); exercising it live would
  require forcing a real Flow failure. Covered by 49 recorder/retention/BDD
  tests including the report canary (no raw exception text) and retention
  classification. No generation path touched.
- **MCP error funnel (#473, PR #489):** server-side masking only; 137 MCP
  tests including the registry-introspection guard (all 11 tools funneled)
  and masking canaries. No Flow interaction exists on this surface.
- **SecretStr settings (#474, PR #485):** pure config typing; canary tests
  pin that `repr`/`str`/`model_dump_json` never contain the secrets and the
  LLM client receives the raw key.
- **Docs correction (PR #490):** no runtime behavior.

## Pre-tag gates

- The Impeccable Routine (hygiene, links, PII, mirror, ruff, format, pyright-baseline): **green** on the release branch; the full pytest matrix runs in release-PR CI.
- `/gflow:doc-review` (mechanical 7-section pass + 3-agent LLM council):

> _Council verdict: RED / RED / YELLOW across the 3 auditors — both REDs narrow and fully resolved. 24 distinct findings; all Tier 1 (llms.txt + bespoke website pages still carrying the false subscription claim, PROJECT_STATUS.md seven releases stale, the removed `video batch` still in USAGE's synopsis, a fictional `--seed` flag documented in the skill AND USER_GUIDE, AGENTS.md exit-code range 3–30 vs actual 31, duplicate CHANGELOG section headings) and all Tier 2 (USAGE `gflow mcp` section + synopsis completeness + exit-1 caveat, AGENTS.md module list missing 8 real modules, `GFLOW_CLI_DAEMON_TOKEN` documented, AUTHENTICATION.md transcript + stale pin example, residual Ultra/Pro phrasing in SKILL.md/DISCLAIMER.md, `mcp setup` added to llms.txt) fixed in the release-prep commit. Tier 3 deferred: `GFLOW_CLI_TRANSPORT`/`GFLOW_CLI_EXPERIMENTAL_TRANSPORTS` absent from CONFIGURATION.md (pre-existing gap). The `website/site/` build output is gitignored and rebuilds on deploy. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

- Filled after publish: release workflow run + PyPI version + GitHub Release link (see the release summary in the session log).
