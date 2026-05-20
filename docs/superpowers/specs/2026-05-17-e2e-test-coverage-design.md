# E2E Test Coverage — Auth Verification & Integration Ergonomics — Design

> **Status:** Approved design · **Date:** 2026-05-17 · **Revision:** 2
> **Branch:** `fix/issue-15-i2v-bearer-auth` (folds into PR #22 → `develop`)
> **Reviewed by:** a 4-agent council — implementability, codebase-accuracy,
> test-strategy, risk/operations. All returned APPROVE-WITH-CHANGES (none
> NEEDS-REWORK); findings folded into Rev 2 (see §10).
> **Related:** issue #15 auth-verification fix (this branch); PR #20
> integration ergonomics (already merged to `develop`).

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
  These tests only ever run locally, from a machine with a real profile (and,
  by definition of how that profile is created, with system Chrome installed).
- **Credit cost.** Transport/image tests spend real Flow credits; a full
  baseline run is ≈69 image generations (criterion C3 alone is 60). Approved.
  The new auth-verification tests cost **zero credits** — they only probe the
  session endpoint and call `health_check()`.
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
issue #15) before we add anything. No code changes in this part. **Do not run
e2e with `pytest-xdist` (`-n`)** — parallel real-Chrome instances risk OOM on
this machine.

## 4. Part B — Issue-#15 auth-verification e2e

New module: **`tests/e2e/test_auth_verification_e2e.py`**, marked
`pytestmark = pytest.mark.e2e`. Async tests need no `@pytest.mark.asyncio`
decorator (`asyncio_mode = "auto"` is set).

`verify_flow_session(profile_dir, *, channel="chrome", source="chrome")`
returns a frozen `FlowSessionStatus` with fields `outcome`, `user_email`,
`source`, and a derived `authenticated` property
(`outcome is FlowSessionOutcome.AUTHENTICATED`).

| Test | What it does | Cost |
|---|---|---|
| `test_e2e_verify_flow_session_authenticated` | Two assertions in one test: (1) `verify_flow_session(<denon82 profile>, channel="chrome", source="chrome")` → `outcome is AUTHENTICATED`, `user_email` is a non-empty `str`, `authenticated is True`; (2) **same profile** — open `FlowApiClient` on it and call `health_check()` → `True`. This proves the real issue-#15 invariant: a profile pronounced `AUTHENTICATED` is actually *usable* by the API client, not just probe-positive. | 0 credits |
| `test_e2e_verify_flow_session_no_session` | Creates a fresh empty profile dir **inside the gflow home** (`get_settings().home / "profile_e2e_nosession_<uuid4>"`), asserts it does not pre-exist, then `verify_flow_session(..., channel="chrome", source="chrome")` → `outcome is NO_SESSION` (empty profile: real headless Chrome launches, no `SAPISID`, `/api/auth/session` returns `200 {}`). | 0 credits |

**Browser channel.** `verify_flow_session` only accepts branded Playwright
channels and always passes `channel`; the bundled-Chromium path (channel
omitted) is not reachable through it. e2e therefore uses `channel="chrome"` —
the exact path the product uses, and the e2e prerequisite (a profile from
`gflow auth login`) already implies system Chrome is installed.

**`NO_SESSION` environmental caveat.** The probe needs outbound network to
`labs.google`. If the network is unreachable the probe returns
`VERIFICATION_ERROR`, not `NO_SESSION`. The test asserts `NO_SESSION`; a
`VERIFICATION_ERROR` result must be read as an environmental failure (no
connectivity), not a code regression — the test surfaces that distinction in
its failure message.

**Temp-dir safety.** The `NO_SESSION` profile dir must be inside
`get_settings().home` (a `tmp_path` dir is outside it and would trip
`verify_flow_session`'s `SecurityError` boundary check). It is UUID-named so it
can never collide with a real profile (`profile_denon82` etc.), and torn down
in a `finally`/fixture with a brief delay + `shutil.rmtree(..., ignore_errors=True)`
to tolerate the Windows Chrome profile lock. A leftover `profile_e2e_nosession_*`
dir is harmless and safe to delete manually.

**Out of e2e scope (deliberate):** `GOOGLE_SESSION_ONLY` needs a
Google-signed-in-but-not-Flow profile and `VERIFICATION_ERROR` needs an
injected network fault — neither is cleanly producible live. Both remain
covered by the existing unit tests in `tests/auth/test_verification.py`
(verified by the council: `GOOGLE_SESSION_ONLY` and seven `VERIFICATION_ERROR`
cases are unit-covered).

## 5. Part C — Additional PR #20 e2e coverage

PR #20's two e2e tests are *run* in Part A. For *new* coverage, the
implementation plan's first step inspects the auto-project-creation and
`FlowApiClient.health_check()` surface, then adds **≤3 targeted tests** for
gaps the existing two do not cover:

- `health_check()` returns `False` on a closed / unusable client context.
- An explicit `project_id` is honoured (no spurious project auto-created).
- *(Contingent)* A second generation reuses an auto-created project rather
  than making a new one — **only if** the feature is specified to behave that
  way. This candidate may require a light instrumentation hook in
  `FlowApiClient` (e.g. a `create_project` call counter) to be assertable;
  it is dropped if inspection shows no such guarantee or no clean seam.

These land in `tests/e2e/test_transports_e2e.py` or a sibling module,
whichever the inspection shows is the better home.

## 6. Shared support

Add **`tests/e2e/conftest.py`** providing:

- An `e2e_profile_dir` pytest fixture that resolves `GFLOW_CLI_E2E_PROFILE` and
  `pytest.skip()`s when it is unset or the directory is absent — the same gate
  logic `test_transports_e2e.py` currently inlines as `_profile_dir()`.
- A fixture for the `NO_SESSION` temp profile: creates a UUID-named dir inside
  `get_settings().home`, asserts non-existence before creating, and tears it
  down Windows-lock-safely (brief delay + `rmtree(ignore_errors=True)`).

New tests use these fixtures. The existing `test_transports_e2e.py` is left
untouched (refactoring its private `_profile_dir()` to the fixture is optional
and out of scope).

## 7. Execution order

1. Part A — baseline run with `denon82`; record results.
2. Inspect PR #20's feature surface (for Part C).
3. Write Part B module + `conftest.py`; write Part C tests.
4. Full e2e run (`-m e2e`, `denon82`, no `-n`) — confirm Part B/C green and no
   regression in the existing suite.
5. `ruff` / `pyright` on the new test files; commit to
   `fix/issue-15-i2v-bearer-auth`.

## 8. Non-goals

- No change to CI (e2e stays excluded).
- No automation of `gflow auth login` / the interactive browser sign-in.
- No refactor of the existing `test_transports_e2e.py` beyond optionally
  adopting the shared fixture.
- No new production code — this is test-only work, with the one possible
  exception of a small, clearly-scoped `FlowApiClient` instrumentation hook
  **only if** Part C's contingent third test is retained after inspection.

## 9. Verification

- New e2e files pass `ruff check`, `ruff format --check`, `pyright`.
- Part B tests pass against `denon82` (positive + usable-chain) and a fresh
  empty profile (`NO_SESSION`).
- The baseline and final full e2e runs are recorded so any pre-existing
  failures are distinguished from regressions.
- If the final run shows failures clustered on auth-shaped errors (HTTP 401,
  `AuthExpiredError`), re-run `gflow auth login --profile denon82` to refresh
  the session and re-run before declaring a regression — a mid-run session
  expiry is indistinguishable from a code fault in the raw output.

## 10. Council review

A 4-agent council reviewed Rev 1. Verdicts: implementability
APPROVE-WITH-CHANGES, codebase-accuracy INACCURACIES-FOUND, test-strategy
APPROVE-WITH-CHANGES, risk/operations APPROVE-WITH-CHANGES. Folded into Rev 2:

- Positive test extended to chain `verify_flow_session` → `FlowApiClient.health_check()`
  (proves "verified ⇒ usable", the true issue-#15 invariant).
- `NO_SESSION` temp dir pinned inside `GFLOW_CLI_HOME`, UUID-named,
  non-existence-asserted, Windows-lock-safe teardown.
- `NO_SESSION` environmental caveat (network → `VERIFICATION_ERROR`) documented.
- `FlowSessionStatus`'s `source` field and the derived `authenticated`
  property described accurately.
- Part C's third test marked contingent and possibly needing an instrumentation
  hook; `§8` non-goals updated accordingly.
- `pytest-xdist` (`-n`) prohibited for e2e; auth-expiry re-run guidance added.

**One council recommendation partially rejected, with reason:** the
implementability reviewer suggested running the `NO_SESSION` test with
`channel="chromium"` to avoid depending on system Chrome. `chromium` is not a
valid Playwright *channel* value (channels are branded distributions —
`chrome`, `msedge`, …), and `verify_flow_session` always passes `channel`, so
`channel="chromium"` would itself raise and yield `VERIFICATION_ERROR`. The
e2e suite runs only on a machine that has already completed `gflow auth login`
(hence has system Chrome), so `channel="chrome"` is both correct and the
genuine product path. Kept `channel="chrome"` — see §4.
