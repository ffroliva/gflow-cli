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

- **Agentic Instructions Implementation (Completed - PR #269):** Programmatic, API-driven, and relational instructions management system in the Google Flow Agentic transport (CRUD commands, TOML/JSON declarative sync, and movie manifest global/per-scene card overrides with pre-generation briefs patching).
- **Decoupled Daemon/Worker Plan:** The MCP→FlowWorker wiring shipped in v0.23.0 (PR #228). The remaining headless SSE Daemon + Tauri/React editor blueprint is scheduled in [gflow-studio-scaffold/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-gflow-studio-scaffold/PLAN.md) and [rest-api-layer/PLAN.md](file:///C:/development/github/gflow-cli/docs/superpowers/plans/2026-06-24-rest-api-layer/PLAN.md).
- **Core Lesson (Retrospective):** 
  - Kept `gflow-cli` strictly headless (running Uvicorn and FastMCP over localhost HTTP/SSE). Bypassed browser context locks by serializing task writes in SQLite queue, allowing parallel client database reads via WAL mode.
  - Relational instruction cards steering generations through the reasoning path must be phrased conversationally (imperative prompts bypass the brief). Multi-scene movie instructions syncing is read-modify-write to preserve server-assigned card IDs and prevent title/ID collisions.

See [PLAN.md](PLAN.md) or type `gflow:status` for the current task. Type `gflow:plan <feature>` to create a new feature plan.
