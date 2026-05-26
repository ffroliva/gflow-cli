# E2E Test Strategy Redesign — Plan

**Date:** 2026-05-26
**Branch:** `claude/e2e-test-strategy-3I5uN`
**Status:** Phase 1 shipped (this PR). Phase 2 backlog.

## Goal

Redesign the e2e test suite from a flat single-marker system into a
cost-stratified layered model that:

1. Lets maintainers run only zero-credit tests (auth sanity check) before
   deciding to spend Imagen or Veo credits.
2. Separates image generation, video generation, and data persistence concerns
   as independently selectable test layers.
3. Eliminates duplicated code (profile resolution helper, log capture fixture,
   e2e environment fixture).
4. Correctly classifies tests — non-API tests out of the e2e suite.
5. Provides a clear roadmap to a contract/replay layer that runs in CI for free.

## Current state before this plan

| Problem | Impact |
|---|---|
| Single `e2e` marker — all or nothing | Running `pytest -m e2e` spends all credits (C3 alone: 60 images × 3 strategies) |
| `_profile_dir()` duplicated in 2 files | Maintenance burden; diverges from fixture in conftest |
| C5 (timeout) in e2e suite | Pure-mock test paying the e2e "real profile required" tax |
| Smoke test uses `GFLOW_E2E=1` / `GFLOW_E2E_PROFILE` | Inconsistent with all other tests using `GFLOW_CLI_E2E_PROFILE` |
| Local `log_capture` fixture in `test_image_batch_e2e.py` | Duplicates `install_log_capture` without `merge_contextvars`; test ordering bleeds |
| `e2e_env` fixture defined in `test_data_layer_e2e.py` | Not reusable by other e2e tests that drive subprocesses |
| `GFLOW_CLI_E2E_RUN_VIDEO` defaulted to `"1"` | Veo credit spent unintentionally on every full e2e run |
| No documentation of the e2e layer model | Developers guess how to run tests; no cost visibility |

## Phase 1 — Shipped (this PR)

### 1.1 Cost sub-markers (pyproject.toml)

Five new markers registered alongside the existing `e2e` marker:

```
e2e_auth   — zero credits: auth/session/health
e2e_image  — ~1 Imagen credit: text-to-image, image-to-image
e2e_batch  — N Imagen credits: batch generation
e2e_video  — ~1 Veo credit: text-to-video, image-to-video (opt-in)
e2e_data   — combined with above: DB persistence assertions
smoke      — golden-path Layer 3 (1 Imagen credit, post-release)
```

### 1.2 Auto-marker hook (root conftest.py)

`pytest_collection_modifyitems` applies `e2e` to any uncovered file under
`tests/e2e/` and `smoke` to any uncovered file under `tests/smoke/` as a
safety net.

### 1.3 Fixture consolidation

- `e2e_profile_dir` moved to canonical home: `tests/e2e/conftest.py` (was
  already there; `_profile_dir()` inline helpers in 2 files removed).
- `e2e_env` moved to `tests/e2e/conftest.py` (was inline in `test_data_layer_e2e.py`).
- Local `log_capture` in `test_image_batch_e2e.py` replaced with shared
  `install_log_capture` from `tests/conftest.py`.

### 1.4 C5 promoted to integration

`test_e2e_30s_timeout_budget` (criterion C5) moved to
`tests/api/transports/test_transport_timeout.py` with `pytest.mark.integration`.
No browser, no credits — pure coroutine mock.
Also fixes the L4 `Path("/dev/null")` → `Path(os.devnull)` bug from tasks/lessons.md.

### 1.5 Video default flipped

`GFLOW_CLI_E2E_RUN_VIDEO` now defaults to `"0"` (opt-out of video).
Developers must explicitly set `GFLOW_CLI_E2E_RUN_VIDEO=1` to run video tests.
This prevents unintended Veo credit burn on a full `pytest -m e2e` run.

### 1.6 Smoke test harmonized

`tests/smoke/test_real_flow.py` migrated from:
- `GFLOW_E2E=1` + `GFLOW_E2E_PROFILE` gating
- `pytestmark = pytest.mark.skipif(...)`

