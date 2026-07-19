# `/gflow:live-verify` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a new `/gflow:live-verify` skill (pre-flight state check + per-feature live-verification gate) and wire it into gflow-cli's existing skill/AGENTS.md/docs cross-references, so the "we reverse-engineer a blackbox, offline checks aren't enough" fact is enforced at the start and end of every feature, not just at release time.

**Architecture:** One new skill file (`skills/live-verify/SKILL.md`) with two clearly delineated sections (Part 1 Pre-flight, Part 2 Live-verify + failure-routing), exposed via a thin command pointer (`.claude/commands/gflow/live-verify.md`) matching the existing `/gflow:check`/`/gflow:release`/`/gflow:doc-review` pattern. Three small edits wire it into existing discovery paths: one bullet + one table row in `AGENTS.md`, one pointer line in `skills/check/SKILL.md`, one row in `docs/INDEX.md`.

**Tech Stack:** Markdown skill files with YAML frontmatter (this repo's existing skill format). No code, no dependencies. Verification is via this repo's existing mechanical gates (`scripts/ci/check_doc_links.py` — note its coverage gap below, `scripts/ci/check_repo_hygiene.py`) plus content greps and manual path resolution — there is no compiler/test runner for prose.

## Global Constraints

- Repo: `C:\development\github\gflow-cli`, branch `docs/e2e-gate-design` (already checked out; spec and this plan's predecessor already committed there under the old `e2e-gate` name, both since renamed to `live-verify` after a 3-agent council review — see `docs/superpowers/specs/2026-07-19-live-verify-design.md` header).
- Every skill file in this repo uses YAML frontmatter with `name`, `version`, `description` (see `skills/release/SKILL.md`, `skills/check/SKILL.md`, `skills/doc-review/SKILL.md` for the exact convention).
- Command pointer files (`.claude/commands/gflow/*.md`) are thin: a one-line `description:` frontmatter field plus "Read `skills/<name>/SKILL.md` and follow the protocol... Do **not** call `Skill(skill="<name>")` — read the file directly." — copy this pattern exactly, do not invent a different shape.
- **`scripts/ci/check_doc_links.py` uses a hardcoded allowlist and does NOT scan `skills/*.md`** — confirmed by council audit. Do not rely on it to validate links inside the new skill file; verify those manually (Step-level instructions below do this).
- Full design source of truth: `docs/superpowers/specs/2026-07-19-live-verify-design.md` — every task below implements a specific section of it; do not deviate from its content without flagging it.
- Every commit in this plan must NOT carry a `Co-Authored-By: Claude` trailer (repo convention — commit-msg hooks strip it anyway, but don't add it).
- Run `PYTHONUTF8=1 python scripts/ci/check_doc_links.py` and `PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py` after every task that touches a file — both must be clean (0 broken links among files it actually covers, 0 hygiene violations) before committing.

---

### Task 1: Create the `/gflow:live-verify` skill file + command entry point

**Files:**
- Create: `skills/live-verify/SKILL.md`
- Create: `.claude/commands/gflow/live-verify.md`

**Interfaces:**
- Consumes: nothing (first task, no dependency on other tasks in this plan).
- Produces: the skill is invocable as `/gflow:live-verify` and proactively triggerable by its frontmatter `description` (per `using-superpowers`'s "invoke if even 1% relevant" rule). Task 2 and Task 3's edits both link to `skills/live-verify/SKILL.md` by this exact path — do not rename the file or its directory.

- [ ] **Step 1: Confirm the target paths don't already exist**

```bash
cd /c/development/github/gflow-cli
ls skills/live-verify/SKILL.md .claude/commands/gflow/live-verify.md
```

Expected: both `ls` calls fail with "No such file or directory" (this is a net-new skill).

- [ ] **Step 2: Create `skills/live-verify/SKILL.md`**

```bash
mkdir -p skills/live-verify
```

Write the following exact content to `skills/live-verify/SKILL.md`:

````markdown
---
name: live-verify
version: "1.0"
description: >
  Two-part gate for gflow-cli feature/fix work. Part 1 (Pre-flight): use when
  starting work on a gflow-cli feature or fix — confirms the checkout
  reflects current develop before investing effort. Part 2 (Live-verify):
  use before claiming gflow-cli work done, especially anything touching a
  generation code path (t2i/i2i/i2v/t2v/r2v) — requires live evidence
  against real Flow, not just offline tests.
---

# `/gflow:live-verify` — Live-verification enforcement

gflow-cli reverse-engineers a blackbox: Google Flow. Offline checks (`ruff`, `pyright`,
unit/BDD tests) verify gflow-cli's own code does what it's supposed to; they cannot verify
Flow still behaves the way it was captured, because Flow is external and changes without
notice (see [#174](https://github.com/ffroliva/gflow-cli/issues/174)). This gate enforces two
things, both evidence-based (no claim without a fresh verification artifact — see the
`superpowers:verification-before-completion` skill):

1. **Part 1 — Pre-flight**, at the *start* of feature/fix work: confirm the checkout reflects
   current `develop` before investing effort.
2. **Part 2 — Live-verify**, *before claiming done* (after `/code-review` and
   `/ponytail:ponytail-review`, before commit/PR): exercise the change against real Flow.

Full design rationale:
[`docs/superpowers/specs/2026-07-19-live-verify-design.md`](../../docs/superpowers/specs/2026-07-19-live-verify-design.md).

---

## Part 1 — Pre-flight

Run before writing any code for a new feature/fix:

```bash
git fetch origin
git rev-parse --abbrev-ref HEAD
git rev-list --count HEAD..origin/develop
git log --oneline -5
```

`git rev-list --count HEAD..origin/develop` is an asymmetric DAG set-difference — it counts
commits `develop` has that the current `HEAD` lacks, regardless of `HEAD`'s own private
unmerged history. This is what catches a genuinely diverged stale branch, not just a
behind-by-fast-forward one.

- **If the count is nonzero:** stop. `git pull` (on `develop`) or rebase/merge (on a feature
  branch) before continuing.
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

**2. Check the cost tier for each variation — the tier follows the operation, not the
   command family:**
   - Bare entity CRUD with no image generation (`create_entity`, `list_characters`,
     `patch_entity` at the API level; `t2i`/`i2i` themselves) are **credit-free** — run as
     needed to cover the matrix without a separate confirm each time. Still mind WAF/volume
     discipline: don't fire dozens of live calls back-to-back without surfacing it to the
     user first.
   - **Anything that generates real media is costed**, even on an otherwise-free command
     family — e.g. `gflow character create --face-prompt` generates real face/body images
     and costs Imagen credits despite being "character CRUD" in name; `i2v` and other
     video-generation paths are always costed. Always get explicit operator go-ahead before
     running a costed variation. Batch the ask: name what will run and why, once, not one
     confirm per call.

**3. Run each variation, capture evidence per run.** Use the release skill's actual 5-layer
   ledger shape (`skills/release/SKILL.md` §4b), adapted per-field to what the change under
   test produces:

   | Layer | Evidence |
   |---|---|
   | File count | New file(s)/DB row(s) produced by the run |
   | Magic bytes / field value | The specific artifact or field the change is supposed to affect (e.g. a real image's magic bytes, or de-tagged prompt text in the catalog) |
   | Dimensions/shape | For media output: actual dimensions match the requested aspect ratio/model |
   | Structlog invariants | The expected log event fired (e.g. `mention_resolved`, not `mention_unresolved`) |
   | User-confirmable artifact | Real output a human could open and check (image/video file, `size > 1024` bytes) |

   Write this to a lightweight per-feature evidence note at `tmp/live-verify/<feature-slug>.md`
   (gitignored — this is not the full `docs/LIVE_VERIFICATION_vX.Y.Z.md` release ceremony).
   Fold it into the real `LIVE_VERIFICATION` doc when the feature ships in a release.

**4. On pass:** all matrix variations green — proceed to commit.

**5. On fail:** go to Failure-routing below.

## Failure-routing (reproducibility re-test)

**1. Check for a known match first** (costed failures only, to avoid an unnecessary
   re-spend): grep `KNOWN_ISSUES.md` and open GitHub issues for a matching error signature.

```bash
gh issue list --repo ffroliva/gflow-cli --search "<error text>" --state all
```

**2. If no match, re-test once.** Free for `t2i`/`i2i` — just re-run. For a costed failure,
   re-testing needs the same operator confirm as any costed run.

**3. Compare outcomes and route:**

   | Outcome | Route |
   |---|---|
   | Same failure, same code, no known-issue match | **Real bug.** Back to execution — fix it (use `superpowers:systematic-debugging` if the cause isn't obvious). Re-run this gate after the fix. |
   | Different outcome, same code, no changes in between — OR a known-issue match | **External flake.** Do not loop trying to "fix" it. Record it in the evidence note (what failed, that it's not reproducible against unchanged code, link to the matching issue if any). Gate passes-with-caveat for this run. |
   | The failure reveals the *plan's premise* was wrong (not a bug, not a flake) | **Back to planning/design**, not execution. Don't keep patching code against a wrong premise. |

**4. Record every outcome** in the evidence note — passes, fails, and flakes are all
   evidence, not just the final green state.

## Driver

Main context or `superpowers:subagent-driven-development` — never a stateless one-shot
subagent. Diagnosing a live failure needs memory of what's already been tried; a fresh,
context-less subagent call breaks a spike-then-fix-then-retest loop.

## Notes

- This gate does not replace `/gflow:check` (offline gates before commit) or
  `/gflow:doc-review` (release-time doc council) — it fills the gap between them.
- Testing this skill itself means dry-running it on the next real feature that touches a
  generation path — there is no synthetic self-test for a live-Flow gate.
````

- [ ] **Step 3: Create `.claude/commands/gflow/live-verify.md`**

Write the following exact content:

```markdown
---
description: Two-part gate for gflow-cli feature work — pre-flight state check at the start, live-verification against real Flow before claiming done.
---

# `/gflow:live-verify` — Live-verification enforcement

**Read `skills/live-verify/SKILL.md` and follow the protocol**, passing `$ARGUMENTS` if given.

> Do **not** call `Skill(skill="live-verify")` — read the file directly.
```

- [ ] **Step 4: Verify both files exist and the skill file's structure is well-formed**

```bash
ls skills/live-verify/SKILL.md .claude/commands/gflow/live-verify.md
grep -c "^## Part 1\|^## Part 2\|^## Failure-routing\|^## Driver\|^## Notes" skills/live-verify/SKILL.md
```

Expected: both `ls` calls succeed; the `grep -c` prints `5` (one match per required section
header).

- [ ] **Step 5: Manually verify the relative link to the design spec resolves**

`check_doc_links.py` does not cover `skills/*.md` (see Global Constraints), so verify by hand:

```bash
python -c "import os; p = os.path.normpath(os.path.join('skills/live-verify', '../../docs/superpowers/specs/2026-07-19-live-verify-design.md')); print(p, '->', os.path.exists(p))"
```

Expected: prints a path ending in `docs\superpowers\specs\2026-07-19-live-verify-design.md`
(or `/`-separated on non-Windows) and `True`.

- [ ] **Step 6: Run the doc-links and repo hygiene gates**

```bash
PYTHONUTF8=1 python scripts/ci/check_doc_links.py
PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py
```

Expected: `All links resolved across N files.` (this only confirms no *existing* covered file
broke — it does not cover the new skill file, per Step 5's manual check above) and `✅ N
tracked files checked — no violations.` (repo hygiene; only an advisory branch-name nit is
acceptable, no hard violations).

- [ ] **Step 7: Commit**

```bash
git add skills/live-verify/SKILL.md .claude/commands/gflow/live-verify.md
git commit -m "feat(skills): add /gflow:live-verify — pre-flight state check + per-feature live-verification gate"
```

---

### Task 2: Wire cross-references into `AGENTS.md` and `skills/check/SKILL.md`

**Files:**
- Modify: `AGENTS.md` (insert one bullet into "Working discipline — verify before you act"; insert one row into the "Skills reference (cross-tool)" table)
- Modify: `skills/check/SKILL.md` (append one pointer line to the existing final paragraph)

**Interfaces:**
- Consumes: `skills/live-verify/SKILL.md` must already exist at that exact path (Task 1's output) — all three edits in this task link to it.
- Produces: nothing consumed by Task 3 (independent file).

- [ ] **Step 1: Locate the exact insertion points in `AGENTS.md`**

```bash
grep -n "If a claim can't be verified in the current environment" AGENTS.md
grep -n "doc-review.*Council-driven documentation audit" AGENTS.md
grep -n "^## Skills reference" AGENTS.md
```

Expected: one match for each — confirms the bullet insertion point, the table-row insertion
point (right before the `release` row, after `doc-review`), and the section header.

- [ ] **Step 2: Insert the new bullet immediately after the "If a claim can't be verified..." bullet**

The existing text (do not otherwise modify this section):

```markdown
- **If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED.** Keep the issue open, reference it with `Refs #N` (not `Closes #N`), and ship diagnostics rather than a blind fix. When you can't reproduce it, hand the fix to whoever can.

## Skills reference (cross-tool)
```

Replace it with:

```markdown
- **If a claim can't be verified in the current environment, it's LIKELY — not CONFIRMED.** Keep the issue open, reference it with `Refs #N` (not `Closes #N`), and ship diagnostics rather than a blind fix. When you can't reproduce it, hand the fix to whoever can.
- **This project reverse-engineers a blackbox.** gflow-cli doesn't own Google Flow — it drives real Flow through inspected HAR/DOM/browser-log behavior. Offline checks (types, lint, unit/BDD tests) verify *our* code does what we think it does; they cannot verify Flow still behaves the way we captured it. Every feature that touches a generation path is **live-verified**, not just offline-tested, before it's called done — see `/gflow:live-verify`.

## Skills reference (cross-tool)
```

- [ ] **Step 3: Insert the new table row before the `doc-review` row**

The existing two rows (do not otherwise modify this table):

```markdown
| `sonar` | [`skills/sonar/SKILL.md`](skills/sonar/SKILL.md) | Drive the SonarCloud quality gate to zero for a PR/branch |
| `doc-review` | [`skills/doc-review/SKILL.md`](skills/doc-review/SKILL.md) | Council-driven documentation audit before a release |
```

Replace with:

```markdown
| `sonar` | [`skills/sonar/SKILL.md`](skills/sonar/SKILL.md) | Drive the SonarCloud quality gate to zero for a PR/branch |
| `live-verify` | [`skills/live-verify/SKILL.md`](skills/live-verify/SKILL.md) | Pre-flight state check at start of work; live-verification against real Flow before claiming done |
| `doc-review` | [`skills/doc-review/SKILL.md`](skills/doc-review/SKILL.md) | Council-driven documentation audit before a release |
```

- [ ] **Step 4: Locate the exact insertion point in `skills/check/SKILL.md`**

```bash
tail -5 skills/check/SKILL.md
```

Expected: the last paragraph of the file ends with "no scoping, no skipping." — confirms
this is the true end of file to append after.

- [ ] **Step 5: Append the pointer as a new final paragraph**

The existing final paragraph (do not otherwise modify this section):

```markdown
The OOM allowance applies to step 6 (coverage) ONLY. Step 4 (`ruff check` + `ruff
format --check src tests`) is cheap, never OOMs, and must ALWAYS be run repo-wide
against the exact tree you are about to push — no scoping, no skipping.
```

Append immediately after it (same file, new paragraph, one blank line between):

```markdown
Offline-green here is not done-done for a change touching a generation code path (t2i/i2i/
i2v/t2v/r2v) — see `/gflow:live-verify` Part 2 before claiming that kind of feature complete.
```

- [ ] **Step 6: Verify all three insertions landed correctly**

```bash
grep -n "This project reverse-engineers a blackbox" AGENTS.md
grep -n "^| \`live-verify\`" AGENTS.md
grep -n "see \`/gflow:live-verify\` Part 2" skills/check/SKILL.md
```

Expected: exactly one match in each of the three grep calls.

- [ ] **Step 7: Run the doc-links and repo hygiene gates**

```bash
PYTHONUTF8=1 python scripts/ci/check_doc_links.py
PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py
```

Expected: same clean results as Task 1 Step 6.

- [ ] **Step 8: Commit**

```bash
git add AGENTS.md skills/check/SKILL.md
git commit -m "docs(agents): point to /gflow:live-verify from working discipline, skills table, and check gate"
```

---

### Task 3: Add `docs/INDEX.md` rows for the spec and plan

**Files:**
- Modify: `docs/INDEX.md`

**Interfaces:**
- Consumes: `docs/superpowers/specs/2026-07-19-live-verify-design.md` and
  `docs/superpowers/plans/2026-07-19-live-verify/PLAN.md` must already exist at these exact
  paths (both already committed on this branch, predating Task 1).
- Produces: nothing consumed by further tasks (last task in the plan).

- [ ] **Step 1: Locate the exact insertion point**

```bash
grep -n "2026-07-18-asset-tagging" docs/INDEX.md
```

Expected: two matches (the spec row and the plan row for the prior asset-tagging feature) —
confirms where to add the new rows immediately after, following the existing chronological
pattern.

- [ ] **Step 2: Insert two new rows immediately after the asset-tagging rows**

The existing two rows (do not otherwise modify this table):

```markdown
| [docs/superpowers/specs/2026-07-18-asset-tagging-design.md](superpowers/specs/2026-07-18-asset-tagging-design.md) | Asset-tagging (`@`-mention) design spec: mention grammar, resolution contract, Option-B architecture, error taxonomy, spike gate | Reviewing or implementing the `@`-mention feature |
| [docs/superpowers/plans/2026-07-18-asset-tagging/PLAN.md](superpowers/plans/2026-07-18-asset-tagging/PLAN.md) | Task-by-task implementation plan for asset tagging (spike gate → resolver → CLI/MCP → live e2e) | Tracking task-by-task execution of the `@`-mention feature |
```

Replace with:

```markdown
| [docs/superpowers/specs/2026-07-18-asset-tagging-design.md](superpowers/specs/2026-07-18-asset-tagging-design.md) | Asset-tagging (`@`-mention) design spec: mention grammar, resolution contract, Option-B architecture, error taxonomy, spike gate | Reviewing or implementing the `@`-mention feature |
| [docs/superpowers/plans/2026-07-18-asset-tagging/PLAN.md](superpowers/plans/2026-07-18-asset-tagging/PLAN.md) | Task-by-task implementation plan for asset tagging (spike gate → resolver → CLI/MCP → live e2e) | Tracking task-by-task execution of the `@`-mention feature |
| [docs/superpowers/specs/2026-07-19-live-verify-design.md](superpowers/specs/2026-07-19-live-verify-design.md) | `/gflow:live-verify` design spec: pre-flight state check + per-feature live-verification gate generalizing release step 4b, council-reviewed | Reviewing or implementing the live-verification enforcement skill |
| [docs/superpowers/plans/2026-07-19-live-verify/PLAN.md](superpowers/plans/2026-07-19-live-verify/PLAN.md) | Task-by-task implementation plan for `/gflow:live-verify` (skill file → AGENTS.md/check.md wiring → INDEX row) | Tracking task-by-task execution of the live-verify skill |
```

- [ ] **Step 3: Verify the insertion landed correctly**

```bash
grep -c "2026-07-19-live-verify" docs/INDEX.md
```

Expected: `4` (two rows, each with a spec/plan path appearing once in the link text and once
in the link target = 2 matches per row).

- [ ] **Step 4: Run the doc-links and repo hygiene gates**

```bash
PYTHONUTF8=1 python scripts/ci/check_doc_links.py
PYTHONUTF8=1 python scripts/ci/check_repo_hygiene.py
```

Expected: `All links resolved across N+2 files.` — `docs/INDEX.md` IS in the doc-links
allowlist (unlike `skills/*.md`), so this run genuinely validates both new links this time.
Repo hygiene: same clean result as before.

- [ ] **Step 5: Commit**

```bash
git add docs/INDEX.md
git commit -m "docs(index): add rows for the live-verify design spec and implementation plan"
```

---

## Post-plan (not a task — a reminder for whoever executes this)

After all three tasks land, this branch (`docs/e2e-gate-design`) should be pushed and opened
as a PR into `develop`, same as any other doc/skill change in this repo (see recent PRs #348,
#350, #351 for the pattern: push branch, `gh pr create --base develop`, wait for CI green
including SonarCloud, `gh pr merge --merge`). This is deliberately left out of the task list
above because it's a repo-standard mechanical step, not a design decision — do it the same
way every other PR in this repo gets merged.

The branch name (`docs/e2e-gate-design`) still carries the pre-rename name — this is cosmetic
only (branch names aren't part of the shipped artifact) and not worth a disruptive rename
mid-flight; leave it as-is when opening the PR.
