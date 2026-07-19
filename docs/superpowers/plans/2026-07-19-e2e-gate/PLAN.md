# `/gflow:e2e-gate` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `/gflow:e2e-gate` skill (pre-flight state check + per-feature live-verification gate) and wire it into gflow-cli's existing skill/AGENTS.md cross-references, so the "we reverse-engineer a blackbox, offline checks aren't enough" fact is enforced at the start and end of every feature, not just at release time.

**Architecture:** One new skill file (`skills/e2e-gate/SKILL.md`) with two clearly delineated sections (Part 1 Pre-flight, Part 2 Live-verify + failure-routing), exposed via a thin command pointer (`.claude/commands/gflow/e2e-gate.md`) matching the existing `/gflow:check`/`/gflow:release`/`/gflow:doc-review` pattern. Two small edits wire it into existing discovery paths: one bullet in `AGENTS.md`'s "Working discipline" section, one pointer line in `skills/check/SKILL.md`.

**Tech Stack:** Markdown skill files with YAML frontmatter (this repo's existing skill format). No code, no dependencies. Verification is via this repo's existing mechanical gates (`scripts/ci/check_doc_links.py`, `scripts/ci/check_repo_hygiene.py`) plus content greps — there is no compiler/test runner for prose.

## Global Constraints

- Repo: `C:\development\github\gflow-cli`, branch `docs/e2e-gate-design` (already checked out, spec already committed there).
- Every skill file in this repo uses YAML frontmatter with `name`, `version`, `description` (see `skills/release/SKILL.md`, `skills/check/SKILL.md`, `skills/doc-review/SKILL.md` for the exact convention).
- Command pointer files (`.claude/commands/gflow/*.md`) are thin: a one-line `description:` frontmatter field plus "Read `skills/<name>/SKILL.md` and follow the protocol... Do **not** call `Skill(skill="<name>")` — read the file directly." — copy this pattern exactly, do not invent a different shape.
- Full design source of truth: `docs/superpowers/specs/2026-07-19-e2e-gate-design.md` — every task below implements a specific section of it; do not deviate from its content without flagging it.
- Every commit in this plan must NOT carry a `Co-Authored-By: Claude` trailer (repo convention — commit-msg hooks strip it anyway, but don't add it).
- Run `PYTHONUTF8=1 python scripts/ci/check_doc_links.py` and `PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py` after every task that touches a file — both must be clean (0 broken links, 0 hygiene violations) before committing.

---

### Task 1: Create the `/gflow:e2e-gate` skill file + command entry point

**Files:**
- Create: `skills/e2e-gate/SKILL.md`
- Create: `.claude/commands/gflow/e2e-gate.md`

**Interfaces:**
- Consumes: nothing (first task, no dependency on other tasks in this plan).
- Produces: the skill is invocable as `/gflow:e2e-gate` and proactively triggerable by its frontmatter `description` (per `using-superpowers`'s "invoke if even 1% relevant" rule). Task 2's `AGENTS.md` and `check.md` edits both link to `skills/e2e-gate/SKILL.md` by this exact path — do not rename the file or its directory.

- [ ] **Step 1: Confirm the target paths don't already exist**

```bash
cd /c/development/github/gflow-cli
ls skills/e2e-gate/SKILL.md .claude/commands/gflow/e2e-gate.md
```

Expected: both `ls` calls fail with "No such file or directory" (this is a net-new skill).

- [ ] **Step 2: Create `skills/e2e-gate/SKILL.md`**

```bash
mkdir -p skills/e2e-gate
```

Write the following exact content to `skills/e2e-gate/SKILL.md`:

````markdown
---
name: e2e-gate
version: "1.0"
description: >
  Two-part gate for gflow-cli feature/fix work. Part 1 (Pre-flight): use when
  starting work on a gflow-cli feature or fix — confirms the checkout
  reflects current develop before investing effort. Part 2 (Live-verify):
  use before claiming gflow-cli work done, especially anything touching a
  generation code path (t2i/i2i/i2v/t2v/r2v) — requires live evidence
  against real Flow, not just offline tests.
---

# `/gflow:e2e-gate` — Live-verification enforcement

gflow-cli reverse-engineers a blackbox: Google Flow. Offline checks (`ruff`, `pyright`,
unit/BDD tests) verify gflow-cli's own code does what it's supposed to; they cannot verify
Flow still behaves the way it was captured, because Flow is external and changes without
notice (see [#174](https://github.com/ffroliva/gflow-cli/issues/174)). This gate enforces two
things, both evidence-based (no claim without a fresh verification artifact — see the
`verification-before-completion` skill):

1. **Part 1 — Pre-flight**, at the *start* of feature/fix work: confirm the checkout reflects
   current `develop` before investing effort.
2. **Part 2 — Live-verify**, *before claiming done* (after `/code-review` and
   `/ponytail:ponytail-review`, before commit/PR): exercise the change against real Flow.

Full design rationale:
[`docs/superpowers/specs/2026-07-19-e2e-gate-design.md`](../../docs/superpowers/specs/2026-07-19-e2e-gate-design.md).

---

## Part 1 — Pre-flight

Run before writing any code for a new feature/fix:

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD
git rev-list --count HEAD..origin/develop
git log --oneline -5
```

- **If behind `origin/develop`:** stop. `git pull` (on `develop`) or rebase/merge (on a
  feature branch) before continuing.
- **If the branch's last real commit looks old relative to recent `develop` activity** (a
  smell for stale WIP — e.g. missing a function/guard that `develop` already has): stop and
  surface it. Don't silently proceed, don't silently switch — name the divergence and ask the
  user how to proceed.
- **A separate sibling checkout is not itself a red flag** — this project's workflow
  routinely uses sibling checkouts for isolated feature branches, and a real feature branch
  is *supposed* to differ from `develop`. The actual signal is "differs from `develop` in a
  way that suggests staleness" (missing something `develop` has), not "differs by adding new
  work on top of it." When in doubt, diff the specific file(s) about to be touched against
  `origin/develop`'s version before assuming they match:

```bash
git diff origin/develop -- <path/to/file>
```

## Part 2 — Live-verify

"Live" means: **drive the real generation commands** (`t2i`, `i2i`, `i2v`, and siblings like
`t2v`/`r2v` where applicable) against a real authenticated Flow account, covering **multiple
variations** of the change — not one happy-path call. A change touching a generation code
path is default-in-scope; skipping this gate requires a named reason, not silence.

**1. Define the live matrix.** Before running anything, name explicitly:
   - Which command(s) does the change touch?
   - Which variations actually exercise it? (E.g. for a mention-resolution fix: a character
     mention, a media mention, an ambiguous-name case, an unresolvable name.)

**2. Check the cost tier for each variation:**
   - `t2i` / `i2i` / character CRUD are **credit-free** — run as needed to cover the matrix
     without a separate confirm each time. Still mind WAF/volume discipline: don't fire
     dozens of live calls back-to-back without surfacing it to the user first.
   - `i2v` and other video-generation paths are **credited** — always get explicit operator
     go-ahead before running. Batch the ask: name what will run and why, once, not one
     confirm per call.

**3. Run each variation, capture evidence per run.** Use the release skill's 5-layer ledger
   shape:

   | Layer | Evidence |
   |---|---|
   | Row count | New DB row(s) in the local catalog after the run |
   | Field value | The specific field the change is supposed to affect (e.g. de-tagged prompt text) |
   | Structlog invariant | The expected log event fired (e.g. `mention_resolved`, not `mention_unresolved`) |
   | User-confirmable artifact | Real output (image/video file, `size > 1024` bytes) |
   | Test result | Exit code / pass-fail of the driving command |

   Write this to a lightweight per-feature evidence note (a few lines is fine — this is not
   the full `docs/LIVE_VERIFICATION_vX.Y.Z.md` release ceremony). Fold it into the real
   `LIVE_VERIFICATION` doc when the feature ships in a release.

**4. On pass:** all matrix variations green — proceed to commit.

**5. On fail:** go to Failure-routing below.

## Failure-routing (reproducibility re-test)

**1. Check for a known match first** (costed failures only, to avoid an unnecessary
   re-spend): grep `KNOWN_ISSUES.md` and open GitHub issues for a matching error signature.

```bash
gh issue list --repo ffroliva/gflow-cli --search "<error text>" --state all
```

**2. If no match, re-test once.** Free for `t2i`/`i2i` — just re-run. For a costed failure
   (`i2v`), re-testing needs the same operator confirm as any costed run.

**3. Compare outcomes and route:**

   | Outcome | Route |
   |---|---|
   | Same failure, same code, no known-issue match | **Real bug.** Back to execution — fix it (use `systematic-debugging` if the cause isn't obvious). Re-run this gate after the fix. |
   | Different outcome, same code, no changes in between — OR a known-issue match | **External flake.** Do not loop trying to "fix" it. Record it in the evidence note (what failed, that it's not reproducible against unchanged code, link to the matching issue if any). Gate passes-with-caveat for this run. |
   | The failure reveals the *plan's premise* was wrong (not a bug, not a flake) | **Back to planning/design**, not execution. Don't keep patching code against a wrong premise. |

**4. Record every outcome** in the evidence note — passes, fails, and flakes are all
   evidence, not just the final green state.

## Driver

Main context or `subagent-driven-development` — never a stateless one-shot subagent.
Diagnosing a live failure needs memory of what's already been tried; a fresh, context-less
subagent call breaks a spike-then-fix-then-retest loop.

## Notes

- This gate does not replace `/gflow:check` (offline gates before commit) or
  `/gflow:doc-review` (release-time doc council) — it fills the gap between them.
- Testing this skill itself means dry-running it on the next real feature that touches a
  generation path — there is no synthetic self-test for a live-Flow gate.
````

- [ ] **Step 3: Create `.claude/commands/gflow/e2e-gate.md`**

Write the following exact content:

```markdown
---
description: Two-part gate for gflow-cli feature work — pre-flight state check at the start, live-verification against real Flow before claiming done.
---

# `/gflow:e2e-gate` — Live-verification enforcement

**Read `skills/e2e-gate/SKILL.md` and follow the protocol**, passing `$ARGUMENTS` if given.

> Do **not** call `Skill(skill="e2e-gate")` — read the file directly.
```

- [ ] **Step 4: Verify both files exist and the skill file's structure is well-formed**

```bash
ls skills/e2e-gate/SKILL.md .claude/commands/gflow/e2e-gate.md
grep -c "^## Part 1\|^## Part 2\|^## Failure-routing\|^## Driver\|^## Notes" skills/e2e-gate/SKILL.md
```

Expected: both `ls` calls succeed; the `grep -c` prints `5` (one match per required section
header).

- [ ] **Step 5: Run the doc-links and repo hygiene gates**

```bash
PYTHONUTF8=1 python scripts/ci/check_doc_links.py
PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py
```

Expected: `All links resolved across N files.` (the relative link to the design spec must
resolve) and `✅ N tracked files checked — no violations.` (repo hygiene, only an advisory
branch-name nit is acceptable, no hard violations).

- [ ] **Step 6: Commit**

```bash
git add skills/e2e-gate/SKILL.md .claude/commands/gflow/e2e-gate.md
git commit -m "feat(skills): add /gflow:e2e-gate — pre-flight state check + per-feature live-verification gate"
```

---

### Task 2: Wire cross-references into `AGENTS.md` and `skills/check/SKILL.md`

**Files:**
- Modify: `AGENTS.md` (insert one bullet into the existing "Working discipline — verify before you act" section)
- Modify: `skills/check/SKILL.md` (append one pointer line to the existing "## Notes" section)

**Interfaces:**
- Consumes: `skills/e2e-gate/SKILL.md` must already exist at that exact path (Task 1's output) — both edits in this task link to it.
- Produces: nothing consumed by further tasks (this is the last task in the plan).

- [ ] **Step 1: Locate the exact insertion point in `AGENTS.md`**

```bash
grep -n "If a claim can't be verified in the current environment" AGENTS.md
grep -n "^## Skills reference" AGENTS.md
```

Expected: one match for each — confirms the bullet to insert after, and the next `##` header
that must remain immediately following the new bullet (no blank-line drift).

- [ ] **Step 2: Insert the new bullet immediately after the "If a claim can't be verified..." bullet**

The existing text (do not otherwise modify this section):

```markdown
- **If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED.** Keep the issue open, reference it with `Refs #N` (not `Closes #N`), and ship diagnostics rather than a blind fix. When you can't reproduce it, hand the fix to whoever can.

## Skills reference (cross-tool)
```

Replace it with:

```markdown
- **If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED.** Keep the issue open, reference it with `Refs #N` (not `Closes #N`), and ship diagnostics rather than a blind fix. When you can't reproduce it, hand the fix to whoever can.
- **This project reverse-engineers a blackbox.** gflow-cli doesn't own Google Flow — it drives real Flow through inspected HAR/DOM/browser-log behavior. Offline checks (types, lint, unit/BDD tests) verify *our* code does what we think it does; they cannot verify Flow still behaves the way we captured it. Every feature that touches a generation path is **live-verified**, not just offline-tested, before it's called done — see `/gflow:e2e-gate`.

## Skills reference (cross-tool)
```

- [ ] **Step 3: Locate the exact insertion point in `skills/check/SKILL.md`**

```bash
grep -n "format --check src tests. is cheap, never OOMs" skills/check/SKILL.md
tail -5 skills/check/SKILL.md
```

Expected: the last paragraph of the file is the "OOM allowance" note ending in "no scoping,
no skipping." — confirms this is the true end of file to append after.

- [ ] **Step 4: Append the pointer as a new final paragraph**

The existing final paragraph (do not otherwise modify this section):

```markdown
The OOM allowance applies to step 6 (coverage) ONLY. Step 4 (`ruff check` + `ruff
format --check src tests`) is cheap, never OOMs, and must ALWAYS be run repo-wide
against the exact tree you are about to push — no scoping, no skipping.
```

Append immediately after it (same file, new paragraph, one blank line between):

```markdown
Offline-green here is not done-done for a change touching a generation code path (t2i/i2i/
i2v/t2v/r2v) — see `/gflow:e2e-gate` Part 2 before claiming that kind of feature complete.
```

- [ ] **Step 5: Verify both insertions landed correctly**

```bash
grep -n "This project reverse-engineers a blackbox" AGENTS.md
grep -n "see \`/gflow:e2e-gate\` Part 2" skills/check/SKILL.md
```

Expected: exactly one match in each file.

- [ ] **Step 6: Run the doc-links and repo hygiene gates**

```bash
PYTHONUTF8=1 python scripts/ci/check_doc_links.py
PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py
```

Expected: same clean results as Task 1 Step 5 — both edits are prose-only, no new links
beyond the already-verified `/gflow:e2e-gate` reference (a command name, not a markdown link,
so nothing new for the link checker to validate).

- [ ] **Step 7: Commit**

```bash
git add AGENTS.md skills/check/SKILL.md
git commit -m "docs(agents): point to /gflow:e2e-gate from working discipline + check gate"
```

---

## Post-plan (not a task — a reminder for whoever executes this)

After both tasks land, this branch (`docs/e2e-gate-design`) should be pushed and opened as a
PR into `develop`, same as any other doc/skill change in this repo (see recent PRs #348, #350,
#351 for the pattern: push branch, `gh pr create --base develop`, wait for CI green including
SonarCloud, `gh pr merge --merge`). This is deliberately left out of the task list above
because it's a repo-standard mechanical step, not a design decision — do it the same way every
other PR in this repo gets merged.
