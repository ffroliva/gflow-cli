---
name: issue-resolve
version: "1.0"
description: >
  Use when an assessed gflow-cli issue (verdict CONFIRMED-BUG or LIKELY-BUG)
  has localized, verifiable scope and should be driven to a fix. Mutating and
  gated: it works in an isolated worktree, fixes test-first, and opens a DRAFT
  PR for human review. Built to run autonomously (hermes-ops) within a strict
  action envelope — never merges, never spends credits, never claims unverified.
---

# `issue-resolve` — drive an assessed issue to a draft PR

Takes a verdict from `issue-assessment` and produces a reviewable fix. The
terminal state is a **draft PR a human promotes** — never an autonomous merge.

**Core principle:** the agent's job is to get the problem *review-ready*, not
to declare victory. A fix is "verified" only after it runs green on the
**affected surface**; when that surface can't be reached here (headed Flow
browser, macOS-only, credits), say so in the PR and stop. (Memory:
`done-means-e2e-verified`, `pr-must-verify-on-affected-surface`.)

---

## Preconditions (all required before any code change)

1. An `issue-assessment` verdict of `CONFIRMED-BUG` or `LIKELY-BUG`.
2. Scope is single-surface / localized (not a cross-cutting redesign).
3. The fix is **verifiable in this environment** (browser-free), OR the
   verification gap is explicitly carried into the PR as "needs human e2e."

If any fails → do not resolve; return to `issue-assessment` (reply-only).

---

## Autonomy envelope (the action contract)

Allowed autonomously:
- ✅ Post one issue comment (status / reply to reporter).
- ✅ Open a **draft** PR (push a `bugfix/`-prefixed branch off `develop`).
- ✅ Run **browser-free / credit-free** verification (unit, lint, type, recording-verif, Gemini tool-path).
- ✅ Run the council review (`/gflow:pr-council-review` / `/gflow:branch-review`), `/gflow:check`,
  `/gflow:sonar`, `/gflow:doc-review` — **without asking.** These are mandated steps, not
  offers. A general "don't spawn subagents unless the user requested it" rule does **not**
  gate them: the user requested them by invoking this workflow. Stopping to ask makes the
  maintainer re-authorize the same step every issue, and it stalls the pipeline at exactly
  the point review is worth most.

Never (these require a human, regardless of pressure):
- ❌ Spend Veo credits (no live video generation to "verify").
- ❌ Mark a PR ready for review or merge it.
- ❌ Push to `main` or `develop`.
- ❌ State a fix is "verified" / "fixed" when it was not run on the affected surface.

Red flags — if you catch yourself reasoning toward any of these, STOP:
"just spend one credit to exercise the path", "it's obviously correct, merge it",
"mark it ready to save the human time", "say it's verified so we can close it".
Urgency does not make an unverified fix verified.

---

## Protocol

### 1. Isolate
Worktree off `origin/develop` on a `bugfix/<slug>` branch (use the
`superpowers:using-git-worktrees` skill). Never work on `develop`/`main`.

### 2. Gate high-stakes changes
If the fix touches auth, a transport, selectors, or a schema → run
`/gflow:predict` first; for edge-case coverage run `/gflow:scenario`. Otherwise
proceed.

### 3. Fix test-first (TDD)
Use `superpowers:test-driven-development`. Write/confirm a **failing** test that
reproduces the bug on the affected surface, then the minimal fix, then green.
If the bug's surface can't be reached here, the test is the closest browser-free
proxy and the PR flags the residual gap.

### 4. Orchestrate (scales with complexity)
For non-trivial fixes: Opus plans → delegates coding to a Sonnet subagent →
Opus reviews the diff → loop until consensus. Gemini (`agy`) as an optional
extra reviewer **if available** (soft dependency, never blocks). Trivial
one-line fixes skip the loop.

### 5. Check
`/gflow:check` clean (ruff + pyright + tests) before the PR. Scope tests
locally (full `pytest --cov` OOMs — memory `full-test-suite-ooms`); trust CI.

### 6. Draft PR
`gh pr create --draft --base develop`. Body is a **plain string** (never a
heredoc — CLAUDE.md MCP rule). Include a **Verification status** section with
checked/unchecked boxes; use `Refs #N` when a human must still verify,
`Closes #N` only when fully verified here. Then run `/gflow:pr-council-review`
(or `/gflow:branch-review` pre-push) — baseline D1–D5 **plus D14 over-engineering /
YAGNI**, which is the lens correctness and quality reviews do not apply. Apply the
findings (or record why you declined each), then **STOP** — a human promotes and merges.

---

## Quick reference

| Step | Tool |
|---|---|
| Worktree | `superpowers:using-git-worktrees` |
| High-stakes gate | `/gflow:predict`, `/gflow:scenario` |
| TDD | `superpowers:test-driven-development` |
| Pre-commit | `/gflow:check` |
| PR review | `/gflow:pr-council-review`, `/gflow:branch-review` |
| Verify discipline | `superpowers:verification-before-completion` |

(Re-derive tool names from `ls skills/` + `ls .claude/commands/gflow/` — don't trust a stale list.)

---

## Common mistakes

- Working on `develop` instead of a `bugfix/` branch off it (memory: `develop-divergence-recovery`).
- Treating step 6's council as optional, or asking permission to run it. It is neither
  (memory: `council-review-is-standing-authorized`). Ask before merging, marking a PR
  ready, or spending credits — never before reviewing.
### Pipeline Continuation (Next Step Handoff)

Upon completing Issue Resolution:
1. **PR Created & SonarCloud Gate 🟢 GREEN:** Proactively announce: **"PR created and SonarCloud gate green. Next step: Phase 10 Release Pipeline (`/gflow:release`) or merge to `develop`."**
2. **SonarCloud Gate 🔴 FAILS:** Run `/gflow:sonar <PR#>` to resolve new code smells/coverage issues before merging.

---

## Provenance

Designed 2026-06-29 (`docs/superpowers/specs/2026-06-29-issue-assessment-workflow-design.md`).
Resolve-path validated by an injected pure-Python canary bug (off-by-one in
`extension_from_magic`) fixed test-first to green on this host. Guardrails are
the autonomous **action contract**, not discipline-rescue — baseline runs showed
capable agents already refuse credit-spend / merge / false-verified under
pressure; the envelope makes the policy explicit and machine-checkable.
