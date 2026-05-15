---
description: Show the active plan — next task if a superpowers plan is running, current phase otherwise.
---

# `/gflow:plan` — Active plan

## Steps

**1. Check conversation context.**

Has the user mentioned a specific feature or invoked the write-plan skill in this session?
If yes, note the feature name (e.g. `shell-multi-prompt`, `image-mvp`, `phase-4-hardening`).

**2. Run the discovery script.**

With a feature name identified in step 1:
```bash
uv run python scripts/dev/active_plan.py --feature <feature-name>
```

Without a feature name (uses most-recent superpowers plan, falls back to PLAN.md):
```bash
uv run python scripts/dev/active_plan.py
```

**3. Return the output verbatim.**

The script already filters to the relevant block. Do not read additional files.

## What the script returns

- **Superpowers plan active:** file path, title, goal, progress (X/N steps), and the next unchecked task block
- **No superpowers plan:** the first incomplete phase from `PLAN.md` (scope, sequence, definition of done)

## When to call

- When starting a task and unsure what's in scope
- When the user asks "what are we working on?" or "what's next?"
- Before adding a feature, to check it belongs to the current scope
