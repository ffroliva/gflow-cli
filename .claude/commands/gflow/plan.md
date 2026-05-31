---
description: Create a structured task-by-task implementation plan for a feature and write it to docs/superpowers/plans/.
---

# `/gflow:plan <feature>` — Create a feature plan

Turns a feature description into a task-by-task implementation plan and writes it to
`docs/superpowers/plans/<YYYY-MM-DD>-<feature-slug>/PLAN.md`.

**Typical position in the workflow:**
```
/gflow:predict <proposal>   →  GO / CAUTION / STOP verdict
/gflow:scenario <feature>   →  edge cases + BDD skeleton
/gflow:plan <feature>       →  writes the task checklist  ← you are here
/gflow:status               →  surfaces next task
/gflow:check                →  before each commit
```

Invoke after a GO or CAUTION verdict. Pass the feature description as `$ARGUMENTS`.

---

## Protocol

### Phase 1 — Gather inputs from context (do not ask if already present)

**From `/gflow:predict` output in context:**
- Verdict (GO / CAUTION / STOP) and confidence score
- Architectural constraints and module placement
- Security risks and mandatory mitigations
- Devil's Advocate simplifications or sequencing blockers

**From `/gflow:scenario` output in context:**
- Critical and High scenarios → these become must-cover tests in the task checklist
- BDD `Scenario:` blocks → seeds the BDD scaffold task

**From `$ARGUMENTS`:**
- Feature name → derive a slug (lowercase, hyphen-separated, no dates)
- Stated goal (one sentence)

**From the repo:**
```bash
uv run python scripts/dev/active_plan.py
```
Confirm the feature belongs to the active phase. Note any relevant ADRs from `PLAN.md`.

### Phase 2 — Ask clarifying questions (only if not answerable from context)

Ask at most 3. Focus on decisions that change the task breakdown:

1. **Scope boundary** — what is explicitly out of scope for this plan?
2. **Module ownership** — which existing module does this extend, or is a new module justified?
3. **Acceptance criteria** — what does "done" look like from the user's perspective (command output, exit code, log event)?

Skip any question already answered by predict/scenario output or `$ARGUMENTS`.

### Phase 3 — Decompose into tasks

**Task rules:**
- Each task must be independently committable as one atomic `git commit`.
- Test scaffold tasks (red tests, BDD skeleton) come before the code that makes them green.
- Tasks that create new files come before tasks that modify callers.
- Derive test requirements from scenario output: Critical → must-cover (`- [ ]`), High → should-cover.
- Every task lists: what it does, which files change, step checklist, test checklist.

**Typical task order for a gflow-cli feature:**

| # | Task | Notes |
|---|---|---|
| 1 | Unit test scaffold | Red tests only. No production code. |
| 2 | BDD scaffold | Red BDD scenarios. No production code. |
| 3 | Core implementation | Domain objects / value types / parsers. |
| 4 | Transport / API layer | `FlowApiClient` or `UiAutomationTransport` changes. |
| 5 | CLI surface | `cli_*.py` + Click commands + `--help` text. |
| 6 | Docs update | `USAGE.md`, `CONFIGURATION.md` (new env vars), `KNOWN_ISSUES.md` if relevant. |
| 7 | Full gates + release prep | `/gflow:check` green; CHANGELOG updated. |

Adjust: not every task applies to every feature. Merge or split tasks as the scope demands.

### Phase 4 — Draft the plan and show it to the user

Produce the full `PLAN.md` content using this schema:

```markdown
# <Feature Display Name> Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature <slug>` to find the next unchecked task.
> Implement one task at a time. Run `/gflow:check` before every commit.

**Goal:** <one sentence — the user-visible outcome>

**Architecture:** <2–3 sentences — which modules change, key design decisions, what stays the same>

**Predict verdict:** <GO / CAUTION — confidence N/10> (or "pending — run /gflow:predict first")

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| (from predict output) | | |

---

## File structure

### New files
\`\`\`
src/gflow_cli/<module>.py
  <one-line description>

tests/<module>/test_<module>.py
  <one-line description>
\`\`\`

### Modified files
\`\`\`
src/gflow_cli/<existing>.py
  <what changes>
\`\`\`

---

## Task 1 — <name> (test scaffold)

**What:** <one sentence>

**Files:**
- `tests/...` — <description>

**Steps:**
- [ ] <step>

**Tests created (red):**
- [ ] <test name> — <what it asserts>

---

## Task 2 — ...

(repeat for each task)

---

## Definition of done

- [ ] All task steps checked off
- [ ] `/gflow:check` green (ruff / format / pyright / pytest ≥ 80% coverage)
- [ ] `CHANGELOG.md` `[Unreleased]` section updated
- [ ] Docs updated (`USAGE.md` / `CONFIGURATION.md` as applicable)
- [ ] BDD feature file covers all Critical + High scenarios from `/gflow:scenario`
- [ ] No `# TODO` in diff without a tracked issue link
```

Show the drafted plan to the user. If they approve (or say "write it"), proceed to Phase 5.

### Phase 5 — Write the file

```bash
mkdir -p docs/superpowers/plans/<YYYY-MM-DD>-<slug>
```

Write the plan to `docs/superpowers/plans/<YYYY-MM-DD>-<slug>/PLAN.md`.

Confirm with:
> Plan written to `docs/superpowers/plans/<YYYY-MM-DD>-<slug>/PLAN.md`.
> Run `/gflow:status --feature <slug>` to start working on it.

---

## When to call

- After `/gflow:predict` returns GO or CAUTION
- When a backlog item in `PLAN.md` needs a concrete task breakdown before starting work
- Any feature larger than a single isolated file change

## When not to call

- Simple bug fixes (< 10 lines, no boundary crossing) — go straight to the fix
- Pure doc changes
- A task already fully specified in a superpowers plan — use `/gflow:status` to find it
