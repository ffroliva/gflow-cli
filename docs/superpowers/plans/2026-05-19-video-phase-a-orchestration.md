# Video Phase A — Execution Orchestration Plan

> **Companion to** `docs/superpowers/plans/2026-05-19-video-phase-a-t2v.md` (rev 2, post-council).
> This document specifies **how** to execute that plan with subagents: the agent
> topology, the dispatch sequence, model assignment, and stop conditions. It
> follows the `superpowers:subagent-driven-development` skill.

**Goal:** Execute the 14-task Phase A plan task-by-task — fresh implementer subagent per task, two-stage review (spec compliance, then code quality) after each — without polluting the orchestrator's context, and complete with a whole-branch final review.

**Who runs this:** the **Orchestrator** — the main Claude Code session (Opus 4.7). The Orchestrator holds the plan, dispatches every subagent, handles their return status, and drives the review loops. Subagents are leaves: they never dispatch each other.

---

## 1. Agent topology — who calls whom

```
                        ┌─────────────────────────┐
                        │      ORCHESTRATOR       │  (main session, Opus 4.7)
                        │  holds plan + TodoWrite │
                        │  dispatches everything  │
                        └────────────┬────────────┘
                                     │  per task N = 1..14, strictly sequential
            ┌────────────────────────┼────────────────────────┐
            ▼                        ▼                        ▼
   ┌─────────────────┐     ┌────────────────────┐    ┌──────────────────────┐
   │ IMPLEMENTER(N)  │ ──▶ │ SPEC REVIEWER(N)   │──▶ │ CODE-QUALITY         │
   │ TDD, commits    │     │ "built exactly     │    │ REVIEWER(N)          │
   │ self-reviews    │     │  the task?"        │    │ "well-built?"        │
   └─────────────────┘     └────────────────────┘    └──────────────────────┘
        ▲     │ ❌ fix loop        ▲     │ ❌ fix loop        ▲      │ ❌ fix loop
        └─────┘                    └─────┘                   └──────┘
                                     │ all 14 tasks ✅
                                     ▼
                        ┌─────────────────────────┐
                        │  FINAL REVIEWER          │  (whole-branch diff, Opus)
                        └────────────┬────────────┘
                                     ▼
                  superpowers:finishing-a-development-branch
                  (optional: user runs /ultrareview on the branch)
```

- **The Orchestrator is the only dispatcher.** Implementer and reviewer subagents have isolated context — they receive exactly what the Orchestrator hands them (full task text, scene-setting, SHAs), never this session's history. They report back; they do not call other agents.
- **Strictly sequential.** Exactly one Implementer is in flight at a time (the red-flag rule: never run implementers in parallel — they would collide on files). See §3 for why Phase A has no task-level fan-out.
- **Review is gated:** code-quality review starts only after spec compliance is ✅.

---

## 2. Roles

| Role | Responsibility | Tool template |
|---|---|---|
| **Orchestrator** | Extract all 14 tasks up front, build TodoWrite, dispatch each subagent, answer implementer questions, handle return status, drive review loops, mark tasks done. Never edits code itself. | — (this session) |
| **Implementer(N)** | Implement Task N exactly as written — TDD per the task's red→green steps, run the quality gate, commit, self-review. Asks questions *before* and *during* work. Reports `DONE` / `DONE_WITH_CONCERNS` / `BLOCKED` / `NEEDS_CONTEXT`. | `subagent-driven-development/implementer-prompt.md` |
| **Spec Reviewer(N)** | Independently read the committed code and verify it matches Task N — nothing missing, nothing extra. Does **not** trust the implementer's report. Returns ✅ or ❌ with `file:line`. | `subagent-driven-development/spec-reviewer-prompt.md` |
| **Code-Quality Reviewer(N)** | After spec ✅: review the task's commit range for clarity, tests-verify-behavior, file responsibility, pattern adherence. Returns Strengths / Issues (Critical/Important/Minor) / Assessment. | `requesting-code-review/code-reviewer.md` via `code-quality-reviewer-prompt.md` |
| **Final Reviewer** | After all 14 tasks: review the whole-branch diff (base = commit before T1) against the plan's goal and the spec §10.3 Phase A scope. | `requesting-code-review` |

---

## 3. Execution graph — why Phase A is a sequential chain

The 14 tasks form a **single dependency chain** — there is no task-level parallelism, and the orchestration must not invent any:

```
T1 → T2 → T3 → T4 → T5 → T6 → T7 → T8 → T9 → T10 → T11 → T12 → T13 → T14
└──── retire HTTP path ────┘└─ video.py ─┘└──── ui_automation_video.py ───┘└docs
```

