---
name: pr-hygiene-revert-and-multi-commit
description: "PR body hygiene — \"Refs"
---

Two related PR-hygiene rules from incidents this session.

## 1. "Refs #N" not "Closes #N" in revert / workaround / scaffold PRs

GitHub auto-closes issues linked via "Closes #N" / "Fixes #N" when the PR merges. A revert PR removes broken code but does NOT fix the underlying bug — the issue should stay open until the actual fix lands. Same for workaround patches, dev-tooling PRs, and verification scaffolds. Reserve "Closes #N" for PRs that genuinely deliver the bug fix.

**Why:** PR #57 (revert of PR #50's i2i half) wrote "Closes #56" in its body — auto-closed the bug on merge, which signaled "resolved, nothing to do" to @svasakorn (the external contributor working on the actual fix). Had to reopen and apologize. svasakorn correctly used "Refs #56" on his follow-up PR #60 (wanted us to verify on ffroliva first) and PR #61 — learned from this mishap.

**How to apply:** in any PR body that's not the bug fix itself, write `Refs #N` / `Part of #N`. Only the final, verified fix PR should say `Closes #N`. If a `Closes #N` slips through and the wrong issue auto-closes, reopen + post a clarifying comment with an explicit `@<contributor>` ping ASAP — the closed state signals "done" even if your other artifacts say otherwise.

## 2. Multi-fix PRs: keep distinct fixes as separate commits

When bundling two related-but-independent fixes in one PR, keep them as separate commits. Partial revert (`git revert <commit>`) is then clean and preserves the good half. If squashed at merge, partial revert is no longer possible without manual extraction.

**Why:** PR #50 = commit `529d335` (i2i ref attach, broken on some accounts) + commit `57d1746` (`__aenter__` teardown guard, broadly valuable, surfaced cleanly during debug). When the i2i half failed live verification, we ran `git revert 529d335` and kept `57d1746` cleanly. PR #57 was a one-commit, one-file revert with the teardown bullet preserved in CHANGELOG. Had PR #50 been squashed at merge, we'd have lost the teardown guard too OR had to manually re-extract it.

**How to apply:**
- For PRs containing 2+ logically distinct fixes, prefer `--merge` over `--squash` so partial revert remains an option. Web UI defaults to "Squash and merge" — explicitly choose "Create a merge commit" if multi-fix.
- Single-purpose PRs (e.g., PR #61 — one file, one logical change) can squash freely.
- Repo convention before this session was `--merge` for all PRs (#48/#50/#51/#53/#57/#59). PR #60 and #61 were squashed via web UI — neither needed partial revert capability, so the convention drift was harmless this time. Just be deliberate.

See [[draft-pr-merge-trap]] for adjacent PR-merge gotchas.
