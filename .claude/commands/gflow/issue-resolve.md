---
description: >
  Drive an assessed gflow-cli issue (verdict CONFIRMED-BUG or LIKELY-BUG, with
  localized verifiable scope) to a fix: isolated worktree off develop, test-first
  fix, /gflow:check, then a DRAFT PR for human review. Mutating and gated — runs
  inside a strict action envelope (never merges, never spends credits, never
  marks a PR ready, never claims an unverified fix verified).
---

# `/gflow:issue-resolve [issue number]`

**Read `skills/issue-resolve/SKILL.md` and follow its protocol now**, passing `$ARGUMENTS` as the issue to resolve.

> Do **not** call `Skill(skill="issue-resolve")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not Skill-tool-invocable (only `.claude/commands/gflow/*` are). Read the file directly instead.

Preconditions (from `/gflow:issue-assessment`): verdict ∈ {`CONFIRMED-BUG`, `LIKELY-BUG`}, scope single-surface, and the fix is verifiable in this environment (or the gap is carried into the PR as "needs human e2e"). The skill isolates a `bugfix/` worktree off `develop`, gates high-stakes changes through `/gflow:predict` + `/gflow:scenario`, fixes test-first (Opus plans → Sonnet codes → Opus reviews for non-trivial work), runs `/gflow:check`, opens a **draft** PR with a Verification-status section, runs `/gflow:pr-council-review`, and **stops** for a human to promote and merge.

**Chain:** `/gflow:issue-assessment <N>` → (if scope clear) `/gflow:issue-resolve <N>` → human promotes draft → human runs any headed/e2e check → merge.
