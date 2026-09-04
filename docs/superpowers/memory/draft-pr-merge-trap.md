---
name: draft-pr-merge-trap
description: "Calling the GitHub `PUT /repos/{owner}/{repo}/pulls/{number}/merge` API on a DRAFT pull request returns HTTP 405 AND can trigger a side-effect that closes the PR + deletes its head ref. Always run `gh pr ready <n>` before any merge attempt."
---

When merging a PR via `gh api -X PUT repos/.../pulls/<n>/merge` (used as a workaround when `gh pr merge` can't checkout the target branch locally because it's in use by another worktree), the API will refuse a draft PR with HTTP 405 `"Pull Request is still a draft"`.

**Observed side-effect on the v0.8.1 cycle:** after that 405 response, PR #37 (sonar-polishing) was *also* moved to CLOSED state with its head ref `sonar-polishing` deleted by the same actor (the gh CLI's authenticated user). Whether the close was triggered by the failed merge attempt or by an unrelated webhook race is unclear, but the timing was sub-second simultaneous.

**Recovery (took 3 extra steps):**
1. `git push origin <local-rebased-branch>:<head-ref>` — recreate the deleted remote branch from the local rebase artifact.
2. `gh pr reopen <n>` — re-open the closed PR.
3. `gh pr ready <n>` — convert from draft.
4. Re-trigger CI by pushing a no-op commit if the CI checks are stale.
5. Then the proper `PUT /merge` succeeds.

**How to apply:**

Before any `PUT /merge` call (or `gh pr merge`), run:

```bash
state=$(gh pr view <n> --json state,isDraft -q '.state + ":" + (.isDraft|tostring)')
case "$state" in
  OPEN:true)   gh pr ready <n> ;;
  OPEN:false)  ;;  # ready, proceed
  *)           echo "PR not in OPEN state ($state); aborting"; exit 1 ;;
esac
```

Also: when `gh pr merge` fails with `'develop' is already used by worktree at ...`, the `gh api PUT /merge` route is the correct fallback — but **only after** confirming the PR isn't draft. The merge-via-API can't checkout locally (good — that's why it works around the worktree conflict) but it does run server-side state validation that includes the draft check.

Related: [[release-back-merge-gap-recovery]] (the back-merge step in releases tries to checkout develop, which collides with active worktree-based release work).
