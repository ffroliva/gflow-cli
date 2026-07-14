# Let your assistant drive it

gflow-cli is built to be driven by an AI assistant. It ships an **MCP server**, so Claude, Cursor, or Copilot connects to it natively — no pasting docs, no copying commands back and forth. CLI-to-MCP parity is enforced in CI: anything the terminal can do, your assistant can do.

## Start the server

```bash
gflow mcp run
#   serving over stdio:
#   • gflow_generate_image
#   • gflow_generate_video
#   ready — your assistant can drive Flow.
```

`gflow mcp run` speaks MCP over stdio; `gflow serve` exposes an HTTP/SSE endpoint if you'd rather connect over the network. `gflow mcp setup` helps wire it into a client.

## Point your agent at it

The repo ships the files an agent reads to understand the tool:

| File | Purpose | Use with |
| --- | --- | --- |
| `AGENTS.md` | Universal agent onboarding | Cursor, Codex, Aider, Claude Code |
| `llms.txt` | LLM-readable command reference | Paste into ChatGPT / Claude / Gemini |
| `CLAUDE.md` | Claude Code memory hub | Claude Code specifically |

Once connected, describe what you want in plain language:

> "Make a 9:16 clip of a lighthouse in a storm, moody, slow push-in."

The agent picks the Veo model, aspect, and prompt tooling, runs the generation on your own Flow session, and hands you the files.

## What the agent leans on

- **`--tool creative-director`** — expands a terse prompt with Google's 5-component formula before spending credits.
- **`gflow instructions`** — persistent, credit-free brief cards that steer look and tone across generations.
- **Asset reuse by UUID** — chain a still into a video by its id, no re-upload.
- **`--ui-mode`** — aborts up front when Flow's UI cohort isn't what the command needs, instead of burning a generation.

!!! note "It runs on your account"
    Even when an agent drives it, gflow-cli still uses your own headed Flow session and bills your own credits. The same alpha/account-risk caveats apply.

Next: [**Known issues →**](KNOWN_ISSUES.md).
