---
description: Show which plan is active and its goal — orientation without task detail.
---

# `/gflow:active` — Active plan identity

Answers "which plan are we in?" without pulling in the full task breakdown.

## Steps

**1. Run:**
```bash
uv run python scripts/dev/active_plan.py
```

**2. Return only the header lines** — Plan path, Title, Goal, Progress count. Stop before the `--- Next task ---` separator. Do not include task steps.

## When to call

- Before `/gflow:predict` or `/gflow:scenario`: confirm the proposal belongs to the active scope
- Quick orientation: "are we in a superpowers plan or the root PLAN.md?"
- When context is long and you need a one-line anchor without re-reading the whole task block
