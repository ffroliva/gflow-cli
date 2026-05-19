# E2E Test Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the existing e2e suite against a real profile to get a baseline, then add e2e coverage for the issue-#15 auth-verification feature and for PR #20's integration-ergonomics feature.

**Architecture:** Test-only work. The issue-#15 e2e tests call `verify_flow_session` directly against real profiles (the full `gflow auth login` CLI cannot be e2e-automated — interactive browser sign-in). The PR #20 e2e tests exercise `FlowApiClient.health_check()` and `generate_images_batch` auto-project-creation. A new `tests/e2e/conftest.py` holds shared fixtures. **No production code changes.**

**Tech Stack:** Python 3.11+, `pytest` + `pytest-asyncio` (`asyncio_mode = "auto"`), Playwright, `uv`.

**Spec:** `docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md` (Rev 2).

---

## Important context for every task

- **Branch:** `fix/issue-15-i2v-bearer-auth` — commit there. NEVER add a `Co-Authored-By` trailer.
- **These are acceptance tests of already-shipped features.** Unlike TDD, the expectation is the test **PASSES on first run**. A FAIL is a real signal — investigate (feature regression / stale profile / network), do not "make the test pass" by weakening it.
- **Environment:** `uv run pytest` fails here with "Failed to canonicalize script path" — always use `uv run python -m pytest`. `uv run ruff` / `uv run pyright` work normally.
- **Running e2e:** e2e tests are gated by `-m e2e` AND the `GFLOW_CLI_E2E_PROFILE` env var. The profile is **`denon82`**. On Windows PowerShell:
  ```
  $env:GFLOW_CLI_E2E_PROFILE = "denon82"
  $env:PYTHONUTF8 = "1"
  uv run python -m pytest tests/e2e -m e2e -v -p no:cov
  ```
  (bash equivalent: `GFLOW_CLI_E2E_PROFILE=denon82 uv run python -m pytest ...`)
- **Never** run e2e with `pytest-xdist` (`-n`) — parallel real-Chrome instances risk OOM on this machine.
- **Credits:** image-generating tests spend real Flow credits (approved). The auth-verification and `health_check` tests spend **zero**.
- If a run shows failures clustered on auth-shaped errors (HTTP 401, `AuthExpiredError`/`AuthMissingError`), the `denon82` session has likely expired — run `gflow auth login --profile denon82` and re-run before treating it as a regression.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `tests/e2e/conftest.py` | Shared e2e fixtures: `e2e_profile_dir` (resolve `GFLOW_CLI_E2E_PROFILE`), `e2e_nosession_profile` (a fresh empty profile dir inside the gflow home). | Create |
| `tests/e2e/test_auth_verification_e2e.py` | Part B — issue-#15 e2e: `verify_flow_session` AUTHENTICATED (+ usable-by-client chain) and NO_SESSION. | Create |
| `tests/e2e/test_transports_e2e.py` | Part C — append `health_check()` false-path and `generate_images_batch` auto-create tests. | Modify |
| `tmp/e2e-baseline.md` | Recorded baseline run results (gitignored scratch — not committed). | Create |

---

## Task 1: Part A — baseline run of the existing e2e suite

Establish a truthful baseline of what the existing suite does *before* adding anything, so pre-existing failures are never mistaken for regressions. No code changes.

**Files:** none (writes a scratch record to `tmp/e2e-baseline.md`).

- [ ] **Step 1: Run the full existing e2e suite**

PowerShell:
```
$env:GFLOW_CLI_E2E_PROFILE = "denon82"
$env:PYTHONUTF8 = "1"
uv run python -m pytest tests/e2e -m e2e -v -p no:cov
```
This is a real, credit-spending run (≈69 image generations). Expect it to take several minutes. Capture the full per-test pass/fail/skip output.

- [ ] **Step 2: Record the baseline**

Write `tmp/e2e-baseline.md` containing, per test (and per `strategy` parameter): the test id and its outcome (passed / failed / skipped), plus the final pytest summary line. For any failure, note whether it looks pre-existing (e.g. an obsolete `bearer`/`sapisidhash` transport strategy, or an auth-shaped error) versus unexplained. This file is scratch — do **not** `git add` it.

- [ ] **Step 3: Report**

Report the baseline table to the orchestrator. Do not commit. If the run could not start at all (e.g. `denon82` profile missing or its session dead), report BLOCKED — the rest of the plan needs a working profile.

---

## Task 2: Shared e2e fixtures — `tests/e2e/conftest.py`

A `conftest.py` so the new auth-verification module gets the profile-resolution gate and a safe temp-profile fixture without duplicating logic.

