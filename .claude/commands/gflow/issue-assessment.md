---
description: >
  Triage a gflow-cli GitHub issue: verify the reporter's claim against code,
  tests, docs, KNOWN_ISSUES, and memory; classify it (CONFIRMED / NEEDS-E2E /
  NEEDS-INFO / DUPLICATE / INVALID / WONTFIX); judge whether it is verifiable
  end-to-end in the current environment; and draft a reporter-facing reply.
  Read-only — changes nothing and posts nothing on its own.
---

# `/gflow:issue-assessment [issue number or URL]`

**Read `skills/issue-assessment/SKILL.md` and follow its protocol now**, passing `$ARGUMENTS` as the issue to assess.

> Do **not** call `Skill(skill="issue-assessment")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not Skill-tool-invocable (only `.claude/commands/gflow/*` are). Read the file directly instead.

The skill at `skills/issue-assessment/SKILL.md` ingests the issue, verifies the
claim (dispatching a search agent to keep context clean), assigns one verdict,
applies the **e2e-gate** (never claim a fix is verified on an unverifiable
surface), and emits a standard report artifact. On a `CONFIRMED` / `LIKELY-BUG`
verdict with localized, verifiable scope it hands off to `issue-resolve`;
otherwise it replies only.

**Typical chain:**
```
/gflow:issue-assessment <N>   →  verdict + e2e-gate + reply artifact
   ↓ (if scope clear & verifiable)
issue-resolve <N>             →  worktree + TDD + draft PR for human review
```
