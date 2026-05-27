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

### 1.1 · Position vs `gsd:*` (rationalized — not duplicating)

The post-survey audit (§A) flagged ~70% interface overlap between `/gflow:assess` and `gsd:list-phase-assumptions` + `gsd:discuss-phase`, and ~80% overlap between `/gflow:handover` and `gsd:pause-work` + `gsd:resume-work`. We are NOT deferring to GSD because:

- **Task-scoped, not phase-scoped.** `gsd:*` imposes a milestone → phase → sub-phase numbering hierarchy with `PROJECT.md` routing. We work in PR-as-unit-of-work + memory drawers; phase numbers would duplicate `git log` and conflict with `[[release-spec-plan-memory-consolidation]]` (delete spec/plan after release).
- **Memory-drawer-native, not `.planning/`-tree-native.** GSD persists state under a per-phase directory tree. Our 7 drawers live in `~/.claude/projects/<slug>/memory/` and auto-load every session.
- **Compose-friendly, not framework-style.** Each of our 3 commands is independently invokable from the existing `/gflow:*` mental model.

`/gflow:branch-review` has no GSD analog at all (council pattern is ours; see §A.1).

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

| Dim | Name | What it asks | Mandatory memory slugs |
|---|---|---|---|
| A1 | **Fit** | Does this align with the project mission (per `CLAUDE.md` + `README.md`)? Is it within the local-first / no-SaaS scope per `[[gflow-strategy-local-first]]`? | `[[gflow-strategy-local-first]]`, `[[readme-hybrid-router-pattern]]` |
| A2 | **Precedent** | Does memory show we've tried this before? Look for: prior approaches, known-issues, abandoned designs. Cite slug + outcome. | (none mandatory; depends on task surface) |
| A3 | **Risk** | What could break? Touch the cardinal traps. | `[[pr-must-verify-on-affected-surface]]`, `[[on-started-callback-recorder-safety]]`, `[[draft-pr-merge-trap]]`, `[[release-back-merge-gap-recovery]]`, `[[verification-ledger-5-layer]]` |
| A4 | **Effort** | Rough sizing: 1-PR / 1-day / 1-week / multi-week. Compare against PRs of similar scope in `git log`. If no comparable PR exists (fresh feature surface, pre-first-PR work), mark A4 as **INSUFFICIENT-DATA**; do not block a GREEN verdict. | `[[release-spec-plan-memory-consolidation]]` |
| A5 | **Memory-Search** | Probe MCP memory servers. **Before invoking any `mcp__*memory*` / `mcp__*mempalace*` / `mcp__*mem0*` tool, call `ToolSearch` with `select:<tool_name>` to load the schema (most MCP memory tools are deferred-tools per the harness convention)**. If no MCP memory server is loaded in-session, state explicitly "file-based memory only" and proceed with `~/.claude/projects/<slug>/memory/` filesystem reads. | `[[release-spec-plan-memory-consolidation]]`, `[[llm-council-code-review-pr93]]` (council-pattern provenance), `[[pr-council-review-portability-backlog]]` |

**Iron Law** (per `superpowers:verification-before-completion`, composes with §5.9 status protocol): *NO VERDICT WITHOUT EVERY DIMENSION HAVING SUBMITTED A STATUS — one of `DONE` / `DONE_WITH_CONCERNS` / `NEEDS_CONTEXT` / `BLOCKED` — AND A5 (Memory-Search) HAVING CITED AT LEAST ONE SLUG OR EXPLICITLY DECLARED `NO_PRIOR_ART`.* Sub-agent timeouts that produce neither status nor citation must NOT be rationalized as GREEN — the synthesizer marks them `BLOCKED` and downgrades the consensus per §5.9 (silent ignore is forbidden).

**Per-finding citation contract** (mirrors `pr-council-review` v2.1 verify-before-claim): every finding MUST cite `file:line` (for code) or memory `slug + line range` (for prior art). Orphan findings (no citation) are dropped at synthesis.

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

