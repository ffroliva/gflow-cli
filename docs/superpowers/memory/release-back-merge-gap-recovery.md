---
name: release-back-merge-gap-recovery
description: "Release-time drift in both directions: main ahead of develop (skipped back-merge → next release branch conflicts on bump + CHANGELOG; take ours, re-add the footer) and develop ahead of the release branch (moved between cut and tag; merge, re-home CHANGELOG, re-run 4b, re-sign while unpushed)"
---

Rule: Always do the **main → develop back-merge** as the LAST step of every release. If you didn't, the next release branch will hit predictable conflicts because main has a version bump + CHANGELOG section that develop never received.

Specifically the conflict shape (observed 2026-05-23 cutting v0.8.0 after v0.7.0 skipped the back-merge):
- `pyproject.toml` — both branches changed `version = "<old>"`. Take ours (the new version).
- `src/gflow_cli/__init__.py` — same. Take ours.
- `CHANGELOG.md` — main has `## [<previous>]` section with content; develop's release branch already moved its [Unreleased] entries into `## [<new>]`. Take ours, then verify the footer manually adds the missing `[<previous>]: ...compare/...` line.
- `uv.lock` — usually a trivial conflict (one line, the package version metadata). Take ours.

**Resolution recipe (verified):**

```bash
# After conflict on the release branch:
git checkout --ours pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md uv.lock
git add pyproject.toml src/gflow_cli/__init__.py CHANGELOG.md uv.lock
# Verify versions:
grep version pyproject.toml  # → version = "<new>"
grep __version__ src/gflow_cli/__init__.py  # → __version__ = "<new>"
# Verify CHANGELOG footer has both <new> AND <previous> links — manually edit if missing.
git commit --no-edit -m "Merge main into chore/release-vX.Y.Z — resolve conflicts (keep version, my CHANGELOG)"
git push
```

**Why:** main carries the bump commit from the previous release because `/gflow:release` step 14 ships the bump via PR on `main`. develop, however, is updated only by step 15's back-merge. If step 15 is skipped, develop's `pyproject.toml` stays at the OLD version (e.g., `0.6.0a6`) even though `__version__` shipped on PyPI is `0.7.0`. The next release branch cut from develop inherits the stale version, then conflicts with main's record of the bump.

The v0.7.0 release skipped this; the v0.8.0 release (PR #42) had to merge main into the release branch with conflicts and resolve manually. The PyPI publish still worked because it's triggered by the TAG (not the PR), but the PR itself was BLOCKED until conflicts were resolved.

**How to apply:**
- After cutting a release, the back-merge `main → develop` is mandatory. Do NOT skip even if it feels redundant. Use:
  ```bash
  git checkout develop && git pull --ff-only
  git merge origin/main --no-ff -m "Merge main into develop — back-merge v<NEW> release (PR #<N>)"
  git push origin develop
  ```
- If you find yourself in the conflict situation again, follow the recipe above — don't reinvent.
- `/gflow:release` step 15 is the canonical doc. Treat it as load-bearing, not optional.
- **Sanity check before tagging:** `git rev-list --left-right --count origin/main...origin/develop` — if the left side is non-zero, develop is behind main and the release branch will conflict.

**Reference:** PR #42 commit `08c081e` is the canonical conflict-resolution commit. `phase-b-followups` memory item D documents the original v0.7.0 skip.

**See also:** [[branch-workflow]], [[release-signing]].

## Shared-checkout race at cut time (2026-09-05, v0.68.0)

The mirror-image failure: `develop` moving *after* the release branch is cut but *before*
the tag. Two things went wrong in one evening, both in a checkout shared by four sessions:

- The release branch was checked out in the shared working tree and another session's
  `git checkout` switched it away mid-prep. Recovery cost real time. **Cut the release
  branch in a dedicated worktree** (`git worktree add -b chore/release-vX ../gflow-cli-release-vX origin/develop`)
  and announce the cut to every session (`ListAgents` + message) before doing it.
- PR #672 merged into `develop` between the cut and the tag. The local signed tag then
  pointed one commit before the fix the release notes claimed. Because nothing had been
  pushed, the fix was `git merge origin/develop` on the release branch, re-home the
  newcomer's `[Unreleased]` CHANGELOG entry under the new version, `git tag -d` +
  re-sign. **Step 12 of the release skill now runs `git rev-list --count HEAD..origin/develop`
  before signing** — expect 0.

Same root cause as the gap above (two branches that must agree drifting between two
steps), opposite direction: there `main` ran ahead of `develop`; here `develop` ran
ahead of the release branch.
