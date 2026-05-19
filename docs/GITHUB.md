# GitHub PR Protocol

This protocol is the maintainer routing guide for GitHub pull requests. Use it
when deciding what to do with an external or internal PR.

## Quick Triage

1. Confirm the PR targets `develop`. Only release PRs target `main`.
2. Check contributor provenance:
   - commits use a real Git identity or GitHub noreply email
   - external commits include `Signed-off-by:`
   - no secrets, cookies, signed URLs, account tokens, or private captured data
3. Review the diff against the base branch:

   ```bash
   git fetch origin pull/<N>/head:pr-<N>-review
   git diff --stat origin/develop...pr-<N>-review
   git diff --check origin/develop...pr-<N>-review
   ```

4. Run focused tests for the touched behavior before trusting broad CI.
5. Read GitHub checks and comments. Treat bot output as advisory unless it is a
   required project gate or has concrete evidence.

## Scenario Matrix

| Scenario | Action |
|---|---|
| Internal PR, all checks green | Review the diff, then squash merge to `develop` when approved. |
| Forked PR, tests/gitleaks green, Sonar skipped | Review manually, then merge to `develop` if the change is low-risk. Sonar runs on trusted pushes after merge. |
| Forked PR, Sonar fails because `SONAR_TOKEN` is empty | Treat as CI configuration behavior, not a code finding. The Sonar job is skipped for forked PRs by design. |
| Forked PR is large, auth/security-sensitive, or unclear | Recreate or cherry-pick the change onto a maintainer-owned branch and open an internal PR so full trusted CI, including Sonar, can run before merge. |
| PR targets `main` | Ask the contributor to retarget to `develop`, or retarget it as maintainer if appropriate. Re-review after retargeting. |
| Contributor metadata is unclear | Ask them to amend author identity and add DCO sign-off. Do not add `Signed-off-by:` for them. |
| Contributor does not respond | Do not merge unclear provenance. Close politely or recreate the fix on a maintainer branch with attribution such as `Reported-by: @user` or `Based on PR #N by @user`. |
| Any required test/lint/type/security check fails | Do not merge until fixed or explicitly documented as an external infrastructure issue. |

## Forked PRs And SonarCloud

GitHub does not pass repository secrets to `pull_request` workflows from forked
repositories, except for `GITHUB_TOKEN`. That means secret-backed jobs such as
SonarCloud cannot run safely on untrusted fork code under the normal
`pull_request` event.

The CI workflow therefore skips SonarCloud for forked PRs and keeps these gates
active:

- Python test matrix
- repo hygiene
- ruff lint and format check
- pyright
- coverage generation
- gitleaks secret scan

This is intentional. Do not switch the normal PR workflow to
`pull_request_target` just to expose `SONAR_TOKEN`; that event runs with base
repository privileges and can expose secrets if it checks out or executes
untrusted PR code.

## If Sonar Did Not Run

For small PRs, merge to `develop` after review when tests and gitleaks are
green. Sonar will run on the trusted push to `develop`; if it flags an issue,
fix it before promoting `develop` to `main`.

For larger or risky external PRs, use a maintainer-owned validation branch:

```bash
git fetch origin pull/<N>/head:pr-<N>-review
git switch -c review/pr-<N> origin/develop
git cherry-pick <contributor-commit>
git push origin review/pr-<N>
```

Open an internal PR from `review/pr-<N>` to `develop` and wait for full CI,
including SonarCloud. Preserve attribution in the commit body when appropriate.

## Green PRs Are Not Automatically Cosmetic

A green PR means the configured checks passed in that context. It does not mean
the change is only cosmetic or risk-free.

Use this rule:

- docs-only, formatting-only, and comment-only diffs are cosmetic
- behavior changes require focused review and focused tests
- auth, secret handling, browser automation, CI, release, and network changes
  require extra scrutiny even when CI is green
- Sonar findings are usually maintainability, reliability, security, or coverage
  signals; small findings may be cosmetic, but they still need classification

## Merge Rule

Merge to `develop` only after:

- base branch is correct
- provenance is clear
- secrets are absent
- focused review is complete
- applicable checks are green, skipped for a documented reason, or reproduced as
  external infrastructure failure

Use squash merge for feature/fix PRs unless there is a specific reason to
preserve commit history.
