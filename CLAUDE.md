# CLAUDE.md

> Project memory hub for **Claude Code**. The universal coding-agent rules for any tool (Cursor, Codex, Aider, Gemini CLI, etc.) live in [AGENTS.md](AGENTS.md) — this file carries Claude-Code-specific session protocol only.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API. See [README.md](README.md) for the user-facing overview.

## On every session start

1. Read **[AGENTS.md](AGENTS.md)** — universal rules every agent must follow.
2. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs and commands.
3. Read **[docs/AGENT_GUIDE.md](docs/AGENT_GUIDE.md)** — mandatory mandates beyond AGENTS.md.
4. Pull deeper context on demand:
   - Starting a feature → `/gflow:plan`
   - Touching auth or reCAPTCHA → `/gflow:known-issues`
   - Cutting a release → `/gflow:release`
   - Before any commit → `/gflow:check`

## Claude-Code-specific

- Slash commands live under `.claude/commands/gflow/` (all prefixed `/gflow:`).
- Skills under `skills/` are auto-discoverable; `gflow-cli` ships its own at [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md).
- Auto-memory at `~/.claude/projects/C--development-github-gflow-cli/memory/MEMORY.md` carries cross-session feedback and project state.

## Active phase

See [PLAN.md](PLAN.md) or run `/gflow:plan` for the current detailed plan.
