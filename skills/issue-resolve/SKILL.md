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

## The Bug Lane — canonical here, cited everywhere

**This is the chain a bug travels in gflow-cli.** It is written out once, in this
file. `AGENTS.md`, `skills/spike`, `skills/scenario` and `docs/E2E_TESTING.md` all
point here — none of them restate it, because a duplicated checklist drifts.

```
0  SPIKE       measure the live surface            ── only when a claim about Flow is in play
1  DEBUG       systematic-debugging → ROOT CAUSE   ── the symptom is never the finding
2  SCENARIO    BDD Gherkin written at the ROOT     ── the reproduction, in Given/When/Then
3  TDD         that Gherkin RED before any fix     ── red for the right reason
4  FIX         minimal change, at the root         ── grep every caller before editing one
5  FORMALIZE   UI/Flow surface ⇒ it is an E2E test ── a browser-free proxy does not discharge it
```

### The surface gate — steps 0–2 are conditional, 3–5 never are

Run **0 SPIKE** when the bug's explanation involves what Flow does — a selector,
a wire response, a host behaviour, any claim of absence. Skip it when the cause is
already proven or lives entirely in our own code.

Run **1 DEBUG** and **2 SCENARIO** whenever the bug touches a Flow surface, a
transport, auth, selectors, or the cause is not yet *proven*. Skip both for a
fix whose cause is self-evident and whose blast radius is one line — a typo, an
exit-code string, a doc correction. **A skip is a claim; say it out loud** ("cause
proven at `<file>:<line>`, skipping the debug step") so the skip is reviewable.

Steps **3–5 have no gate** — but read step 3 correctly when step 2 was skipped.
There is no bug small enough to fix without a test that failed first, and no
Flow-surface change that a unit test discharges. What step 3 requires is **the
reproduction, red first**; Gherkin is its form only when step 2 produced Gherkin.
Skip step 2 and step 3 still owes you a failing test — an ordinary `test_*` that
reproduces the bug — it just is not a scenario. The gate is *red before green*,
never *Gherkin before green*.

### A flag is a claim — decide it with evidence or don't change it

`retryable`, an exit code, a capability-table entry, a `skip_if_*` predicate: these are
read by code that **acts** on them. `retryable=True` is not a hint, it is an instruction
to try again. So metadata obeys the same rule as prose — *a claim you have not run is a
guess with formatting.* The trap is that a flag changes for free when you re-route a
raise to a different class, so a refactor smuggles in an assertion nobody reviewed.

**How to decide, in order:**

| Can you reproduce the condition? | Then |
|---|---|
| **Yes** | Measure it. N sequential attempts, cheapest surface that reaches it. N/N = stable · mixed = it flaps · and **write down which reading means what before you run** |
| **No, but the surface already gave an answer** | **Preserve that answer** and record that it is preserved, not measured. The status quo is not a claim; changing it is |
| **No, and the condition is new** | Leave the class default and say so at the raise site. An unset flag is honest; a guessed one is not |

Never let a class default speak for a raise site it was not written for. When one class
covers shapes with genuinely different semantics, give the raise site an override rather
than picking one answer for both — `FlowAppError.retryable` exists for exactly this,
and lives on that class alone until a second one needs it.

> **Written from a near-miss in the same session that wrote this file.** Routing #756's
> `/about` landing to `FlowAppError` (exit 31) would have flipped it from non-retryable
> to retryable purely as a side effect of the exit-code change, and the first draft
> shipped that with a confident remediation string saying a retry was "unlikely to help"
> — also unmeasured. The maintainer caught it: *"I need evidence and test. otherwise
> everything will be a guess."* The measurement came back **inconclusive** (the redirect
> had stopped reproducing), which is why the rule's middle row exists: inconclusive is a
> real result, and it means preserve, not pick.

### Step 5 is the one that gets rationalised away

```
IF THE SCENARIO CAN ONLY HAPPEN IN A BROWSER,
THE TEST THAT PROVES IT IS AN E2E TEST.
```

Not a unit test with a mocked page. Not "the CLI path is covered and it shares the
service." Those assert that *our* code does what we think; the bug was that Flow
did something else. Write `tests/e2e/test_<slug>_bdd.py`, binding the Gherkin from
step 2 — see [`docs/E2E_TESTING.md`](../../docs/E2E_TESTING.md) § BDD-bound e2e for
the tag/marker mechanics.

