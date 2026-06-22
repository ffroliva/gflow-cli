# Predict: Model Context Protocol (MCP) Server

## Verdict: GO
**Confidence:** 8.6/10

## Summary
The five personas collectively agree that exposing `gflow-cli` tools via a Python-native MCP server is highly feasible and provides a clean, structured interface for IDE agents. The primary architectural risk is standard output (stdout) contamination from logger/print statements, which would corrupt the JSON-RPC stream. Mitigations include a global stdout-to-stderr redirect wrapper and strict logging configuration during server startup.

---

## Persona findings

### Architect — GO (9/10)
- Exposing tools via MCP introduces a new primary adapter (`src/gflow_cli/mcp/`) that sits side-by-side with the Click CLI (`cli.py`). Both drive the core application domain (`FlowApiClient` and `data/repository.py`).
- This layout respects the dependency rule: `interfaces (cli, mcp) -> application/infrastructure (api, data)`.
- Reusing `FlowApiClient` directly avoids duplicating business logic. No complex DDD modifications are needed yet.

### Security / reCAPTCHA — CAUTION (8/10)
- **Trust Boundary:** The MCP server executes locally with the user's permissions, running commands against their authenticated Google profile.
- **Hanging / Timeout:** If a tool is executed and session cookies are expired/missing, Playwright will hang or prompt for input, causing the host agent to time out. 
  - *Mitigation:* The server must check cookie validity via SQLite or profile storage *before* starting Playwright, returning a clean "Authentication required" text response.
- **Secret Leaking:** Ensure reCAPTCHA tokens or auth headers are never logged to `stderr` or returned in error payloads. `show_locals=False` must remain active on structlog settings.

### Performance / Playwright — CAUTION (8/10)
- **Page Pool Concurrency:** Desktop agents can issue parallel tool calls. If two tool calls execute concurrently on the same Google Chrome profile, Playwright persistent context will crash due to profile locking.
  - *Mitigation:* Implement a serialization lock on the server execution wrapper to queue tool invocations sequentially.
- **Direct DB Reads:** For read-only queries (`list_projects`, `list_characters`), the server should bypass Playwright entirely and read directly from the SQLite catalog via `DataStore`/`repository.py` (<50ms execution).

### CLI UX / Cross-platform — GO (8/10)
- Introduce Click command `gflow mcp run` to boot the server and `gflow mcp setup` to automatically register the server block in the Claude Desktop configuration (`%APPDATA%/Castano/Claude/claude_desktop_config.json` or `~/.claude/settings.json`).
- Standardize exit codes: exit cleanly if stdio disconnects.
- Windows path handling: ensure file paths returned by tools are converted to absolute `file://` URIs.

### Devil's Advocate — GO (9/10)
- **Why not just let the LLM use CLI commands?** While LLMs can run `gflow image t2i`, parsing terminal stdout/stderr is fragile due to line wraps, escape codes, and structural logging formats. Native MCP returns structured JSON, handles type validation, and provides explicit error responses.
- The timing is perfect: we have a stable SQLite data layer and a robust `UiAutomationTransport` ready to be driven.

---

## High-confidence risks (flagged by 2+ personas)
1. **Stdout pollution:** Any package, third-party library, or print statement writing to `stdout` corrupts the MCP JSON-RPC stream, crashing the server.
   - *Mitigation:* Force all internal logging (including `structlog` and external libraries) to `stderr`. Wrap the server runner in a context manager that redirects standard `sys.stdout` to `sys.stderr` except for the actual JSON-RPC transport stream.

---

## Conflicts resolved
- *CLI vs MCP execution:* Devil's Advocate questioned if configuring a separate server adds bloat. Resolution: Stdio MCP is lightweight, requires only a few files under `src/gflow_cli/mcp/` and one extra dependency (`mcp`), keeping the CLI lean while unlocking native agent capabilities.

---

## Required mitigations before EXECUTE
1. Global stdout redirection inside `src/gflow_cli/mcp/server.py`.
2. Session cookie pre-flight check before checking out a Playwright page.
3. Queue lock to serialize concurrent tool executions on a single profile.

---

## Recommended next step
Proceed to `/gflow:scenario` and outline the BDD / integration tests verifying stderr redirection and error boundaries.
