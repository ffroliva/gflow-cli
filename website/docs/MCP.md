# Model Context Protocol (MCP) Server for gflow-cli

This document describes the design, configuration, security model, and developer setup for the `gflow-cli` MCP server.

---

## 1. Architecture

The `gflow-cli` MCP server acts as a type-safe JSON-RPC interface, supporting two transport mechanisms:
1. **stdio Subprocess Transport (`gflow mcp run`):** Runs over standard input/output (`stdio`), ideal for direct integration with local desktop agents like Claude Desktop, Cursor, or VS Code.
2. **Streamable HTTP Transport (`gflow serve`):** Runs as a background web daemon over HTTP at `/mcp`, ideal for decoupled web UI dashboards, concurrent scripts, or external clients. The legacy HTTP+SSE transport remains available for one deprecation cycle via `gflow serve --transport sse`.

### Protocol versions

The server is built on the `mcp>=2` Python SDK and negotiates the protocol
**per connection**, serving both eras from one binary:

| Era | Versions | Notes |
| :--- | :--- | :--- |
| Handshake (legacy) | `2024-11-05`, `2025-03-26`, `2025-06-18`, `2025-11-25` | `initialize`/`initialized` exchange, `Mcp-Session-Id` |
| Modern (stateless) | `2026-07-28` | No handshake, no session id; protocol version and client capabilities ride in `_meta` on every request |

We write no protocol code for this — the SDK's low-level `Server.run` drives
`serve_dual_era_loop`, and the client's first request decides the era.

The 2026-07-28 stateless core costs us nothing structurally: the server holds no
per-connection state. Cross-call continuity lives in SQLite (`gflow.db`) and the
Chromium profile directory, keyed by the `profile` argument that every tool
already takes — which is exactly the "server-minted handle passed as an ordinary
tool argument" pattern the spec now prescribes. `ProfileLease` is a cross-process
file lock, not a session, so it is unaffected. We use none of the features the
spec deprecated (Roots, Sampling, Logging).

**Dependency bound.** `pyproject.toml` pins `mcp>=2.0.0,<3`. The upper bound is
mandatory, not cosmetic: MCP SDK majors carry breaking protocol-era changes
(2.0.0 deleted `mcp.server.fastmcp` outright, which an unbounded `mcp>=1.0.0`
happily resolved into — breaking every fresh install of this surface while CI
stayed green on the lockfile). The `resolve-drift` CI job installs from the
declared ranges *without* the lockfile and smoke-imports this surface, so the
next such break fails CI instead of reaching users.

**Response caching.** 2026-07-28 added `ttlMs`/`cacheScope` to list results. Our
listing surfaces are decided at import time by decorators, so `tools/list`,
`prompts/list`, and `resources/list` advertise a one-hour TTL. `resources/read`
gets five minutes because it is not static — the known-issues resource reads
`KNOWN_ISSUES.md` off disk. `cacheScope` is `private` throughout: gflow is a
local, single-user daemon driving one user's authenticated browser profile, so
`public` would authorize shared caching of user-scoped responses.

```
┌────────────────────────────────────────────────────────────┐
│      Client AI Agent (Claude / Cursor / Web Dashboard)     │
└─────────────┬───────────────────▲──────────────────────────┘
              │ JSON-RPC          │ JSON-RPC
              │ (stdio / HTTP)    │ (stdout / SSE)
┌─────────────▼───────────────────┴──────────────────────────┐
│        MCP Server Adapter (MCPServer / FastAPI app)         │
│  Exposes: Tools, Prompts, Resources                        │
└─────────────┬──────────────────────────────────────────────┘
              │ internal calls
              ▼
┌────────────────────────────────────────────────────────────┐
│                       gflow-cli Core                       │
│  - FlowApiClient (Playwright / REST requests)               │
│  - SQLite operations catalog (DataStore)                   │
├──────────────────────────────┬─────────────────────────────┤
│   Chromium Profile Lock      │      Direct SQLite Read     │
│   (asyncio & file-based)     │      (Fast read paths)      │
└─────────────┬────────────────┴──────────────┬──────────────┘
              │ writes cookies                │ queries history
              ▼                               ▼
     [profile_<name>/]                  [gflow.db]
```

