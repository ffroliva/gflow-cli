# Predict: Model Context Protocol (MCP) Server

## Verdict: CAUTION
**Confidence:** 7.8/10

## Summary
The LLM Council evaluates the proposed MCP Server. While the Architect and UX Critics agree that structured JSON-RPC represents a major usability upgrade over raw terminal logs and escape codes, the Devil's Advocate and Security Critics flag significant concerns: Windows-specific pipe fragility (encoding issues), the risk of malicious prompts causing credit-burning loops, and the hallucination risk if agents are fed terminal-focused skill documentation.

The verdict is downgraded from **GO to CAUTION**. Mitigations have been added to the plan to address these critical failure modes.

---

## Persona findings

### Architect — GO (9/10)
- Exposing tools via MCP introduces a new primary adapter (`src/gflow_cli/mcp/`) that sits side-by-side with the Click CLI (`cli.py`). Both drive the core application domain (`FlowApiClient` and `data/repository.py`).
- SQLite data operations read directly from the repository layer without launching a browser.
- **Context Locking:** Playwright persistent contexts lock the Chromium profile directory. 
  - *Mitigation:* The server must serialize requests via an internal `asyncio.Lock` AND use a file-based lock on the profile context directory to prevent concurrent CLI and MCP runs from colliding.

### Security / reCAPTCHA — CAUTION (7/10)
- **Trust Boundary:** The MCP server inherits the host user's local execution permissions. It grants the client LLM agent access to drive the user's active Google account.
- **Prompt Injection & Credit Burning:** Malicious prompts could trick the client agent into running expensive generation loops, exhausting the user's Flow credits.
  - *Mitigation:* Implement sliding-window rate-limiting (max 3 runs/minute) inside tool wrappers. Exclude billing modifications from tool parameters.
- **Hanging / Timeout:** Prevent interactive auth calls on stdin by checking cookie validity *before* spawning the browser, returning a standard text response when unauthenticated.

### Performance / Playwright — CAUTION (8/10)
- Exposing SQLite catalog reads via tools (`list_projects`, `list_characters`) yields fast results (<50ms) by bypassing Playwright entirely.
- Browser-based generations run headless and are serialized to prevent queue pollution.

### CLI UX & Agentic — CAUTION (7/10)
- Exposing the terminal-targeted `skills/gflow-cli/SKILL.md` directly via `gflow://docs/skill` introduces a **hallucination risk** where agents may try to write and run shell scripts instead of calling native MCP tools.
  - *Mitigation:* Expose a dedicated, agent-targeted `gflow://docs/mcp-guide` resource that instructs the agent to prefer native JSON-RPC tools and prompts.
- Click subcommands `gflow mcp run` and `gflow mcp setup` streamline setup and configuration writing.

### Devil's Advocate — CAUTION (6/10)
- **Stdio Pipe Fragility:** Stdio-based JSON-RPC on Windows is fragile to encoding conflicts (e.g. non-ASCII prompts causing pipe crashes on `cp1252`).
  - *Mitigation:* Explicitly reconfigure stdout/stdin to use `utf-8` encoding during server startup.
- **Stdout Pollution:** Any debug prints from core libraries will corrupt the JSON-RPC stream. Redirecting all stdout to stderr is mandatory and must be carefully tested.

---

## High-confidence risks (flagged by 2+ personas)
1. **Stdout pollution:** Corrupts the MCP stream. Mitigated by global stdout-to-stderr captures.
2. **Windows pipe encoding crashes:** Mitigated by forcing `utf-8` encoding on stdio.
3. **Credit depletion via prompt injection:** Mitigated by local rate-limiting inside the tool handlers.

---

## Conflicts resolved
* *Is MCP redundant vs CLI?* Yes, agents can run CLI commands directly via shell tools. However, shell runs suffer from high escaping risks and process launch latency, and concurrent runs crash Playwright. The MCP server encapsulates these issues in a single, safe process lifecycle, justifying the extra layer.

---

## Required mitigations before EXECUTE
1. Global stdout capture and redirection to `stderr`.
2. Windows stdio encoding reconfiguration to `utf-8` on startup.
3. Local sliding-window rate limiting (max 3 runs per minute).
4. Dedicated agentic guide resource `gflow://docs/mcp-guide` to replace raw `SKILL.md` exposure.
5. Internal `asyncio.Lock` + file-based profile lock.

---

## Recommended next step
Proceed to `/gflow:scenario` and update the test plan with assertions for stdio encoding, rate limits, and lock concurrency.
