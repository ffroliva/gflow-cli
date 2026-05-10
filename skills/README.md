# Skills

This directory ships an installable Skill that lets agents (Claude Code, Cursor, Codex, Gemini CLI, Aider, etc.) discover and invoke `gflow-cli` correctly.

## gflow-cli skill

[`gflow-cli/SKILL.md`](gflow-cli/SKILL.md) — describes when to invoke `gflow`, prerequisites, the command surface, recipes, and common errors. Plain Markdown with frontmatter, so it works as both a Claude Code skill and a generic agent reference doc.

### Install for Claude Code

```bash
git clone git@github.com:ffroliva/gflow-cli.git
cd gflow-cli
ln -s "$(pwd)/skills/gflow-cli" ~/.claude/skills/gflow-cli

# Verify Claude Code picks it up:
ls ~/.claude/skills/gflow-cli/SKILL.md
```

### Use with other agents

The SKILL.md file is plain Markdown. Read it into your agent's context however your tool prefers:

- **Cursor / Aider**: paste the contents into a `.cursorrules` / `.aider.md` file or include in a prompt.
- **Codex / Gemini CLI**: read it as a reference doc when the user asks about video generation.
- **Custom agents**: include it in your system prompt or knowledge base.

The CLI itself (`gflow`) is identical regardless of which agent invokes it.
