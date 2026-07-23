# Debugging, testing & troubleshooting

> Where to look and which tool to reach for when something breaks. Per-release
> evidence (e.g. live verification runs) lives in sibling docs like
> [`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md). This file is
> evergreen.

## Quick reference

| Symptom | First thing to try | Read |
|---|---|---|
| Any operational failure (crash, drift, timeout, WAF, lock) | Open the incident bundle path printed with the error | [Automatic incident bundles](#automatic-incident-bundles) |
| `gflow image t2i` hangs ≥ 3 min then fails with `TimeoutError` | Re-run with `--verbose` and grep for `batch_response_seen` | [Listener log keys](#listener--http-layer-debugging) |
| `aspect_ratio_set_failed` warning then wrong-aspect output | The aspect-tab selector cascade missed; capture a DOM snapshot of the gen-settings panel | [Inspecting Flow's live UI](#inspecting-flows-live-ui) |
| `UnicodeEncodeError: 'charmap' codec can't encode` on Windows | Set `PYTHONUTF8=1` (PowerShell: `$env:PYTHONUTF8="1"`) before any `gflow` invocation | [Windows console](#windows-console-encoding) |
| `AuthBrowserRejectedError` / exit 14 | Re-login with `--browser chrome` | [`AUTHENTICATION.md`](AUTHENTICATION.md), `/gflow:known-issues` |
| `BrowserSessionClosedError` / exit 15 in a long-lived worker | Recreate the `FlowApiClient` via its async context manager | [Lifecycle errors](#lifecycle--browser-state) |
| Test suite OOMs / sandbox crashes | Run dirs separately (`tests/api`, `tests/auth tests/cli`, `tests/features`, then the rest with `--ignore`) | [Test suite memory](#test-suite-memory) |
| New Flow UI label breaks a selector | Add a candidate to `_ASPECT_TAB_CANDIDATES` (or the relevant cascade) and live-verify | [Selector cascades](#selector-cascades) |

## Listener & HTTP-layer debugging

`UiAutomationTransport._attach_batch_response_listener` records every
`batchGenerateImages` response. With `--verbose` you get these structured
log events (`structlog` JSON lines):

| Event key | When it fires | Useful fields |
|---|---|---|
| `ui_automation.image_mode_entered` | After `_enter_editor` + `_dismiss_blocking_overlays`, once the editor has been switched to Image mode. Mirror on the video side: `ui_automation_video.video_mode_entered`. | _(none — presence is the signal)_ |
| `ui_automation.batch_response_seen` | EVERY `batchGenerateImages` URL observed, BEFORE the project-id filter | `url`, `status`, `filter_project_id` |
| `ui_automation.batch_response_dropped_project_id_mismatch` | URL contained `batchGenerateImages` but project-id filter rejected it | `url`, `filter_project_id` |
| `ui_automation.batch_response_captured` | Response passed all filters AND parsed cleanly | `url`, `status` |
| `ui_automation.batch_response_parse_failed` | Listener fired but `response.json()` raised | `url`, `error` |
| `ui_automation.batch_403_body` | A `batchGenerateImages` response returned HTTP 403; body prefix logged for WAF / reCAPTCHA diagnosis (subsequently raises `WafRejectionError`) | `body_prefix`, `route` |

If `prompt_submitted` is followed by NO `batch_response_seen` log within the
timeout, the network request didn't fire (or didn't return as a Playwright
`response` event). Most common cause (resolved by [PR #40](https://github.com/ffroliva/gflow-cli/pull/40)
on 2026-05-23): the editor was in Video mode and Flow fired `generateVideo`
instead — the listener filter (`batchGenerateImages` URL) never matched.
Confirm by checking for `ui_automation.image_mode_entered` in the log; if it
is missing, the mode switch did not run.  If `image_mode_entered` is present
but `batch_response_seen` is still missing, the remaining hypothesis is the
historical UI-flake noted in
[`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md#open-follow-ups)
(transient overlay swallowed the arrow-forward click).

**Two extra debug-screenshot file names worth searching for in the out dir**
when the mode switch itself fails: `debug_no_mode_trigger.png` (crop-icon
dropdown trigger not found) and `debug_no_image_tab.png` (Image tab missing
inside the open dropdown).

Pipe `--verbose` output through `Select-String` (PowerShell) or `grep` to
filter:

```powershell
$env:PYTHONUTF8 = "1"
uv run gflow --verbose image t2i "prompt" --profile <name> --out tmp\debug 2>&1 |
  Tee-Object tmp\debug\run.log |
  Select-String 'batch_response|aspect_ratio|prompt_submitted|error_unhandled'
```

## Automatic incident bundles

Since v0.43.0, relevant operational failures automatically write a **private
incident bundle** (`GFLOW_CLI_INCIDENT_CAPTURE`, default `true` — see
[CONFIGURATION § GFLOW_CLI_INCIDENT_CAPTURE](CONFIGURATION.md#gflow_cli_incident_capture)).
The human error output prints the bundle path; `--json` carries it as the
`error.incident` object; remote surfaces (MCP/HTTP/worker) receive only an
opaque `{id, capture_status}`.

### Layout

```text
<GFLOW_CLI_HOME>/incidents/<YYYY-MM-DD>/<UTC-stamp>-<correlation-fingerprint>-<rand>/
├── manifest.json             # allowlisted facts: version/env, error class + exit
│                             # code + retryable, sanitized route, phase, artifact
│                             # classifications, HAR state, suppression count
├── ui.json                   # structural DOM only: Material-Symbol ligatures,
│                             # signal booleans, tag counts, viewport/scroll,
│                             # bounded overlay geometry — never raw text/HTML
├── network.json              # last ≤100 responses/failures: method, host
│                             # category, canonical route, status, duration
├── browser.json              # last ≤100 console warn/error + ≤50 page errors —
│                             # class/category + length only, never message text
├── sensitive/screenshot.png  # UI-state failures only — REVIEW BEFORE SHARING
└── .pending                  # present only while a capture is still staging
```

`manifest.json` is written last and marks the bundle complete; a directory
with only `.pending` is a crash leftover and is pruned automatically after
24 h. Retention keeps at most 50 complete bundles / 250 MiB (oldest pruned
at command startup).

### What triggers a capture (and what never does)

Captured: `FlowAppError` (31), `FlowAgentUiError` (25),
`UiModeUnavailableError` (28), `UiSelectorDriftError` (23),
`TransportTimeoutError` (9), `BrowserSessionClosedError` (15),
`WireFormatError` (7), `WafRejectionError` (10), `NetworkError` (6),
unexpected exceptions while a page is alive, and `ProfileLockedError` (11)
as a **metadata-only** bundle (no Chrome is launched; the local human output
also shows the recorded lock owner's PID/start-time evidence — advisory
only, the kernel lock stays authoritative and nothing is ever reclaimed).

Never captured: expected `ContentPolicyError`, ordinary `AuthExpiredError`,
usage/config validation, cancellation (Ctrl-C). Successful commands write
nothing. At most 3 bundles per command; repeats of the same failure
fingerprint increment `suppressed_count` in the manifest instead.

Capture is **observation-only** (it never retries, navigates, or submits)
and best-effort: the original error, exit code, and traceback always win,
and a capture problem surfaces only as an `incident.capture_failed` log
event.

### Escalation path

The automatic bundle answers "what state was Flow in?". When you need raw
payloads (wire-format surprises, WAF forensics), escalate explicitly to
[`GFLOW_CLI_HAR_PATH`](CONFIGURATION.md#gflow_cli_har_path) — the manifest's
`har_state` field records honestly whether that session's HAR demonstrably
finalized (`disabled` / `pending_flush` / `complete` / `possibly_incomplete`).
Raw HAR is never enabled or copied automatically.

## Inspecting Flow's live UI

When a selector breaks, the cheapest path is a DOM snapshot of the page
state at the moment of failure.

### 1. Browser DevTools (manual, free)
Open Flow in real Chrome → DevTools → Elements. Click the gen-settings
panel button (crop icon), then inspect the aspect-ratio tabs. Capture the
`role`, `aria-label`, and exact text content of each tab. Copy that into
the appropriate `_*_CANDIDATES` constant.

### 2. Chrome DevTools MCP (`mcp__chrome-devtools__*`)
Available in this session. Lets an agent drive a Chromium instance
programmatically — click, evaluate JS, take snapshots, list network
requests, run Lighthouse, etc. Useful when you want a snapshot saved
without manual copying. Caveats verified during the v0.7.0 work:
- Launches its own browser; it does NOT share your local `--profile` /
  Chrome user data. Authentication has to happen inside the MCP browser.
- `https://labs.google/fx/tools/flow` returns "quota exceeded, error code
  253" to unauthenticated probes (Flow rate-limits anonymous calls), then
  redirects to Google OAuth. Public marketing pages like `labs.google/fx`
  work fine.
- The accessibility tree (`take_snapshot`) is more reliable than the raw
  DOM for asserting tab labels.
- For authenticated UI inspection, prefer (4) [Playwright trace viewer]
  or ad-hoc DOM-dump instrumentation inside `UiAutomationTransport` —
  both reuse our existing logged-in profile without a parallel sign-in.

### 3. Firecrawl (`firecrawl-scrape` / `firecrawl-interact`)
Best for unauthenticated docs (e.g., Material Symbols icon names, third-
party API references). Not ideal for Flow itself — Flow requires a
logged-in Pro/Ultra session that Firecrawl's hosted browser doesn't
share with your local profile.

### 4. Playwright trace viewer
For deep diagnosis of a single failed run, switch the transport to record
a Playwright trace, then open `playwright show-trace path/to.zip`. This is
not currently wired into `UiAutomationTransport`; add it ad-hoc when
diagnosing a specific failure and remove after.

## Selector cascades

Long-lived UI selectors should be a CASCADE of likely labels, not a
single match. Pattern:

```python
_ASPECT_TAB_CANDIDATES: dict[str, tuple[str, ...]] = {
    "1:1": ("1:1", "Square", "1×1", "1x1"),
    ...
}
```

For each CLI value, try each candidate with `:text-is(label)` (exact
match — avoids substring collisions like the v0.7.0 `1:1` regression).
First visible-and-clickable wins. Log:
- `matched_label=` when one wins (so you can see WHICH variant Flow is
  currently rendering — useful when triaging future drift).
- `candidates_tried=` on total failure (so the next debug pass knows
  what was attempted).

## Lifecycle & browser state

| Error class | Exit code | What it means | Recover by |
|---|---|---|---|
| `BrowserSessionClosedError` | 15 | Playwright page/context/browser was closed mid-call (translated from `TargetClosedError`) | Recreate `FlowApiClient` via `async with` |
| `AuthExpiredError` | 3 | Session cookies no longer valid | `gflow auth login --profile <name>` |
| `AuthBrowserRejectedError` | 14 | Google rejected Playwright's bundled Chromium | Re-login with `--browser chrome` |
| `AuthLoginTimeoutError` | 12 | User did not finish the OAuth flow in time | Run `gflow auth login` again; raise `GFLOW_CLI_AUTH_LOGIN_TIMEOUT` |
| `TransportTimeoutError` | 9 | A single API call exceeded its timeout | Retry; check Flow status |
| `WafRejectionError` | 10 | reCAPTCHA / WAF blocked the request | Wait + retry; verify session is healthy |

For health probes from a worker process, use
`FlowApiClient.health_check()` — it returns `bool` and never raises.

## Reproducing image gen end-to-end

See [`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md) for the
canonical example. Short form:

```powershell
$env:PYTHONUTF8 = "1"
mkdir -p tmp\debug

uv run gflow --verbose image t2i "<prompt>" `
  --profile <your-profile-name> `
  --count 1 `
  --aspect <9:16|16:9|1:1|4:3|3:4> `
  --out tmp\debug
```

## Test suite memory

The full smoke set (`-m "not e2e and not live"`) with `--cov=gflow_cli`
runs cleanly in a plain shell (~90 s on a developer laptop, ~700 tests).
Inside the **context-mode MCP sandbox**, coverage instrumentation crosses
the sandbox's memory ceiling and the MCP sidecar exits with
`Connection closed`. Workarounds when running inside MCP:
- Run dirs separately: `tests/api` → `tests/auth tests/cli` →
  `tests/features` → `tests --ignore=...` (everything else).
- Or drop `--cov` while iterating; re-enable for the final pass in a
  plain shell.

## Windows console encoding

PowerShell defaults to cp1252. Rich/structlog output that includes the
`●` glyph (used in `gflow auth` profile tables) will crash with
`UnicodeEncodeError`. Always export UTF-8 before `gflow` calls:

```powershell
$env:PYTHONUTF8 = "1"
```

Bash users on Git Bash / WSL inherit a UTF-8 locale and don't need this.

## WAF cadence

Flow's WAF reacts to **cumulative submission cadence**, not individual
requests. Field data (issue [#241](https://github.com/ffroliva/gflow-cli/issues/241),
2026-07-05, real account):

- 3-prompt multi-prompt runs spread 30–60 min apart all passed.
- A burst of ~14 image submissions in ~10 min ended in a **403**
  (`Flow returned 403; blocked by WAF or fingerprint check`,
  `PUBLIC_ERROR_UNUSUAL_ACTIVITY` — see
  [KNOWN_ISSUES § batchGenerateImages HTTP 403](../KNOWN_ISSUES.md)).
- After ~30 min of cooldown, individually-paced calls (45–120 s apart) on the
  same profile and project passed 4/4.

Practical guidance:

- **Pace multi-prompt runs — escalate only when needed.** All multi-prompt
  image paths (`image batch`, `image t2i --prompts-file` / `--stdin` /
  multiple prompts, `gflow run` image batches) apply a small random
  0.5–1.5 s pause between submissions by default — enough to break a
  perfectly uniform burst signature without wasting wall-clock. If runs
  start returning 403s, widen with `--jitter 10-30` or
  `GFLOW_CLI_JITTER_RANGE` ([CONFIGURATION § GFLOW_CLI_JITTER_RANGE](CONFIGURATION.md#gflow_cli_jitter_range)),
  then dial back once the score decays. `--jitter 0` disables pacing.
- **Cool down after a 403.** Wait 30–60 min, then probe with a single small
  generation before batching again.
- **Reuse a project.** Repeated project churn (`project.createProject` calls
  between generations) adds to the bot-like signature. For repeated
  single-prompt runs (`t2i`, `i2i`), pass `--project` to reuse a standing
  project. Multi-prompt runs already create only **one** shared project per
  run (never one per prompt).

## Common failure modes

See [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md) for the canonical list and
remediations. The most-hit categories:
- **Flow UI drift** → selector cascade needs a new candidate
  ([selector-cascades](#selector-cascades))
- **Session expiry on a long-running worker** → use `health_check()`
  + recreate on `BrowserSessionClosedError`
- **reCAPTCHA score too low** → must use real Chrome
  (`--browser chrome`); never Playwright's bundled Chromium

## See also

- [`KNOWN_ISSUES.md`](../KNOWN_ISSUES.md) — canonical bug list
- [`AUTHENTICATION.md`](AUTHENTICATION.md) — sign-in flow details
- [`DEVELOPMENT.md`](DEVELOPMENT.md) — branching, PRs, quality gates
- [`USAGE.md`](USAGE.md) — CLI surface
- [`LIVE_VERIFICATION_v0.7.0.md`](LIVE_VERIFICATION_v0.7.0.md) —
  per-release end-to-end evidence
- `/gflow:known-issues` — surface open known issues from the CLI
- `/gflow:check` — local quality gate (lint, format, types, tests)
