# Lifecycle Protocol Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship 3 library commands (`/gflow:handover`, `/gflow:branch-review`, `/gflow:assess`) that extend the multi-dim council pattern across the gflow-cli development lifecycle (per spec `docs/superpowers/specs/2026-05-27-lifecycle-protocol-design.md`).

**Architecture:** Wrapper-around-skill pattern (canonical SKILL.md + ≤10-line slash-command wrapper) for all 3 commands. Compose with existing `/gflow:pr-council-review` (the v2.1 council we just shipped). Use `<HARD-GATE>` XML blocks for structural enforcement. Reuse the 5 baseline + 8 adaptive council dimensions from `pr-council-review`.

**Tech Stack:** Pure Markdown (skills + commands), Python helper scripts only where verification needs it (none expected). Git/gh CLI for branch state. `ctx_execute` for any output >20 lines.

---

## Spec → Plan mapping

| Spec section | Plan PR | Notes |
|---|---|---|
| §3.1 `/gflow:assess` | **PR #3** (shipped last per §6) | Largest scope; depends on handover + branch-review |
| §3.2 `/gflow:branch-review` | **PR #2** | Extends existing `pr-council-review` skill |
| §3.3 `/gflow:handover` | **PR #1** (smallest, ships first) | Self-contained; validates wrapper template |
| §5.7 composition risks | All 3 PRs | Each PR addresses its own slice |
| §5.8 HARD-GATEs (XML) | All 3 PRs | Per-command gates baked into SKILL.md |
| §5.9 4-status protocol | PR #2 + PR #3 (council-dispatching only) | Not applied to PR #1 (handover dispatches no agents) |
| §A.4 Tier-2 deferred items | Embedded as plan tasks below | 8 items, each a concrete task with file:line |

---

## File structure (locked decisions)

| File | Created by PR | Responsibility |
|---|---|---|
| `skills/handover/SKILL.md` | #1 | Canonical handover protocol — 7 drawers, MEMORY.md patching, atomicity, HARD-GATEs |
| `.claude/commands/gflow/handover.md` | #1 | Thin wrapper (~8 lines) — invokes Skill(handover) |
| `.gitignore` (modify) | #1 | Add `.planning/*` + `!.planning/.gitkeep` |
| `.planning/.gitkeep` | #1 | Empty file to keep dir in git |
| `tests/test_lifecycle_handover.py` | #1 | Schema + behavior smoke tests for handover SKILL.md |
| `skills/pr-council-review/SKILL.md` (modify) | #2 | Add "Branch mode" section to existing skill |
| `.claude/commands/gflow/branch-review.md` | #2 | Thin wrapper (~8 lines) — invokes Skill(pr-council-review) in branch mode |
| `tests/test_lifecycle_branch_review.py` | #2 | Schema test for wrapper + skill diff |
| `skills/assess/SKILL.md` | #3 | Canonical assess protocol — 5 dimensions, slug table, HARD-GATEs, severity matrix |
| `.claude/commands/gflow/assess.md` | #3 | Thin wrapper (~8 lines) |
| `tests/test_lifecycle_assess.py` | #3 | Schema + 5-dim presence tests for assess SKILL.md |

