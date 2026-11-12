---
name: plan
version: "1.0"
description: >
  Creates a structured task-by-task implementation plan for a gflow-cli feature.
  Gathers predict/scenario context, asks ≤3 clarifying questions, decomposes the
  feature into atomic committable tasks with step and test checklists, and writes
  docs/superpowers/plans/<YYYY-MM-DD>-<slug>/PLAN.md. Invoke after
  /gflow:predict returns GO or CAUTION and /gflow:scenario output is available.
---

# `plan` — Feature Plan Creator

Turns a feature description into a task-by-task implementation plan and writes it
to `docs/superpowers/plans/<YYYY-MM-DD>-<feature-slug>/PLAN.md`.

**Position in the gflow-cli workflow:**
```
/gflow:predict <proposal>   →  GO / CAUTION / STOP verdict
/gflow:scenario <feature>   →  edge cases + BDD skeleton
/gflow:plan <feature>       →  writes the task checklist  ← this skill
/gflow:status               →  surfaces next task during execution
/gflow:check                →  before each commit
```

---

## When to invoke

- After `/gflow:predict` returns GO or CAUTION
- When a backlog item in `PLAN.md` needs a concrete task breakdown before starting work
- Any feature larger than a single isolated file change

## When not to invoke

- Simple bug fixes (< 10 lines, no boundary crossing) — go straight to the fix
- Pure doc changes
- A task already fully specified in a superpowers plan — use `/gflow:status` to find it

---

## Protocol

### Phase 1 — Gather inputs from context

**From `/gflow:predict` output in context (do not ask if already present):**
- Verdict (GO / CAUTION) and confidence score
- Architectural constraints and module placement
- Security risks and mandatory mitigations
- Devil's Advocate simplifications or sequencing blockers

**From `/gflow:scenario` output in context (do not ask if already present):**
- Critical and High scenarios → these become must-cover tests in the task checklist
- BDD `Scenario:` blocks → seeds the BDD scaffold task

**From the feature description passed to this skill:**
- Feature name → derive a slug (lowercase, hyphen-separated, no dates)
- Stated goal (one sentence)

**From the repo — run once:**
```bash
uv run python scripts/dev/active_plan.py
```
Note the active phase name and its open tasks. Then read `PLAN.md` § "Phase status" and § "Decision log" directly to verify the proposed feature is within current scope and does not contradict an existing ADR. (The script shows the current task, not a backlog index — use `PLAN.md` for scope confirmation.)

### Phase 2 — Ask clarifying questions (only if not answerable from context)

Ask at most 3. Focus on decisions that materially change the task breakdown:

1. **Scope boundary** — what is explicitly out of scope for this plan?
2. **Module ownership** — which existing module does this extend, or is a new module justified?
3. **Acceptance criteria** — what does "done" look like from the user's perspective (command output, exit code, log event)?

Skip any question already answered by predict/scenario output or the feature description.

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
| 6 | **MCP surface mirror** | **Never optional when task 5 exists.** `mcp/tools.py` signature + docstring claims, the queued-path payload keys in `worker/codec.py`, `tests/mcp/test_cli_parity.py` for a new leaf. |
| 7 | Docs update | `USAGE.md`, `CONFIGURATION.md` (new env vars), `docs/MCP.md`, `KNOWN_ISSUES.md` if relevant. |
| 8 | Full gates + release prep | `/gflow:check` green; CHANGELOG updated. |

Adjust: not every task applies to every feature. Merge or split tasks as the scope demands —
**except task 6.** If the plan has a task 5, it has a task 6, because gflow ships every
capability twice and the automated gates cannot see the two drifting apart. A plan that
touches the CLI and has no MCP task is incomplete, not lean. The mirror axes are enumerated
once, in `skills/check/SKILL.md` step 1b; cite them, do not copy them.

**Engineering discipline — every plan carries it** (these are the project's working rules,
written here so every agent sees them; Uncle Bob's `ub-mode` skill, where installed, states
the same rules and is optional):

- **Make it work first: pin behaviour before changing it.** Task 1 is always the red test. For
  a refactor it is a **golden master** recorded on develop (signatures, source hashes of what
  moves, the observable outcomes a caller sees), and the plan says how you watched it fail.
- **Get the data shape right before the logic.** Domain types that make illegal states
  unrepresentable come before the code that uses them.
- **Build the lever.** When the work is repetitive (moving dozens of methods, editing many call
  sites), plan a task that builds the tool: do one unit by hand, then make the tool match it.
- **Verifiable units.** One task, one commit, tests green between commits. A refactor moves code;
  it does not rewrite bodies, error handling or log events in the same commit.
- **Measure what you claim to improve.** A refactor or speed plan has a task that records
  metrics **before** and **after**: CRAP per function (complexity x coverage, no function
  worse, new ones under 10), duplicate blocks (no new ones), and mutation testing on new
  wiring (surviving mutants are killed by a behaviour test or the dead code is deleted).
  Speed work follows [`optimize`](../optimize/SKILL.md).
- **Attack the premise.** When two fixes resting on the same assumption fail the same gate,
  stop and write that assumption down before a third attempt.
- **Keep a log.** Every plan ends with a **Decision log** (date, decision, why, what was
  rejected), updated as the work runs, including dead ends.

### Phase 4 — Draft the plan and show it to the user

Produce the full `PLAN.md` content using this schema:

```markdown
# <Feature Display Name> Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature <slug>` to find the next
> unchecked task. Implement one task at a time. Run `/gflow:check` before every commit.

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
- [ ] Task 1's red test (or golden master) was seen failing before the change
- [ ] Before/after metrics recorded in the PR (refactor and speed plans)
- [ ] Worktree clean: no untracked scratch files, no leftover clones or temp dirs

## Decision log

| Date | Decision | Why | Rejected |
|---|---|---|---|
| <YYYY-MM-DD> | <what was decided> | <evidence> | <alternative and why not> |
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

## Output example (header only)

```
# Batch Manifest Ledger Implementation Plan

> **For agentic workers:** Run `/gflow:status --feature batch-manifest-ledger` to
> find the next unchecked task. Implement one task at a time. Run `/gflow:check`
> before every commit.

**Goal:** Add a local SQLite ledger to `gflow video batch` so interrupted runs skip
already-completed items on resume.

**Predict verdict:** GO — confidence 8/10

**Risk register:**
| Severity | Risk | Mitigation |
|---|---|---|
| High | Schema migration on user's existing DB | Checksummed migration runner (already in data/) |
| Medium | Ledger path drift between runs | Normalize to absolute path at record time |
```

---

## Integration & Pipeline Continuation (Next Step Handoff)

- **Claude Code:** invoke via `/gflow:plan <feature>` (thin wrapper around this skill).
- **Cursor / Aider / Codex:** paste this file into your context and call `plan <feature>`.
- **Antigravity (`agy`):** include in system context before asking for a plan.
- **Next step:** Upon completing and user-approving `PLAN.md`, proactively announce: **"Implementation Plan approved. Next step: Phase 6 Task Execution (`/gflow:status --feature <slug>`)."**
