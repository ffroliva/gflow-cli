---
description: Multi-dimensional LLM council review of an open PR. Five baseline dimensions (correctness, quality, security, tests, memory-hygiene) plus adaptive per-surface dimensions. Sub-agents invoke specialized skills (security-review, code-review, verify). With no argument, lists open PRs ranked by priority. Wrapper around skills/pr-council-review/SKILL.md (canonical body).
---

# `/gflow:pr-council-review [PR#]`

**Read `skills/pr-council-review/SKILL.md` and follow its protocol now**, treating `$ARGUMENTS` as the PR number (or empty for prioritize-mode). That file is the canonical body: preflight, dimension detection, parallel dispatch, synthesis, report.

> Do **not** call `Skill(skill="pr-council-review")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable (only `.claude/commands/gflow/*` are). Invoking it errors with `Unknown skill: pr-council-review`. Read the file directly instead.

Sibling: `/review` is the single-agent Claude-Code built-in (fast, one-pass). Use it for spot-checks; use this command for pre-merge multi-dim audits.
