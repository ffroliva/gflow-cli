---
description: >
  Pre-implementation 5-persona adversarial analysis for high-stakes gflow-cli proposals.
  Produces a GO / CAUTION / STOP verdict before any code is written.
  Invoke before new transports, auth changes, selector redesigns, schema migrations,
  or any PLAN.md backlog item gated on an investigation step.
---

# `/gflow:predict [proposal]`

**Read `skills/predict/SKILL.md` and follow its protocol now**, passing `$ARGUMENTS` as the proposal description.

> Do **not** call `Skill(skill="predict")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable (only `.claude/commands/gflow/*` are). Invoking it errors with `Unknown skill: predict`. Read the file directly instead.

The skill at `skills/predict/SKILL.md` runs five independent expert personas
(Architect · Security/reCAPTCHA · Performance/Playwright · CLI UX · Devil's Advocate),
resolves conflicts, and returns a GO / CAUTION / STOP verdict with a confidence score.

**Typical workflow after a GO or CAUTION:**
```
/gflow:scenario <feature>   →  edge cases + BDD skeleton
/gflow:plan <feature>       →  writes PLAN.md task checklist
/gflow:status               →  surfaces next task during execution
/gflow:check                →  before each commit
```
