---
description: Show current plan state — active plan file, goal, progress, and next unchecked task.
---

# `/gflow:status [feature]` — Current plan state

**Read `skills/status/SKILL.md` and follow the `status` variant protocol**, passing `$ARGUMENTS` as the optional feature slug.

> Do **not** call `Skill(skill="status")` — read the file directly.

The skill at `skills/status/SKILL.md` runs `scripts/dev/active_plan.py` and returns
the full output: plan file path, title, goal, progress (X/N), and next task block.
