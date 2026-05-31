---
description: Show only the next unchecked task — minimal output, no context noise.
---

# `/gflow:next [feature]` — Next task

**Read `skills/status/SKILL.md` and follow the `next` variant protocol**, passing `$ARGUMENTS` as the optional feature slug.

> Do **not** call `Skill(skill="status")` — read the file directly.

The skill at `skills/status/SKILL.md` runs `scripts/dev/active_plan.py` and returns
only the `--- Next task ---` block. Header lines (Plan, Title, Goal, Progress) are omitted.
