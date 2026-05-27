---
description: Multi-dimensional LLM council review of an open PR. Five baseline dimensions (correctness, quality, security, tests, memory-hygiene) plus adaptive per-surface dimensions. Sub-agents invoke specialized skills (security-review, code-review, verify). With no argument, lists open PRs ranked by priority. Wrapper around skills/pr-council-review/SKILL.md (canonical body).
---

# `/gflow:pr-council-review [PR#]` — PR Council Review Gate

This command is a **thin wrapper** around the canonical skill at `skills/pr-council-review/SKILL.md`. The skill contains all logic (preflight, phase-by-phase execution, per-dimension prompts, synthesis rules, report format). Keeping the body in a single source-of-truth means other tools (Gemini CLI / Codex / Cursor / Aider) can consume the same logic by loading the SKILL.md directly, and the Claude Code slash command stays a one-line invocation.

## How to invoke

1. Load the skill with the `Skill` tool: `Skill(skill="pr-council-review")`.
2. Pass the `PR#` argument (or leave empty for the prioritize-mode list).
3. Follow the skill's phases exactly.

Two modes:
- **No argument** → list open PRs ranked by review priority; user picks.
- **`PR#` argument** → run the full council (5 baseline + N adaptive parallel reviewers).

## Why a wrapper instead of inlining

- **Single source of truth:** the body lives in `skills/pr-council-review/SKILL.md`. Edits land in one place.
- **Cross-tool portability:** the canonical SKILL.md is consumable by Gemini CLI's `activate_skill`, Cursor's `.cursor/rules`, Aider's `--prompt`, Codex's prompt-template flow, etc. See memory `[[pr-council-review-portability-backlog]]`.
- **Token efficiency:** the Claude Code harness loads the skill on demand; the slash command itself is a few lines instead of ~330.

## Sibling commands

- `/review` — single-agent Claude Code built-in. Quick one-pass review. Use for spot-checks, draft iteration, or when council is overkill.
- `/gflow:pr-council-review` — multi-agent council. Use before merge, on high-risk surfaces (auth, transports, data, release-gate), or when a single-agent pass would miss cross-dimension defects.

## Provenance

- v1 (PR #97, 2026-05-26) — initial slash command, validated on PR #93 (locale selectors).
- v2 (this PR) — extracted body into `skills/pr-council-review/SKILL.md`; added 5th baseline dimension (D5 Memory hygiene); fixed stale-working-tree-reads bug (sub-agents now use `git show origin/<head>:<path>` not `Read`); added per-dimension specialized-skill invocation (`security-review`, `code-review`, `verify`).
- Provenance memory: `[[llm-council-code-review-pr93]]`, `[[pr-council-review-stale-tree-reads]]`, `[[pr-council-review-portability-backlog]]`.

For all execution details — phases, dimension table, per-dimension prompt skeletons, mandatory memory slug table, synthesis rules, report format — see `skills/pr-council-review/SKILL.md`. This wrapper exists only so Claude Code users can type `/gflow:pr-council-review` instead of `Skill(skill="pr-council-review")`.
