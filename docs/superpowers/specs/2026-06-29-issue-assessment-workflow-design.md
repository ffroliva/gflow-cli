# Issue-assessment workflow — design

> Status: **approved design** (2026-06-29). Next step: `writing-plans` → author skills with `writing-skills`.
> Scope: two new project skills that let an agent triage a GitHub issue, report to the reporter, and — when scope is clear — drive a guard-railed fix to a draft PR. Built to run both interactively and autonomously (hermes-ops on the VPS).

## Problem

Incoming GitHub issues (e.g. #222) need a **systematic, repeatable** assessment instead of ad-hoc investigation. We want a process that:

1. Reads the reporter's claim and verifies it against code, tests, docs, `KNOWN_ISSUES.md`, and auto-memory.
2. Produces a defensible verdict and a reply to the reporter.
3. **Requires e2e evidence before claiming a fix works** — and is honest when e2e is impossible in the current environment.
4. When scope is clear, performs the full development workflow in an isolated worktree and opens a PR **for human review** (never merges).
5. Can run **autonomously** (hermes-ops VPS) without taking wrong outward-facing actions.
6. Tells the agent **which project skill to use for what**, so it does not hallucinate skill names.

## Key environmental constraint

The hermes-ops VPS is **headless Linux**. Flow generation auth requires a **headed real-Chrome** session (`real-browser-auth-mandatory` in memory). Therefore, for any Flow-generation/selector/auth bug, the autonomous agent **cannot e2e-verify** — it can only get the problem review-ready and hand to a human. This is encoded as a **standing known limitation**, never hidden. The design goal is to make a wrong autonomous action *rare*, not to pretend the agent can verify everything.

## Architecture — two skills

The repo already ships the fix pipeline as skills (`/gflow:predict` → `/gflow:scenario` → `/gflow:plan` → `/gflow:check` → `/gflow:pr-council-review`) plus superpowers TDD/worktrees. The new work is a **conductor** on top, split for a clean safety boundary:

- **`issue-assessment`** — READ-ONLY. Always safe to run, VPS-friendly. Ingest → verify → classify → e2e-gate → report. Decides (graded, not a hard stop) whether to chain into `issue-resolve`.
- **`issue-resolve`** — MUTATING, gated, autonomous-with-guardrails. Worktree off `develop` → predict/scenario/plan → TDD with Opus-orchestrated review loop → check → browser-free verification → **draft** PR → council review → STOP.

```
GitHub issue ─► issue-assessment (read-only)
                  1. Ingest    gh issue view → claim, env, repro
                  2. Verify    Explore agent over code/tests/docs + KNOWN_ISSUES.md + memory
                  3. Classify  verdict taxonomy
                  4. e2e-gate  is verification possible here & now?
                  5. Report    comment to reporter (verdict + next step)
                       ├─ INVALID / DUP / WONTFIX / NEEDS-INFO ─► stop (comment only)
                       └─ CONFIRMED/LIKELY-BUG & scope clear ──► issue-resolve (if autonomy gate allows)

                issue-resolve (mutating, gated)
                  worktree off develop → /gflow:predict → /gflow:scenario → /gflow:plan
                  → TDD (Opus plans, Sonnet codes, Opus reviews, loop to consensus)
                  → /gflow:check → browser-free verification only
                  → DRAFT PR → /gflow:pr-council-review → STOP
                  Human: promote draft → headed e2e → merge.
```

## `issue-assessment` detail

### Verdict taxonomy (exactly one per assessment)

- `CONFIRMED-BUG` — reproduced or root-caused in code with line-level evidence.
- `LIKELY-BUG / NEEDS-E2E` — strong code hypothesis, unverifiable here (e.g. #222: macOS-only, no headed browser). **The common VPS outcome.**
- `NEEDS-INFO` — reporter must supply a discriminating diagnostic first.
- `DUPLICATE` / `KNOWN-ISSUE` — matches `KNOWN_ISSUES.md` or an open issue/PR.
- `WORKING-AS-INTENDED` / `INVALID` — usage error or expected behavior.
- `WONTFIX / OUT-OF-SCOPE` — real but deliberately not addressed.

### e2e-gate (the honesty mechanism)

Before claiming any verification, classify what verification *requires*:

- **Browser-free** (Gemini tool-path, unit, lint, pyright, recording-verif) → runnable autonomously; run it.
- **Headed-Flow-browser required** (generation, selector, auth) → NOT possible on VPS → mark `NEEDS-E2E`, never claim success, hand to human.

### Report format

A reporter-facing comment: restated claim, verdict + confidence, evidence (`file:line`), root-cause hypothesis, the discriminating diagnostic requested (if `NEEDS-INFO`/`NEEDS-E2E`), and the next step (draft PR link if `issue-resolve` ran, else what we need).

## `issue-resolve` guardrails

Autonomous allow-list:
- ✅ Post issue comment (text reply to reporter).
- ✅ Open a **draft** PR (push branch + open PR in draft state).
- ✅ Browser-free / credit-free verification.
- ❌ **Never** spend Veo credits (no credit-spending e2e).
- ❌ Never mark a PR ready; ❌ never merge; ❌ never push to `main`/`develop` (always a `bugfix/`-prefixed branch off `develop`).

Hard preconditions before opening even a draft PR:
1. Verdict ∈ {`CONFIRMED-BUG`, `LIKELY-BUG`} **and** scope single-surface / localized.
2. A failing test exists first (TDD) and passes after the fix.
3. `/gflow:check` clean (ruff + pyright + tests).
4. If verification needed a headed browser → PR body states **"NOT e2e-verified — requires human macOS/headed run"** explicitly.

Trigger gate (autonomy safety): the agent acts **only on issues a human labels** (e.g. `triage`), never on every new issue.

### Orchestration model

Opus plans → delegates coding to Sonnet → Opus reviews → loop until consensus → `/gflow:pr-council-review`. Gemini (`agy`) as an optional extra reviewer **if available** — soft dependency, never blocks.

## Anti-hallucination skill routing

The skill ships a **literal routing table** mapping situation → skill → how to invoke. Critical trap (in memory `skill-wrapper-registration-trap`): this repo's `skills/*/SKILL.md` are plain markdown invoked by **reading** `.claude/commands/gflow/*`, NOT by `Skill()`. Superpowers skills ARE `Skill()`-invocable. The skill instructs the agent to **re-derive** the table from `ls skills/` + `ls .claude/commands/gflow/` rather than trust a possibly-stale list.

| Situation | Use | How |
|---|---|---|
| High-stakes change (auth/transport/selector/schema) | `/gflow:predict` | Read `skills/predict/SKILL.md` |
| Edge cases + BDD skeleton | `/gflow:scenario` | Read `skills/scenario/SKILL.md` |
| Write task checklist | `/gflow:plan` | Read `skills/plan/SKILL.md` |
| Before any commit | `/gflow:check` | `.claude/commands/gflow/check.md` |
| Pre-PR / PR review | `/gflow:pr-council-review`, `branch-review` | Read `skills/pr-council-review/SKILL.md` |
| Touching auth/reCAPTCHA | `/gflow:known-issues` | `.claude/commands/gflow/known-issues.md` |
| Worktree, TDD, finishing a branch | superpowers | `Skill()` tool (these are invocable) |

## File layout

```
skills/issue-assessment/SKILL.md     # read-only conductor
skills/issue-resolve/SKILL.md        # mutating, gated
.claude/commands/gflow/issue-assessment.md   # thin wrapper: "read skills/.../SKILL.md"
.claude/commands/gflow/issue-resolve.md
```

## Worked example — issue #222 (validation case)

macOS, v0.22.0: `image t2i` launches a logged-out browser → 401 on `createProject` despite `.gflow_browser_strategy=chrome`. Code investigation hypothesis: `channel_for_profile()` returns `None` because macOS `is_chrome_available()` (`src/gflow_cli/browser_manager.py:146`) only checks the hardcoded `/Applications/Google Chrome.app/...` path with no `shutil.which()` fallback (unlike Windows/Linux), so generation falls back to bundled Chromium, which can't decrypt Keychain-protected cookies. **Caveat:** if detection failed, a `browser_manager.chrome_marker_but_unavailable` warning should fire — the reporter didn't mention it, so the channel may instead never be resolved on the `setup_shared_page` path. This ambiguity is exactly why the verdict is `LIKELY-BUG / NEEDS-E2E` (macOS + headed browser, unverifiable on Windows or the VPS) rather than `CONFIRMED-BUG`. Expected workflow output: a reporter reply requesting the discriminating diagnostic (does the warning appear? actual Chrome path? DEBUG run with channel logging), plus optionally a draft PR adding the `shutil.which` fallback with a unit test, flagged NOT-e2e-verified.

## VPS deployment (later phase)

To be completed from hermes-ops documentation (host, deploy path, agent-memory format, workflow activation, GitHub credentials). Deferred until after build + local e2e validation. This section will be filled before any remote action; nothing remote runs until the user confirms the target.

## Out of scope (YAGNI)

- A standalone reusable agent-orchestration skill (the Opus/Sonnet loop lives inside `issue-resolve` for now).
- Auto-merging or auto-promoting PRs.
- Acting on unlabeled issues.
- Cross-repo generalization beyond gflow-cli.
