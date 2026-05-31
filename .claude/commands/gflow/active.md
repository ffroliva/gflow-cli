---
description: Show which plan is active and its goal — orientation without task detail.
---

# `/gflow:active` — Active plan identity

**Read `skills/status/SKILL.md` and follow the `active` variant protocol.**

> Do **not** call `Skill(skill="status")` — read the file directly.

The skill at `skills/status/SKILL.md` runs `scripts/dev/active_plan.py` and returns
only the header lines (Plan path, Title, Goal, Progress count). Stops before the
`--- Next task ---` separator.
