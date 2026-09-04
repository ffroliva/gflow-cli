---
name: sonarcloud-hotspot-review-workflow
description: "SonarCloud PR quality gate fails on new_security_hotspots_reviewed<100%; mark verified-safe hotspots via SONAR_TOKEN (POST /api/hotspots/change_status), then re-run the SonarCloud CI job to repost the GitHub status."
---

A green test/pyright run can still leave the PR's GitHub merge state `UNSTABLE` because the **SonarCloud quality gate** is a separate check. The condition that most often blocks a feature PR is **`new_security_hotspots_reviewed` (needs 100%)** — a single unreviewed hotspot fails the gate even when coverage/duplication/ratings all pass.

**Diagnose** (token = `SONAR_TOKEN` in `.env.local`; project key `ffroliva_gflow-cli`; curl is blocked by the context-mode hook → use `ctx_execute` javascript `fetch` with `Authorization: Basic base64(token+":")`):
- `GET /api/qualitygates/project_status?projectKey=ffroliva_gflow-cli&pullRequest=<N>` → lists each condition + which is ERROR.
- `GET /api/hotspots/search?projectKey=ffroliva_gflow-cli&pullRequest=<N>&status=TO_REVIEW` → the offending hotspot key + file:line.

**Resolve a verified false positive:** `POST /api/hotspots/change_status` with `hotspot=<key>&status=REVIEWED&resolution=SAFE&comment=<justification>` (HTTP 204 = success). Re-poll project_status → should be OK.

**🚨 GitHub check stays stale:** marking a hotspot SAFE does NOT auto-repost the GitHub commit status — the analysis job must re-run. `gh run rerun <run-id> --job <sonar-job-id>` (the coverage-xml artifact from the original run is still available to it) → SonarCloud re-scans, sees gate OK, posts a passing check → state flips `UNSTABLE`→`CLEAN`.

**Other gate that bites: `new_coverage < 80%` (PR #137, 2026-06-01).** A PR can be fully tests-green (all `test (3.x)` pass) yet Sonar-RED because **coverage of NEW/changed lines** dipped below 80%. `GET project_status` shows `ERROR new_coverage: actual=78.5 LT 80`. Cause is usually a new orchestration function (e.g. a CLI `_run_*` that wires the pieces) that unit tests skip. Fix = add a direct unit test for it (mock the client + recorder); `cli_scene` went 53%→82%, clearing the gate. There is NO marking-safe shortcut for coverage — you must add tests. Diagnose the exact failing condition via the `project_status` API before guessing.

**Example (PR #135, 2026-05-31):** the lone hotspot was SHA-1 in `_sapisidhash.py:14` — Google's mandated SAPISIDHASH wire scheme (non-crypto, `# noqa: S324`, dead/experimental since the Bearer pivot), D3-council-verified safe. Only mark SAFE when genuinely a false positive; the `.env.local` comment provisions the token for exactly this. Related: [[sonarcloud-setup]], [[pyright-src-whole-tree-gate]].
