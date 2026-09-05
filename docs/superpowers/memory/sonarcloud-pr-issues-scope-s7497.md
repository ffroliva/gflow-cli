---
name: sonarcloud-pr-issues-scope-s7497
description: "SonarCloud \"new_bugs>0 but issues API shows 0\" = forgot &pullRequest=N; plus S7497 fix"
---

When SonarCloud reports `new_bugs > 0` (failing `new_reliability_rating`) but the issues API "returns 0 bugs", the API call was almost certainly **not scoped to the PR**. New-code issues live on the pull-request branch, not `main`.

Scope every issues/measures/qualitygate call with `&pullRequest=<N>`:
- `api/issues/search?componentKeys=ffroliva_gflow-cli&pullRequest=204&types=BUG`
- `api/measures/component?...&pullRequest=204&metricKeys=new_bugs,new_reliability_rating`
- `api/qualitygates/project_status?projectKey=ffroliva_gflow-cli&pullRequest=204` → shows exactly which condition fails.

These are real trackable findings, NOT phantom data-flow sensor results. Auth: `curl -u "$SONAR_TOKEN:"` (token in `.env.local`). Rule desc: `api/rules/show?key=python:S7497&organization=ffroliva-github`.

**python:S7497** ("Cancellation exceptions should be re-raised") = an `async` function catches `asyncio.CancelledError` and swallows it (`pass` / `break` / log-only). Fix = re-raise after cleanup. PR #204: daemon worker loop did `except CancelledError: break` (→ log + `raise`); lifespan shutdown did `except CancelledError: pass` after `await worker_task` (→ `await asyncio.gather(worker_task, return_exceptions=True)`, which captures the child's cancel but re-raises a genuine cancel of the lifespan task — no `except` clause to flag). `CancelledError` is `BaseException`, so a sibling `except Exception` never catches it. See [[sonarcloud-new-code-gotchas]], [[sonarcloud-setup]].
