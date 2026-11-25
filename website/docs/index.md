# Introduction

[![OpenSSF Scorecard](https://api.scorecard.dev/projects/github.com/ffroliva/gflow-cli/badge)](https://scorecard.dev/viewer/?uri=github.com/ffroliva/gflow-cli)

**gflow-cli** is an open-source MCP server and CLI for [Google Flow](https://labs.google/fx/tools/flow): it generates **Veo 3.1** and **Omni** video and **Nano Banana** and **Imagen** images from Claude Code, Claude Desktop, Cursor, Codex, VS Code or the terminal, instead of clicking through the web UI. And because it ships an MCP server, you can point an AI assistant at it and let the agent drive the generations for you.

!!! warning "Read this before you install"
    gflow-cli is an **independent project, not affiliated with Google**. It drives a headed browser on *your own* Google Flow session, so treat it as your own account risk. It works with any Google account that has Flow access, and every video generation bills your Flow credits (images spend none but count against a daily image quota).

## Why it exists

The Flow web UI is designed for interactive use. gflow-cli adds a terminal surface for batches, character consistency across shots, asset reuse, and generation driven from a script or an agent. It also adds an MCP server so an AI assistant can do the same on the user's behalf.

## The two ways to use it

<div class="grid cards" markdown>

-   __Drive it yourself__

    ---

    Script Veo and Imagen from the terminal: `gflow image`, `gflow video`, `gflow character`, `gflow movie`, `gflow run`.

    [:octicons-arrow-right-24: Onboarding](onboarding.md)

-   __Let your assistant drive it__

    ---

    Run `gflow mcp run` and point Claude, Cursor, or Copilot at it. The agent picks the models, prompt tooling, and settings.

    [:octicons-arrow-right-24: Agent-driven](agents.md)

</div>

## What it handles for you

- **`--tool creative-director`** — rewrites a terse prompt with Google's 5-component formula before a single credit is spent.
- **`gflow instructions`** — persistent, credit-free brief cards that steer look and tone across generations.
- **Asset reuse by UUID** — reference an already-generated still in a video without re-uploading it.
- **`--ui-mode`** — fails fast, *before* spending credits, when Flow's UI cohort isn't what the command needs.
- **`GFLOW_CLI_JITTER_RANGE`** — configurable pacing between requests to reduce rate limiting.
- **`movie.toml`** — direct a multi-scene sequence from one manifest.

## Requirements

- A Google account with Flow access (a paid AI Pro/Ultra plan affects credit allowances and tier-gated features such as 4K upscale).
- `uv` (to install the tool) and a Chromium the browser transport can drive.
- A display for the one-time browser login.

Ready? [**Start with the Onboarding →**](onboarding.md)
