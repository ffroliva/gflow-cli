# Model Context Protocol (MCP) Server Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature mcp-server` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Expose core `gflow-cli` tools (T2I, T2V, list projects, list characters) via a Python Model Context Protocol (MCP) server, allowing AI desktop clients and IDE agents to natively drive image and video generation through the user's account.

**Architecture:**
* Build a python-native MCP server module in `src/gflow_cli/mcp/` using the official `mcp` SDK.
* Expose tool schemas using strict validation.
* Integrate the server startup and registration workflows under a new CLI command group `gflow mcp`.
* Enforce absolute `stdout` isolation: redirect all internal logs, diagnostics, and prints to `stderr` to prevent JSON-RPC transport corruption.

**Predict verdict:** GO — confidence 8.6/10

**Risk Register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Stdout pollution from logs/prints crashes the JSON-RPC channel | Wrap the server event loop and globally redirect `sys.stdout` to `sys.stderr` for any sub-call print writes. |
| High | Unauthenticated tool requests hang on background browser launch | Check session cookies locally *before* spawning Playwright; fail fast if authentication is missing. |
| Medium | Parallel tool execution conflicts on single-profile contexts | Implement a profile-level lock to queue concurrent request threads sequentially. |

---

## File structure

### New files
```
src/gflow_cli/mcp/server.py
  Core server loop, JSON-RPC transport wrapper, and stdout redirection layer.
src/gflow_cli/mcp/tools.py
  Exposed tool schemas and execution routers binding to FlowApiClient.
tests/mcp/test_server.py
  Integration tests for JSON-RPC tool list queries and mock executions.
docs/MCP.md
  Developer and user setup guide for IDE configurations.
```

### Modified files
```
pyproject.toml
  Add python-mcp dependency.
src/gflow_cli/cli.py
  Register click group command `gflow mcp` (`mcp run` / `mcp setup`).
CHANGELOG.md
  Update unreleased section.
```

---

## Task 1 — Add Dependencies & Test Scaffold (test-first)

**What:** Add MCP dependencies and write mock integration test skeletons verifying JSON-RPC routing.

**Files:**
- `pyproject.toml`
- `tests/mcp/test_server.py`

**Steps:**
- [ ] Add `mcp>=0.1.0` to dependency lists in `pyproject.toml`.
- [ ] Run `uv sync` to update the lockfile.
- [ ] Create `tests/mcp/test_server.py` containing:
  - Test verifying that a tool query requests list matches our schema.
  - Test verifying stdout redirection intercepts raw prints.
  - Test verifying error responses catch exceptions without crashing.

**Tests created (red):**
- [ ] `test_mcp_list_tools`
- [ ] `test_mcp_stdout_redirection`
- [ ] `test_mcp_error_boundary`

---

## Task 2 — Implement MCP Server & Stdout Redirection

**What:** Write the base server loop and stdout intercept logic.

**Files:**
- `src/gflow_cli/mcp/server.py`

**Steps:**
- [ ] Create `src/gflow_cli/mcp/server.py`.
- [ ] Set up the official `mcp.server.fastmcp.FastMCP` instance or standard `mcp.server.Server`.
- [ ] Implement a context manager that redirects `sys.stdout` to `sys.stderr` while keeping stdio streams isolated for JSON-RPC communications.
- [ ] Configure `structlog` to write strictly to `stderr` under all formatting modes during server startup.

---

## Task 3 — Expose Tool Schemas & Handlers

**What:** Write execution mappings for core image and video tools.

**Files:**
- `src/gflow_cli/mcp/tools.py`

**Steps:**
- [ ] Create `src/gflow_cli/mcp/tools.py`.
- [ ] Implement `@mcp.tool` registrations for:
  - `gflow_generate_image` (T2I)
  - `gflow_generate_video` (T2V)
  - `gflow_list_projects` (reads SQLite catalog directly)
  - `gflow_list_characters` (reads SQLite catalog directly)
- [ ] Ensure generation tools verify authentication cookies *before* starting Playwright to prevent hanging processes.
- [ ] Convert resulting local asset filepaths into absolute `file://` URIs.
- [ ] Run `pytest tests/mcp/test_server.py` until checks pass green.

---

## Task 4 — Add Click `gflow mcp` Subcommands

**What:** Expose Click command targets to run and register the server.

**Files:**
- `src/gflow_cli/cli.py`

**Steps:**
- [ ] Define Click group `mcp` in `cli.py`.
- [ ] Add command `gflow mcp run` to start the server process.
- [ ] Add command `gflow mcp setup` (similar to `setup_mcp.py` in `banana-claude`) to auto-append the server configuration block to `~/.claude/settings.json` or `claude_desktop_config.json`.

---

## Task 5 — Documentation & Validation

**What:** Create user guides and verify full repository test suite compliance.

**Files:**
- `docs/MCP.md`
- `CHANGELOG.md`

**Steps:**
- [ ] Create `docs/MCP.md` documenting tool names, configurations, and step-by-step setup guides for Claude Code, Cursor, and VS Code.
- [ ] Update `CHANGELOG.md` under `[Unreleased]`.
- [ ] Run `/gflow:check` to ensure the entire suite is 100% green.

---

## Definition of done

- [ ] All task steps checked off.
- [ ] `/gflow:check` green.
- [ ] `docs/MCP.md` written and validated.
- [ ] User can add the MCP configuration to their IDE agent and invoke `gflow` commands automatically.
