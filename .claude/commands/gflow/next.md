---
description: Show only the next unchecked task — minimal output, no context noise.
---

# `/gflow:next [feature]` — Next task

Returns the single next unchecked task block. Nothing else.

## Steps

**1. Resolve the feature name** — same logic as `/gflow:status`.

**2. Run:**
```bash
uv run python scripts/dev/active_plan.py [--feature <name>]
```

**3. Return only the task block** — the content from `--- Next task ---` onward. Drop the Plan / Title / Goal / Progress header lines.

If the output contains "All steps complete", say so and suggest:
- `/gflow:changelog` to review unreleased changes
- `/gflow:release` if the phase is fully done

## When to call

- "What do I do right now?" — between tasks, no orientation needed
- Resuming mid-session after a context switch