**File reading discipline:** ⚠️ different from PR mode — sub-agents CAN use `Read` on the working tree IF `git branch --show-current` matches the orchestrator's captured branch (local files reflect HEAD on the same branch). They MUST still verify with `git show HEAD:<path>` if uncertain. **For large diffs (`additions + deletions > 5000` OR > 20 files), route `git diff base..HEAD` through `ctx_execute(language="shell", ...)` to avoid context flood — per `context-mode:context-mode` routing rule.**

**`release/*` branch special case:** if `git branch --show-current` matches `release/*`, the council's D11 (Release-gate compliance) will RED-flag an in-progress CHANGELOG / version bump. Treat D11 as **YELLOW not RED until tag is cut** — surface this downgrade in the verdict report so users don't chase a false negative. **"Tag is cut" detection:** `git tag --list "v*" --contains HEAD` returns non-empty (per D3 v1 council ambiguity finding). Do NOT use `gh release list` (requires network) or branch-name parsing.

**REVIEWED_SHA drift in branch mode** (mirrors `pr-council-review` v2.1 §5 step 5 for PR mode): capture `REVIEWED_SHA = $(git rev-parse HEAD)` BEFORE dispatching agents. At Phase 5 synthesis, compare `git rev-parse HEAD` to `REVIEWED_SHA`; if diverged (user committed mid-review, ran `git stash`, etc.), prepend the report with: *"Local HEAD moved during review (was X, now Y). Findings apply to X."* Do NOT silently re-review the new commits.

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

**Idempotent reconstruction** (adapted from `gsd:resume-work`): if any drawer file is missing on a subsequent run, the command auto-reconstructs it as empty (with frontmatter only). A missing drawer must NOT abort the handover.

**Drawer rotation policy:** `drawer-session-log.md` is append-only and would grow unbounded. **Rotate at 500 lines** — when the file crosses 500 lines, rename to `drawer-session-log-archive-<YYYY-MM>.md` and start a fresh `drawer-session-log.md`. Archives are not auto-loaded next session (only the current `drawer-session-log.md` is). Other drawers do NOT auto-rotate (they should stay small by design).

**Iron Law** (per `superpowers:verification-before-completion`): *NO HANDOVER WITHOUT VERIFIED HEAD SHA + CLEAN GIT STATUS.* If `git status --porcelain` is non-empty and no `--reason <string>` was passed, refuse to write drawers — uncommitted work captured as "session state" would mislead the next session.

**Drawer auto-load contract** (CRITICAL — drawers are dead weight without this; D6 v1 council audit found that the harness only auto-loads `MEMORY.md` by default, not arbitrary `.md` files in the memory dir): on every successful handover, the command MUST patch `~/.claude/projects/<slug>/memory/MEMORY.md` to ensure a `## Drawers` section exists and contains `[[drawer-project-state]]`-style wikilinks to every drawer file. Without this `MEMORY.md` patch, the next session's auto-memory loader will not surface drawer content. The patch is idempotent (only adds missing links; does not duplicate existing ones).

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

**Cross-tool portability is read-only** (per D4 v1 council audit): other tools (Gemini CLI, Cursor, Aider, Codex) can consume the SKILL.md as a reference document — they will read the protocol, dimension lists, and per-command checks. However, the structural enforcement (`<HARD-GATE>` / `<EXTREMELY-IMPORTANT>` XML blocks honored as bulletproofing) and the composition mechanism (`Skill(skill="...")` invocations in §A.3) are **Claude-Code-specific** — other tools will see them as prose hints but won't structurally enforce them. Plan for graceful degradation: the protocol's intent (verify-before-claim, council pattern, status taxonomy) is portable; the gates are not.

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

### 5.7 Composition with existing commands (resolves 5 hidden coupling risks)

Surfaced by the pre-council survey (§A.3):

