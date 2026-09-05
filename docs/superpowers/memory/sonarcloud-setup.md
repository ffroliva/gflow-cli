---
name: sonarcloud-setup
description: "How SonarCloud is wired for gflow-cli — project key, org, token location, hotspot-review API, quality-gate-wait + coverage-exclusion policy"
---

SonarCloud project for gflow-cli: projectKey `ffroliva_gflow-cli`, organization `ffroliva-github` (see `sonar-project.properties`). CI analysis runs in `.github/workflows/ci.yml` — the `sonar` job (`needs: test`, `if: always()`), which consumes the `coverage.xml` artifact uploaded by the test job.

For one-off API work, a SonarCloud user token lives in `.env.local` at the repo root as `SONAR_TOKEN=` (gitignored via `.gitignore:40`). The user is fine with API-driven operations; read the token inside a sandbox so it never lands in chat/logs.

Read open issues / quality gate anonymously (no token):
- `GET https://sonarcloud.io/api/issues/search?componentKeys=ffroliva_gflow-cli&resolved=false`
- `GET https://sonarcloud.io/api/qualitygates/project_status?projectKey=ffroliva_gflow-cli`

Mark a Security Hotspot reviewed (needs the token, `Authorization: Bearer <token>`):
- `POST https://sonarcloud.io/api/hotspots/change_status` form body `hotspot=<key>&status=REVIEWED&resolution=SAFE&comment=<why>` → HTTP 204 on success.
- List hotspots: `GET https://sonarcloud.io/api/hotspots/search?projectKey=ffroliva_gflow-cli&status=TO_REVIEW`.

Hotspots reviewed Safe on 2026-05-17: `S2245` in `api/_retry.py` (backoff jitter randomness) and `S4790` in `api/transports/experimental/sapisidhash.py` (SHA-1 mandated by Google's SAPISIDHASH protocol). Both are correct code, not defects.

## Quality-gate visibility & coverage policy (added 2026-05-29, PR #118)

**Trap:** a passing GitHub `SonarCloud analysis` check does NOT mean the quality gate passed — that job goes green as soon as the analysis is *submitted*. A failed gate (e.g. new-code coverage below threshold) was reported only on the SonarCloud dashboard → a misleading green. PR #117 looked CI-clean while its gate was red on coverage.

**Fix:** `sonar.qualitygate.wait=true` in `sonar-project.properties` makes the scanner poll the gate and fail the CI step on ERROR, so a red gate ⇒ red check. If the GitHub check is green, the gate genuinely passed.

**Coverage policy:** gate requires **80% coverage on new code** (changed lines vs `main`). Browser-automation + live-auth transports (`api/transports/ui_automation*.py`, `api/transports/experimental/*.py`) are e2e-tested (real Chrome / live Flow), NOT unit-tested → listed under `sonar.coverage.exclusions` (still analysed for bugs/smells, only coverage ignored). **Do NOT** game a coverage-gate failure with fake browser unit tests or by widening the exclusion list — add real tests for testable surfaces (CLI, data, helpers). Full rationale: `docs/GITHUB.md § SonarCloud Quality Gate`. See also [[sonar-dataclasses-replace-cast]].
