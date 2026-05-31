# `.claude/` — repo-local Claude Code surface

This directory holds **internal maintainer workflows** that are bundled with the repo so any contributor running Claude Code in this checkout gets the same environment.

> Distinct from `skills/gflow-cli/SKILL.md` at the repo root, which is the **published** Skill end users install into their own Claude Code to learn how to use `gflow` from agent contexts. `.claude/` is for *us*, the maintainers; `skills/` is for *them*, the users.

## Layout

```
.claude/
├── README.md              ← this file
└── commands/              ← repo-local slash commands
    └── gflow/             ← `/gflow:*` namespace (avoids collision with built-ins)
        ├── check.md       ← `/gflow:check` — hygiene + ruff + pyright + pytest
        ├── status.md      ← `/gflow:status` — full plan state (file, goal, progress, next task)
        ├── next.md        ← `/gflow:next` — next unchecked task only
        ├── active.md      ← `/gflow:active` — which plan is active, goal only
        ├── plan.md        ← `/gflow:plan <feature>` — create a task-by-task implementation plan
        ├── known-issues.md← `/gflow:known-issues` — open/mitigated items
        ├── changelog.md   ← `/gflow:changelog` — [Unreleased] + last tagged
        └── release.md     ← `/gflow:release` — full release flow (signed tags)
```

Optional additions when needed:

```
.claude/
├── settings.json          ← Claude Code settings (NOT committed if user-specific)
├── hooks/                 ← repo-local PreToolUse / PostToolUse / Stop hooks
└── skills/                ← internal maintainer skills (not for end users)
    └── <name>/SKILL.md
```

## Adding a slash command

Drop a Markdown file into `commands/gflow/` to add a `/gflow:<name>` command. Filename (without `.md`) becomes the command name. The first paragraph is the description Claude shows in `/help`. The rest is the prompt the agent executes. **Use the `gflow/` subdirectory** so commands stay namespaced and don't collide with Claude Code built-ins or user-global commands.

Example: `commands/gflow/foo.md` → invoked as `/gflow:foo`.

## Adding an internal skill

Same shape as the published one in `skills/gflow-cli/`:

```markdown
---
name: my-skill
description: When to invoke this skill (1-2 sentences).
---

# Body of the skill — how to do the thing.
```

Drop into `.claude/skills/<name>/SKILL.md`.

## Settings

`settings.json` is intentionally not committed by default — permissions and tool allowlists are usually a per-developer preference. If we ever agree on a project-wide set (e.g. always allow `uv run pytest` without a prompt), commit it then.

## Hooks

Same caveat as settings — repo-local hooks override user hooks and can surprise contributors. Only commit a hook when there's a strong, project-wide reason (e.g. block commits that would expose secrets).

## See also

- [CLAUDE.md](../CLAUDE.md) — project memory hub for any AI agent.
- [docs/INDEX.md](../docs/INDEX.md) — full documentation index.
