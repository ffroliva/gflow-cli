# Issue #15 Auth-Verification Fix — Orchestration Plan

> **Purpose:** define *which subagent does what, in what order, with which
> model, and how each is reviewed* — before any dispatch. This is the
> execution schedule for the implementation plan.

## Inputs

- **Spec:** `docs/superpowers/specs/2026-05-17-issue-15-auth-verification-fix-design.md` (Rev 2).
- **Implementation plan:** `docs/superpowers/plans/2026-05-17-issue-15-auth-verification-fix.md` — 7 tasks, complete code and tests inline.
- Both are twice-reviewed by agent councils and committed.

## Branch & workspace

- Execute on the **existing** branch `fix/issue-15-i2v-bearer-auth` (already cut from `develop`). No new worktree — the branch already isolates the work.
- Each task produces one or more **atomic commits** on that branch (the plan's tasks each end with a `git commit`).
- On completion: PR into `develop` (never `main`). `main` is protected.

## Roles

| Role | Who | Job |
|---|---|---|
| **Orchestrator** | This session (me) | Extracts each task's full text, dispatches subagents, answers their questions, runs the review loop, handles failures, tracks progress. Never edits code directly. |
| **Implementer** | Fresh `general-purpose` subagent, one per task | Implements one task TDD-style (test → red → code → green → commit), self-reviews, reports a status. |
| **Spec reviewer** | Fresh subagent, one per task | Confirms the code matches the task spec — nothing missing, nothing extra. |
| **Code-quality reviewer** | `everything-claude-code:python-reviewer`, one per task | Reviews the diff for quality, idioms, types, safety. |
| **Final reviewer** | `everything-claude-code:code-reviewer` | One whole-implementation review after all 7 tasks. |

Each subagent starts with **zero session history** — the orchestrator hands it exactly the context it needs (full task text, spec references, conventions, prior-task outcomes). Subagents never read the plan file themselves.

## Task inventory

| # | Task | Files | Implementer model | Depends on |
|---|---|---|---|---|
| T1 | `verification.py` — evaluation core (`FlowSessionOutcome`, `FlowSessionStatus`, `evaluate_session_response`) | create `verification.py`, `test_verification.py` | sonnet | — |
| T2 | `verification.py` — `verify_flow_session` async probe | modify `verification.py`, `test_verification.py` | sonnet | T1 |
| T3 | `errors.py` — `AuthMissingError` docstring refresh | modify `errors.py` | haiku | — |
| T4 | `real_chrome.py` — verify the Flow app session | modify `real_chrome.py`, `test_strategies.py` | sonnet | T2 |
| T5 | `internal_chromium.py` — live `/api/auth/session` poll | modify `internal_chromium.py`, `test_strategies.py` | sonnet | T1 |
| T6 | `KNOWN_ISSUES.md` — record the endpoint coupling | modify `KNOWN_ISSUES.md` | haiku | — |
| T7 | Full verification gate (5 CI gates + BDD + CHANGELOG) | modify `CHANGELOG.md` | sonnet | T1–T6 |

Reviewer subagents run on **sonnet**; the final whole-implementation review on **opus**.

## Dependency graph

```
T1 ──┬──> T2 ──> T4 ──┐
     │                ├──> T7
     └──> T5 ─────────┘
T3 ───────────────────┘   (independent)
T6 ───────────────────┘   (independent)
```

- **T2 needs T1** — same module; `verify_flow_session` builds on T1's enum/dataclass/`evaluate_session_response`.
- **T4 needs T2** — `real_chrome.py` imports `verify_flow_session`.
- **T5 needs T1** — `internal_chromium.py` imports `SESSION_API_URL`, `FlowSessionOutcome`, `evaluate_session_response`.
- **T4 and T5 both modify `test_strategies.py`** — they must not overlap; T4 runs before T5.
- **T3, T6** depend on nothing — isolated files.
- **T7 needs everything** — it runs the full quality gate over the finished code.

## Execution sequence

Implementation is **strictly sequential** — one implementer subagent at a time (parallel implementers would conflict on shared files and interleave commits). The order **T1 → T2 → T3 → T4 → T5 → T6 → T7** satisfies every dependency:

1. **T1** — foundation: the verification module's pure core.
2. **T2** — extends T1's module with the async probe.
3. **T3** — `errors.py` docstring (independent; slotted here so the `errors.py` touch precedes T4's new use of `AuthMissingError`).
4. **T4** — `real_chrome.py`, consuming `verify_flow_session`.
5. **T5** — `internal_chromium.py` (after T4 — shared `test_strategies.py`).
6. **T6** — `KNOWN_ISSUES.md` (independent; isolated file).
7. **T7** — full quality gate over the complete change, plus the `CHANGELOG.md` entry.

T3 and T6 are dependency-free; their slots are flexible, but they still run in the single sequential lane.

## Per-task protocol

For each task, in order:

1. **Dispatch implementer.** The orchestrator hands a fresh `general-purpose` subagent: the task's **full verbatim text** from the implementation plan (every step, every code block); scene-setting context (§ below); and the instruction to follow TDD and end with a status of `DONE` / `DONE_WITH_CONCERNS` / `NEEDS_CONTEXT` / `BLOCKED`.
2. **Answer questions.** If the implementer asks anything before/during work, the orchestrator answers fully, then the implementer proceeds.
3. **Implementer works:** writes the failing test → runs it (confirms red) → writes minimal code → runs it (confirms green) → lint/type-check → commits → self-reviews. Reports status + the commit SHA(s).
4. **Spec-compliance review.** Dispatch a fresh reviewer with the task spec + the commit diff. It confirms the code does exactly what the task specifies — nothing missing, nothing extra.
   - Issues found → re-dispatch the **same** implementer with the issue list → it fixes → re-review. Loop until ✅.
5. **Code-quality review.** Only after spec ✅. Dispatch `python-reviewer` on the diff.
   - Issues found → implementer fixes → re-review. Loop until ✅.
6. **Mark the task complete.** Proceed to the next task.

After T7: dispatch the **final whole-implementation reviewer** (opus) over the full branch diff, then invoke `superpowers:finishing-a-development-branch` to present merge/PR options.

## Context handed to each subagent

The orchestrator constructs, per subagent (they inherit nothing):

- **Implementer:** project one-liner (gflow-cli, Python CLI for Google Flow); the spec path for reference; the task's full text; the relevant conventions — TDD red→green→commit, `uv run` for all commands, `ruff` + `pyright --strict` on `src/`, Conventional Commits, **no `Co-Authored-By` trailer**, runtime output only under `tmp/`; and the **state from prior tasks** (e.g. for T2: "`verification.py` already exists with `FlowSessionOutcome`, `FlowSessionStatus`, `evaluate_session_response` from T1").
- **Spec reviewer:** the task's spec text + the commit SHA(s) to inspect. Read-only.
- **Code-quality reviewer:** the commit SHA range + the project conventions. Read-only.

## Failure handling

| Implementer status | Orchestrator action |
|---|---|
| `DONE` | Proceed to spec review. |
| `DONE_WITH_CONCERNS` | Read concerns; if about correctness/scope, resolve before review; if observational, note and proceed. |
| `NEEDS_CONTEXT` | Supply the missing context, re-dispatch the same task. |
| `BLOCKED` | Diagnose: context gap → re-dispatch with more context; needs deeper reasoning → re-dispatch on a stronger model; task too large → split it; **plan itself is wrong → stop and escalate to the human.** |

A reviewer finding issues is **not** a failure — it triggers the fix→re-review loop. Never proceed with an open issue from either review.

## Human checkpoints

- **Now:** review and approve this orchestration plan.
- **During execution:** none — once approved, all 7 tasks run **continuously**, no check-ins between tasks. The only mid-run stop is an unresolvable `BLOCKED` or a discovered plan defect.
- **End:** the final whole-implementation review, then `finishing-a-development-branch` presents the merge/PR decision for approval.

## Estimated subagent count

7 implementers + ~7 spec reviewers + ~7 code-quality reviewers + review-loop re-dispatches + 1 final reviewer ≈ **22–30 subagent invocations**. Cost is front-loaded into review; it catches issues before they compound.
