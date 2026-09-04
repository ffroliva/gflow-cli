---
name: git-add-all-sweeps-scratch-files
description: "`git add -A` swept gate-output and live-run captures into two PRs; check_repo_hygiene does NOT catch them because its allowlist governs root docs only"
---

**`git add -A` committed my own scratch files into two PRs on 2026-09-02** —
`g618.txt` (a quality-gate capture) into #618, and `e2e_run.txt` + `e2e_control.txt`
(live-run transcripts) into #637. Caught only by grepping `git ls-files` afterwards.

**Why nothing stopped it:** `scripts/ci/check_repo_hygiene.py` enforces an allowlist of
**root** documents. A stray `.txt` inside a worktree subdir is invisible to it, and
`check_doc_links` / the mirror check do not look at untracked-turned-tracked files
either. There is no mechanical gate for this.

**How to apply:** when redirecting command output while working in a worktree, write to
the **scratchpad dir**, never the repo tree. If you must write in-tree, `git status
--short` before staging and prefer explicit paths (`git add docs/ src/`) over `-A`. A
quick audit after any `-A` commit: `git ls-files | grep -E "\.(txt|log|json)$"` and check
nothing new appeared.

**Related smell:** the same `-A` also carried a doc onto the wrong branch. The
verification record linked from #618's CHANGELOG was committed to #637's branch, so
`check_doc_links` failed on #618 with `file not found` — a **cross-PR** dependency the
per-branch gates cannot see until you actually run them on the branch that carries the
link. Put a doc on whichever PR merges FIRST if another PR's prose links it.

Related: [[never-destructive-git-during-review]], [[pre-pr-verification-discipline]].
