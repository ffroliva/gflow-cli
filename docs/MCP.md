# Model Context Protocol (MCP) Server for gflow-cli

This document describes the design, configuration, security model, and developer setup for the `gflow-cli` MCP server.

---

## 1. Architecture

The `gflow-cli` MCP server acts as a type-safe JSON-RPC interface over standard input/output (`stdio`), allowing AI desktop agents (like Claude Desktop, Cursor, and VS Code) to natively trigger image and video generation through the user's Google account.

```
┌────────────────────────────────────────────────────────────┐
│              Client AI Agent (Claude / Cursor)             │
└─────────────┬───────────────────▲──────────────────────────┘
              │ JSON-RPC          │ JSON-RPC
              │ (stdin)           │ (stdout)
┌─────────────▼───────────────────┴──────────────────────────┐
│             gflow mcp run (FastMCP Subprocess)            │
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
* `gflow_generate_image(prompt, model, aspect, count, seed)`: Triggers text-to-image (Imagen 3.5 / Nano Banana).
* `gflow_generate_video(prompt, initial_frame_path, aspect, seed)`: Triggers vertical or landscape video generation (Veo 3.1).
* `gflow_list_projects(limit, offset)`: Queries SQLite catalog for recent generation folders.
* `gflow_list_characters(project_id)`: Lists saved project-scoped characters.

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
Add this configuration block under the `mcpServers` key:
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
3. Configure:
   * **Name:** `gflow-cli`
   * **Type:** `command`
   * **Command:** `uv --directory C:/development/github/gflow-cli run gflow mcp run`

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
