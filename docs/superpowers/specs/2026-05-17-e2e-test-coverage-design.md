# E2E Test Coverage — Auth Verification & Integration Ergonomics — Design

> **Status:** Approved design · **Date:** 2026-05-17
> **Branch:** `fix/issue-15-i2v-bearer-auth` (folds into PR #22 → `develop`)
> **Related:** issue #15 auth-verification fix (this branch); PR #20 integration
> ergonomics (already merged to `develop`).

## 1. Goal

Two things:

1. **Run** the existing e2e suite against a real authenticated profile and
   record a truthful baseline of what passes — including PR #20's two e2e
   tests, which were merged unexecuted.
2. **Add** e2e coverage for the issue-#15 auth-verification feature (which has
   none today) and fill obvious gaps in PR #20's integration-ergonomics e2e
   coverage.

## 2. Constraints (these shape the design)

- **`gflow auth login` cannot be e2e-automated.** It opens a browser and waits
  for interactive Google sign-in; Google blocks scripted login. So the
  issue-#15 e2e tests exercise the **`verify_flow_session` probe directly**
  against real profiles — the honest, feasible seam. It still makes a real
  call to Google's `/api/auth/session`.
- **e2e never runs in CI** — unchanged. CI keeps `-m "not e2e and not live"`.
  These tests only ever run locally, from a machine with a real profile.
- **Credit cost.** Transport/image tests spend real Flow credits; a full
  baseline run is 60+ image generations. Approved. The new auth-verification
  tests cost **zero credits** — they only probe the session endpoint.
- **Profile.** `GFLOW_CLI_E2E_PROFILE=denon82` is the authenticated profile
  for all live runs. (`profile_issue15` was deleted — invalid; do not rely on
  any Google-only profile existing.)

## 3. Part A — Baseline run

Run the existing `tests/e2e/test_transports_e2e.py` (criteria C2–C5 across the
three transport strategies, plus PR #20's `test_e2e_generate_image_without_project_id`
and `test_e2e_health_check_returns_true_when_active`):

```
GFLOW_CLI_E2E_PROFILE=denon82  uv run python -m pytest tests/e2e -m e2e -v -p no:cov
```

Record pass/fail/skip per test. This is reconnaissance — it tells us what
currently works (some parametrized transport strategies may be obsolete after
issue #15) before we add anything. No code changes in this part.

## 4. Part B — Issue-#15 auth-verification e2e

New module: **`tests/e2e/test_auth_verification_e2e.py`**, marked
`pytestmark = pytest.mark.e2e`.

| Test | What it does | Cost |
|---|---|---|
| `test_e2e_verify_flow_session_authenticated` | `verify_flow_session(<denon82 profile>, channel="chrome", source="chrome")` → asserts `outcome is AUTHENTICATED`, `user_email` is a non-empty `str`, `authenticated is True`. Proves the issue-#15 fix works against the real Google endpoint. | 0 credits |
| `test_e2e_verify_flow_session_no_session` | Creates a fresh empty profile dir inside the gflow home, probes it → asserts `outcome is NO_SESSION` (empty profile: real headless Chrome launches, no `SAPISID`, `/api/auth/session` returns `200 {}`). Deterministic; the temp dir is removed in teardown. | 0 credits |

**Out of e2e scope (deliberate):** `GOOGLE_SESSION_ONLY` needs a
Google-signed-in-but-not-Flow profile and `VERIFICATION_ERROR` needs an
injected network fault — neither is cleanly producible live. Both remain
covered by the existing unit tests in `tests/auth/test_verification.py`.

## 5. Part C — Additional PR #20 e2e coverage

PR #20's two e2e tests are *run* in Part A. For *new* coverage, the
implementation plan's first step inspects the auto-project-creation and
`FlowApiClient.health_check()` surface, then adds **≤3 targeted tests** for
gaps the existing two do not cover. Candidate tests (finalized after that
inspection):

- `health_check()` returns `False` on a closed / unusable client context.
- An explicit `project_id` is honoured (no spurious project auto-created).
- A second generation reuses an auto-created project rather than making a new
  one — if the feature is specified to do so.

These land in `tests/e2e/test_transports_e2e.py` or a sibling module,
whichever the inspection shows is the better home.

## 6. Shared support

Add **`tests/e2e/conftest.py`** providing an `e2e_profile_dir` pytest fixture
that resolves `GFLOW_CLI_E2E_PROFILE` and `pytest.skip()`s when it is unset or
the directory is absent — the same gate logic `test_transports_e2e.py`
currently inlines as `_profile_dir()`. New tests use the fixture. The existing
`test_transports_e2e.py` is left untouched (refactoring its private helper to
the fixture is optional and out of scope).

## 7. Execution order

1. Part A — baseline run with `denon82`; record results.
2. Inspect PR #20's feature surface (for Part C).
3. Write Part B module + `conftest.py`; write Part C tests.
4. Full e2e run (`-m e2e`, `denon82`) — confirm Part B/C green and no
   regression in the existing suite.
5. `ruff` / `pyright` on the new test files; commit to
   `fix/issue-15-i2v-bearer-auth`.

## 8. Non-goals

- No change to CI (e2e stays excluded).
- No automation of `gflow auth login` / the interactive browser sign-in.
- No refactor of the existing `test_transports_e2e.py` beyond optionally
  adopting the shared fixture.
- No new production code — this is test-only work.

## 9. Verification

- New e2e files pass `ruff check`, `ruff format --check`, `pyright`.
- Part B tests pass against `denon82` (positive) and a fresh empty profile
  (`NO_SESSION`).
- The baseline and final full e2e runs are recorded so any pre-existing
  failures are distinguished from regressions.
