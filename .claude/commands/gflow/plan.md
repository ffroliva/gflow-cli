---
description: Show the current phase — scope, implementation sequence, and definition of done.
---

# `/gflow:plan` — Current phase

Read PLAN.md and return only the block marked **CURRENT FOCUS**. Do not return past or future phases unless asked.

## Steps

1. Read [PLAN.md](../../../PLAN.md)
2. Identify the phase marked **CURRENT FOCUS**
3. Return:
   - Phase name and number
   - Scope paragraph
   - Implementation sequence with per-step status
   - Definition of done checklist
   - Open questions relevant to current work (if any)

## When to call

- When starting a new task and unsure what's in scope
- When the user asks "what are we working on?" or "what's next?"
- Before adding a feature, to check it belongs to the current phase
