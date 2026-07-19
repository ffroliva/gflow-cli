# `/gflow:e2e-gate` — live-verification enforcement design

> **Status:** DRAFT — pre-implementation · **Date:** 2026-07-19
> **Origin:** two failures in the same session shipping v0.40.0 — (1) an hour spent chasing a bug that
> only existed on a stale local branch (testing against real `develop` would have caught this
> immediately), (2) a 3-agent doc-review council caught 5 places where docs directly contradicted a
> just-shipped feature, which no code-level check would ever find. Both are instances of one root
> cause: this project verifies a blackbox it doesn't own, and nothing in the workflow enforces that
> fact per-feature — only at release time (`skills/release/SKILL.md` step 4b).
> **Scope:** gflow-cli only. Generalizing to other repos (e.g. compile-growth-monorepo) is a
> deliberate future follow-up, not baked in here.

## 1. Problem

gflow-cli drives Google Flow — a system it does not own — via reverse-engineered HAR/DOM/browser-log
behavior. Offline checks (`ruff`, `pyright`, unit/BDD tests) verify that *gflow-cli's own code* does
what it's supposed to; they cannot verify that Flow still behaves the way it was captured, because
Flow is an external blackbox that changes without notice (see issue [#174], an A/B UI rollout that
silently broke entity staging on one account and not another).

Today's session hit two distinct, related failures:

1. **Stale-state waste.** Work was built and live-verified against a local checkout
   (`gflow-cli-pr-344`) that turned out to predate current `develop` — missing a guard
   (`has_reference_images`), a consolidation (`resolve_and_apply`), and a test fix that `develop`
   already had. An hour was spent diagnosing and fixing a bug that didn't exist in the real
   codebase. A cheap state check at the start would have caught this immediately.
2. **Release-only live verification.** The only place this project currently enforces "prove it
   against real Flow" is `skills/release/SKILL.md` step 4b — a gate that fires once, at release
   time, for the whole accumulated batch of unreleased work. Nothing enforces it per-feature, so a
   feature can sit "offline-green" for weeks before its live behavior is ever checked.

## 2. Goal

Enforce two things, both scoped to gflow-cli, both evidence-based (no claim without a fresh
verification artifact, per `verification-before-completion`):

1. **Before investing effort** on a feature/fix, confirm the checkout actually reflects current
   `develop` — catch the stale-branch trap before it wastes time, not after.
2. **Before claiming a feature done**, require it to be exercised against real Flow (not just
   offline tests) with recorded evidence — generalizing release step 4b to fire per-feature, not
   just per-release.

Non-goals: this does not replace `/gflow:check` (offline gates before commit) or `/gflow:doc-review`
(release-time doc council) — it fills the gap between them: a state check before work starts, and a
live-verify gate before work is called finished, independent of whether a release is imminent.

## 3. Design

### 3.1 One skill, two entry points

`skills/e2e-gate/SKILL.md` + `.claude/commands/gflow/e2e-gate.md` → `/gflow:e2e-gate`, matching the
existing `/gflow:check` / `/gflow:release` / `/gflow:doc-review` convention. Two clearly delineated
sections within the one file (mirroring how `release.md` already handles multiple phases in one
skill), each referenced from a different moment in the workflow rather than invoked together:

- **Part 1 — Pre-flight**, referenced at the *start* of feature/fix work.
- **Part 2 — Live-verify**, referenced *before claiming done* (after `/code-review` and
  `/ponytail:ponytail-review`, before commit/PR).

**Trigger mechanism:** this project's skills are proactively invoked, not manually remembered — per
`using-superpowers`, "if you think there is even a 1% chance a skill might apply, you MUST invoke
it." `skills/e2e-gate/SKILL.md`'s frontmatter `description` must therefore name both moments
explicitly (e.g. "Use when starting work on a gflow-cli feature/fix — Part 1; use before claiming
gflow-cli work done, especially anything touching a generation path — Part 2") so the standard
proactive-invocation mechanism fires it at both points, the same way `brainstorming` already
auto-triggers on "let's build X." The `AGENTS.md` bullet (3.5) and the `/gflow:check` pointer are
reinforcement, not the primary trigger.

### 3.2 Part 1 — Pre-flight

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD             # what branch am I actually on?
git rev-list --count HEAD..origin/develop   # am I behind?
git log --oneline -5                        # do recent commits match what I expect?
```

- If the checkout is behind `origin/develop`, or the branch's last real commit predates recent
  `develop` activity in a way that smells like stale WIP: **stop and surface it** before investing
  further effort. Don't silently proceed, don't silently switch — name the divergence and ask.
- Working in a separate sibling checkout is a normal pattern in this project's workflow and is not
  itself a red flag — a real feature branch is *supposed* to differ from `develop`. The actual
  signal is "differs from `develop` in a way that suggests staleness" (e.g. missing a
  function/guard `develop` already has) rather than "differs by adding new work on top of it." When
  in doubt, diff the specific file(s) about to be touched against `origin/develop`'s version before
  assuming they match.

### 3.3 Part 2 — Live-verify

"Live" means concretely: **drive the real generation commands** (`t2i`, `i2i`, `i2v`, and siblings
like `t2v`/`r2v` where applicable) against a real authenticated Flow account, covering **multiple
variations** of the change — not one happy-path call. A change is default-in-scope for this gate if
it touches a generation code path; skipping requires a named reason, not silence.

1. **Define the live matrix** before running anything: which command(s) does the change touch, and
   which variations actually exercise it (e.g. for a mention-resolution fix: a character mention, a
   media mention, an ambiguous-name case, an unresolvable name)?
2. **Cost tiers gate confirmation, not existence:**
   - `t2i` / `i2i` / character CRUD are **credit-free** — run as needed to cover the matrix without a
     separate confirm each time, but still mindful of WAF/volume discipline (cumulative daily volume,
     not burst-rate — don't fire dozens of live calls back-to-back without surfacing it).
   - `i2v` and other video-generation paths are **credited** — always requires explicit operator
     go-ahead before running, batched (name what will run and why) rather than one-off asks per call.
3. **Run each variation, capture evidence per run** using the release skill's 5-layer ledger shape
   (row count / field value / structlog invariant / user-confirmable artifact / test result), written
   to a lightweight per-feature evidence note — not the full `docs/LIVE_VERIFICATION_vX.Y.Z.md`
   ceremony (that stays release-scoped); the per-feature note gets folded into the real
   `LIVE_VERIFICATION` doc when the feature actually ships in a release.
4. **On pass** — all matrix variations green, proceed to commit.
5. **On fail** — Part 2 hands off to the failure-routing logic (3.4).

### 3.4 Failure-routing (reproducibility re-test)

1. **Re-test once before concluding anything.** Free for `t2i`/`i2i` (just re-run). For a costed
   failure (`i2v`), re-testing needs the same operator confirm as any costed run.
2. **Before re-testing a costed failure, check for a known match first** — grep `KNOWN_ISSUES.md` /
   open GitHub issues for a matching error signature. If matched, skip the costed re-test; treat as
   known external flake immediately.
3. **Compare outcomes:**
   - **Same failure, same code, no known-issue match** → real bug. Route back to execution (fix it;
     use `systematic-debugging` for root-causing if the cause isn't obvious). Re-run the gate after
     the fix.
   - **Different outcome, same code, no changes in between** (or a known-issue match) → external
     flake (e.g. issue [#174]). Do not loop trying to "fix" it. Record it in the evidence note —
     what failed, that it's not reproducible against unchanged code, and a link to the matching
     issue if one exists. Treat the gate as passed-with-a-caveat for *this* run, not blocked.
   - **The failure reveals the plan's premise was wrong** (not a coding bug, not a flake — e.g. a
     wrong assumption about Flow's API behavior that a spike would have caught) → route back to
     planning/design, not execution. Don't keep patching code against a wrong premise. (This nearly
     happened today before the `resolve_and_apply` discovery reframed the whole investigation.)
4. Every branch's outcome is recorded in the evidence note — passes, fails, and flakes are all
   evidence, not just the final green state.

### 3.5 Standing awareness (AGENTS.md)

New bullet in `AGENTS.md`'s existing "Working discipline — verify before you act" section, next to
"If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED":

> **This project reverse-engineers a blackbox.** gflow-cli doesn't own Google Flow — it drives real
> Flow through inspected HAR/DOM/browser-log behavior. Offline checks (types, lint, unit/BDD tests)
> verify *our* code does what we think it does; they cannot verify Flow still behaves the way we
> captured it. Every feature that touches a generation path is **live-verified**, not just
> offline-tested, before it's called done — see `/gflow:e2e-gate`.

Plus a one-line pointer added to `skills/check/SKILL.md`'s output/notes section, so anyone running
`/gflow:check` before commit is reminded that offline-green isn't done-done for generation-path
changes.

### 3.6 Driver

Main context or `subagent-driven-development` — never a stateless one-shot subagent. Diagnosing a
live failure needs memory of what's already been tried (today's spike → fix → re-test loop would
break across fresh, context-less subagent calls).

## 4. Testing

This is a prose skill file, not code — "testing" it means dry-running it on the next real feature
that touches a generation path, and confirming: (a) the pre-flight check actually fires and would
catch today's stale-branch case if re-run against it; (b) the live-verify matrix produces a real
evidence note; (c) the `AGENTS.md` pointer is where a fresh agent would actually look. No synthetic
self-test — the first real usage is the test, per this project's own "verify third-party runtime
behavior empirically" principle applied to itself.

## 5. Explicitly deferred

- Generalizing this skill (or its pattern) to other repos, e.g. compile-growth-monorepo's own live
  surfaces (n8n pipeline, Supabase, MinIO).
- A follow-up, broader council audit of the *entire* gflow-cli skill set (`release`, `check`,
  `doc-review`, and this new `e2e-gate`) for HLD coherence and LLD cross-reference correctness —
  planned as a separate initiative immediately after this skill ships, not part of this spec.

[#174]: https://github.com/ffroliva/gflow-cli/issues/174
