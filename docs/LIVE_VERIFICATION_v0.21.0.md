# Live Verification — v0.21.0

Evidence that the user-facing features shipped in v0.21.0 work against a real
process (not just in-process unit tests). The headline feature is the **MCP
server** (`gflow mcp run` over stdio, `gflow serve` over HTTP/SSE). MCP boot and
protocol exchange are **credit-free** — no Veo generation is triggered — so this
was exercised end-to-end.

Verified on 2026-06-26, Windows 11, Python 3.13, `gflow-cli==0.21.0`.

## Feature 1 — `gflow mcp run` (MCP over stdio)

Launched the real CLI entry point as a subprocess and drove a full JSON-RPC
session over stdin/stdout (`initialize` → `notifications/initialized` →
`tools/list` → `prompts/list`).

5-layer ledger:

1. **Process boot** — startup event emitted on **stderr** (never stdout):
   `{"transport":"stdio","event":"mcp.server.starting","cli_version":"0.21.0",...}`
2. **Protocol on the correct stream** — the `initialize` result and every
   subsequent reply arrived on **stdout** (the channel a real MCP client reads);
   stdout contained **zero** non-JSON lines. (Regression fixed this cycle: the
   stdout→stderr redirect previously ran before FastMCP captured the protocol
   stream, so all responses went to stderr and clients saw nothing.)
3. **Capability shape** — `initialize` advertised `tools`, `resources`, and
   `prompts` capabilities.
4. **Inventory** — `tools/list` → **4 tools** (`gflow_generate_image`,
   `gflow_generate_video`, `gflow_list_projects`, `gflow_list_characters`);
   `prompts/list` → **2 prompts** (`expand_prompt`, `create_character`);
   `resources/list` → **3 resources** (`gflow://docs/mcp-guide`,
   `gflow://docs/known-issues`, `gflow://db/schema`). (Regression fixed this
   cycle: the startup path imported only `tools` and `resources`, so prompts
   registered as **0**; it now imports `prompts` too.)
5. **User-confirmable artifact** — a user pastes this into
   `claude_desktop_config.json` and the gflow tools appear in the client:
   ```json
   { "mcpServers": { "gflow": { "command": "gflow", "args": ["mcp", "run"] } } }
   ```

Automated regression guard: `tests/mcp/test_stdio_transport.py` launches the
real subprocess and asserts JSON-RPC (incl. tools and prompts) is served on
stdout — coverage the in-process FastMCP client cannot provide.

## Feature 2 — `gflow serve` (MCP over HTTP/SSE)

Started the daemon on `127.0.0.1` and probed the endpoints with `curl`.

1. **Process boot** — startup event on stderr:
   `{"transport":"sse","host":"127.0.0.1","port":...,"event":"mcp.server.starting","cli_version":"0.21.0"}`
2. **SSE endpoint live** — `GET /sse` → **HTTP 200** (event stream opens).
3. **Path correctness** — the advertised URL was corrected this cycle from the
   non-existent `/mcp/sse` (returned 404) to the real `/sse`; messages are POSTed
   to `/messages/`.
4. **Bind safety** — non-loopback binds abort (exit 11) unless
   `GFLOW_DAEMON_TOKEN` is set (verified by code path; loopback default needs no
   token).

## Not yet user-facing (no live verification required)

The FastAPI lifespan daemon, `FlowWorker`, and SQLite generation queue
(`worker/`, `ui/app.py`, migration `0007_queue`) landed as internal foundation
and are **not wired into a user command** in this release — `gflow serve` runs
the MCP/SSE server only. They are covered by unit/integration tests
(`tests/worker/`, `tests/ui/`, `tests/data/`) and will be live-verified when a
command exposes them.
