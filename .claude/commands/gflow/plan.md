---
description: Create a structured task-by-task implementation plan for a feature and write it to docs/superpowers/plans/.
---

# `/gflow:plan <feature>` — Create a feature plan

**Read `skills/plan/SKILL.md` and follow its protocol now**, passing `$ARGUMENTS` as the feature description.

> Do **not** call `Skill(skill="plan")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable. Read the file directly instead.

The skill at `skills/plan/SKILL.md` gathers predict/scenario context, asks ≤3
clarifying questions, decomposes the feature into atomic committable tasks with
step + test checklists, and writes `docs/superpowers/plans/<date>-<slug>/PLAN.md`.

**Typical workflow:**
```
/gflow:predict <proposal>   →  GO / CAUTION / STOP
/gflow:scenario <feature>   →  edge cases + BDD skeleton
/gflow:plan <feature>       →  writes PLAN.md  ← this command
/gflow:status               →  surfaces next task
/gflow:check                →  before each commit
```