**Files:**
- Create: `tests/e2e/conftest.py`

- [ ] **Step 1: Create `tests/e2e/conftest.py`**

```python
"""Shared fixtures for the e2e test suite.

e2e tests hit the real Flow API / real Google auth endpoints and are opt-in:
run with `-m e2e` and `GFLOW_CLI_E2E_PROFILE=<profile_name>` set. See
docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md.
"""

from __future__ import annotations

import os
import shutil
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from gflow_cli.config import get_settings

_E2E_PROFILE_ENV = "GFLOW_CLI_E2E_PROFILE"


@pytest.fixture
def e2e_profile_dir() -> Path:
    """Resolve the authenticated Chromium profile from GFLOW_CLI_E2E_PROFILE.

    Skips the test when the env var is unset or the profile dir is absent.
    """
    name = os.environ.get(_E2E_PROFILE_ENV, "")
    if not name:
        pytest.skip(
            f"E2E tests require {_E2E_PROFILE_ENV} - set it to a logged-in "
            "profile name and re-run with -m e2e"
        )
    from gflow_cli.auth import profile_dir as _resolve_profile_dir

    candidate = _resolve_profile_dir(name)
    if not candidate.exists():
        pytest.skip(
            f"Profile directory not found: {candidate}. "
            f"Run `gflow auth login --profile {name}` to create it."
        )
    return candidate


@pytest.fixture
def e2e_nosession_profile() -> Iterator[Path]:
    """Yield a fresh, empty profile dir INSIDE the gflow home.

    `verify_flow_session` enforces a boundary check that the profile dir
    resolves inside GFLOW_CLI_HOME, so a pytest `tmp_path` dir (system temp)
    cannot be used. The dir is UUID-named so it can never collide with a real
    `profile_<name>` dir, and is removed in teardown — `ignore_errors=True`
    plus a short delay tolerate the Windows Chrome profile lock that may
    briefly outlive `ctx.close()`.
    """
    home = get_settings().home
    home.mkdir(parents=True, exist_ok=True)
    path = home / f"profile_e2e_nosession_{uuid.uuid4().hex}"
    assert not path.exists(), f"temp profile dir unexpectedly already exists: {path}"
    path.mkdir()
    try:
        yield path
    finally:
        time.sleep(0.5)  # let Chrome release the Windows profile lock
        shutil.rmtree(path, ignore_errors=True)
```

- [ ] **Step 2: Lint and type-check**

Run:
```
uv run ruff check tests/e2e/conftest.py
uv run ruff format --check tests/e2e/conftest.py
uv run pyright tests/e2e/conftest.py
```
Expected: ruff clean (run `uv run ruff format tests/e2e/conftest.py` if formatting is off), pyright 0 errors.

- [ ] **Step 3: Verify the conftest collects cleanly**

Run: `uv run python -m pytest tests/e2e --collect-only -q`
Expected: collection succeeds with no import error (tests are listed; they will not run without `-m e2e`).

- [ ] **Step 4: Commit**

```
git add tests/e2e/conftest.py
git commit -m "test(e2e): add shared profile fixtures for e2e suite"
```

---

## Task 3: Part B — issue-#15 auth-verification e2e

New module probing the real Google `/api/auth/session` endpoint via
`verify_flow_session`. Zero credits.

**Files:**
- Create: `tests/e2e/test_auth_verification_e2e.py`

- [ ] **Step 1: Create `tests/e2e/test_auth_verification_e2e.py`**

