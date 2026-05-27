# gflow-cli Development Lifecycle Protocol — Design

**Date:** 2026-05-27
**Status:** Phase 1 — design ratified; ship-ready in 3 PRs.
**Author:** Flavio Oliva (with Claude Opus 4.7)
**Provenance:** validated through `/gflow:pr-council-review` v1 → v2 → v2.1 (PRs #97, #99, #100); informed by PR #83's reference-repo analysis.

> **Consolidation note:** per memory `[[release-spec-plan-memory-consolidation]]`, this spec is a project-management artifact. After the 3 implementation PRs ship, delete this file and extract durable patterns into memory (proposed slugs listed at the end).

---

## 1 · Goal

Apply the multi-dim council pattern at lifecycle phases beyond just PR review. Make system evolution more robust by:
- catching design-fit / risk / precedent issues **before** planning starts (assess phase)
- catching code issues **on the branch**, not only at PR review (branch review phase)
- formalizing **session handover** so context survives across sessions (deliver phase)

Out of scope: full guided orchestration (rejected as over-prescriptive vs `gsd:*` family). CI/CD integration of these commands (manual invocation only). Web UI / dashboard.

---

## 2 · 5-phase lifecycle map

| Phase | Existing tool (no change) | New (Phase 1) | Phase 2 router |
|---|---|---|---|
| 1. Assess | (gap — this design closes it) | **`/gflow:assess <task>`** | `/gflow:work assess` |
| 2. Plan | `superpowers:writing-plans`, `superpowers:brainstorming` | — | `/gflow:work plan` |
| 3. Develop | `superpowers:test-driven-development`, `superpowers:verification-before-completion` | — | `/gflow:work develop` (reminders only) |
| 4. Review | `/gflow:pr-council-review` (PR only) | **`/gflow:branch-review`** | `/gflow:work review` |
| 5. Deliver | `superpowers:finishing-a-development-branch`, `/gflow:release` | **`/gflow:handover`** (per PR #83 T5) | `/gflow:work deliver` |

---

## 3 · Phase 1 — 3 new library commands

All three follow the **wrapper-around-skill pattern** validated in v2 of `pr-council-review`:
- `~10-line` wrapper at `.claude/commands/gflow/<name>.md`
- canonical body at `skills/<name>/SKILL.md` (cross-tool portable)

### 3.1 `/gflow:assess <task-description>`

**Purpose:** multi-dim audit BEFORE planning. Surface fit / risk / precedent so we don't build the wrong thing.

**Input:** free-text task description (one sentence to one paragraph).

**Output:**
- `.planning/assess-<task-slug>.md` (per-finding tagged MUST / FORBID / NORM per PR #83 T2)
- `AskUserQuestion` offering: (a) save non-obvious findings as memory ADDs, (b) proceed to planning, (c) refine the task description and re-assess

**Council dimensions (5, baseline):**

| Dim | Name | What it asks |
|---|---|---|
| A1 | **Fit** | Does this align with the project mission (per `CLAUDE.md` + `README.md`)? Is it within the local-first / no-SaaS scope per `[[gflow-strategy-local-first]]`? |
| A2 | **Precedent** | Does memory show we've tried this before? Look for: prior approaches, known-issues, abandoned designs. Cite slug + outcome. |
| A3 | **Risk** | What could break? Touch the cardinal traps: `[[draft-pr-merge-trap]]`, `[[pr-must-verify-on-affected-surface]]`, `[[on-started-callback-recorder-safety]]`, `[[release-back-merge-gap-recovery]]`, etc. |
| A4 | **Effort** | Rough sizing: 1-PR / 1-day / 1-week / multi-week. Compare against PRs of similar scope in `git log`. |
| A5 | **Memory-Search** | Probe MCP memory servers (`mcp__*memory*`, `mcp__*mempalace*`, `mcp__*mem0*`, `mcp__*context-mode*`) per v2.1 D5 protocol. Enumerate relevant slugs, cite line ranges. |

**Specialized skill invocation:**
- A1 (Fit) and A3 (Risk) agents invoke `Skill: review` (single-agent design review framing)
- A3 (Risk) additionally invokes `Skill: security-review`

**Severity markers** (per PR #83 T2):
- **MUST** = the assessor's view is "do not skip" — non-negotiable for the task
- **FORBID** = pattern/approach is explicitly out of scope
- **NORM** = standard convention; deviation requires justification

**Verdict semantics** (mirrors council protocol):
- 🟢 GREEN — go, no concerns
- 🟡 YELLOW — proceed with the listed MUST items addressed in the plan
- 🔴 RED — do not start; fundamentals need re-thinking

### 3.2 `/gflow:branch-review`

**Purpose:** same council as `/gflow:pr-council-review` but on a local branch — catches issues before PR review.

**Input:** none required (defaults). Optional: `--base <ref>` (default `develop`), `--locale <bcp47>` for locale-relevant changes.

**Diff source:** `git diff <base>..HEAD` (NOT a PR).

**REVIEWED_SHA:** current `HEAD` (no remote pin — local).

**Council dimensions:** same as `pr-council-review` v2.1 (5 baseline D1-D5 + adaptive D6-D13).

**File reading discipline:** ⚠️ different from PR mode — sub-agents CAN use `Read` on the working tree IF `git branch --show-current` matches the orchestrator's captured branch (local files reflect HEAD on the same branch). They MUST still verify with `git show HEAD:<path>` if uncertain.

**Output:** verdict report identical to `pr-council-review` Phase 6, but without "post to PR" action — instead offers: (a) apply fixes now, (b) save findings as `.planning/branch-review-<branch>-<ts>.md`, (c) defer to PR-time review.

**Implementation note:** reuse `skills/pr-council-review/SKILL.md` by extending it with a "branch mode" section (rather than duplicating in a new skill). The wrapper at `.claude/commands/gflow/branch-review.md` invokes `Skill(skill="pr-council-review")` AND, in the same message, instructs the orchestrator to treat the invocation as branch mode (no skill-tool parameter mechanism exists; mode signaling is done in the wrapper's prompt body to Claude). **Decision recorded — sibling-file vs mode-on-existing-skill: chose mode for single-source-of-truth.**

### 3.3 `/gflow:handover` (PR #83 T5)

**Purpose:** capture session state at session end OR when pausing mid-work, so the next session resumes cleanly.

**Input:** optional `--reason <string>` (e.g. "context window full", "shipping for the night", "blocked on user input").

**Output:**
- structured handover markdown (printed to terminal + saved to `.planning/handover-<ts>.md`)
- updates to 7 memory drawers in `~/.claude/projects/<slug>/memory/` per PR #83 T5:
  - `drawer-project-state.md` — what we shipped this session
  - `drawer-open-handovers.md` — what's mid-flight
  - `drawer-reference-repositories.md` — any new external refs touched
  - `drawer-harness-decisions.md` — harness changes (skills, commands, agents)
  - `drawer-active-blockers.md` — anything stuck on user/external
  - `drawer-cross-session-rules-digest.md` — newly-learned rules to surface
  - `drawer-session-log.md` — chronological session activity (append-only)

**Note on drawers:** these are NEW per-drawer memory files, distinct from but complementary to PR #83's `MEMORY.md` initialization (PR #83 sets up the directory + index; this command creates the individual drawer files). The command creates them idempotently if absent. If a drawer file already exists, append; never overwrite.

**Severity markers** (per PR #83 T2) apply to entries in `drawer-cross-session-rules-digest.md` only.

---

## 4 · Phase 2 — `/gflow:work` thin router (documented, deferred)

**When to ship Phase 2:** only if Phase 1 usage shows users want a single entry point that walks all phases. Otherwise the library style is sufficient.

**Shape:**
```
/gflow:work <phase> [args]
  assess <task>   → /gflow:assess <task>
  plan            → invoke superpowers:writing-plans
  develop         → print reminder of TDD + verification skills
  review          → /gflow:branch-review (if no PR) OR /gflow:pr-council-review (if PR exists)
  deliver         → invoke superpowers:finishing-a-development-branch + /gflow:handover
```

**State persistence:** `.planning/work-<task-slug>.md` is a ledger of phase outputs:
```
TASK: <task description>
ASSESS: .planning/assess-<slug>.md (verdict: YELLOW, 2 MUST items)
PLAN: <link to plan>
BRANCH: feature/<name>
REVIEW: .planning/branch-review-<branch>-<ts>.md (verdict: GREEN)
PR: #<N>
HANDOVER: .planning/handover-<ts>.md
```

**Why deferred:** the existing `/gflow:*` commands are all library-style; adding orchestration is non-trivial to design well; YAGNI until usage proves the need.

---

## 5 · Cross-cutting design choices

### 5.1 Wrapper-around-skill pattern
All 3 Phase 1 commands follow the v2 precedent (PR #99): thin imperative wrapper at `.claude/commands/gflow/<name>.md` (invokes `Skill(skill="<name>")`); canonical body at `skills/<name>/SKILL.md` (cross-tool portable for Gemini/Codex/Cursor/Aider).

### 5.2 Severity markers — MUST / FORBID / NORM
Adopted from PR #83 T2 (`yzddp/harnesscode` reference repo). Apply to:
- `/gflow:assess` findings (mandatory per finding)
- `/gflow:handover` `drawer-cross-session-rules-digest.md` entries (mandatory)
- `/gflow:branch-review` findings (inherited from `pr-council-review` — currently free-text; future enhancement to formalize)

**Definitions** (this spec proposes the canonical wording, also captured in memory):
- **MUST**: required behavior. Deviation = bug.
- **FORBID**: explicitly disallowed behavior. Deviation = bug.
- **NORM**: convention. Deviation requires justification.

### 5.3 Memory drawers (PR #83 T5)
The 7 drawers form a stable taxonomy for handover state. Created idempotently by `/gflow:handover` on first run. NOT prescribed for other content — they coexist with the existing flat-memory pattern.

### 5.4 PR #83 inputs used as design references (not blocking dependencies)
- T1 (pi behavioral rules in AGENTS.md) — referenced from `/gflow:assess` A3 (Risk) dimension; if T1 ships, the rules become enforceable; if T1 doesn't ship, A3 still works via memory traversal
- T2 (MUST/FORBID/NORM markers) — adopted as the spec's severity model (above)
- T3 (handover template) — adopted as `/gflow:handover` output shape
- T4 (doc stability badges) — NOT adopted in this spec; orthogonal to lifecycle phases
- T5 (`/gflow:handover` command) — implemented as Phase 1.3 above

### 5.5 Dogfooding
Each implementation PR is reviewed by the council command it relates to:
- handover PR → `/gflow:pr-council-review` (v2.1)
- branch-review PR → use the new `/gflow:branch-review` locally before push, then `/gflow:pr-council-review` on PR
- assess PR → use the new `/gflow:assess` on a hypothetical follow-up task before PR

### 5.6 State persistence
- Assess + branch-review + handover all write to `.planning/` (gitignored)
- Handover ALSO writes to memory drawers (out-of-repo, per-user)
- Branch-review is otherwise read-only (no commits, no pushes, no PR comments)

---

## 6 · Implementation order (3 PRs)

**Order: handover → branch-review → assess.**

| # | PR | Why this order | Risk if wrong order |
|---|---|---|---|
| 1 | `/gflow:handover` | Smallest scope. Self-contained — depends only on PR #83 ideas (already public). Lets us validate the wrapper-around-skill template a third time. | None. |
| 2 | `/gflow:branch-review` | Mid scope. Extends `skills/pr-council-review/SKILL.md` with branch mode. Composes with the just-shipped handover. | If shipped before handover, no handover ledger to attach review output to. |
| 3 | `/gflow:assess` | Largest scope. Needs new 5-dim table + Risk-skill invocation + MUST/FORBID/NORM tooling. Composes with both handover (records assess in ledger) and branch-review (runs after assess as the pre-commit gate). | If shipped first, no place to log its output (no `.planning/` ledger yet). |

Each PR ~1 day; full series ~3-5 days with reviews.

---

## 7 · Testing strategy

- **No new pytest tests** required (skills/commands are markdown, not Python).
- **Dogfooding** (see §5.5).
- **Validation criteria for each PR:** the skill registers in `Skill` tool list, the wrapper is ≤15 lines, all memory slug references exist, all `git show` examples in the SKILL.md actually run.
- **Manual smoke**: after each PR, run the new command on a known-good test case (a closed PR, a previously-shipped task) and validate output shape.

---

## 8 · Memory updates (out-of-repo, applied alongside the PRs)

Proposed new memory slugs (created when relevant PR ships):
- `lifecycle-protocol-overview.md` — index of the 3 new commands + Phase 2 router roadmap
- `severity-markers-must-forbid-norm.md` — canonical definitions of MUST/FORBID/NORM (from §5.2)
- `handover-7-drawers.md` — the 7 memory drawers + idempotent creation contract

Existing memory slugs to update:
- `pr-council-review-portability-backlog.md` — note that branch-review is a sibling shipped before the meta-council Phase C
- `llm-council-pr95-v2-validation.md` — add forward link to lifecycle-protocol-overview

---

## 9 · Decisions log

| ID | Decision | Why |
|---|---|---|
| LP-1 | 3 library commands, not 1 orchestrator | Matches established `/gflow:*` mental model; YAGNI |
| LP-2 | `branch-review` is a mode on existing pr-council-review skill, not a sibling | Single source of truth; reduces drift risk |
| LP-3 | Adopt MUST / FORBID / NORM severity markers from PR #83 T2 | Already a proven taxonomy in `yzddp/harnesscode` reference |
| LP-4 | Adopt 7 memory drawers from PR #83 T5 | Same — proven elsewhere |
| LP-5 | Phase 2 router deferred (documented but not shipped) | YAGNI; ship Phase 1 first and see if usage warrants |
| LP-6 | No CI/CD integration | Manual invocation matches existing `/gflow:*` pattern |
| LP-7 | `.planning/` stays gitignored | Per existing convention; per `[[release-spec-plan-memory-consolidation]]` |
| LP-8 | Implementation order: handover → branch-review → assess | Smallest-first lets us validate the wrapper template; later PRs compose with earlier ones |
| LP-9 | Spec file should be DELETED after the 3 PRs ship | Per `[[release-spec-plan-memory-consolidation]]`; durable bits extracted to memory per §8 |

---

## 10 · Open questions for review

1. Should `branch-review` use a literal flag `--branch` on `pr-council-review`, or a separate slash command that internally invokes the same skill in branch mode? (Currently: separate command, same skill — see §3.2.)
2. Should the 7 memory drawers replace the existing flat-memory pattern over time, or coexist indefinitely? (Currently: coexist; drawers only for handover state.)
3. Should `/gflow:assess` block planning if verdict is RED, or just surface findings? (Currently: surface findings; user retains escape valve like the council's YELLOW dismiss option.)

---

**End of spec.**
