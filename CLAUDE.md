# CLAUDE.md

> Project memory hub for **Claude Code**. The universal coding-agent rules for any tool (Cursor, Codex, Aider, Antigravity, etc.) live in [AGENTS.md](AGENTS.md) — this file carries Claude-Code-specific session protocol only.

## What this project is

`gflow-cli` is an unofficial Python CLI that drives [Google Flow](https://labs.google/fx/tools/flow) (Veo image-to-video, Imagen text-to-image) from the terminal by reverse-engineering Flow's private REST API. See [README.md](README.md) for the user-facing overview.

## On every session start

1. Read **[AGENTS.md](AGENTS.md)** — universal rules every agent must follow.
2. Read **[docs/INDEX.md](docs/INDEX.md)** — routing layer for all project docs and commands.
3. Pull deeper context on demand:
   - Current task / where we left off → `/gflow:status`
   - Starting a new feature → `/gflow:predict` → `/gflow:scenario` → `/gflow:plan <feature>`
   - Touching auth or reCAPTCHA → `/gflow:known-issues`
   - Referencing a saved asset (`@Name` mention vs `--ref`/`--reference-entity`) → [docs/REFERENCE_STRATEGIES.md](docs/REFERENCE_STRATEGIES.md)
   - Cutting a release → `/gflow:release`
   - Before any commit → `/gflow:check`

## Claude-Code-specific

- Slash commands live under `.claude/commands/gflow/` (all prefixed `/gflow:`).
- Skills under `skills/` are auto-discoverable; `gflow-cli` ships its own at [`skills/gflow-cli/SKILL.md`](skills/gflow-cli/SKILL.md).
- Auto-memory at `~/.claude/projects/C--development-github-gflow-cli/memory/MEMORY.md` carries cross-session feedback and project state.
- **Worktrees:** the native `EnterWorktree` tool branches from `origin/main` (the default branch). Feature work integrates via `develop` — after entering a fresh worktree, immediately `git switch -c <type>/<name> origin/develop` and delete the auto-created `worktree-*` branch. On Windows, worktree removal can fail on a file lock (the worktree's `.venv`); `git worktree prune` + manual delete later is fine.

## MCP GitHub tool — PR body rule (non-negotiable)

When calling `mcp__github__create_pull_request` or `mcp__github__update_pull_request`, the `body` parameter **must be a plain string**. Shell heredoc syntax (`$(cat <<'EOF' ... EOF)`) is **never valid** here — MCP tool parameters are JSON, not shell; the heredoc is not evaluated and appears literally in the PR description.

```
# WRONG — produces literal "$(cat <<'EOF'" in the PR body:
body: "$(cat <<'EOF'\n## Summary\n...\nEOF\n)"

# CORRECT — plain multiline string:
body: "## Summary\n\n- Item 1\n- Item 2\n\n## Test plan\n- [ ] ..."
```

The heredoc pattern (`$(cat <<'EOF' ... EOF)`) is only valid inside a `Bash` tool call because the **shell** evaluates it there. In every MCP tool parameter it is a literal string. This mistake has recurred across multiple PRs — treat this rule as a hard blocker before every PR creation or update.

## Active phase

See [PLAN.md](PLAN.md) or run `/gflow:status` for current task. Run `/gflow:plan <feature>` to create a new feature plan.
