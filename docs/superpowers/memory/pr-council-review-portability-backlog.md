---
name: pr-council-review-portability-backlog
description: "Backlog of next-phase work for /gflow:pr-council-review — agent-agnostic skill form, Python helpers for token optimization, codified meta-council audit"
---

`/gflow:pr-council-review` shipped in PR #97 as a Claude-Code-specific slash command at `.claude/commands/gflow/`. Three follow-up phases captured 2026-05-26. **Phase A SHIPPED 2026-05-27 via PR #99 + polished in PR for v2.1.** Phases B + C still backlog.

**Why:** Flavio asked whether the command could be made agent-agnostic (Gemini CLI / Codex / Cursor / Aider), wrapped in a skill for token optimization, and audited by a reusable meta-council skill. All three are real architectural improvements but each is a phase of work — keeping PR #97 focused, deferred to future sessions.

**How to apply (when picking up):**

### Phase A — Extract command to portable skill form ✅ SHIPPED (PR #99, 2026-05-27)

**Done:** body lives at `skills/pr-council-review/SKILL.md` (canonical); `.claude/commands/gflow/pr-council-review.md` is a thin imperative wrapper (~6 lines after v2.1 polish). Validated empirically by re-running the v2 command on PR #95 the same day — see `[[llm-council-pr95-v2-validation]]`. Cross-tool playbook below is now actionable for non-Claude tools:

Move the command body into `skills/pr-council-review/SKILL.md` so non-Claude-Code tools can consume it directly (Cursor reads SKILL.md, Gemini CLI's `activate_skill` is similar, Aider can load as a prompt template). Keep `.claude/commands/gflow/pr-council-review.md` as a thin wrapper that just invokes the skill. Cross-tool playbook needed:

- Claude Code: `Skill` tool invocation, slash command alias
- Gemini CLI: `activate_skill` registration in `GEMINI.md` or skill manifest
- Codex CLI: prompt-template reference
- Cursor: include in `.cursor/rules/` index
- Aider: `--prompt` flag pattern

Validation: same PR # gets comparable verdicts when reviewed under Claude Code vs Gemini CLI. If verdicts diverge wildly, the skill's prompt isn't tool-agnostic yet.

### Phase B — Python helpers for token optimization

The current command dispatches 4-12 agents, each pulling the diff via `gh pr diff` and traversing memory. Two helpers would cut per-agent context cost ~30-50%:

1. `scripts/dev/pr_council_prefetch.py` — single `gh pr view <N>` + `gh pr diff <N>` call; emits a structured JSON blob with metadata, touched-paths-to-dimensions mapping, pre-computed memory-slug list per dimension. Agents read the JSON instead of re-shelling.
2. `scripts/dev/memory_filter.py` — given a touched-path list, output only the relevant memory slug bodies (not the whole MEMORY.md index). Tighter context.

Use Python (not Node `mjs`/`cjs`) — the repo standardizes on Python via `pyproject.toml`; adding a Node runtime dep just for tooling is friction. Helpers stay in `scripts/dev/` per existing convention.

### Phase C — Codify the meta-council as a reusable skill

We applied a 3-agent meta-council (completeness vs spec / robustness / prompt-clarity) to audit pr-council-review.md itself, surfacing 13 must-fix items the single-pass review would have missed. Worth codifying as `/gflow:meta-council-audit` (or `skills/meta-council-audit/`) so it can be reused on future skills/commands.

Schema: `/gflow:meta-council-audit <path-to-skill-or-command-file>` → dispatches the 3-dim audit with mandatory `[[writing-skills-best-practices]]`-style memory slugs.

User confirmed 2026-05-26: existing audit skill they were thinking of will be run "another time" — Phase C is the gflow-cli-native version that codifies *our* pattern.

### Sequencing

Recommended order: **A → C → B**. Portability (A) unblocks broader use; meta-council (C) is the QA gate for future skill-form rewrites; helpers (B) are an optimization once the skill is stable and shape is unlikely to churn.

Related: [[llm-council-code-review-pr93]] (validation evidence), [[llm-council-data-layer-fixes]] (YELLOW-as-soft-block precedent), [[agents-md-vs-llms-txt]] (cross-tool naming conventions).
