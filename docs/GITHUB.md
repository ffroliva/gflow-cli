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

## Automated External PR Triage

External PR routing is handled by
`.github/workflows/external-pr-triage.yml`. It uses `pull_request_target`, but
only for repository metadata operations:

- label external PRs as `external-contribution`
- label them as `needs-maintainer-review`
- label them as `needs-copilot-review`
- request `@ffroliva` as reviewer
- post or update the external-contribution checklist comment

Bot PRs such as Dependabot updates are skipped by this human-contributor
triage. They still run normal CI and remain subject to branch protection.

This workflow must not checkout the PR branch, install dependencies, run tests,
or execute contributor code. Keep all code execution in the normal `pull_request`
CI workflow, where forked PRs do not receive repository secrets.

## GitHub Copilot Code Review

Copilot code review is the first AI review layer for this project. It is
advisory only: Copilot leaves a comment review, does not approve or request
changes, and does not replace maintainer review.

Repository-specific Copilot guidance lives in
`.github/copilot-instructions.md`. Keep those instructions focused on this
project's review risks: auth, browser automation, CI, release, secret handling,
provenance, and focused tests.

Enable automatic Copilot code review in GitHub repository settings/rulesets if
available for the account. If automatic review is unavailable, request Copilot
manually from the PR reviewers menu for PRs carrying the `needs-copilot-review`
label.

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

## SonarCloud Quality Gate

The gate is evaluated on **new code** (changed lines vs `main`, per
`sonar.newCode.referenceBranch`). Conditions: new-code coverage ≥ 80 %,
reliability / security / maintainability rating A, duplicated lines ≤ 3 %, and
all new security hotspots reviewed.

**The gate blocks CI.** `sonar-project.properties` sets
`sonar.qualitygate.wait=true`, so the scanner polls for the verdict and the
`SonarCloud analysis` check goes **red** when the gate is ERROR. Without that
flag the check only confirmed the scan was *submitted* — a failed gate showed a
misleading green while the real verdict sat on the dashboard. If you see a green
`SonarCloud analysis` check, the gate genuinely passed.

### Coverage exclusions (why a refactor can still be green)

Browser-automation and live-auth transports are exercised by the **e2e suite**
(real Chrome / live Flow), not unit tests. They are listed under
`sonar.coverage.exclusions` so unreachable Playwright/network glue does not drag
new-code coverage below the gate:

- `api/transports/ui_automation.py`, `ui_automation_video.py`
- `api/transports/experimental/{bearer,evaluate_fetch,sapisidhash}.py`

Excluded files are **still analysed** for bugs and code smells — only the
coverage metric ignores them. Everything else (CLI, data layer, helpers, REST
plumbing) must hit 80 % on changed lines: add unit tests, don't widen the
exclusion list.

### Reading a red gate

Open the PR Summary at `sonarcloud.io/summary/new_code?id=ffroliva_gflow-cli&pullRequest=<N>`.
- **Coverage failed** → add tests for the changed non-excluded lines.
- **New issues** (e.g. `S5655` "function expects a different type" after a
  `cast(...)` removal) → fix them, or keep the
  `cast(T, ...)  # pyright: ignore[reportUnnecessaryCast]` pattern that satisfies
  S5655/S5890 (removing those casts reintroduces the finding).

### SonarCloud outage handling

SonarCloud is a third-party service and does have outages (seen live
2026-07-16 — a ~504 across its whole API, including the unauthenticated
`/api/system/status` endpoint). The `sonar` job's `Check SonarCloud
availability` step probes that endpoint fresh on every run and skips the
`SonarCloud scan` step (job stays green) when it's down — no manual flag to
remember to flip back once it recovers.

During an outage there's still local coverage: `git push` runs
`scripts/dev/sonar-pre-push-gate.sh` (wired via `.pre-commit-config.yaml`'s
`pre-push` stage — install with `pre-commit install --hook-type pre-push`).
It re-checks SonarCloud itself and only falls back to a scan against the
shared local SonarQube instance
(`../shared-infra/sonarqube`, via `scripts/dev/sonar-local-scan.sh`) when
SonarCloud is unreachable. Both scripts skip gracefully (exit 0) if that
optional local infra isn't checked out — most contributors won't have it,
and that must never block a push. If the outage persists past your push,
re-run the `sonar` job once SonarCloud recovers before merging.

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
