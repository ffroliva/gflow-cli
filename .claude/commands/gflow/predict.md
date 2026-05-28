---
description: >
  Pre-implementation 5-persona adversarial analysis for high-stakes gflow-cli proposals.
  Produces a GO / CAUTION / STOP verdict before any code is written.
  Invoke before new transports, auth changes, selector redesigns, schema migrations,
  or any PLAN.md backlog item gated on an investigation step.
---

# `/gflow:predict [proposal]`

**Invoke `Skill(skill="predict")` now**, passing `$ARGUMENTS` as the proposal description.

The skill at `skills/predict/SKILL.md` runs five independent expert personas
(Architect · Security/reCAPTCHA · Performance/Playwright · CLI UX · Devil's Advocate),
resolves conflicts, and returns a GO / CAUTION / STOP verdict with a confidence score.

**Pair with `/gflow:scenario`** after a GO or CAUTION to enumerate edge cases before EXECUTE.
