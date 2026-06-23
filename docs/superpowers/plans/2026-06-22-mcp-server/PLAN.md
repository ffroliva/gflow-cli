# Model Context Protocol (MCP) Server Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature mcp-server` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** Expose core `gflow-cli` tools (T2I, T2V, list projects, list characters) via a Python Model Context Protocol (MCP) server, allowing AI desktop clients and IDE agents to natively drive image and video generation through the user's account.

**Architecture:**
* Build a python-native MCP server module in `src/gflow_cli/mcp/` using the official `mcp` SDK.
* Expose tool schemas using strict validation.
* Integrate the server startup and registration workflows under a new CLI command group `gflow mcp`.
* Enforce absolute `stdout` isolation: redirect all internal logs, diagnostics, and prints to `stderr` to prevent JSON-RPC transport corruption.

**Predict verdict:** CAUTION — confidence 7.8/10

**Risk Register:**
| Severity | Risk | Mitigation |
|---|---|---|
| Critical | Stdout pollution from logs/prints crashes the JSON-RPC channel | Wrap the server event loop and globally redirect `sys.stdout` to `sys.stderr` for any sub-call print writes. |
| High | Unauthenticated tool requests hang on background browser launch | Check session cookies locally *before* spawning Playwright; fail fast if authentication is missing. |
| High | Windows pipe encoding crashes on non-ASCII prompt strings | Explicitly reconfigure stdout/stdin to use utf-8 encoding on server startup. |
| High | Malicious prompt injection burns credits in loops | Implement local sliding-window rate-limiting (max 3 runs/minute) inside the tool wrappers. |
| Medium | Parallel tool execution conflicts on single-profile contexts | Implement a profile-level lock to queue concurrent request threads sequentially. |
| Medium | Agent hallucinations leading to CLI scripting instead of tool calls | Expose a dedicated, agent-targeted `gflow://docs/mcp-guide` resource instead of the raw `SKILL.md`. |

---

## File structure

### New files
```
src/gflow_cli/mcp/server.py
  Core server loop, JSON-RPC transport wrapper, and stdout redirection layer.
src/gflow_cli/mcp/tools.py
  Exposed tool schemas and execution routers binding to FlowApiClient.
src/gflow_cli/mcp/prompts.py
  Exposed MCP prompts (templates for prompt expansion and characters).
src/gflow_cli/mcp/resources.py
  Exposed MCP resources (serving SKILL.md, schema details, and known-issues).
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
- [ ] Explicitly reconfigure stdout/stdin to use utf-8 encoding during server initialization to prevent Windows pipe encoding crashes.
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
- [ ] Implement a sliding-window rate limit (max 3 generations per minute) inside the generation tool wrappers to prevent automated credit-burning loops.
- [ ] Add an internal `asyncio.Lock` queue to serialize concurrent Playwright execution requests on the single Chromium profile context.
- [ ] Ensure generation tools verify authentication cookies *before* starting Playwright to prevent hanging processes.
- [ ] Convert resulting local asset filepaths into absolute `file://` URIs.
- [ ] Run `pytest tests/mcp/test_server.py` until checks pass green.

---

## Task 4 — Expose MCP Prompts & Resources

**What:** Expose useful prompt templates (creative director prompt formula) and static/dynamic documentation resources (like local gflow skills or schema information) via the MCP standard.

**Files:**
- `src/gflow_cli/mcp/prompts.py`
- `src/gflow_cli/mcp/resources.py`

**Steps:**
- [ ] Create `src/gflow_cli/mcp/prompts.py` exposing `@mcp.prompt` mappings:
  - `expand_prompt`: Prompt template helper for 5-component Google prompt formula.
  - `create_character`: Prompt template helper for consistent character profile descriptions.
- [ ] Create `src/gflow_cli/mcp/resources.py` exposing `@mcp.resource` mappings:
  - `gflow://docs/mcp-guide`: Serves a dedicated, agentic guide instructing the LLM to use registered tools instead of terminal shell wrappers.
  - `gflow://docs/known-issues`: Serves `KNOWN_ISSUES.md` warnings.
  - `gflow://db/schema`: Serves the SQLite database schema query details.
- [ ] Wire both prompts and resources modules into the server boot file `src/gflow_cli/mcp/server.py`.
- [ ] Write tests in `tests/mcp/test_server.py` verifying that prompts and resources list queries return correct schemas.

---

## Task 5 — Add Click `gflow mcp` Subcommands

**What:** Expose Click command targets to run and register the server.

**Files:**
- `src/gflow_cli/cli.py`

**Steps:**
- [ ] Define Click group `mcp` in `cli.py`.
- [ ] Add command `gflow mcp run` to start the server process.
- [ ] Add command `gflow mcp setup` (similar to `setup_mcp.py` in `banana-claude`) to auto-append the server configuration block to `~/.claude/settings.json` or `claude_desktop_config.json`.

---

## Task 6 — Documentation & Validation

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
- [ ] User can add the MCP configuration to their IDE agent and invoke `gflow` tools, prompts, and resources automatically.