| Risk | Resolution |
|---|---|
| `/gflow:handover` writes `.planning/handover-<ts>.md` then `/gflow:check` could reformat it | `.planning/` is gitignored already; explicitly state that `/gflow:check`'s lint/format pass MUST exclude `.planning/`. Handover output is read-only post-write. |
| `/gflow:assess` A4 (Effort) compares against `git log` on a fresh repo with no comparable PRs | A4 reports `INSUFFICIENT-DATA` and does NOT block a GREEN verdict (see §3.1 table). |
| `/gflow:branch-review` runs on a `release/*` branch mid-release; D11 RED-flags WIP CHANGELOG | D11 downgrades RED → YELLOW until release tag is cut (see §3.2). |
| Drawer files grow unbounded over many sessions | `drawer-session-log.md` rotates at 500 lines; archives are not auto-loaded (see §3.3). |
| Phase 2's `/gflow:work` could nest dispatch via `Skill` → council sub-agents → `Skill: security-review` → its own sub-agents (3-level recursion) | Phase 2 router MUST use **direct command invocation** (`/gflow:assess <task>`, not `Skill(skill="assess")`) to avoid nested-dispatch recursion limits. Documented in §4. |

### 5.8 Structural enforcement — `<HARD-GATE>` and `<EXTREMELY-IMPORTANT>` XML conventions

Adopted from `superpowers:brainstorming` and `superpowers:using-superpowers`. These XML-tagged blocks survive prompt compression and pressure scenarios better than prose ("the bulletproofing pattern").

**ALL 3 new SKILL.mds open with this `<EXTREMELY-IMPORTANT>` block verbatim:**

```xml
<EXTREMELY-IMPORTANT>
These commands are local-only side-channels. They do NOT replace
`/gflow:pr-council-review`, `/gflow:check`, or `/gflow:release`.
Skipping the canonical gates because an assess / branch-review /
handover passed is a rationalization. Do NOT skip.
</EXTREMELY-IMPORTANT>
```

**Per-command HARD-GATES — must be written as actual XML blocks in each SKILL.md (not markdown rows; the XML tagging is the enforcement mechanism per the bulletproofing thesis):**

```xml
<!-- /gflow:assess SKILL.md -->
<HARD-GATE>
Do NOT emit a verdict until all 5 dimensions have submitted a status
(DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED) AND A5 has cited
at least one memory slug OR explicitly declared NO_PRIOR_ART.
</HARD-GATE>

<HARD-GATE>
Do NOT invoke `/gflow:plan` or `superpowers:writing-plans` after a RED
verdict. The user MUST refine the task description and re-assess first.
</HARD-GATE>
```

```xml
<!-- /gflow:branch-review SKILL.md -->
<HARD-GATE>
Do NOT run the council before `/gflow:check` passes (lint + format +
types + tests must be green). Pre-existing red baseline will be
mis-flagged as new findings.
</HARD-GATE>

<HARD-GATE>
Do NOT post findings to a PR — this command is local-only. Use
`/gflow:pr-council-review <N>` for PR-time review.
</HARD-GATE>
```

```xml
<!-- /gflow:handover SKILL.md -->
<HARD-GATE>
Do NOT write to memory drawers if `git status --porcelain` is non-empty
AND no `--reason <string>` flag was passed. Uncommitted work captured
as session state will mislead the next session.
</HARD-GATE>

<HARD-GATE>
Drawer files are APPEND-ONLY. NEVER overwrite. On any write, use
`open(path, "a")` (POSIX O_APPEND semantics — single-syscall appends
under PIPE_BUF are atomic). On Windows, prefer
`pathlib.Path.open("a", encoding="utf-8", newline="\n")`.
</HARD-GATE>
```

