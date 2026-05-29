---
description: >
  Pre-implementation edge-case explorer for gflow-cli features.
  Decomposes a proposed change across 12 gflow-cli-specific dimensions
  (WAF/reCAPTCHA, selector drift, auth lifecycle, batch resume, data layer,
  cross-platform paths, error propagation, observability) and produces a
  severity-ranked scenario table and BDD skeleton.
---

# `/gflow:scenario [feature description]`

**Read `skills/scenario/SKILL.md` and follow its protocol now**, passing `$ARGUMENTS` as the feature or change description.

> Do **not** call `Skill(skill="scenario")` — the repo's `skills/*/SKILL.md` files are plain Markdown, not registered as Skill-tool-invocable (only `.claude/commands/gflow/*` are). Invoking it errors with `Unknown skill: scenario`. Read the file directly instead.

The skill at `skills/scenario/SKILL.md` covers 12 dimensions tuned to gflow-cli's
known failure surfaces and outputs:
- A severity-ranked scenario table (Critical / High / Medium / Low)
- Must-cover acceptance criteria for the PLAN.md task
- Suggested BDD `Scenario:` blocks for `tests/features/`
- Cross-references to open KNOWN_ISSUES entries

**Typical order:** `/gflow:predict` → `/gflow:scenario` → PLAN.md task → `/gflow:check` → PR.