The only exit is a **named external blocker** (AGENTS.md Iron Law): an account you
do not control, hardware you do not have, an exhausted quota. Write it down, use
`Refs #N` not `Closes #N`, and leave the issue open. "I could not reach it here" is
a blocker only after you have said *what* stopped you.

> **Written from the contradiction it removes.** Until 2026-09-10 step 3 of this
> file read "the test is the closest browser-free proxy" while AGENTS.md's Iron Law
> read "if no e2e test covers the change, write one — that is part of the change,"
> and listed "it's covered by unit tests" among the excuses that are *not* blockers.
> Two files, disjoint, no merge conflict, no gate that could see it — memory
> `prose-conflicts-hide-in-disjoint-files`. An agent following this skill could
> ship a mocked proxy and be, by the letter, compliant.

---

## Preconditions (all required before any code change)

1. An `issue-assessment` verdict of `CONFIRMED-BUG` or `LIKELY-BUG`.
2. Scope is single-surface / localized (not a cross-cutting redesign).
3. The fix is **verifiable in this environment**, OR the gap is a **named external
   blocker** carried into the PR. "Browser-free" is not itself a blocker — this host
   has a warm profile and runs the `e2e_auth` tier at zero credits nightly. Name what
   actually stops the run (an account you do not control, a Mac, an exhausted quota)
   or run it.

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

### 2. Find the root cause — lane steps 0–2
Apply **the surface gate** above, then:

- **0 SPIKE** — `/gflow:spike` when the explanation involves what Flow does. A
  selector that missed is evidence about the selector, never about the feature.
- **1 DEBUG** — `superpowers:systematic-debugging`. Backtrack from the symptom to
  the line that causes it. The issue reports a symptom; the fix goes at the root,
  so **grep every caller** of the function you are about to touch. One guard in the
  shared path is a smaller diff than a guard per caller, and patching only the path
  the ticket names leaves every sibling still broken. State the root cause as
  `<file>:<line>` plus the evidence that pins it.
- **2 SCENARIO** — `/gflow:scenario`. Write the reproduction as Gherkin **at the
  root cause**, not at the symptom. Two bugs with one root cause are one scenario.

If the fix touches auth, a transport, selectors, or a schema, `/gflow:predict`
runs here too.

### 3. Fix test-first — lane steps 3–5
Use `superpowers:test-driven-development`. The step-2 Gherkin goes **red first**,
and red for the right reason — read the failure, don't just see a red dot. Then
the minimal fix at the root, then green.

**Where the test lives is decided by the surface, not by convenience:**

| The scenario can only happen… | The test is | Marked |
|---|---|---|
| in a real browser / against real Flow | `tests/e2e/test_<slug>_bdd.py` binding the feature | `@e2e` + a cost tier |
| in our own code (parsing, routing, exit codes) | `tests/features/test_<slug>_steps.py` | untagged (offline) |

`tests/features/test_e2e_binding_guard.py` enforces the binding both ways and
runs offline in normal CI. A browser-free proxy **does not** discharge a
UI-surface scenario; only a named external blocker does (see step 5 above).

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
| 0 Spike (live-surface claims) | `/gflow:spike` |
| 1 Debug → root cause | `superpowers:systematic-debugging` |
| 2 Scenario (BDD at the root) | `/gflow:scenario` |
| High-stakes gate | `/gflow:predict` |
| 3 TDD | `superpowers:test-driven-development` |
| 5 Formalize (UI ⇒ e2e) | `docs/E2E_TESTING.md` § BDD-bound e2e |
| Pre-commit | `/gflow:check` |
| PR review | `/gflow:pr-council-review`, `/gflow:branch-review` |
| Verify discipline | `superpowers:verification-before-completion` |

(Re-derive tool names from `ls skills/` + `ls .claude/commands/gflow/` — don't trust a stale list.)

---

## Common mistakes

- **Fixing the symptom the issue names.** The reporter saw a symptom; lane step 1
  exists because the cause is usually a caller or two above it. Two issues that
  reproduce differently can share one root — fix it once, where they meet.
- **Letting a mocked test stand in for a browser scenario.** It is the most
  comfortable wrong answer in this repo, which is why step 5 is written as a rule
  and enforced by `tests/features/test_e2e_binding_guard.py` rather than left to
  judgement.
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
