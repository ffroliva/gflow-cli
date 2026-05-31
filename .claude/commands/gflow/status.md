---
description: Show current plan state — active plan file, goal, progress, and next unchecked task.
---

# `/gflow:status [feature]` — Current plan state

Full picture of where work stands: which plan is active, how far along it is, and what comes next.

## Steps

**1. Resolve the feature name.**

- If `$ARGUMENTS` is non-empty, treat it as the feature slug (e.g. `shell-multi-prompt`, `phase-4-hardening`).
- Otherwise check conversation context for a feature name mentioned this session.

**2. Run the discovery script.**

With a feature name:
```bash
uv run python scripts/dev/active_plan.py --feature <feature-name>
```

Without a feature name:
```bash
uv run python scripts/dev/active_plan.py
```

**3. Return the output verbatim.**

The script already formats the output. Do not read additional files.

## What the script returns

- **Superpowers plan active:** file path · title · goal · progress (X/N steps complete) · next unchecked task block
- **No superpowers plan:** the first incomplete phase from `PLAN.md` (scope, sequence, definition of done)
- **All steps complete:** says so — prompt the user to run `/gflow:changelog` and then `/gflow:release` if the phase is finished

## When to call

- Starting a session: "where did we leave off?"
- After completing a task: "what comes next?"
- Before adding scope: "does this belong to the current plan?"
