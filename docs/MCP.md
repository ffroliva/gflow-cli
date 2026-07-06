# Model Context Protocol (MCP) Server for gflow-cli

This document describes the design, configuration, security model, and developer setup for the `gflow-cli` MCP server.

---

## 1. Architecture

The `gflow-cli` MCP server acts as a type-safe JSON-RPC interface, supporting two transport mechanisms:
1. **stdio Subprocess Transport (`gflow mcp run`):** Runs over standard input/output (`stdio`), ideal for direct integration with local desktop agents like Claude Desktop, Cursor, or VS Code.
2. **SSE HTTP Transport (`gflow serve`):** Runs as a background web daemon over HTTP and Server-Sent Events (SSE), ideal for decoupled web UI dashboards, concurrent scripts, or external clients.

```
┌────────────────────────────────────────────────────────────┐
│      Client AI Agent (Claude / Cursor / Web Dashboard)     │
└─────────────┬───────────────────▲──────────────────────────┘
              │ JSON-RPC          │ JSON-RPC
              │ (stdio / HTTP)    │ (stdout / SSE)
┌─────────────▼───────────────────┴──────────────────────────┐
│          MCP Server Adapter (FastMCP / FastAPI app)         │
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
* `gflow_generate_image(prompt, model, aspect, count, seed, reference_images, tools, profile, project)`: Triggers text-to-image / image-to-image (Imagen / Nano Banana). `reference_images` switches to i2i; `project` generates into an existing Flow project id (mirrors CLI `--project`).
* `gflow_generate_video(prompt, mode, aspect, initial_frame, end_frame, reference_images, tools, profile, project)`: Triggers vertical or landscape video generation (Veo). `mode` is `t2v`/`i2v`/`r2v`; `i2v` requires `initial_frame`, `r2v` requires `reference_images`; `project` generates into an existing Flow project id (mirrors CLI `--project`). `initial_frame`, `end_frame`, and `reference_images` each accept **either a local file path or the Flow image UUID of a generated asset** — pass a generated image's id straight in to chain image→video with no download/re-upload. A UUID that isn't in your local asset catalog is rejected up front with a clear "Reference Not Found" error.
* `gflow_list_projects(profile, limit)`: Queries SQLite catalog for recent generation folders.
* `gflow_list_characters(profile)`: Lists Flow Character entities (requires an active browser session).

### Prompts (Orchestration templates)
* `expand_prompt`: Helps the agent structure simple ideas into Google's official 5-component prompt formula (Subject + Action + Location + Composition + Style) before sending them to the generation tools.
* `create_character`: Assists agents in defining face, body, and voice parameters for consistent subject generation.

### Resources (Context feeds)
* `gflow://docs/mcp-guide`: A specialized, agent-targeted guide instructing the LLM to use the registered MCP tools (rather than running raw shell wrapper commands).
* `gflow://docs/known-issues`: Feeds critical reCAPTCHA, cookie expirations, and anti-bot mitigation details.
* `gflow://db/schema`: Exposes SQLite schema definitions, allowing agents to understand project and media tables.

---

## 3. The Dilemma: Why we need the MCP Server (vs. Pure Skill)

We analyzed whether a terminal-driven CLI guided by a text skill (e.g., `skills/gflow-cli/SKILL.md`) is sufficient. The LLM Council concluded that the MCP server is fully justified for these reasons:

| Dimension | Direct CLI via Terminal Execution | Native MCP Server Daemon |
| :--- | :--- | :--- |
| **Output Fragility** | Ephemeral `stdout`/`stderr` log outputs are fragile to format adjustments, progress bars, and ANSI colors. | Strictly structured JSON payloads containing explicit metadata and absolute output file URIs. |
| **Process Lifecycle**| High process startup overhead (Python import latency) on every individual execution. | Warm daemon process. Retains active caching of database metadata. |
| **Concurrency** | Independent OS runs collide and crash the Chromium profile lock. | Serialized request execution via an internal `asyncio.Lock` queue. |
| **Error Handling** | Agent must scan logs for strings or parse exit codes to check status. | Strongly-typed JSON-RPC errors with mapped codes and clear remediation. |
| **Transport Safety** | Volatile console printing. | Stdout is strictly isolated for JSON-RPC; all logs and warnings route to stderr. |

---

## 4. Setup Instructions

### Claude Desktop Integration
Run the configuration helper command in your terminal:
```bash
gflow mcp setup
```
This automatically appends the server entry to your Claude Desktop configuration file:
* **Windows:** `%APPDATA%\Castano\Claude\claude_desktop_config.json`
* **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`

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

### SSE Daemon Setup (`gflow serve`)
For decoupled clients, local web interfaces, or multi-process frontends, you can run the daemon as an HTTP/SSE service:
```bash
gflow serve --port 8000 --host 127.0.0.1 --profile default
```
This serves the MCP server over Server-Sent Events under FastMCP's standard paths:
* **Connection endpoint (SSE stream):** `http://127.0.0.1:8000/sse`
* **Command posting endpoint:** `http://127.0.0.1:8000/messages/`

Non-loopback binds (e.g. `--host 0.0.0.0`) require `GFLOW_DAEMON_TOKEN` to be set.

> **Note:** the background `FlowWorker` queue manager and the REST `/api/v1`
> surface are built as internal foundation but are **not yet wired into**
> `gflow serve` — it currently runs the MCP/SSE server only. See the
> [CHANGELOG](../CHANGELOG.md) for the roadmap.

---

## 5. Security & Anti-Bot Mitigations

Because the MCP server runs locally, inheriting the host user's permissions and access to their authenticated browser cookies, the following security constraints are enforced:

1. **Pre-flight Auth Validation:** Before launching Chromium/Playwright (which would hang on interactive stdin prompts if cookies are expired), the server checks session validity. If unauthenticated, it returns:
   `"Authentication required. Run 'gflow auth login' in your local terminal."`
2. **Channel Isolation:** All internal `structlog` configurations are forced to write to `sys.stderr`. The standard output stream (`sys.stdout`) is globally captured and redirected to `sys.stderr` for any unexpected prints, preserving the integrity of the stdio JSON-RPC pipe.
3. **Windows Stdio Encoding:** During startup, stdio streams are explicitly reconfigured:
   ```python
   sys.stdout.reconfigure(encoding='utf-8')
   sys.stdin.reconfigure(encoding='utf-8')
   ```
   This prevents crashes caused by non-ASCII prompt strings on Windows.
4. **Local Rate-Limiting:** Enforces a token-bucket rate limiter with a capacity of 8 tokens and a refill rate of 1 token every 20 seconds (allowing burst filmmaking tasks without timeouts). It also evaluates cumulative session and daily limits (`GFLOW_CLI_SESSION_CREDIT_LIMIT` and `GFLOW_CLI_DAILY_BUDGET`) against SQLite logs, failing fast to prevent credit depletion attacks.
5. **CLI-MCP Parameter Symmetry:** Automated checks in the CI test suite (`tests/mcp/test_server.py`) compare CLI Click parameters with registered MCP tool signatures, ensuring that any added options or flags in the CLI are instantly mirrored in the MCP layer to prevent schema drift.