To:
- `GFLOW_CLI_E2E_PROFILE` gating (consistent with all other e2e)
- `pytestmark = pytest.mark.smoke`
- Added PNG magic bytes assertion (was only checking size)

### 1.7 docs/E2E_TESTING.md

New comprehensive strategy document added at `docs/E2E_TESTING.md`:
- Layer model diagram
- Marker cost table
- All env vars with defaults
- Run commands per tier
- File map
- Shared fixture reference
- Cost minimization patterns
- Isolation guarantees
- Roadmap to contract/replay layer

## Phase 2 — Backlog (not yet implemented)

### 2.1 Contract / HAR replay layer

Add `tests/contract/` as a new test layer (marker: `@pytest.mark.contract`)
that runs in CI without credentials.

**Playwright path (UiAutomationTransport):**
- Record real API interactions as HAR files during a live session
- Commit sanitized HAR cassettes (strip signed URLs, tokens, reCAPTCHA)
- Replay with `ctx.route_from_har("cassettes/t2i.har", not_found="fallback")`
- CI runs without `GFLOW_CLI_E2E_PROFILE`

**HTTPX path (BearerTransport, SapisidhashTransport):**
- Use `pytest-recording` (VCR.py) to record/replay HTTPX exchanges
- Configure `vcr_cassette_dir = "tests/contract/cassettes"` in pyproject.toml
- `vcr_record_mode = "none"` in CI; recording requires `GFLOW_LIVE=1`

Error paths that currently burn credits (C4a — stale credential → 401 response)
would move from Layer 4 e2e to Layer 3 contract tests.

### 2.2 `storageState` caching for non-profile tests

For contract tests that need an authenticated browser session but are not
testing Chrome profile management, use Playwright's `storageState` (once per
pytest session) instead of reloading the full user-data-dir profile per test.
60–80% faster browser initialization.

### 2.3 `--strict-markers` enforcement

Once all teams are using the new markers consistently, add `--strict-markers` to
`addopts` to prevent typos in marker names from silently passing:

```toml
addopts = "--basetemp=tmp/pytest --strict-markers"
```

Prerequisite: audit all test files for any undeclared custom markers.

### 2.4 CI pipeline scheduling

Map layers to CI pipeline stages:

| Layer | Trigger | Gate |
|---|---|---|
| 0–2 | every commit, PR open | mandatory (blocks merge) |
| 3 (contract) | every PR (replay, free) | mandatory (blocks merge) |
| 4a (smoke) | post-merge to main, nightly | advisory |
| 4b (e2e image) | weekly, release gate, manual | advisory |
| 4c (e2e video) | manual only, release gate | advisory |

### 2.5 Cost ledger in CI output

Emit structured lines at the end of Layer 3–4 runs:

```
[COST] e2e_auth:  0 imagen, 0 veo
[COST] e2e_image: 3 imagen, 0 veo
[COST] e2e_batch: 8 imagen, 0 veo
[COST] e2e_video: 0 imagen, 2 veo
```

Track per-run totals over time to detect unexpectedly expensive test additions.

## Decision log

**ADR E2E-1:** Chose cost sub-markers over separate test directories.
Sub-markers allow a test to be in one file (e.g. `test_transports_e2e.py`)
while still being selectable by cost. Separate directories would require
duplicating the profile resolution logic and make it harder to co-locate related
tests (e.g. C2/image and C2/i2v are naturally in the same file).

**ADR E2E-2:** Chose Playwright HAR replay over Pact for contract testing.
Pact requires the API provider to run a verification server. Google Flow's
private API has no publicly accessible Pact provider. HAR replay is the
idiomatic Playwright mechanism and works at the browser network layer.

**ADR E2E-3:** Video defaults to opt-out (`GFLOW_CLI_E2E_RUN_VIDEO=0`).
Veo credits are the most expensive resource in the test suite. Defaulting to
opt-in (old behavior) caused accidental spending when developers ran `pytest -m e2e`
without reading the docs. Opt-out puts the credit-spend decision in the user's hands.

**ADR E2E-4:** C5 (timeout) promoted to integration, not deleted.
The test is valuable — it verifies that each transport's internal `asyncio.wait_for`
fires within 35 s. Moving it to `integration` (pure mock) preserves the coverage
while removing the e2e tax (real profile, real browser).
