# Skills

This directory ships an installable Skill that lets agents (Claude Code, Cursor, Codex, Gemini CLI, Aider, etc.) discover and invoke `gflow-cli` correctly.

## gflow-cli skill

[`gflow-cli/SKILL.md`](gflow-cli/SKILL.md) — describes when to invoke `gflow`, prerequisites, the command surface, recipes, and common errors. Plain Markdown with frontmatter, so it works as both a Claude Code skill and a generic agent reference doc.

## predict skill

[`predict/SKILL.md`](predict/SKILL.md) — pre-implementation 5-persona adversarial analysis (Architect · Security/reCAPTCHA · Performance/Playwright · CLI UX · Devil's Advocate). Returns a GO / CAUTION / STOP verdict before any code is written. Invoke via `/gflow:predict <proposal>` for high-stakes decisions: new transport, auth change, selector redesign, schema migration.

## scenario skill

[`scenario/SKILL.md`](scenario/SKILL.md) — 12-dimension edge-case explorer tuned to gflow-cli's known failure surfaces (WAF/reCAPTCHA scoring, Playwright selector drift, auth token lifecycle, batch resume idempotency, SQLite data layer, RFC 9457 error propagation, cross-platform paths, observability contract). Produces a severity-ranked scenario table and BDD `Scenario:` blocks. Invoke via `/gflow:scenario <feature>` after a predict GO/CAUTION, before writing the PLAN.md task spec.

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
