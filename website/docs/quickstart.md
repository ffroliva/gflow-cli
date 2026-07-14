# Quickstart

From install to your first Veo clip in about five minutes — or hand the whole thing to your AI assistant.

!!! warning "Before you begin"
    gflow-cli is **unofficial, alpha, and reverse-engineered**. It drives a headed browser on *your own* Google Flow session — treat it as your own account risk. It requires a Google AI **Ultra or Pro** subscription with Flow access, and every generation bills your account. Not affiliated with Google.

## 1. Install

gflow-cli installs as a standalone tool with `uv` — no global Python environment to manage.

```bash
# install the CLI
uv tool install gflow-cli

# one-time: the browser transport needs Chromium
uv tool run --from gflow-cli playwright install chromium
```

## 2. Authenticate

A one-time login saves a browser session against your own Flow account. The `--browser chrome` flag is **mandatory** — the CLI fails fast on any other strategy.

```bash
gflow auth login --browser chrome
```

!!! note
    A Chrome window opens once for you to sign in (2FA included). The session persists after that — you won't log in every run.

## 3. Your first generation

The shortest happy path is one text-to-image call. Output lands in `./out/` (or `$GFLOW_CLI_OUTPUT_DIR`).

```bash
gflow image t2i "a lighthouse in a storm, moody, cinematic" --tool creative-director
#   ↳ creative-director expanded the prompt (Google's 5-component formula)
#   ✓ out/lighthouse_storm.png
```

Turn that still into an 8-second Veo clip with image-to-video:

```bash
gflow video i2v out/lighthouse_storm.png --aspect 9:16
```

## 4. Let your assistant drive it

Everything above, an agent can do for you. Start the MCP server and point Claude, Cursor, or Copilot at it — CLI-to-MCP parity is enforced in CI, so any command is reachable as a tool.

```bash
gflow mcp run
#   serving over stdio:
#   • gflow_generate_image   • gflow_generate_video
```

Then just ask, in plain language — the agent picks the model, aspect, and prompt tooling and hands you the files. See [Agent-driven](agents.md) for the full setup.

## Where to next

- [**Authentication →**](AUTHENTICATION.md) — profiles, reCAPTCHA, and the headed-browser transport in depth.
- [**Let your assistant drive it →**](agents.md) — wire gflow-cli into your agent via MCP.
- [**Known issues →**](KNOWN_ISSUES.md) — WAF/403 pacing, credit traps, and current limitations.
