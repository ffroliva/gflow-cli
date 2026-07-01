---
description: Check the SonarCloud quality gate for a PR (or the current branch) and drive it to zero. Reports GREEN, or the exact PR-scoped new issues/hotspots/coverage gaps to fix.
---

# `/gflow:sonar [PR#]` — SonarCloud quality gate

The reusable Sonar primitive. `/gflow:check` is **local** and cannot see Sonar (it
is server-side, post-push); `/gflow:pr-council-review` is an LLM diff review, not the
gate verdict. This command answers one question: **is the SonarCloud gate green
(zero new issues) for this PR, and if not, exactly what must be fixed?**

`$ARGUMENTS` is the PR number. If empty, resolve it from the current branch
(`gh pr view --json number -q .number`).

Project: key `ffroliva_gflow-cli`, org `ffroliva-github` (see `sonar-project.properties`).
The CI scan sets `sonar.qualitygate.wait=true`, so a **green `SonarCloud analysis`
check means the gate genuinely passed** — but the gate **API is stale until that
check finishes**, so always check the GitHub check FIRST.

## Steps

**1. Check the GitHub check first (never trust the gate API while it's pending).**

```bash
gh pr checks <N> --json name,state,bucket | \
  jq -r '.[] | select(.name=="SonarCloud analysis") | "\(.bucket) \(.state)"'
```

- `pass` → **gate is GREEN / zero new issues. Done.** Report GREEN and stop.
- `pending` → the SonarCloud job (~50s) runs *after* the ~3.5min test matrix; wait
  (`gh pr checks <N> --watch`) before reading the API, or the API returns the
  previous commit's verdict.
- `fail` → continue to step 2 to enumerate the exact failing conditions.

**2. Enumerate the failing conditions (PR-scoped — this is the #1 gotcha).**

New-code issues live on the PR branch, NOT `main`. **Scope every call with
`&pullRequest=<N>`** or the API reports 0 and you chase phantoms. Token is in
`.env.local` as `SONAR_TOKEN` — read it inside a sandbox so it never lands in chat.
`curl` may be blocked by the context-mode hook → use `ctx_execute` (javascript
`fetch` with `Authorization: Basic base64(token+":")`).

```bash
# Which gate conditions are ERROR:
GET /api/qualitygates/project_status?projectKey=ffroliva_gflow-cli&pullRequest=<N>
# New issues (bugs/smells) with file:line + rule + creationDate:
GET /api/issues/search?componentKeys=ffroliva_gflow-cli&pullRequest=<N>&resolved=false
# Unreviewed security hotspots:
GET /api/hotspots/search?projectKey=ffroliva_gflow-cli&pullRequest=<N>&status=TO_REVIEW
```

**3. Fix by condition — no gaming.**

- **`new_coverage < 80%`** → add **real** unit tests for the uncovered new lines
  (usually a new orchestration/`_run_*`/helper). There is NO mark-safe shortcut and
  NEVER widen `sonar.coverage.exclusions` to dodge it.
- **`new_code_smells` / `new_maintainability_rating` (e.g. S1192 duplicated literal,
  S3776 cognitive complexity)** → fix at source. For a duplicated literal your diff
  merely *touched*, collapse it to one module-scope alias so it falls under the
  threshold (pyright/tests prove it's safe). For S7497, re-raise swallowed
  `asyncio.CancelledError`.
- **`new_security_hotspots_reviewed < 100%`** → only for a **genuine** false positive:
  `POST /api/hotspots/change_status` form body
  `hotspot=<key>&status=REVIEWED&resolution=SAFE&comment=<justification>` (204 = ok),
  then **re-run the SonarCloud job** so it reposts the GitHub status
  (`gh run rerun <run-id> --job <sonar-job-id>` — the coverage artifact is reused).
  Never mark a real hotspot SAFE.

**4. Push fixes, then re-verify from step 1** (the check must read `pass`).

## Output

- Verdict: **GREEN** (gate passed, zero new issues) or **RED** with the exact failing
  conditions and a fix list (`file:line · rule · what to do`).
- After any fix: re-confirm via step 1 — green check = gate passed.

## See also

- `docs/GITHUB.md` § SonarCloud Quality Gate — full policy + coverage-exclusion rationale.
- `/gflow:check` — the local pre-commit gates (coverage floor pre-empts `new_coverage`).
- Memory: [[sonarcloud-pr-issues-scope-s7497]], [[sonarcloud-new-code-gotchas]],
  [[sonarcloud-hotspot-review-workflow]], [[sonarcloud-setup]].
