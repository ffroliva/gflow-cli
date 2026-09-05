---
name: full-test-suite-ooms
description: "Running the unscoped pytest suite OOMs the sandbox (exit 137) — `pyproject.toml` has no `addopts` filter and e2e/smoke tests spawn real Chromium; scope unit verification to `tests/api/ tests/cli/`."
---

**Partial mitigation shipped — `addopts` filter now in place.** As of 2026-05-27 (PR #95 merged into develop), `pyproject.toml` `[tool.pytest.ini_options]` carries `addopts = "--basetemp=tmp/pytest -m 'not e2e and not live and not smoke'"` — a bare `pytest` or `pytest -q` now auto-excludes the three credit-spending tiers and no longer OOMs from collecting `tests/e2e/`. The historic trap (originally documented 2026-05-21) is therefore largely closed for default invocations.

**Still risky:** any command that overrides addopts and forces full collection — most importantly `uv run pytest -q --cov=gflow_cli` or `pytest --override-ini=addopts=` — can still SIGKILL at exit 137 because pytest-cov's line tracer pushes the process past the sandbox limit even when the test bodies skip. The original memory text below describes that scenario.

---

Do not run `uv run pytest -q --cov=gflow_cli` (or any unscoped `pytest` invocation that bypasses addopts) on this sandbox. It SIGKILLs at exit 137 (OOM) because pytest collects the entire `tests/` tree, including `tests/e2e/`, `tests/smoke/`, and `tests/test_browser_manager.py`. Marker registration alone (`@pytest.mark.e2e`, `@pytest.mark.live`) does NOT auto-skip; markers only filter when `-m` is passed on the command line — which the default `addopts` now does. Tests that don't depend on the gated `e2e_profile_dir` fixture, or that spawn real Playwright Chromium before the skip check, balloon memory; pytest-cov's line tracer pushes the process past the sandbox limit.

**Why:** Confirmed 2026-05-21 — Task 5 of the video-download plan asked for `pytest -q --cov=gflow_cli` as a full-suite regression sweep and that exact command SIGKILLed the harness (exit 137). The user noted "this has happened regularly" before.

**How to apply:**
- For unit verification of changes under `src/gflow_cli/api/` or `src/gflow_cli/cli*.py`, scope to the affected directories: `uv run python -m pytest tests/api/ tests/cli/ -q --cov=gflow_cli --cov-report=term-missing`.
- Never invoke `pytest` (or `pytest -q`, `pytest tests/`) without a path or `-m` filter on this machine.
- The CI workflow (`.github/workflows/ci.yml`) runs the full suite on hosted GitHub runners which have enough RAM; trust CI for the unscoped sweep.
- If a future plan asks for a full local sweep, propose splitting it into `tests/api/ tests/cli/ tests/auth/ tests/test_browser_manager.py tests/e2e/ -m 'not e2e and not live'` and run each separately, OR propose adding `addopts = "-m 'not e2e and not live'"` to `pyproject.toml` as a permanent fix (architectural change — discuss first).
- Related: [[branch-workflow]] for how to gate this in CI rather than locally.