**Memory files** (out-of-repo, applied per PR — no `git` commit needed; tracked in commit message body):
- `~/.claude/projects/<slug>/memory/lifecycle-protocol-overview.md` (PR #1 or PR #3 — index)
- `~/.claude/projects/<slug>/memory/severity-markers-must-forbid-norm.md` (PR #1 — definitions)
- `~/.claude/projects/<slug>/memory/handover-7-drawers.md` (PR #1 — drawer contract)
- `~/.claude/projects/<slug>/memory/MEMORY.md` index entries for each (per PR)

---

## PR #1 — `/gflow:handover` (~5 features, est. ~1 day)

**Branch:** `feature/lifecycle-handover` (per `[[branch-naming-convention]]`)

### Task 1.1: Bootstrap `.planning/` directory and `.gitignore`

**Files:**
- Create: `.planning/.gitkeep`
- Modify: `.gitignore` (add 2 lines)

- [ ] **Step 1: Verify current `.gitignore` state**

Run: `git show HEAD:.gitignore | grep -n planning`
Expected: shows existing `.planning/e2e-logs/` rule (line ~83) only — root `.planning/` is NOT excluded.

- [ ] **Step 2: Add `.planning/*` + `!.planning/.gitkeep` to `.gitignore`**

Edit `.gitignore`, immediately after the existing `.planning/e2e-logs/` line, add:

```gitignore
.planning/*
!.planning/.gitkeep
```

- [ ] **Step 3: Create `.planning/.gitkeep`**

Run: `mkdir -p .planning && type nul > .planning/.gitkeep`
(or `touch .planning/.gitkeep` on POSIX-like shells)

- [ ] **Step 4: Verify gitignore behavior**

Run: `echo TEST > .planning/foo.txt && git status -s`
Expected: `.planning/foo.txt` does NOT appear in status; `.planning/.gitkeep` is staged.
Cleanup: `rm .planning/foo.txt`

- [ ] **Step 5: Commit**

```bash
git add .gitignore .planning/.gitkeep
git commit -m "chore(planning): initialize .planning/ dir with gitkeep + ignore rule"
```

### Task 1.2: Write the handover skill schema test

**Files:**
- Create: `tests/test_lifecycle_handover.py`

- [ ] **Step 1: Write the failing test**

```python
"""Schema + presence tests for the /gflow:handover skill body."""

from __future__ import annotations

from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / "skills" / "handover" / "SKILL.md"


def test_skill_file_exists() -> None:
    assert SKILL.exists(), f"Expected handover skill at {SKILL}"


def test_skill_frontmatter_has_name_and_description() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "Skill must start with YAML frontmatter"
    fm_end = text.find("\n---\n", 4)
    assert fm_end > 0, "Frontmatter must close with ---"
    fm = text[4:fm_end]
    assert "name: handover" in fm, "frontmatter must declare name: handover"
    assert "description:" in fm, "frontmatter must declare description"


def test_skill_lists_7_drawers() -> None:
    text = SKILL.read_text(encoding="utf-8")
    drawers = [
        "drawer-project-state",
        "drawer-open-handovers",
        "drawer-reference-repositories",
        "drawer-harness-decisions",
        "drawer-active-blockers",
        "drawer-cross-session-rules-digest",
        "drawer-session-log",
    ]
    for d in drawers:
        assert d in text, f"Skill must reference drawer {d!r}"


def test_skill_has_iron_law_and_hard_gates() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "Iron Law" in text or "IRON LAW" in text.upper(), "Skill must include Iron Law"
    assert "<HARD-GATE>" in text, "Skill must include literal <HARD-GATE> XML blocks"
    assert "<EXTREMELY-IMPORTANT>" in text, "Skill must include <EXTREMELY-IMPORTANT> block"


def test_skill_has_memory_md_patch_contract() -> None:
    """D6 MF#2 (CRITICAL): drawers are dead weight unless MEMORY.md links them."""
    text = SKILL.read_text(encoding="utf-8")
    assert "MEMORY.md" in text, "Skill must reference MEMORY.md patching"
    assert "## Drawers" in text or "[[drawer-" in text, (
        "Skill must describe the wikilink-based drawer index"
    )


def test_skill_specifies_append_only_atomicity() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert 'open(path, "a")' in text or "O_APPEND" in text or 'open(path, \'a\')' in text, (
        "Skill must specify append-only atomicity (O_APPEND or 'a' mode)"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_handover.py -v`
Expected: FAIL with "Expected handover skill at ..." (file doesn't exist yet)

### Task 1.3: Write the canonical `skills/handover/SKILL.md`

**Files:**
- Create: `skills/handover/SKILL.md`

- [ ] **Step 1: Create the skill body**

Create `skills/handover/SKILL.md` with this exact content (≤200 lines, frontmatter + body):

````markdown
---
name: handover
description: Capture gflow-cli session state into 7 memory drawers + a structured handover markdown at session end OR mid-work pause. Patches MEMORY.md to link drawers (without this, drawers are invisible to next session). HARD-GATE-enforced clean-tree-or-reason discipline. Append-only drawer writes with POSIX O_APPEND atomicity.
---

# `handover` — session-state capture skill

Captures the current gflow-cli session state into a structured handover markdown (printed + `.planning/handover-<ts>.md`) and 7 memory drawers under `~/.claude/projects/<slug>/memory/`. Triggered at session end OR when pausing mid-work.

<EXTREMELY-IMPORTANT>
These commands are local-only side-channels. They do NOT replace
`/gflow:pr-council-review`, `/gflow:check`, or `/gflow:release`.
Skipping the canonical gates because an assess / branch-review /
handover passed is a rationalization. Do NOT skip.
</EXTREMELY-IMPORTANT>

## 0 · Pre-flight

1. **Inside a gflow-cli repo** — assert `AGENTS.md` AND `CLAUDE.md` exist in the working directory.
2. **Memory dir resolvable** — `~/.claude/projects/<slug>/memory/` must exist; if not, create it.
3. **Optional argument:** `--reason <string>` for capturing the trigger context (e.g. "context window full").

## 1 · Iron Law

**NO HANDOVER WITHOUT VERIFIED HEAD SHA + CLEAN GIT STATUS.**

If `git status --porcelain` is non-empty AND no `--reason <string>` was passed, refuse to write drawers. Uncommitted work captured as "session state" would mislead the next session.

<HARD-GATE>
Do NOT write to memory drawers if `git status --porcelain` is non-empty
AND no `--reason` flag was passed. Emit literal first-line:
`HARD-GATE FAILED: dirty tree with no --reason`
</HARD-GATE>

<HARD-GATE>
Drawer files are APPEND-ONLY. NEVER overwrite. All writes use POSIX
O_APPEND semantics: `open(path, "a", encoding="utf-8", newline="\n")`.
Drawer creation uses `open(path, "x")` (exclusive); EEXIST is non-fatal
— proceed with append.
</HARD-GATE>

## 2 · The 7 drawers

Path: `~/.claude/projects/<slug>/memory/drawer-<name>.md`

| Drawer | Content | Update mode |
|---|---|---|
| `drawer-project-state.md` | What we shipped this session (PRs merged, commits, deliverables) | append |
| `drawer-open-handovers.md` | What's mid-flight (open PRs not ready, blocked tasks) | append (deduplicate by PR# / branch) |
| `drawer-reference-repositories.md` | Any new external refs touched this session | append (deduplicate by URL) |
| `drawer-harness-decisions.md` | Harness changes (new skills, commands, agents, config) | append |
| `drawer-active-blockers.md` | Anything stuck on user/external | append (deduplicate by description) |
| `drawer-cross-session-rules-digest.md` | Newly-learned rules to surface across sessions; tag each with `MUST` / `FORBID` / `NORM` (per [[severity-markers-must-forbid-norm]]) | append |
| `drawer-session-log.md` | Chronological session activity (timestamp + 1-line summary) | append; rotate at 500 lines |

### 2.1 Idempotent reconstruction

Per `gsd:resume-work` pattern: if any drawer file is missing on a subsequent run, the command auto-reconstructs it as an empty file with frontmatter only. A missing drawer must NOT abort the handover.

### 2.2 Rotation (session log only)

When `wc -l drawer-session-log.md` returns > 500 BEFORE appending, rename to `drawer-session-log-archive-<YYYY-MM>.md` (UTC month) and start a fresh `drawer-session-log.md`. Archives are not auto-loaded next session — only the current `drawer-session-log.md` is. Other drawers do NOT auto-rotate.

## 3 · MEMORY.md patching (CRITICAL drawer-loader contract)

The harness auto-memory loader only surfaces files explicitly linked from `MEMORY.md`. Drawer files are dead weight unless linked.

On every successful handover, the command MUST patch `~/.claude/projects/<slug>/memory/MEMORY.md` to ensure a `## Drawers` section exists with one wikilink per drawer:

```markdown
## Drawers

- [[drawer-project-state]]
- [[drawer-open-handovers]]
- [[drawer-reference-repositories]]
- [[drawer-harness-decisions]]
- [[drawer-active-blockers]]
- [[drawer-cross-session-rules-digest]]
- [[drawer-session-log]]
```

The patch is idempotent: only adds missing links; never duplicates.

## 4 · Output

1. **Console**: structured handover markdown (~30-100 lines depending on session length).
2. **`.planning/handover-<ts>.md`**: same markdown saved to disk for later reference. Filename uses ISO-8601 compact: `handover-20260527T143205.md`. Slug collisions append `-N` suffix.
3. **Memory drawers**: appended as described above.
4. **`MEMORY.md`**: patched with `## Drawers` section if absent.

## 5 · Handover markdown shape

```markdown
# Handover — <ISO-8601-ts>

**Branch:** <branch>
**HEAD:** <short-SHA> — <commit-title>
**Reason:** <--reason value, or "session end">
**Clean tree:** <yes/no>

## What we shipped
<bullets from drawer-project-state additions>

## In flight (resume next session here)
<bullets from drawer-open-handovers additions + cross-session rules to surface>

## Active blockers
<bullets from drawer-active-blockers additions>

## Cross-session rules to remember
- MUST | FORBID | NORM <one-line rule> — <one-line why>

## Session log
<append-only chronological list saved to drawer-session-log.md>
```

## 6 · Always end with AskUserQuestion

After writing drawers + `MEMORY.md` patch + `.planning/handover-<ts>.md`, emit an AskUserQuestion offering:
1. Print the handover markdown to chat (default yes).
2. Open the file in an editor.
3. Mark session as ended (no follow-up).

## 7 · Provenance

- Spec: `docs/superpowers/specs/2026-05-27-lifecycle-protocol-design.md` §3.3.
- Validated by the v1 spec council (2026-05-27).
- Memory: `[[handover-7-drawers]]`, `[[severity-markers-must-forbid-norm]]`, `[[lifecycle-protocol-overview]]`.
````

- [ ] **Step 2: Run the schema tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_handover.py -v`
Expected: PASS — all 6 tests green.

- [ ] **Step 3: Commit**

```bash
git add skills/handover/SKILL.md tests/test_lifecycle_handover.py
git commit -m "feat(skills): add handover skill with 7-drawer protocol + MEMORY.md patching"
```

### Task 1.4: Write the thin wrapper command

**Files:**
- Create: `.claude/commands/gflow/handover.md`

- [ ] **Step 1: Create the wrapper**

Create `.claude/commands/gflow/handover.md` with this exact content:

```markdown
---
description: Capture gflow-cli session state into 7 memory drawers + a structured handover markdown. Append-only with POSIX O_APPEND atomicity. HARD-GATE: clean tree OR `--reason <string>` required. Wrapper around skills/handover/SKILL.md.
---

# `/gflow:handover [--reason <string>]`

**Invoke `Skill(skill="handover")` now**, passing `$ARGUMENTS` if present. The skill at `skills/handover/SKILL.md` is the canonical body — preflight, 7-drawer protocol, MEMORY.md patching, idempotent reconstruction, drawer rotation, output shape.

Sibling: `/gflow:pause-work` and `/gflow:resume-work` from the gsd:* family cover phase-numbered project-management workflows; this command is task-scoped (PR-level) and memory-drawer-native — see spec §1.1.
```

- [ ] **Step 2: Verify wrapper line count and skill reference**

Run: `wc -l .claude/commands/gflow/handover.md`
Expected: ≤15 lines.

Run: `grep -c 'Skill(skill="handover")' .claude/commands/gflow/handover.md`
Expected: 1.

- [ ] **Step 3: Commit**

```bash
git add .claude/commands/gflow/handover.md
git commit -m "feat(commands): /gflow:handover thin wrapper around skills/handover"
```

### Task 1.5: Save memory entries + update MEMORY.md

**Files (out-of-repo):**
- Create: `~/.claude/projects/<slug>/memory/lifecycle-protocol-overview.md`
- Create: `~/.claude/projects/<slug>/memory/severity-markers-must-forbid-norm.md`
- Create: `~/.claude/projects/<slug>/memory/handover-7-drawers.md`
- Modify: `~/.claude/projects/<slug>/memory/MEMORY.md` (add 3 rows)

- [ ] **Step 1: Write `lifecycle-protocol-overview.md`**

Index of the 3 new commands + Phase 2 router. Cross-links to spec, council validation memories. ~30 lines.

- [ ] **Step 2: Write `severity-markers-must-forbid-norm.md`**

Canonical definitions of MUST / FORBID / NORM (per spec §5.2). Cross-link to action-tier companion (Critical / Important / Minor per §5.9). ~20 lines.

- [ ] **Step 3: Write `handover-7-drawers.md`**

Drawer contract + idempotent reconstruction + rotation policy. Reference MEMORY.md patching as the load-bearing detail. ~40 lines.

- [ ] **Step 4: Update `MEMORY.md`**

Append 3 rows to the index (one per memory file above).

### Task 1.6: Dogfood the handover skill on this PR

- [ ] **Step 1: Verify skill registers**

In a fresh Claude Code session OR via tool inspection: confirm `handover` appears in the Skill tool list (auto-discovered from `skills/handover/SKILL.md` frontmatter).

- [ ] **Step 2: Manual smoke — invoke the wrapper**

Type `/gflow:handover --reason "PR #1 dogfooding"` in Claude Code session.
Expected: skill loads; preflight passes; drawer files created in `~/.claude/projects/<slug>/memory/`; `MEMORY.md` patched with `## Drawers` section; `.planning/handover-<ts>.md` saved.

- [ ] **Step 3: Verify drawer auto-load on a new session**

Open a new Claude Code session against this repo. Check the harness loads `MEMORY.md` and that the `## Drawers` section makes drawer content accessible (`view file` on a drawer should work).

- [ ] **Step 4: Commit any plan refinements discovered**

If dogfooding surfaced issues, fix them in skill body, re-test, commit.

### Task 1.7: Open PR

- [ ] **Step 1: Push branch**

Run: `git push -u origin feature/lifecycle-handover`

- [ ] **Step 2: Create PR via `gh pr create`**

Title: `feat(commands): /gflow:handover — session state capture with 7-drawer protocol`

Body should reference:
- Spec section §3.3
- Memory entries created (lifecycle-protocol-overview, severity-markers, handover-7-drawers)
- Test plan checkboxes (schema tests pass; manual dogfooding evidence)

- [ ] **Step 3: Mark as Ready (NOT draft) once CI passes**

Per `[[draft-pr-merge-trap]]`: `gh pr ready <N>` before any merge attempt.

---

## PR #2 — `/gflow:branch-review` (~4 features, est. ~1 day)

**Branch:** `feature/lifecycle-branch-review`

**Dependency:** PR #1 (handover) merged — branch-review will reference `.planning/` ledger from handover output.

### Task 2.1: Write the wrapper schema test

**Files:**
- Create: `tests/test_lifecycle_branch_review.py`

- [ ] **Step 1: Write the failing test**

```python
"""Schema test for the /gflow:branch-review wrapper + pr-council-review branch-mode insertion."""

from __future__ import annotations

from pathlib import Path

import pytest

WRAPPER = Path(__file__).resolve().parents[1] / ".claude" / "commands" / "gflow" / "branch-review.md"
COUNCIL_SKILL = Path(__file__).resolve().parents[1] / "skills" / "pr-council-review" / "SKILL.md"


def test_wrapper_exists() -> None:
    assert WRAPPER.exists(), f"Wrapper missing: {WRAPPER}"


def test_wrapper_is_thin() -> None:
    """Wrapper must be ≤15 lines per spec §5.1 (PR #99 precedent)."""
    lines = WRAPPER.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 15, f"Wrapper has {len(lines)} lines; expected ≤15"


def test_wrapper_invokes_pr_council_review_skill() -> None:
    text = WRAPPER.read_text(encoding="utf-8")
    assert 'Skill(skill="pr-council-review")' in text, (
        "branch-review wrapper must invoke the pr-council-review skill"
    )
    assert "branch mode" in text.lower(), "Wrapper must signal branch mode"


def test_council_skill_has_branch_mode_section() -> None:
    """The shared skill must contain a Branch Mode section per spec §3.2 LP-2."""
    text = COUNCIL_SKILL.read_text(encoding="utf-8")
    assert "branch mode" in text.lower(), "pr-council-review SKILL.md must document branch mode"
    assert "REVIEWED_SHA" in text, "Skill must specify REVIEWED_SHA pinning (v2.1 precedent)"


def test_council_skill_has_release_branch_downgrade() -> None:
    """Spec §3.2: D11 RED → YELLOW until tag is cut on release/* branches."""
    text = COUNCIL_SKILL.read_text(encoding="utf-8")
    assert "release/*" in text, "Skill must document release/* branch special case"
    assert "git tag --list" in text, "Skill must specify tag-cut detection mechanism"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_branch_review.py -v`
Expected: FAIL — wrapper missing, branch-mode section missing.

### Task 2.2: Add Branch Mode section to `pr-council-review` SKILL.md

**Files:**
- Modify: `skills/pr-council-review/SKILL.md` — add `## Branch mode (NEW v2.2)` section after the existing Phase 7 (Provenance & extensions)

- [ ] **Step 1: Locate the insertion point**

Run: `grep -n "Provenance & extensions" skills/pr-council-review/SKILL.md`
Note the line number.

- [ ] **Step 2: Append Branch Mode section**

After the Provenance section, add:

````markdown
---

## Branch mode (v2.2 — for `/gflow:branch-review`)

The same protocol runs on a local branch instead of a PR. Activated when the wrapper passes a `BRANCH_MODE` signal in its invocation prompt. Differences:

### Phase 0 changes
- No `gh pr view <N>` validation; instead `head_branch = $(git branch --show-current)` and `REVIEWED_SHA = $(git rev-parse HEAD)`.
- No draft-PR banner (irrelevant; no PR exists).
- No `gh pr ready` reminder.
- Skip the "no open PRs" Phase 1 short-circuit — branch mode always reviews `HEAD vs base` (default `develop`).

### Phase 2 changes
- Diff source: `git diff <base>..HEAD` instead of `gh pr diff <N>`.
- `PR_META` becomes `BRANCH_META`: `git log <base>..HEAD --oneline`, `git diff --stat <base>..HEAD`, `git status --porcelain`.
- File reading discipline: sub-agents CAN use `Read` on the working tree IF `git -C <orchestrator-cwd> branch --show-current` matches the orchestrator's `head_branch` AND `git -C <orchestrator-cwd> rev-parse HEAD` matches `REVIEWED_SHA`. Mismatch on either → fall back to `git show REVIEWED_SHA:<path>`.

### `release/*` branch downgrade
If `head_branch` matches `release/*`, the council's **D11 (Release-gate compliance)** will RED-flag an in-progress CHANGELOG / version bump. Treat D11 as **YELLOW not RED until tag is cut** — surface this downgrade in the verdict report.

**Tag-cut detection:** `git tag --list "v*" --contains HEAD` returns non-empty → tag is cut. Do NOT use `gh release list` (requires network) or branch-name parsing.

### Phase 5 synthesis — REVIEWED_SHA drift handling
At Phase 5 step 5, compare `git rev-parse HEAD` to `REVIEWED_SHA`. If diverged (user committed mid-review, ran `git stash`, etc.), prepend the report with: *"Local HEAD moved during review (was X, now Y). Findings apply to X."* Do NOT silently re-review the new commits.

### Output differences
Phase 6 report shape is identical, but the `AskUserQuestion` offers:
1. **Apply fixes now** (invokes `Skill: superpowers:receiving-code-review`).
2. **Save findings as `.planning/branch-review-<branch>-<ts>.md`** (for later reference).
3. **Defer to PR-time review** (close branch-review session; findings discarded unless saved).

NO option to post to a PR — that's `/gflow:pr-council-review`'s job.

### HARD-GATES (branch mode)

<HARD-GATE>
Do NOT run the council before `/gflow:check` passes (lint + format +
types + tests must be green). Pre-existing red baseline will be
mis-flagged as new findings.
</HARD-GATE>

<HARD-GATE>
Do NOT post findings to a PR — this command is local-only. Use
`/gflow:pr-council-review <N>` for PR-time review.
</HARD-GATE>

### Provenance
Branch mode shipped 2026-05-27 (PR #2 of the lifecycle protocol). Validated by the v1 council audit on the spec (`docs/superpowers/specs/2026-05-27-lifecycle-protocol-design.md` §3.2).
````

- [ ] **Step 3: Verify the addition**

Run: `grep -n "Branch mode" skills/pr-council-review/SKILL.md`
Expected: at least 2 matches (the new section header + at least 1 reference in the body).

### Task 2.3: Write the thin wrapper

**Files:**
- Create: `.claude/commands/gflow/branch-review.md`

- [ ] **Step 1: Create the wrapper**

```markdown
---
description: Multi-dim council review of the current local branch (git diff base..HEAD). Same dimensions and protocol as /gflow:pr-council-review but no PR needed. HARD-GATE: /gflow:check baseline must be green first. Branch-mode addendum lives in skills/pr-council-review/SKILL.md.
---

# `/gflow:branch-review [--base <ref>]`

**Invoke `Skill(skill="pr-council-review")` now in BRANCH MODE** — pass `BRANCH_MODE=true` and `BASE_REF=$(arg or default 'develop')` to the skill. The canonical body at `skills/pr-council-review/SKILL.md` has a "Branch mode" section that documents the Phase 0/2/5 differences (no PR meta; `git diff base..HEAD` for diff source; `REVIEWED_SHA = current HEAD`; release/* branch D11 downgrade).

Sibling: `/gflow:pr-council-review <N>` for PR-time review on the same skill.
```

- [ ] **Step 2: Run all tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_branch_review.py -v`
Expected: all 5 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add skills/pr-council-review/SKILL.md .claude/commands/gflow/branch-review.md tests/test_lifecycle_branch_review.py
git commit -m "feat(commands): /gflow:branch-review extends pr-council-review with branch mode"
```

### Task 2.4: Dogfood — run the FIRST branch-review on the next-arriving feature branch

Per spec §A.4 Tier-2 (D2 MF#3 dogfooding chicken-egg): the first branch-review PR can't run itself. Skip self-dogfooding for PR #2; first real dogfooding run is on PR #3 (assess) before pushing.

- [ ] **Step 1: Document in PR body that self-dogfooding is intentionally deferred to PR #3**

- [ ] **Step 2: Push + open PR + mark Ready when CI green**

```bash
git push -u origin feature/lifecycle-branch-review
gh pr create --base develop --title "feat(commands): /gflow:branch-review (branch mode for council)" --body "<see plan §A.4 + spec §3.2>"
```

---

## PR #3 — `/gflow:assess <task>` (~9 features, est. 2-3 days)

**Branch:** `feature/lifecycle-assess`

**Dependency:** PR #1 + PR #2 merged.

### Task 3.1: Write the skill schema + dimension tests

**Files:**
- Create: `tests/test_lifecycle_assess.py`

- [ ] **Step 1: Write the failing test**

```python
"""Schema + presence tests for the /gflow:assess skill body."""

from __future__ import annotations

from pathlib import Path

SKILL = Path(__file__).resolve().parents[1] / "skills" / "assess" / "SKILL.md"


def test_skill_exists() -> None:
    assert SKILL.exists(), f"Assess skill missing: {SKILL}"


def test_frontmatter_valid() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "Skill must start with YAML frontmatter"
    fm_end = text.find("\n---\n", 4)
    assert fm_end > 0
    fm = text[4:fm_end]
    assert "name: assess" in fm
    assert "description:" in fm


def test_5_dimensions_present() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for dim in ("A1", "A2", "A3", "A4", "A5"):
        assert dim in text, f"Skill must document dimension {dim}"
    for name in ("Fit", "Precedent", "Risk", "Effort", "Memory-Search"):
        assert name in text, f"Dimension name {name!r} missing"


def test_iron_law_present() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "Iron Law" in text or "IRON LAW" in text.upper()
    assert "NO_PRIOR_ART" in text, "Iron Law must mention NO_PRIOR_ART fallback"


def test_hard_gates_xml() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "<HARD-GATE>" in text and "</HARD-GATE>" in text
    assert "<EXTREMELY-IMPORTANT>" in text
    assert text.count("<HARD-GATE>") >= 2, "Need ≥2 HARD-GATES per spec §5.8"


def test_4_status_protocol() -> None:
    text = SKILL.read_text(encoding="utf-8")
    for status in ("DONE", "DONE_WITH_CONCERNS", "NEEDS_CONTEXT", "BLOCKED"):
        assert status in text, f"4-status protocol missing {status}"


def test_severity_action_tier_matrix() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "MUST" in text and "FORBID" in text and "NORM" in text
    assert "Critical" in text and "Important" in text and "Minor" in text


def test_slug_generation_algorithm() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "kebab" in text.lower() or "slugify" in text.lower(), (
        "Slug algorithm must be specified (kebab-case truncation per D6 MF#3)"
    )


def test_mandatory_memory_slugs_table() -> None:
    text = SKILL.read_text(encoding="utf-8")
    assert "[[pr-must-verify-on-affected-surface]]" in text
    assert "[[gflow-strategy-local-first]]" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_assess.py -v`
Expected: FAIL — skill file doesn't exist.

### Task 3.2: Write `skills/assess/SKILL.md`

**Files:**
- Create: `skills/assess/SKILL.md`

The skill body must cover:
- Frontmatter with `name: assess`, comprehensive `description`
- `<EXTREMELY-IMPORTANT>` block at top
- 5-dimension table (A1-A5) with mandatory memory slugs column
- Iron Law block (composes with §5.9 status taxonomy)
- 4-status protocol (DONE/DONE_WITH_CONCERNS/NEEDS_CONTEXT/BLOCKED) with synthesizer handler
- Severity × action-tier matrix (MUST/FORBID/NORM × Critical/Important/Minor with valid combinations noted)
- Slug generation algorithm: `re.sub(r'[^a-z0-9-]', '', task.lower().replace(' ','-'))[:40]`; collision → append `-<HHMMSS>`
- `<HARD-GATE>` blocks for (a) status-before-verdict, (b) no-plan-on-RED
- A5 Memory-Search MUST instruct ToolSearch with `select:<mcp__*memory*>` before invoking
- INSUFFICIENT-DATA fallback for A4 on fresh repos
- Per-finding citation contract (`file:line` or memory `slug + lines`)
- Skill compositions (Skill: review for A1, Skill: security-review for A3, Skill: verification-before-completion before final report, Skill: superpowers:brainstorming on RED for refine-task loop)
- Phase 6 always ends with AskUserQuestion (apply fixes / proceed to plan / refine task)

- [ ] **Step 1: Create the file with the structure above** (use spec §3.1 as the source-of-truth for content; ~250-300 lines)

- [ ] **Step 2: Run schema tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_lifecycle_assess.py -v`
Expected: all 9 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add skills/assess/SKILL.md tests/test_lifecycle_assess.py
git commit -m "feat(skills): add assess skill with 5-dim council protocol + HARD-GATEs"
```

### Task 3.3: Write the thin wrapper

**Files:**
- Create: `.claude/commands/gflow/assess.md`

- [ ] **Step 1: Create the wrapper**

```markdown
---
description: Multi-dim audit BEFORE planning. Five dimensions (Fit/Precedent/Risk/Effort/Memory-Search). Each finding tagged MUST/FORBID/NORM × Critical/Important/Minor. HARD-GATE: RED verdict blocks /gflow:plan invocation. Wrapper around skills/assess/SKILL.md.
---

# `/gflow:assess <task-description>`

**Invoke `Skill(skill="assess")` now**, passing `$ARGUMENTS` as the task description. The skill at `skills/assess/SKILL.md` runs the 5-dim council, outputs `.planning/assess-<slug>.md`, and offers next-step AskUserQuestion (apply fixes / proceed to /gflow:plan / refine task and re-assess).

Sibling: `/gflow:plan` reads the latest `.planning/assess-*.md` for context if invoked after a GREEN/YELLOW assess.
```

- [ ] **Step 2: Commit**

```bash
git add .claude/commands/gflow/assess.md
git commit -m "feat(commands): /gflow:assess thin wrapper around skills/assess"
```

### Task 3.4: Dogfood — run `/gflow:branch-review` on this branch BEFORE pushing

Per spec §A.4: first real dogfooding run of branch-review happens here.

- [ ] **Step 1: Run `/gflow:check`**

Run: `/gflow:check`
Expected: green baseline (lint, format, types, tests all pass).

- [ ] **Step 2: Run `/gflow:branch-review`**

Run: `/gflow:branch-review --base develop`
Expected: council dispatches; verdict produced; HARD-GATE on green-baseline-first allows execution.

- [ ] **Step 3: Apply any must-fixes the branch-review surfaces**

- [ ] **Step 4: Re-run branch-review to confirm GREEN**

### Task 3.5: Dogfood — run `/gflow:assess` on a hypothetical follow-up task

- [ ] **Step 1: Pick a hypothetical task** (e.g., "add MCP server for gflow-cli per PLAN.md Phase 7 backlog")

- [ ] **Step 2: Run `/gflow:assess "<task>"`**

Expected: 5 dimensions dispatched in parallel; verdict produced; `.planning/assess-add-mcp-server.md` saved; AskUserQuestion offered.

- [ ] **Step 3: Document the dogfooding output in PR body**

- [ ] **Step 4: Commit any plan refinements discovered**

### Task 3.6: Run `/gflow:handover` to capture session state

- [ ] **Step 1: Run `/gflow:handover --reason "PR #3 ready for review"`**

Verify drawers updated, MEMORY.md patched.

### Task 3.7: Open PR + final review

- [ ] **Step 1: Push**

Run: `git push -u origin feature/lifecycle-assess`

- [ ] **Step 2: Create PR**

Title: `feat(commands): /gflow:assess — multi-dim audit BEFORE planning`

Body covers: spec §3.1, the 5 dimensions, severity matrix, dogfooding evidence from Tasks 3.4 and 3.5.

- [ ] **Step 3: Run `/gflow:pr-council-review <N>` for PR-time review** (final validation that the skill we just shipped works in PR mode).

- [ ] **Step 4: Mark Ready when CI green** (`gh pr ready <N>`)

---

## Cross-PR — Memory consolidation after all 3 ship

### Task X.1: Delete the spec file (per LP-9 + `[[release-spec-plan-memory-consolidation]]`)

- [ ] **Step 1: Verify all 3 PRs merged into develop**

Run: `gh pr list --state merged --search "lifecycle-protocol"`

- [ ] **Step 2: Open a final cleanup PR**

Branch: `chore/lifecycle-protocol-spec-cleanup`

- [ ] **Step 3: Delete spec + plan**

```bash
git rm docs/superpowers/specs/2026-05-27-lifecycle-protocol-design.md
git rm docs/superpowers/plans/2026-05-27-lifecycle-protocol-implementation.md
```

- [ ] **Step 4: Verify durable patterns are in memory**

Memory entries that must exist: `lifecycle-protocol-overview`, `severity-markers-must-forbid-norm`, `handover-7-drawers`.
Run: `ls ~/.claude/projects/<slug>/memory/ | grep -E "lifecycle|severity|handover"`

- [ ] **Step 5: Commit + open PR**

```bash
git commit -m "chore(docs): consolidate lifecycle protocol spec/plan into memory (per LP-9)"
git push -u origin chore/lifecycle-protocol-spec-cleanup
gh pr create --base develop --title "chore(docs): consolidate lifecycle protocol spec/plan into memory"
```

---

## Self-review checklist

**1. Spec coverage check:**
- [x] §3.1 `/gflow:assess` → PR #3 Tasks 3.1-3.5
- [x] §3.2 `/gflow:branch-review` → PR #2 Tasks 2.1-2.4
- [x] §3.3 `/gflow:handover` → PR #1 Tasks 1.2-1.6
- [x] §4 Phase 2 router — explicitly deferred in spec (no plan tasks needed)
- [x] §5.7 composition risks — addressed per PR (e.g. branch-review HARD-GATE for green baseline in Task 2.2)
- [x] §5.8 HARD-GATEs — Task 1.3 and Task 3.2 require XML blocks (tested)
- [x] §5.9 4-status protocol — Task 3.1 tests for it; Task 2.2 adds branch-mode synthesis handler
- [x] §A.4 Tier-2 items — all 8 mapped to concrete tasks:
  - Probe algorithm → Task 3.2 (A5 ToolSearch)
  - Sub-agent cwd → Task 2.2 (branch-mode file-reading discipline)
  - Rotation actor → Task 1.3 (skill body: "the handover command MUST check `wc -l`")
  - Status downgrade rules → Task 3.2 (§5.9 table in skill body)
  - Slug algorithm → Task 3.2 (kebab-case-truncate-40)
  - Drawer atomicity → Task 1.3 (HARD-GATE specifies `open(path, "a")`)
  - HARD-GATE user messages → Task 1.3 + Task 3.2 (first-line `HARD-GATE FAILED:` prefix)
  - Dogfooding ordering → Task 2.4 (defer self-dogfooding to PR #3)

**2. Placeholder scan:** searched for "TBD" / "TODO" / "implement later" — none found.

**3. Type consistency:** drawer file names match across Task 1.2 (test) and Task 1.3 (skill body). HARD-GATE XML format matches across Task 1.3 + Task 3.2.

**4. No spec requirement without a task.** Confirmed via the §A.4 mapping above.

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-05-27-lifecycle-protocol-implementation.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration. Best for this plan since the 22 tasks are well-isolated and the skill body Tasks (1.3, 2.2, 3.2) benefit from focused single-task attention.

**2. Inline Execution** — Execute tasks in this session using `executing-plans`, batch execution with checkpoints. Faster but less isolated; risk of context bleed between PR #1, PR #2, PR #3 task chains.

**Which approach?**