```python
"""E2E tests for the issue-#15 Flow-session verification feature.

These probe the REAL Google `/api/auth/session` endpoint via
`verify_flow_session`, and (positive case) confirm a verified profile is
actually usable by `FlowApiClient`. Opt-in: `-m e2e` + `GFLOW_CLI_E2E_PROFILE`.
Zero Flow credits are spent - no image generation.

Async tests need no `@pytest.mark.asyncio` decorator: `asyncio_mode = "auto"`
is set in pyproject.toml.

See docs/superpowers/specs/2026-05-17-e2e-test-coverage-design.md.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from gflow_cli.api.client import FlowApiClient
from gflow_cli.auth.verification import FlowSessionOutcome, verify_flow_session

pytestmark = pytest.mark.e2e


async def test_e2e_verify_flow_session_authenticated(e2e_profile_dir: Path) -> None:
    """A logged-in profile verifies as AUTHENTICATED and is usable by the client.

    Two linked assertions - the real issue-#15 invariant:
      1. verify_flow_session pronounces the profile AUTHENTICATED.
      2. the SAME profile actually works: FlowApiClient.health_check() is True.
    """
    status = await verify_flow_session(
        e2e_profile_dir, channel="chrome", source="chrome"
    )
    assert status.outcome is FlowSessionOutcome.AUTHENTICATED, (
        f"expected AUTHENTICATED, got {status.outcome} - is the profile's "
        "Flow session still valid? Re-run `gflow auth login` if not."
    )
    assert isinstance(status.user_email, str) and status.user_email, (
        "an AUTHENTICATED status must carry a non-empty user_email"
    )
    assert status.authenticated is True

    # Verified => usable: a profile pronounced AUTHENTICATED must actually work.
    async with FlowApiClient(
        profile_dir=e2e_profile_dir, transport="evaluate_fetch"
    ) as client:
        assert await client.health_check() is True, (
            "a verified profile must pass FlowApiClient.health_check()"
        )


async def test_e2e_verify_flow_session_no_session(
    e2e_nosession_profile: Path,
) -> None:
    """A fresh, empty profile verifies as NO_SESSION.

    Empty profile dir -> real headless Chrome launches -> no SAPISID cookie ->
    `/api/auth/session` returns `200 {}` -> NO_SESSION. Zero credits.

    Environmental caveat: the probe needs outbound network to labs.google.
    A VERIFICATION_ERROR result indicates no connectivity, not a bug.
    """
    status = await verify_flow_session(
        e2e_nosession_profile, channel="chrome", source="chrome"
    )
    assert status.outcome is FlowSessionOutcome.NO_SESSION, (
        f"expected NO_SESSION for an empty profile, got {status.outcome}. "
        "If VERIFICATION_ERROR: check network connectivity to labs.google."
    )
```

- [ ] **Step 2: Lint and type-check**

Run:
```
uv run ruff check tests/e2e/test_auth_verification_e2e.py
uv run ruff format --check tests/e2e/test_auth_verification_e2e.py
uv run pyright tests/e2e/test_auth_verification_e2e.py
```
Expected: ruff clean (`uv run ruff format ...` if needed), pyright 0 errors.

- [ ] **Step 3: Run the two tests (expect PASS)**

PowerShell:
```
$env:GFLOW_CLI_E2E_PROFILE = "denon82"
$env:PYTHONUTF8 = "1"
uv run python -m pytest tests/e2e/test_auth_verification_e2e.py -m e2e -v -p no:cov
```
Expected: **2 passed.** `test_e2e_verify_flow_session_authenticated` proves the
issue-#15 fix works against the real endpoint and the profile is usable;
`test_e2e_verify_flow_session_no_session` proves the empty-profile path.
If `authenticated` fails with a non-AUTHENTICATED outcome, the `denon82`
session may be stale — re-run `gflow auth login --profile denon82`. If
`no_session` returns `VERIFICATION_ERROR`, that is a network issue, not a bug.

- [ ] **Step 4: Commit**

```
git add tests/e2e/test_auth_verification_e2e.py
git commit -m "test(e2e): verify Flow-session auth against real endpoint (issue #15)"
```

---

## Task 4: Part C — additional PR #20 integration-ergonomics e2e

Append two tests to the existing transport e2e module. They cover the genuine
gaps PR #20's two existing e2e tests leave: `health_check()`'s false path, and
`generate_images_batch` auto-project-creation (PR #20's existing test only
covers the single-image `generate_image` auto-create).

**Files:**
- Modify: `tests/e2e/test_transports_e2e.py` (append at end of file)

- [ ] **Step 1: Append the two tests to `tests/e2e/test_transports_e2e.py`**

Add these at the very end of the file (after `test_e2e_health_check_returns_true_when_active`).
They reuse the file's existing helpers `_profile_dir()`, `_make_client()`, the
`STRATEGIES` list, `_PROMPT`, `GenerateImageRequest`, and `Model` — all already
imported/defined in that file.

```python


# ---------------------------------------------------------------------------
# health_check() false path (Issue #16 — new method)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_health_check_false_after_close(strategy: str) -> None:
    """health_check() returns False (never raises) once the client is closed.

    A long-lived worker holding a client whose context has been torn down must
    get a clean False, not an exception. Zero credits — no image generation.
    """
    profile = _profile_dir()
    client = _make_client(strategy, profile)

    async with client:
        assert await client.health_check() is True, (
            "health_check() must be True while the context is live"
        )

    # Context is now closed.
    assert await client.health_check() is False, (
        "health_check() must return False (not raise) on a closed client"
    )


# ---------------------------------------------------------------------------
# generate_images_batch auto-create project_id (Issue #16 — optional project_id)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy", STRATEGIES)
@pytest.mark.asyncio
async def test_e2e_generate_images_batch_without_project_id(strategy: str) -> None:
    """generate_images_batch(req=req, count=4) without project_id auto-creates
    a project and returns 4 images.

    PR #20's existing auto-create e2e test only covers single-image
    generate_image(); generate_images_batch() has its own distinct
    "resolve project_id once" path (one project for N parallel shots).
    """
    profile = _profile_dir()
    req = GenerateImageRequest(prompt=_PROMPT, model=Model.NARWHAL)

    async with _make_client(strategy, profile) as client:
        # Intentionally omit project_id — the client must create one internally.
        images = await client.generate_images_batch(req=req, count=4)

    assert len(images) == 4, f"expected 4 images, got {len(images)}"
    for img in images:
        assert img.fife_url.startswith("https://"), (
            f"fife_url must be an https:// URL, got: {img.fife_url!r}"
        )
```

