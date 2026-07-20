---
description: Run pr-council-review (PR or branch mode) plus an external-tools corroboration layer (codex, plus Antigravity via the agy harness) for high-stakes reviews where a same-model-family blind spot is a real risk. Tiers: small (internal only, default) / medium (+codex) / high (+codex+antigravity). Wrapper around skills/llm-council/SKILL.md.
---

# `/gflow:llm-council [PR# | --base <ref>] [--tier small|medium|high]`

**Read `skills/llm-council/SKILL.md` and follow its protocol now**, treating `$ARGUMENTS` as: the PR number or `--base <ref>` (branch mode, same as `/gflow:pr-council-review` / `/gflow:branch-review`), plus `--tier <small|medium|high>` (default `small`). The `high` tier adds Antigravity (`agy`) alongside `codex`.

> Do **not** call `Skill(skill="llm-council")` or `Skill(skill="pr-council-review")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable (only `.claude/commands/gflow/*` are). Invoking either errors with `Unknown skill: ...`. Read the files directly instead.

`skills/llm-council/SKILL.md` is the canonical body: tier resolution, the tool registry (fixed invocation recipes per external tool — read this before running any external tool, `codex review` in particular is a known trap), the availability-probe step, dispatch flow, and synthesis. It wraps `skills/pr-council-review/SKILL.md` unchanged for the internal dimension council.

Siblings: `/gflow:pr-council-review <N>` (internal-only, PR mode), `/gflow:branch-review` (internal-only, branch mode) — use either directly when external corroboration isn't warranted.
