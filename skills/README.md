# Skills

This directory ships an installable Skill that lets agents (Claude Code, Cursor, Codex, Gemini CLI, Aider, etc.) discover and invoke `flow-cli` correctly.

## flow-cli skill

[`flow-cli/SKILL.md`](flow-cli/SKILL.md) — describes when to invoke `gflow`, prerequisites, the command surface, recipes, and common errors. Plain Markdown with frontmatter, so it works as both a Claude Code skill and a generic agent reference doc.

### Install for Claude Code

```bash
git clone git@github.com:ffroliva/flow-cli.git
ln -s "$(pwd)/flow-cli/skills/flow-cli" ~/.claude/skills/flow-cli

# Verify Claude Code picks it up:
ls ~/.claude/skills/flow-cli/SKILL.md
```

### Use with other agents

The SKILL.md file is plain Markdown. Read it into your agent's context however your tool prefers:

- **Cursor / Aider**: paste the contents into a `.cursorrules` / `.aider.md` file or include in a prompt.
- **Codex / Gemini CLI**: read it as a reference doc when the user asks about video generation.
- **Custom agents**: include it in your system prompt or knowledge base.

The CLI itself (`gflow`) is identical regardless of which agent invokes it.
