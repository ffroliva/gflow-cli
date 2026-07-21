---
name: status
version: "1.0"
description: >
  Surfaces the current state of work in gflow-cli. Three variants at different
  levels of detail: full state (status), next task only (next), active plan
  identity (active). All variants run scripts/dev/active_plan.py and filter
  its output to the requested detail level.
---

# `status` — Plan State Reader

Three variants for different contexts. All backed by `scripts/dev/active_plan.py`.

---

## Variants

### `status [feature]` — Full state

The full picture: which plan file is active, its goal, progress (X/N tasks complete),
and the next unchecked task block.

**Script invocation:**
```bash
# With a feature name (from $ARGUMENTS or conversation context):
uv run python scripts/dev/active_plan.py --feature <slug>

# Without a feature name (uses root PLAN.md):
uv run python scripts/dev/active_plan.py
```

**Return:** the complete script output verbatim.

**When to call:**
- Starting a session: "where did we leave off?"
- After completing a task: "what comes next?"
- Before adding scope: "does this belong to the current plan?"

---

### `next [feature]` — Next task only

The single next unchecked task block. No header noise.

**Script invocation:** same as `status`.

**Return:** only the content from `--- Next task ---` onward. Drop the Plan / Title /
Goal / Progress header lines entirely.

If no `--- Next task ---` separator is present (root PLAN.md mode), return the full
script output — the phase block is already task-level content.

If the script output contains "All steps complete", say so and suggest:
- `/gflow:changelog` to review unreleased changes
- `/gflow:release` if the phase is fully done

**When to call:**
- "What do I do right now?" — between tasks, no orientation needed
- Resuming mid-session after a context switch

---

### `active` — Plan identity only

Just which plan is active and its goal. No task detail.

**Script invocation:** same as `status`.

**Return:** only the header lines — Plan path, Title, Goal, Progress count.
- If the output contains `--- Next task ---`, stop before that separator.
- If no separator is present (root PLAN.md mode — `_summarise_root_plan()` output), return the full output; it contains only orientation-level content with no task block.

**When to call:**
- Before `/gflow:predict` or `/gflow:scenario`: confirm the proposal fits the active scope
- Quick check: "are we in a superpowers plan or the root PLAN.md?"
- When context is long and you need a one-line anchor without task block noise

---

## What the script returns (reference)

- **Superpowers plan active:** file path · title · goal · progress (X/N steps complete) · next unchecked task block
- **No superpowers plan:** the first incomplete phase from `PLAN.md` (scope, sequence, definition of done)
- **All complete:** "All steps complete." — prompt user toward `/gflow:changelog` + `/gflow:release`

## Feature slug resolution

1. If the caller passed a slug (e.g. `shell-multi-prompt`), pass it as `--feature <slug>`.
2. Otherwise check conversation context for a feature name mentioned this session.
3. If neither, run the script without `--feature` (falls back to root `PLAN.md`).

---

## Integration

- **Claude Code:** invoke via `/gflow:status`, `/gflow:next`, or `/gflow:active` (thin wrappers around this skill).
- **Cursor / Aider / Codex:** include this file in context; call the variant by name.
- **Antigravity (`agy`):** read this file before asking "what's the next task?".