**Structural enforcement hook (per D6 NH#6):** synthesizers MUST emit a literal `HARD-GATE FAILED: <reason>` as the first line of output when a gate trips. Wrappers grep for this prefix to abort downstream invocation. Without this hook, gates are advisory not enforceable.

**Gate context table** (rationale for each gate):

| Command | Gate | Rationale |
|---|---|---|
| `/gflow:assess` | Status-before-verdict | Mirrors `[[verification-ledger-5-layer]]`; prevents silent-ignore of sub-agent timeouts |
| `/gflow:assess` | No-plan-on-RED | Mirrors `superpowers:brainstorming` pre-implementation gate; resolves §10 Q3 |
| `/gflow:branch-review` | Green-baseline-first | Adopts `superpowers:finishing-a-development-branch` Step 1 |
| `/gflow:branch-review` | No-PR-post | Forces the dogfooding split in §5.5 to be enforced not documented |
| `/gflow:handover` | Clean-tree-or-reason | Closes dirty-tree-as-intentional-state loophole |
| `/gflow:handover` | Append-only-drawers | Promotes the §3.3 prose rule to structural enforcement |

### 5.9 Sub-agent status reporting (4-status protocol)

Adopted from `superpowers:subagent-driven-development`. Each council sub-agent (in `/gflow:assess` and `/gflow:branch-review`) reports both a **verdict** (GREEN / YELLOW / RED) AND a **status** (orthogonal axis):

| Status | Meaning | Synthesizer action |
|---|---|---|
| `DONE` | Dimension reviewed cleanly | Use verdict as-is |
| `DONE_WITH_CONCERNS` | Reviewed but the agent had reservations about its own confidence | Use verdict, but downgrade GREEN → YELLOW automatically |
| `NEEDS_CONTEXT` | Agent couldn't complete due to missing context (couldn't find a file, ambiguous prompt) | Mark dimension `UNKNOWN`, downgrade consensus by one step (per `pr-council-review` v2.1 §5 step 2) |
| `BLOCKED` | Agent failed (timeout, dispatch error, tool unavailable) | Same as `NEEDS_CONTEXT`; surface for re-dispatch |

**Severity action-tier** (companion to MUST / FORBID / NORM per §5.2; adopted from `superpowers:requesting-code-review`): each finding ALSO carries an action-tier:

- **Critical** — fix immediately (blocks merge / handover)
- **Important** — fix before proceeding to next phase
- **Minor** — note for later (does not block)

So a complete finding is `<SEVERITY> <ACTION-TIER>` — e.g. `MUST Critical: missing test coverage on affected surface`, or `NORM Minor: comment density above project convention`.

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