### Adapters side-by-side
Both Click CLI (`src/gflow_cli/cli.py`) and MCP Server (`src/gflow_cli/mcp/`) are thin adapter layers that drive the core application services (`FlowApiClient` and `repository.py`), ensuring zero duplication of business logic.

---

## 2. Tools, Prompts, and Resources

The server registers three protocol surfaces:

### Tools (Executable actions)
* `gflow_generate_image(prompt, model, aspect, count, seed, reference_images, tools, profile, project, project_name, instructions, ui_mode, output, wait)`: Triggers text-to-image / image-to-image (Imagen / Nano Banana). `instructions` is an optional list of ephemeral agent-instruction strings (agentic cohort only). `reference_images` switches to i2i and accepts **either a local file path or a generated image's Flow media UUID**. A UUID reference is attached by **selecting the already-existing asset in Flow's reference picker — no duplicate copy is uploaded** (locating the tile by the media id in its thumbnail URL, and searching the recorded display name to surface it when needed); gflow falls back to uploading the asset's on-disk local file only when it can't be located in place (e.g. it lives in a different project's picker). `project` generates into an existing Flow project id (mirrors CLI `--project`) — pass the reference's project to keep it selectable in place. `ui_mode` selects the Flow UI arm (`auto`/`classic`/`agentic`, mirroring CLI `--ui-mode`, matched case-insensitively). Since [#595](https://github.com/ffroliva/gflow-cli/issues/595) `auto` means "no arm was asked for" and **resolves to `classic`** — the arm that can satisfy an image request — so an account in Flow's agentic cohort aborts pre-submit (exit-28 equivalent envelope, zero credits) instead of failing mid-run with selector drift or video bytes; the agentic arm is bound only when named. Passing `instructions` forces `agentic` automatically, so `ui_mode="classic"` + `instructions` is a hard conflict rather than a silent drop. An unknown value returns a 400 problem-details envelope. See [CONFIGURATION § GFLOW_CLI_UI_MODE](CONFIGURATION.md#gflow_cli_ui_mode).
* `gflow_generate_video(prompt, mode, aspect, initial_frame, end_frame, reference_images, model, duration, count, tools, profile, project, project_name, ui_mode, output, wait)`: Triggers vertical or landscape video generation (Veo). `mode` is `t2v`/`i2v`/`r2v`; `model` (`veo_lite`/`veo_fast`/`veo_quality`/`omni_flash`, aliases accepted), `duration` (seconds — 4/6/8 for Veo 3.1 and 4/6/8/10 for omni_flash; 10 is omni_flash-only). **Whether Flow renders a duration control at all is account/cohort-dependent**: on an account that renders none, the job is accepted here and fails in the worker pre-submit (exit 23 equivalent on the labs driver, exit 11 equivalent on the migrated `flow.google.com` host — now the default t2v route, #650 — no credits spent either way) rather than being rejected up front — see [KNOWN_ISSUES](https://github.com/ffroliva/gflow-cli/blob/main/KNOWN_ISSUES.md) (#451/#288/#630), and `count` mirror the CLI `gflow video` flags — an omitted `model` lets the transport apply its i2v veo-lite default (issue #125), and every model — `omni_flash` included — accepts i2v with a start frame and with an end frame (wire-verified 2026-09-02, issue #626); `i2v` requires `initial_frame`, `r2v` requires `reference_images`; `project` generates into an existing Flow project id (mirrors CLI `--project`); on an account Google has moved to `flow.google.com` (`GFLOW_CLI_FLOW_HOST`, read from the server/daemon environment, not per call — see [CONFIGURATION § GFLOW_CLI_FLOW_HOST](CONFIGURATION.md#gflow_cli_flow_host)) `project` is **required** and omitting it returns the exit-11-equivalent envelope; there the ported modes are text-to-video and image-to-video with a **local** `initial_frame` and no `end_frame` (the file is uploaded through the editor and bound on the Start chip by file name — it stays in the Flow project like any upload); a Flow media UUID as `initial_frame`, an `end_frame` and `r2v` return the exit-36-equivalent envelope. `initial_frame`, `end_frame`, and `reference_images` each accept **either a local file path or the Flow image UUID of a generated asset** — pass a generated image's id straight in to chain image→video, and gflow attaches it for you. Since v0.58.0 (#529) the CLI and MCP surfaces are unified for i2v frames: the UUID keeps its identity and is enriched with the catalog's recorded display name plus an integrity-verified local fallback, so the transport prefers **selecting the exact asset in the project's media picker** (no duplicate upload) and re-uploads the recorded local file only when the tile is unreachable — and only if its byte count/SHA-256 still match. A UUID that isn't in your local asset catalog is rejected up front with a clear "Reference Not Found" error; a catalogued asset with neither a display name nor a verified local file gives a "Reference Not Usable" error (re-generate it or pass a local path). `r2v` UUID refs are resolved to the recorded local file for upload. `ui_mode` selects the Flow UI arm (#299 PR-A, mirroring CLI `--ui-mode`) and **applies to every mode of this tool, including `r2v`** — unlike the CLI, where `video r2v`/`chain` have no flag and follow the env-only path. Video generation has **only a classic driver**, so `auto` ≡ `classic`: both verify the classic editor pre-submit and abort before spending credits if it is unreachable. `ui_mode="agentic"` is rejected with a 400 problem-details envelope, because no agentic video driver exists yet. Values are matched case-insensitively. (MCP tools return envelopes, never process exit codes — the CLI equivalents of these aborts are exit 28 and exit 2 respectively.) See [CONFIGURATION § GFLOW_CLI_UI_MODE](CONFIGURATION.md#gflow_cli_ui_mode).
* `gflow_auth_status(profile)`: Credit-free, non-interactive Flow session probe (#497) — wraps the same fail-closed `verify_flow_profile` check as `gflow auth status`. Returns `{"status": "authenticated", "profile", "user_email"}` or a problem-details error with a `remediation_hint`; a `verification_error` outcome means a network/endpoint problem, which re-login does not fix. Call it before a generation tool to fail fast on dead auth (the queue is async — without it, an auth failure surfaces only later from the daemon). Login/logout remain CLI-only (genuinely interactive).
* `gflow_list_projects(profile, limit, offset)`: Queries the SQLite catalog for recent generation folders, paginated — the response carries `count` (rows in this page), `offset`, `has_more`, and `next_offset` (pass it back as `offset` for the next page; `null` on the last page).
* `gflow_list_tools()`: Lists the prompt tools (name/title/description/category) accepted by the generate tools' `tools` param.
* `gflow_instructions_list(project, profile)`: Lists a project's persistent Agent-Mode instruction cards (live server brief; credits-free).
* `gflow_instructions_add(project, title, text, refs, enabled, profile)`: Adds a persistent instruction card. Each ref is classified automatically — local image path → uploaded image reference, asset UUID → image reference, anything else → character id/name (mirrors CLI `instructions add --ref`).
* `gflow_instructions_set_enabled(project, enabled, title|card_id, profile)`: Enables/disables one card selected by title or stable card id (covers CLI `instructions enable`/`disable`).
* `gflow_instructions_rm(project, title|card_id, profile)`: Removes one card from the brief.
* `gflow_instructions_toggle_mode(project, enabled, profile)`: Flips the brief-level master switch; cards are left untouched.
* `gflow_instructions_apply(project, cards, profile)`: Declarative **full-sync** — REPLACES all cards with the given set (destructive; same entry shape as the CLI `instructions apply` file).

CLI↔MCP parity is enforced programmatically: `tests/mcp/test_cli_parity.py` walks every CLI leaf command and fails when one has neither a mapped MCP tool nor an explicit exemption with a stated reason. Note the deliberate asymmetry: `gflow_generate_video` has **no** `instructions` param (unlike `gflow_generate_image`) because the video pipeline (`GenerateVideoRequest` / the worker) has no instructions support — agentic-video is a typed divergence, and a dead parameter would be silently dropped. Both `gflow_generate_image` and `gflow_generate_video` support an `output` parameter mirroring the CLI `-o`/`--output` flag (#414, #415), decoding explicit output destinations in the worker daemon. `gflow update` is a considered exemption, not a backlog item: the package manager would replace the venv under a live `gflow mcp run` and write its output onto the JSON-RPC stdout channel — the operator runs it and restarts the server. A read-only twin of `gflow update --check` would be harmless; it is the upgrade path if agents ever need version awareness beyond the `initialize` handshake.

### Prompts (Orchestration templates)
* `expand_prompt`: Helps the agent structure simple ideas into Google's official 5-component prompt formula (Subject + Action + Location + Composition + Style) before sending them to the generation tools.
* `create_character`: Assists agents in defining face, body, and voice parameters for consistent subject generation.

### Resources (Context feeds)
* `gflow://docs/mcp-guide`: A specialized, agent-targeted guide instructing the LLM to use the registered MCP tools (rather than running raw shell wrapper commands).
* `gflow://docs/known-issues`: Bounded index of KNOWN_ISSUES.md (titles + status, a few KB — #501); read `gflow://docs/known-issues/{slug}` for one issue's full text (capped at 16 KB). The old behavior injected the whole ~70 KB file per fetch.
* `gflow://db/schema`: Exposes SQLite schema definitions, allowing agents to understand project and media tables.

---

## 3. The Dilemma: Why we need the MCP Server (vs. Pure Skill)

We analyzed whether a terminal-driven CLI guided by a text skill (e.g., `skills/gflow-cli/SKILL.md`) is sufficient. The LLM Council concluded that the MCP server is fully justified for these reasons:

| Dimension | Direct CLI via Terminal Execution | Native MCP Server Daemon |
| :--- | :--- | :--- |
| **Output Fragility** | Ephemeral `stdout`/`stderr` log outputs are fragile to format adjustments, progress bars, and ANSI colors. | Strictly structured JSON payloads containing explicit metadata and absolute output file URIs. |
| **Process Lifecycle**| High process startup overhead (Python import latency) on every individual execution. | Warm daemon process. Retains active caching of database metadata. |
| **Concurrency** | A second concurrent process against the same profile is rejected immediately by the cross-process `ProfileLease` with `ProfileLockedError` (exit code 11) — a clean fail-fast, never a crash, never a wait. Different profiles run fully in parallel. | Same `ProfileLease` enforcement applies to the daemon's requests — a same-profile call made while another is in flight (from the CLI or the MCP daemon) is rejected immediately with `ProfileLockedError` rather than queued or crashed; different profiles still run fully in parallel. |
| **Error Handling** | Agent must scan logs for strings or parse exit codes to check status. | Strongly-typed JSON-RPC errors with mapped codes and clear remediation. |
| **Transport Safety** | Volatile console printing. | Stdout is strictly isolated for JSON-RPC; all logs and warnings route to stderr. |

### Error envelope

MCP tool failures return a structured error object built from the same RFC 9457
Problem Details as the CLI, plus a **`retryable`** boolean. `retryable: true`
marks a transient failure a scheduler can re-run without operator intervention —
WAF/reCAPTCHA bounce (`WafRejectionError`), rate-limit (`RateLimitError`),
transport timeout (`TransportTimeoutError`), network blip (`NetworkError`), a
dropped browser session (`BrowserSessionClosedError`), a Flow web-app crash
(`FlowAppError`), an agentic-cohort flap (`FlowAgentUiError`), an unreachable
UI arm (`UiModeUnavailableError`), and a partially-completed sync
(`SyncPartialError`). That list is the whole of `errors.RETRYABLE_ERRORS`.
Everything
else (auth, content-policy, configuration, security) is terminal
(`retryable: false`): retrying the identical request fails the same way. This
flag is the **same shared classification** the CLI `--json` payload and the
worker-queue error record use (`errors.is_retryable`) — the three surfaces
cannot drift. On a captured failure the envelope also carries a remote-safe
`incident` object (`{id, capture_status}` only — never a local path); see
[DEBUGGING § Automatic incident bundles](DEBUGGING.md#automatic-incident-bundles).

Every tool routes through one error funnel. **Unexpected (non-gflow) exceptions
are masked**: the client sees only the exception class name
(`"Unexpected RuntimeError; details were logged server-side."`, `status: 500`,
`retryable: false`) — raw exception text can embed filesystem paths, profile
names, or token material and never leaves the server; the full message and
traceback go to the server-side structured log (`mcp.tool.unexpected_error`).

---

## 4. Setup Instructions

### Claude Desktop Integration
Run the configuration helper command in your terminal:
```bash
gflow mcp setup
```
This merges the server entry into your Claude Desktop configuration file — existing content is preserved, and a pre-existing file is backed up as `<name>.gflow-backup` first. A corrupt config fails loud (exit 11) and is never overwritten:
* **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
* **Linux:** `~/.config/Claude/claude_desktop_config.json`

Other targets: `gflow mcp setup --target cursor` (`~/.cursor/mcp.json`) and `--target vscode` (the user-profile `mcp.json`, written with VS Code's `servers` + `"type": "stdio"` schema).

> **Existing entries are preserved:** if your config already has a `gflow` or `gflow-cli` server entry (including the local-clone `uv --directory` variant below), `gflow mcp setup` leaves it completely untouched and reports "Already configured" — it only ever adds a missing entry.

#### Manual Configuration
Depending on how you installed `gflow-cli`, add one of the following configuration blocks under the `mcpServers` key of your `claude_desktop_config.json`:

##### Option A: Global Installation (Recommended)
Use this if you installed `gflow-cli` globally (e.g. via `uv tool install gflow-cli` or `pip install gflow-cli`):
```json
{
  "mcpServers": {
    "gflow-cli": {
      "command": "gflow",
      "args": [
        "mcp",
        "run"
      ]
    }
  }
}
```

##### Option A2: Read-only server

Use this when the agent must never be able to spend credits (#496). The two
credit-spending tools (`gflow_generate_image`, `gflow_generate_video`) are then
**never registered**, so they do not appear in `tools/list` at all — invisible
rather than refused. Every read-only tool stays available.

```json
{
  "mcpServers": {
    "gflow-cli": {
      "command": "gflow",
      "args": [
        "mcp",
        "run",
        "--no-spend"
      ]
    }
  }
}
```

Setting `GFLOW_MCP_NO_SPEND=1` in the environment does the same thing and also
covers `gflow serve`. `gflow mcp setup` writes the plain (spending) block above —
add the flag or the env var yourself if you want the read-only server.

##### Option B: Local Clone (Development)
Use this if you cloned the repository locally and run it via `uv`:
```json
{
  "mcpServers": {
    "gflow-cli": {
      "command": "uv",
      "args": [
        "--directory",
        "C:/development/github/gflow-cli",
        "run",
        "gflow",
        "mcp",
        "run"
      ]
    }
  }
}
```

### Cursor Setup
1. Open Cursor Settings -> Features -> MCP.
2. Click **+ Add New MCP Server**.
3. Configure depending on your installation:
   * **Global Installation:**
     * **Name:** `gflow-cli`
     * **Type:** `command`
     * **Command:** `gflow mcp run`
   * **Local Clone (Development):**
     * **Name:** `gflow-cli`
     * **Type:** `command`
     * **Command:** `uv --directory C:/development/github/gflow-cli run gflow mcp run`

### HTTP Daemon Setup (`gflow serve`)
For decoupled clients, local web interfaces, or multi-process frontends, run the daemon as an HTTP service:
```bash
gflow serve --port 8000 --host 127.0.0.1 --profile default

# Read-only daemon — the two credit-spending tools are never registered (#496):
gflow serve --port 8000 --host 127.0.0.1 --profile default --no-spend
```
This serves the MCP server over **Streamable HTTP**, the current spec transport:
* **Endpoint:** `http://127.0.0.1:8000/mcp`

The legacy HTTP+SSE transport is still available for one deprecation cycle:
```bash
gflow serve --transport sse --port 8000   # deprecated; logs a warning
```
* **Connection endpoint (SSE stream):** `http://127.0.0.1:8000/sse`
* **Command posting endpoint:** `http://127.0.0.1:8000/messages/`

> **Deprecated:** the MCP 2026-07-28 spec reclassified HTTP+SSE as deprecated.
> Prefer the default `--transport http`. The spec's lifecycle policy guarantees a
> minimum twelve months between deprecation and removal.

`stateless_http` is deliberately **not** enabled. The stateless core exists so
servers can scale out across interchangeable instances; gflow's value is the
opposite — a warm daemon holding one live Chromium profile, serialized by
`ProfileLease`. The *protocol* is stateless either way; that flag only governs
transport bookkeeping we want to keep.

Non-loopback binds (e.g. `--host 0.0.0.0`) require `GFLOW_DAEMON_TOKEN` to be set.

> **Note:** the background `FlowWorker` queue manager and the REST `/api/v1`
> surface are built as internal foundation but are **not yet wired into**
> `gflow serve` — it currently runs the MCP/SSE server only. See the
> [CHANGELOG](../CHANGELOG.md) for the roadmap.

---

## 5. Security & Anti-Bot Mitigations

Because the MCP server runs locally, inheriting the host user's permissions and access to their authenticated browser cookies, the following security constraints are enforced:

1. **Profile Pre-flight:** Before enqueuing work, each tool resolves and validates the target profile directory (`_resolve_and_validate_profile` — existence and home-boundary checks). There is **no session-validity probe before launching Chromium**: expired cookies surface as a typed `AuthExpiredError` from the generation run itself, with the remediation `Run 'gflow auth login' in your local terminal.`
2. **Channel Isolation:** All internal `structlog` configurations are forced to write to `sys.stderr`. The standard output stream (`sys.stdout`) is globally captured and redirected to `sys.stderr` for any unexpected prints, preserving the integrity of the stdio JSON-RPC pipe.
3. **Windows Stdio Encoding:** During startup, stdio streams are explicitly reconfigured:
   ```python
   sys.stdout.reconfigure(encoding='utf-8')
   sys.stdin.reconfigure(encoding='utf-8')
   ```
   This prevents crashes caused by non-ASCII prompt strings on Windows.
4. **Local Rate-Limiting:** Enforces a token-bucket rate limiter with a capacity of 8 tokens and a refill rate of 1 token every 20 seconds (allowing burst filmmaking tasks without timeouts). This is the **only** spend brake — there is no credit-budget accounting or per-session/daily cap (#495; a registration-time `--no-spend` gate is tracked in #496).
5. **CLI-MCP Parameter Symmetry:** Two CI layers guard the surfaces against drift: `tests/mcp/test_cli_parity.py` forces an explicit MCP decision (mapped tool or stated exemption) for every CLI leaf command, and `tests/mcp/test_server.py::TestCliMcpParameterSymmetry` compares CLI Click parameters against registered tool signatures for the two generate tools. Parameter-level comparison does not yet cover the other tools.
6. **No-Spend Mode (#496):** `gflow mcp run --no-spend` (or `GFLOW_MCP_NO_SPEND=1`, which also covers `gflow serve`) never registers the credit-spending generate tools — `gflow_generate_image` and `gflow_generate_video` are absent from `tools/list` entirely, rather than present-but-refusing. Both are gated because image generation is only empirically free and no-spend is a hard guarantee. Listing, instructions, and other read-only tools remain available.