- [ ] **Step 2: Lint and type-check**

Run:
```
uv run ruff check tests/e2e/test_transports_e2e.py
uv run ruff format --check tests/e2e/test_transports_e2e.py
uv run pyright tests/e2e/test_transports_e2e.py
```
Expected: ruff clean (`uv run ruff format ...` if needed), pyright 0 errors.

- [ ] **Step 3: Run the two new tests (expect PASS)**

PowerShell:
```
$env:GFLOW_CLI_E2E_PROFILE = "denon82"
$env:PYTHONUTF8 = "1"
uv run python -m pytest tests/e2e/test_transports_e2e.py -m e2e -v -p no:cov -k "health_check_false_after_close or batch_without_project_id"
```
Expected: **6 passed** (each test ×3 strategies). `health_check_false_after_close`
spends 0 credits; `batch_without_project_id` generates 4 images per strategy
(12 total). If a strategy's run fails with an obsolete-transport error that
also appeared in the Task 1 baseline, that is pre-existing — note it, it is not
a regression introduced here.

- [ ] **Step 4: Commit**

```
git add tests/e2e/test_transports_e2e.py
git commit -m "test(e2e): cover health_check false-path and batch auto-create (issue #16)"
```

---

## Task 5: Full verification

Confirm the new tests are clean and the whole e2e suite still behaves as the
baseline (modulo the new tests). No new code.

**Files:** none.

- [ ] **Step 1: Lint and type-check all new/changed e2e files**

```
uv run ruff check tests/e2e
uv run ruff format --check tests/e2e
uv run pyright tests/e2e/conftest.py tests/e2e/test_auth_verification_e2e.py tests/e2e/test_transports_e2e.py
```
Expected: ruff clean, ruff format clean, pyright 0 errors. Fix any issue, then
`git add` the affected file and commit with `chore(e2e): ...`.

- [ ] **Step 2: Confirm the non-e2e suite is unaffected**

The new files are e2e-gated, but confirm a normal collection is not broken:
```
uv run python -m pytest tests/e2e --collect-only -q
```
Expected: all e2e tests collected, no import/collection error.

- [ ] **Step 3: Full e2e run**

PowerShell:
```
$env:GFLOW_CLI_E2E_PROFILE = "denon82"
$env:PYTHONUTF8 = "1"
uv run python -m pytest tests/e2e -m e2e -v -p no:cov
```
This is the full credit-spending run. Expected: the existing suite behaves as
the Task 1 baseline (any failure that was already failing in `tmp/e2e-baseline.md`
is pre-existing, not a regression); the 4 new tests (Part B ×2 + Part C ×2,
parametrized) all pass.

- [ ] **Step 4: Report**

Report a final table: baseline outcome vs final outcome per test. Explicitly
state (a) the 4 new tests' results, and (b) that no previously-passing test
regressed. If a previously-passing test now fails, that IS a regression —
report it and stop for review. Do not commit anything in this step unless
Step 1 required a lint/type fix.

---

## Notes on spec reconciliation

One concrete decision made while planning, flagged for plan review:

1. **Part C is two tests, not three; no production code changes.** The spec
   (§5) listed a contingent third test — "a second generation reuses an
   auto-created project" — possibly needing a `FlowApiClient` instrumentation
   hook. Inspecting the code: `generate_image`/`generate_images_batch` resolve
   `project_id` with a plain `if project_id is None: create_project()`. The
   "explicit `project_id` is honoured" case is **already** covered by the
   existing C2 test (`test_e2e_single_image_gen` passes an explicit
   `project_id`). The "create one project for N shots" guarantee is mock-test
   territory, not e2e, and adding a counter hook to production code for a
   single e2e assertion is unjustified. The contingent test is therefore
   **dropped**, and the spec's §8 "one possible exception" (a production hook)
   does **not** materialise — this plan is strictly test-only.
