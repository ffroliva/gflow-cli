# GEMINI.md

> Project memory hub for **Gemini CLI**. The universal coding-agent rules for any tool (Cursor, Codex, Aider, Gemini CLI, etc.) live in [AGENTS.md](AGENTS.md) — this file carries Gemini-specific session protocol only.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API. See [README.md](README.md) for the user-facing overview.

## On every session start

1. Read **[AGENTS.md](AGENTS.md)** — universal rules every agent must follow.
2. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs and commands.
3. Pull deeper context on demand (type as plain text in the `agy` TUI prompt):
   - Current task / where we left off → `gflow:status`
   - Starting a new feature → `gflow:predict` → `gflow:scenario` → `gflow:plan <feature>`
   - Touching auth or reCAPTCHA → `gflow:known-issues`
   - Cutting a release → `gflow:release`
   - Before any commit → `gflow:check`

## Gemini-specific

- Use specialized skills when relevant (e.g., `find-docs` for library research, `pr-council-review` for PR audits).
- Maintain memory via the `mcp-mempalace` tool if available.
- Prioritize **turn efficiency** and **high-signal output**.

## Active phase

- **Decoupled Daemon/Worker Plan:** The blueprint for the headless SSE Daemon, SQLite generation queue, and Tauri/React editor is scheduled in [PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-23-mcp-ui-worker-integration/PLAN.md) and [SCENARIO.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-23-mcp-ui-worker-integration/SCENARIO.md).
- **Core Lesson (Retrospective):** Kept `gflow-cli` strictly headless (running Uvicorn and FastMCP over localhost HTTP/SSE). Bypassed browser context locks by serializing task writes in SQLite queue, allowing parallel client database reads via WAL mode.

See [PLAN.md](PLAN.md) or type `gflow:status` for the current task. Type `gflow:plan <feature>` to create a new feature plan.
