# Predict: Model Context Protocol (MCP) Server

## Verdict: CAUTION
**Confidence:** 8.2/10

## Summary
The LLM Council evaluates the proposed MCP Server. While the Architect and UX Critics agree that structured JSON-RPC represents a major usability upgrade over raw terminal logs and escape codes, the Devil's Advocate and Security Critics flag significant concerns: Windows-specific pipe fragility (encoding issues), the risk of malicious prompts causing credit-burning loops, and the hallucination risk if agents are fed terminal-focused skill documentation.

The verdict is **CAUTION**. Mitigations have been updated in the plan to address these failure modes, specifically focusing on workflow-friendly token-bucket rate-limiting (allowing burst filmmaking tasks) and dynamic Click-to-MCP option symmetry checks in CI.

---

## Persona findings

### Architect — GO (9/10)
- Exposing tools via MCP introduces a new primary adapter (`src/gflow_cli/mcp/`) that sits side-by-side with the Click CLI (`cli.py`). Both drive the core application domain (`FlowApiClient` and `data/repository.py`).
- SQLite data operations read directly from the repository layer without launching a browser.
- **Context Locking:** Playwright persistent contexts lock the Chromium profile directory. 
  - *Mitigation:* The server must serialize requests via an internal `asyncio.Lock` AND use a file-based lock on the profile context directory to prevent concurrent CLI and MCP runs from colliding.
- **Schema Symmetry:** Reject dynamic runtime Click AST introspection due to coupling and startup latency. Enforce CLI-to-MCP parameter symmetry via automated CI tests.

### Security / reCAPTCHA — CAUTION (7/10)
- **Trust Boundary:** The MCP server inherits the host user's local execution permissions. It grants the client LLM agent access to drive the user's active Google account.
- **Prompt Injection & Credit Burning:** Malicious prompts could trick the client agent into running expensive generation loops.
  - *Mitigation:* Implement a local token-bucket rate limiter (capacity of 8 tokens to allow burst filmmaking, refill rate of 1 token every 20 seconds). Query SQLite to enforce cumulative `GFLOW_CLI_SESSION_CREDIT_LIMIT` and `GFLOW_CLI_DAILY_BUDGET` limits.
- **Hanging / Timeout:** Prevent interactive auth calls on stdin by checking cookie validity *before* spawning the browser, returning a standard text response when unauthenticated.

### Performance / Playwright — CAUTION (8/10)
- Exposing SQLite catalog reads via tools (`gflow_list_projects`, `gflow_list_characters`) yields fast results (<50ms) by bypassing Playwright entirely.
- Browser-based generations run headless and are serialized to prevent queue pollution.

### CLI UX & Agentic — CAUTION (8/10)
- Exposing the terminal-targeted `skills/gflow-cli/SKILL.md` directly via `gflow://docs/skill` introduces a **hallucination risk** where agents may try to write and run shell scripts instead of calling native MCP tools.
  - *Mitigation:* Expose a dedicated, agent-targeted `gflow://docs/mcp-guide` resource that instructs the agent to prefer native JSON-RPC tools and prompts.
- Click-to-MCP Option Symmetry: Click CLI changes must be automatically mirrored in the MCP tool parameters to treat the MCP server as a first-class citizen. This is validated by CI checks to prevent drift.

### Devil's Advocate — CAUTION (7/10)
- **Stdio Pipe Fragility:** Stdio-based JSON-RPC on Windows is fragile to encoding conflicts (e.g. non-ASCII prompts causing pipe crashes on `cp1252`).
  - *Mitigation:* Explicitly reconfigure stdout/stdin to use `utf-8` encoding during server startup.
- **Stdout Pollution:** Any debug prints from core libraries will corrupt the JSON-RPC stream. Redirecting all stdout to stderr is mandatory and must be carefully tested.

---

## High-confidence risks (flagged by 2+ personas)
1. **Stdout pollution:** Corrupts the MCP stream. Mitigated by global stdout-to-stderr captures.
2. **Windows pipe encoding crashes:** Mitigated by forcing `utf-8` encoding on stdio.
3. **Credit depletion via prompt injection:** Mitigated by a token-bucket rate limiter and SQLite budget checks.
4. **Parameter drift between CLI & MCP:** Mitigated by automated CI checks asserting options symmetry.

---

## Conflicts resolved
* *Filmmaking Throttling vs Credit Security:* A sliding-window rate limit of 3/minute is too restrictive for filmmaking (e.g. storyboards/chains) and causes agent timeouts. We resolved this by implementing a token-bucket (capacity 8, refill 1/20s) combined with cumulative session/daily SQLite credit limits.
* *CLI vs MCP duplication:* Resolved by using shared Pydantic request models for core API schemas while keeping adapter layers decoupled, verified via a programmatic CI symmetry test.

---

## Required mitigations before EXECUTE
1. Global stdout capture and redirection to `stderr`.
2. Windows stdio encoding reconfiguration to `utf-8` on startup.
3. Local token-bucket rate-limiting (capacity 8, refill 1/20s) and session/daily SQLite credit checks.
4. Dedicated agentic guide resource `gflow://docs/mcp-guide` to replace raw `SKILL.md` exposure.
5. Internal `asyncio.Lock` + file-based profile lock.
6. Click-to-MCP parameter symmetry tests in `tests/mcp/test_server.py`.

---

## Recommended next step
Proceed to `/gflow:scenario` and update the test plan with assertions for stdio encoding, token-bucket limits, and lock concurrency.
