# Skills

This directory ships installable agent skill docs for `gflow-cli`. Each `SKILL.md` is plain Markdown with YAML frontmatter, consumable by Claude Code, Cursor, Codex, Gemini CLI, Aider, and any custom agent.

| Skill | Path | `version` |
|---|---|---|
| gflow-cli | [`gflow-cli/SKILL.md`](gflow-cli/SKILL.md) | 1.1 |
| predict | [`predict/SKILL.md`](predict/SKILL.md) | 1.0 |
| scenario | [`scenario/SKILL.md`](scenario/SKILL.md) | — |
| pr-council-review | [`pr-council-review/SKILL.md`](pr-council-review/SKILL.md) | 2.1 |
| plan | [`plan/SKILL.md`](plan/SKILL.md) | 1.0 |
| status | [`status/SKILL.md`](status/SKILL.md) | 1.0 |
| issue-assessment | [`issue-assessment/SKILL.md`](issue-assessment/SKILL.md) | — |
| issue-resolve | [`issue-resolve/SKILL.md`](issue-resolve/SKILL.md) | — |
| check | [`check/SKILL.md`](check/SKILL.md) | 1.0 |
| changelog | [`changelog/SKILL.md`](changelog/SKILL.md) | 1.0 |
| known-issues | [`known-issues/SKILL.md`](known-issues/SKILL.md) | 1.0 |
| sonar | [`sonar/SKILL.md`](sonar/SKILL.md) | 1.0 |
| doc-review | [`doc-review/SKILL.md`](doc-review/SKILL.md) | 1.0 |
| release | [`release/SKILL.md`](release/SKILL.md) | 1.0 |

## gflow-cli skill

[`gflow-cli/SKILL.md`](gflow-cli/SKILL.md) — describes when to invoke `gflow`, prerequisites, the command surface, recipes, and common errors. Plain Markdown with frontmatter, so it works as both a Claude Code skill and a generic agent reference doc.

## predict skill

[`predict/SKILL.md`](predict/SKILL.md) — pre-implementation 5-persona adversarial analysis (Architect · Security/reCAPTCHA · Performance/Playwright · CLI UX · Devil's Advocate). Returns a GO / CAUTION / STOP verdict before any code is written. Invoke via `/gflow:predict <proposal>` for high-stakes decisions: new transport, auth change, selector redesign, schema migration.

## scenario skill

[`scenario/SKILL.md`](scenario/SKILL.md) — 12-dimension edge-case explorer tuned to gflow-cli's known failure surfaces (WAF/reCAPTCHA scoring, Playwright selector drift, auth token lifecycle, batch resume idempotency, SQLite data layer, RFC 9457 error propagation, cross-platform paths, observability contract). Produces a severity-ranked scenario table and BDD `Scenario:` blocks. Invoke via `/gflow:scenario <feature>` after a predict GO/CAUTION, before `/gflow:plan <feature>`.

## plan skill

[`plan/SKILL.md`](plan/SKILL.md) — creates a structured task-by-task implementation plan for a feature and writes it to `docs/superpowers/plans/<date>-<slug>/PLAN.md`. Gathers predict/scenario context, asks ≤3 clarifying questions, decomposes into atomic committable tasks with step + test checklists. Invoke via `/gflow:plan <feature>` after a predict GO/CAUTION verdict.

## status skill

[`status/SKILL.md`](status/SKILL.md) — three variants for surfacing plan state at different detail levels: `status` (full state: plan path, goal, progress, next task), `next` (next task only), `active` (plan identity only). All variants run `scripts/dev/active_plan.py`. Invoke via `/gflow:status`, `/gflow:next`, or `/gflow:active`.

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

## SkillOpt harness

The harness at [`../scripts/dev/skillopt/`](../scripts/dev/skillopt/) measures how accurately any LLM agent performs against the 20-task scored dataset when guided by a skill doc. Supports Anthropic, OpenAI-compat (GPT-4o, Gemini, Ollama, LM Studio), and any custom provider.

```bash
# Dry-run (no API call)
python scripts/dev/skillopt/harness.py --dry-run

# Claude
ANTHROPIC_API_KEY=... python scripts/dev/skillopt/harness.py

# GPT-4o
OPENAI_API_KEY=... python scripts/dev/skillopt/harness.py --provider openai --model gpt-4o

# Gemini
OPENAI_API_KEY=$GEMINI_API_KEY python scripts/dev/skillopt/harness.py \
    --provider openai \
    --base-url https://generativelanguage.googleapis.com/v1beta/openai/ \
    --model gemini-2.0-flash
```

See [`scripts/dev/skillopt/README.md`](../scripts/dev/skillopt/README.md) for the full improvement loop.