- **T1→T5 (retire the dead path)** is a deliberately ordered chain — the plan sequenced it so the test tree stays green after *every* task (collection never breaks). Reordering breaks that invariant.
- **T6→T8** all build up `video.py` / `test_video.py` — each appends to what the prior produced.
- **T9→T13** all build up the new `ui_automation_video.py` — each appends to the same file; T13 also wires `ui_automation.py`.
- **T14** (docs) reflects the final state, so it runs last.

**Consequence:** the `dispatching-parallel-agents` fan-out pattern does **not** apply to task execution here. The value of subagent-driven execution for Phase A is *not* speed-through-parallelism — it is **fresh context per task** (no drift across 14 tasks) and **enforced two-stage review** at every step. The only concurrency in the whole run is the council-style **final review** (§7), where independent reviewers *can* run in parallel.

---

## 4. Model assignment — what model does what

Per the skill: cheapest model that can do the job; capable models for integration and review. Task-type drives the choice.

| Task | What it is | Implementer | Spec Reviewer | Quality Reviewer |
|---|---|---|---|---|
| T1 stub `cli_video.py` + BDD delete | mechanical, multi-file, verbatim code | **Sonnet** | Sonnet | Sonnet |
| T2 delete dead tests/scripts | pure deletion | **Haiku** | Haiku | Haiku |
| T3 remove `client.py` methods | deletion + import/ruff care | **Sonnet** | Sonnet | Sonnet |
| T4 remove `dto` classes + `__init__` | pure deletion | **Haiku** | Haiku | Haiku |
| T5 reduce `video.py` to value objects | full file content given | **Haiku** | Sonnet | Sonnet |
| T6 `GenerateVideoRequest` + validation | TDD, code given, 1 file + test | **Sonnet** | Sonnet | Sonnet |
| T7 `VideoStatus` value object | trivial TDD, code given | **Haiku** | Haiku | Sonnet |
| T8 response parsers | TDD vs captured JSON | **Sonnet** | Sonnet | Sonnet |
| T9 new module: typing contract + listener | subtle pyright-strict contract | **Sonnet** | Sonnet | **Opus** |
| T10 status listener + poll loop | real logic (stall detection) | **Sonnet** | Sonnet | **Opus** |
| T11 selectors + cascade + mode switch | multi-helper, code given | **Sonnet** | Sonnet | Sonnet |
| T12 output-count + aspect helpers | code given | **Sonnet** | Sonnet | Sonnet |
| T13 `generate_video` + mix into transport | **integration** — 2 files, MRO, pyright | **Opus** | **Opus** | **Opus** |
| T14 docs (PLAN/CHANGELOG/README) | mechanical doc edits | **Haiku** | Haiku | Haiku |
| Final review | whole-branch, goal-backward | — | — | **Opus** |

**Rationale signals:** T13 is the one true integration task (touches `ui_automation_video.py` *and* `ui_automation.py`, wires the mixin MRO, must pass `pyright --strict`) → Opus end-to-end. T9/T10 implementers get Sonnet (the plan hands them verbatim code) but Opus *quality* review (the typing host-contract and the poll-loop stall logic are where subtle bugs hide). Pure-deletion and doc tasks → Haiku. Set the model via the `Agent` tool's `model` parameter per dispatch.

**Escalation overrides the table:** if an implementer returns `BLOCKED` for lack of reasoning power, re-dispatch one tier up (Haiku→Sonnet→Opus). Never re-dispatch the same model on a reasoning-blocked task without changing something.

---

## 5. The per-task protocol

For each Task N, 1 → 14, the Orchestrator runs this loop. **Do not pause to check in with the human between tasks** (skill: continuous execution) — only stop on the §6 conditions.

1. **Prepare.** Take Task N's full text (already extracted from the plan). Record `BASE_SHA = git rev-parse HEAD`.
2. **Dispatch Implementer(N)** (`Agent`, `general-purpose`, model per §4) using `implementer-prompt.md`. The prompt MUST contain:
   - The **full verbatim text of Task N** from the plan (every step, every code block — never make the subagent open the plan file).
   - **Scene-setting context** (§6 below): which file(s), where Task N sits in the chain, the relevant Prerequisites + Deviations, the green-tree invariant for T1-T5.
   - The working directory (the worktree from §6 pre-flight).
