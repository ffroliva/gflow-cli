# CLAUDE.md

> Project memory hub for **Claude Code**. The universal coding-agent rules for any tool (Cursor, Codex, Aider, Gemini CLI, etc.) live in [AGENTS.md](AGENTS.md) — this file carries Claude-Code-specific session protocol only.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API. See [README.md](README.md) for the user-facing overview.

## On every session start

1. Read **[AGENTS.md](AGENTS.md)** — universal rules every agent must follow.
2. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs and commands.
3. Pull deeper context on demand:
   - Starting a feature → `/gflow:plan`
   - Touching auth or reCAPTCHA → `/gflow:known-issues`
   - Cutting a release → `/gflow:release`
   - Before any commit → `/gflow:check`

## Claude-Code-specific

- Slash commands live under `.claude/commands/gflow/` (all prefixed `/gflow:`).
- Skills under `skills/` are auto-discoverable; `gflow-cli` ships its own at [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md).
- Agent memory lives at `~/.claude/projects/<project-slug>/memory/MEMORY.md`. The slug is derived from the local checkout path (e.g., `-home-user-gflow-cli` on Linux). Create the directory if it doesn't exist. The file carries cross-session state, harness decisions, and the session log. See `docs/REFERENCES.md` for the reference repositories that inform our harness decisions.

## Active phase

See [PLAN.md](PLAN.md) or run `/gflow:plan` for the current detailed plan.
