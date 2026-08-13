# Introduction

**gflow-cli** is an unofficial CLI and MCP server for [Google Flow](https://labs.google/fx/tools/flow) — it scripts **Veo** video and **Imagen** image generation from the terminal instead of clicking through the web UI. And because it ships an MCP server, you can point an AI assistant at it and let the agent drive the generations for you.

!!! warning "Read this before you install"
    gflow-cli is **unofficial, alpha, and reverse-engineered**. It drives a headed browser on *your own* Google Flow session, so treat it as your own account risk. It works with any Google account that has Flow access with Flow access, and every generation bills your account. **Not affiliated with Google.**

## Why it exists

Flow's web UI is fine for one-off clips. It's slow for real work — batches, character consistency across shots, reusing assets, or driving generation from a script or an agent. gflow-cli gives you the terminal surface for all of that, and an MCP server so your assistant can do it on your behalf.

## The two ways to use it

<div class="grid cards" markdown>

-   __Drive it yourself__

    ---

    Script Veo and Imagen from the terminal: `gflow image`, `gflow video`, `gflow character`, `gflow movie`, `gflow batch`.

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
- **`GFLOW_CLI_JITTER_RANGE`** — configurable pacing so batch runs stop tripping rate limits.
- **`movie.toml`** — direct a multi-scene sequence from one manifest.

## Requirements

- A Google account with Flow access (a paid AI Pro/Ultra plan only affects credit allowances).
- `uv` (to install the tool) and a Chromium the browser transport can drive.
- A display for the one-time browser login.

Ready? [**Start with the Onboarding →**](onboarding.md)