1. ~~Should `branch-review` use a literal flag `--branch` on `pr-council-review`, or a separate slash command that internally invokes the same skill in branch mode?~~ **Resolved by LP-2**: separate slash command (`/gflow:branch-review`), same skill body (extended with mode signaling in the wrapper's prompt body). LP-2 is the canonical decision; this question previously contradicted it (per D2 v1 council audit).
2. Should the 7 memory drawers replace the existing flat-memory pattern over time, or coexist indefinitely? (Currently: coexist; drawers only for handover state.)
3. Should `/gflow:assess` block planning if verdict is RED, or just surface findings? **Resolved by §5.8 HARD-GATE**: RED blocks `/gflow:plan` invocation; user can override via explicit re-assess (refine task description, re-run).
4. **(NEW from survey, §A.2)** Should `/gflow:assess` invoke `gsd:list-phase-assumptions` or `gsd:discuss-phase` as a sub-skill when the repo has a `.planning/` tree, instead of fully reimplementing the gray-area probe? Currently: no — keep `/gflow:assess` task-scoped per §1.1. Revisit if GSD adoption grows in the project.

---

## A · Appendix — Survey integration record

Three parallel surveys (gsd, superpowers, gflow/other) produced ~40 findings. The high-value subset was applied directly to the spec sections above; the remainder is captured here for the implementation plan to consume.

### A.1 Patterns ADOPTED into the spec

| Source skill | Pattern | Spec section now containing it |
|---|---|---|
| `superpowers:verification-before-completion` | Iron Law block | §3.1 (assess), §3.3 (handover) |
| `superpowers:subagent-driven-development` | 4-status protocol (DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED) | §5.9 |
| `superpowers:requesting-code-review` | Severity action-tiers (Critical / Important / Minor) | §5.9 |
| `superpowers:brainstorming` + `using-superpowers` | `<HARD-GATE>` / `<EXTREMELY-IMPORTANT>` XML conventions | §5.8 |
| `superpowers:finishing-a-development-branch` | Step-1 baseline verification gate | §5.8 (branch-review HARD-GATE) |
| `gsd:research-phase` | Downstream consumer contract (named consumers + required headings) | §3.1 output schema (implicit via slug table + citation contract) |
| `gsd:execute-phase` + `map-codebase` | Orchestrator-stays-lean wave (sub-agents write to disk, return path) | (deferred to implementation plan — too granular for spec) |
| `gsd:resume-work` | Idempotent reconstruction of missing state | §3.3 |
| `gsd:discuss-phase` | Adaptive 4-questions-per-gray-area probe | (deferred — assess uses fixed 5 dims for now; probe deferred to Phase 2 router) |
| `gflow:pr-council-review` (own) | Mandatory memory-slug table per dimension | §3.1 (A1-A5 slug table) |
| `gflow:pr-council-review` (own) | REVIEWED_SHA + per-finding citation discipline | §3.1, §3.2, §3.3 |
| `gflow:pr-council-review` (own) | Always end with AskUserQuestion | §3.1 output (explicit), §3.2/§3.3 (implied — to be explicit in SKILL.md) |
| `figma:figma-use` family | MANDATORY-prerequisite block on wrappers | (deferred to wrapper files in implementation PRs) |
| `context-mode:context-mode` | "Bash >20 lines → ctx_execute" routing rule | §3.2 file-reading discipline |
| `context-mode` (deferred-tools) | `ToolSearch` for MCP memory tool schemas | §3.1 A5 (Memory-Search) |

### A.2 Patterns considered and EXPLICITLY NOT adopted

| Source | Pattern | Why rejected |
|---|---|---|
| `gsd:new-milestone` / `add-phase` / `insert-phase` | Milestone → phase → sub-phase numbering with PROJECT.md routing | Already rejected per §1.1 (task-scoped, not phase-scoped). Adopting would conflict with `[[release-spec-plan-memory-consolidation]]`. |
| `gsd:validate-phase` | "Nyquist retroactive audit" for already-completed phases | Over-engineering. We already have `pr-council-review` + `[[verification-ledger-5-layer]]` + `[[pr-must-verify-on-affected-surface]]` covering this. |
| `superpowers:writing-skills` | TDD-adapted RED→GREEN→REFACTOR phase for new skills | Adapted instead (§A.1 dogfooding) — the manual baseline-run requirement satisfies the RED phase without bolting on full TDD ceremony for markdown skills. |
| `gsd:list-phase-assumptions` | Pre-planning assumption-listing as standalone command | We fold this into `/gflow:assess` A2 (Precedent) instead of building a separate command. §10 Q4 leaves the door open. |

### A.3 Composition opportunities deferred to implementation PRs (not spec content)

Each SKILL.md should explicitly invoke these via `Skill(skill="...")` at the indicated phase:

| Target command | Step | Skill to invoke |
|---|---|---|
| `/gflow:assess` A1 (Fit) | per-dimension agent prompt | `Skill: review` |
| `/gflow:assess` A3 (Risk) | per-dimension agent prompt | `Skill: security-review` |
| `/gflow:assess` (final report) | before printing | `Skill: superpowers:verification-before-completion` |
| `/gflow:assess` (RED handler) | when verdict is RED | `Skill: superpowers:brainstorming` (refine task description loop) |
| `/gflow:branch-review` (orchestrator) | before dispatch | `Skill: superpowers:dispatching-parallel-agents` (already implied; make explicit) |
| `/gflow:branch-review` "apply fixes" path | per finding | `Skill: superpowers:receiving-code-review` |
| `/gflow:branch-review` (final exit) | when verdict GREEN + apply path | `Skill: superpowers:finishing-a-development-branch` |
| `/gflow:handover` (final write) | before saving drawers | `Skill: superpowers:verification-before-completion` |
| `/gflow:handover` drawer entries | per-entry write | `Skill: superpowers:writing-skills` ("close every loophole explicitly" pattern) for `drawer-cross-session-rules-digest.md` |

### A.4 Other items deferred to implementation plan / follow-up

**From the original survey:**
- Quick-Reference tables per command (mirroring `finishing-a-development-branch` style)
- `figma:*` MANDATORY-prerequisite block at top of each wrapper
- Per-dimension language-selection mini-table for sandbox execution (mirror `context-mode`)
- Orchestrator-stays-lean: sub-agents write `.planning/<dim>-<ts>.md` and return `{verdict, path}` instead of inline reports
- Drawer rotation extended beyond `drawer-session-log.md` if other drawers prove to grow

**From the v1 spec council audit (2026-05-27, 6 agents) — Tier 2 items the implementation plan MUST address as concrete tasks:**

| Source | Item | Plan task |
|---|---|---|
| D3 MF#1 | Define what "probe" means in A5 — exact `ToolSearch` + query algorithm | Concrete probe contract in SKILL.md |
| D3 MF#2 | Sub-agent cwd + HEAD comparison (orchestrator vs sub-agent context divergence) | `git -C <orchestrator-cwd>` discipline in SKILL.md |
| D3 MF#4 | Drawer rotation actor (the handover command itself does it pre-append) | Algorithm in SKILL.md: `wc -l` → rename if `current + new > 500` |
| D3 MF#5 | Status downgrade rules for YELLOW (no-op) and RED (no-op) cases | One-row addition to §5.9 table or SKILL.md |
| D6 MF#3 | Slug generation algorithm — kebab-case-truncate-40 + HHMMSS suffix on collision | `re.sub` regex in SKILL.md; applies to assess + branch-review + handover filenames |
| D6 MF#4 | Drawer atomicity contract — `open(..., 'a')`/`open(..., 'x')` semantics | Specified inline in §5.8 HARD-GATE (just done in Tier 1); verify implementation honors |
| D4 MF#4 | User-visible error message per HARD-GATE | One-line message per gate in SKILL.md table |
| D2 MF#3 | Dogfooding chicken-egg: first branch-review PR can't run itself | Plan PR #2 notes "skip self-dogfooding; first dogfooding run is on PR #3 (assess)" |

**From the v1 spec council audit — Tier 3 items deferred to follow-up issues / memory:**

| Source | Item | Disposition |
|---|---|---|
| D4 MF#1 | PR #3 (assess) effort is 2-3 days not 1 | Plan re-estimates; not a spec edit |
| D4 MF#2 | Drawer rotation YAGNI for PR #1 | Plan defers rotation to follow-up PR #4 if usage warrants |
| D5 MF#2 | Severity × action-tier matrix — which combinations valid | Plan clarifies in §5.9 table; not blocking |
| D6 MF#1 | `.planning/*` not in `.gitignore` (only `.planning/e2e-logs/`) | PR #1 (handover) adds `.planning/*` + `!.planning/.gitkeep`; not a spec edit |
| D1 NH#1 | §3.1/§3.2 missing explicit "Synthesis" sub-section | Cosmetic; plan adds one sentence per command |
| D1 NH#2 | §9 LP-6 (No CI/CD) not defended in §1 body | Cosmetic; plan adds one sentence to §1 Out-of-scope at next revision |

**Tier 1 items applied directly to this spec** (this commit):
- D2 MF#1 — Iron Law rewording (§3.1) — composes with §5.9 status taxonomy
- D2 MF#2 — §10 Q1 struck-through with LP-2 resolution noted
- D5 MF#1 — §5.8 HARD-GATEs rewritten as actual XML blocks (not markdown rows)
- D6 MF#2 — §3.3 drawer auto-load contract (MEMORY.md patching mandatory)
- D6 MF#5 — §3.2 REVIEWED_SHA drift handling for branch mode
- D4 MF#5 — §5.1 cross-tool portability clarified as read-only
- Bonus from D6 NH#6 — §5.8 `HARD-GATE FAILED: <reason>` structural hook for synthesizer + wrapper

---

**End of spec.**