3. **If the implementer asks questions** → answer from the plan/spec, re-dispatch. (Rare — the plan is fully specified and placeholder-free.)
4. **Handle the return status:**
   - `DONE` → go to step 5.
   - `DONE_WITH_CONCERNS` → read the concerns. Correctness/scope concern → resolve before review. Observation only → note it, proceed.
   - `NEEDS_CONTEXT` → supply the missing context, re-dispatch (same model).
   - `BLOCKED` → context gap: re-dispatch with more context. Reasoning gap: re-dispatch one model tier up. Task too large: split it and renumber locally. Plan is wrong: **stop, escalate to the human.**
5. **Dispatch Spec Reviewer(N)** (`spec-reviewer-prompt.md`, model per §4). Give it Task N's full text + the implementer's report. It reads the *committed code* and verifies — including that the task's quality-gate commands were actually run and passed.
   - ❌ issues → dispatch a fresh Implementer(N) with the specific findings → re-run Spec Reviewer. Loop until ✅.
6. **Dispatch Code-Quality Reviewer(N)** (`code-quality-reviewer-prompt.md`, model per §4) with `BASE_SHA` and `HEAD_SHA = git rev-parse HEAD`.
   - Critical/Important issues → fresh Implementer(N) fixes → re-run the quality reviewer. Loop until approved. (Minor issues: fix if cheap, else log.)
7. **Mark Task N complete** in TodoWrite. Proceed to Task N+1.

**Never:** start quality review before spec ✅; move to N+1 with an open issue on N; run two implementers at once; let an implementer's self-review substitute for the two review stages.

---

## 6. Pre-flight (before Task 1)

1. **Resolve the uncommitted change.** `git status` shows `M tests/api/transports/test_ui_automation.py`. Commit or stash it — the plan's Prerequisites require this so task commits stay atomic.
2. **Isolated workspace.** Invoke `superpowers:using-git-worktrees` to create a worktree branched from `chore/video-wire-captures` (PR #23 is the integration target). All 14 tasks' commits land there.
3. **Baseline gate.** Run the full 5-command quality gate once on the clean worktree to confirm a green starting point — so any later failure is attributable to a task, not pre-existing.
4. **Extract & track.** Read the plan once; extract all 14 tasks with full text; create a TodoWrite with 14 items.
5. **Scene-setting context** to fold into every implementer prompt: the project is `gflow-cli` (CLAUDE.md conventions — TDD, `pyright --strict`, structlog, Conventional Commits, **no `Co-Authored-By`**); Phase A retires a 401-dead HTTP video path and builds T2V on `UiAutomationTransport`; the plan is rev 2 (post-council) and its "Deviations" section is binding (e.g. the `VideoGenerationMixin` host-contract typing, listeners returned with handles). For T1-T5 stress the **green-tree-after-every-task** invariant.

---

## 7. Final stage (after Task 14)

1. **Whole-branch final review.** Dispatch the **Final Reviewer** (Opus) over the diff from the pre-T1 baseline to HEAD — goal-backward: does the branch deliver spec §10.3 Phase A (retired HTTP path; T2V `generate_video` working; `video.py` value objects + parsers; `ui_automation_video.py` mixin), and is the full quality gate green?
2. **Optional second council pass.** For high assurance, re-run the §-style 5-dimension review (correctness/completeness/compliance/robustness/security) as parallel reviewers on the finished branch — this *is* a valid `dispatching-parallel-agents` fan-out (the reviewers are independent).
3. **Cloud review.** The user may run **`/ultrareview`** on the branch (or `/ultrareview 23` for PR #23) — a billed, user-triggered multi-agent cloud review. The Orchestrator cannot launch it; surface it as a recommended human step.
4. **Finish.** Invoke `superpowers:finishing-a-development-branch` to choose merge / PR-update / cleanup for the worktree branch into `chore/video-wire-captures` (PR #23).

---

## 8. Stop conditions

The Orchestrator runs continuously through all 14 tasks. It stops **only** for:

- **Unresolvable `BLOCKED`** — an implementer is stuck and the blocker is not context/model/size (i.e. the plan itself is wrong). Escalate to the human with specifics.
- **Genuine ambiguity** — something in the plan that genuinely prevents progress and is not answerable from the plan or spec.
- **All 14 tasks complete** — proceed to §7.

A spec or quality reviewer finding issues is **not** a stop condition — it is the normal fix-loop (§5 steps 5-6).

---

## 9. Cost shape

14 tasks × (1 implementer + 1 spec reviewer + 1 quality reviewer) = **42 base subagent invocations**, plus review-loop re-dispatches and 1 final reviewer. Model mix keeps it economical: ~5 tasks on Haiku, ~7 on Sonnet, T13 + the subtle quality reviews on Opus. The two-stage review trades subagent invocations for defects caught at the cheapest possible point — before they compound across the sequential chain.
