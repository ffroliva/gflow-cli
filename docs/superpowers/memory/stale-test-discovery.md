---
name: stale-test-discovery
description: "When restoring a stubbed command, grep `tests/` for the old assertion strings BEFORE pushing — stale top-level test files survive new tests in subdirs and turn CI red."
---

When restoring or rewriting a command that was previously stubbed, **grep all of `tests/` for the old stub's assertion strings before pushing** — old top-level test files in `tests/` can survive when you only add new tests under `tests/<area>/` and won't show up in scoped local runs (`pytest tests/api tests/cli`).

**Why:** Confirmed 2026-05-21 — when restoring `gflow video t2v` from the v0.7.0 "temporarily unavailable" stub, I added comprehensive new tests at `tests/cli/test_cli_video.py` but missed a stale `tests/test_cli_video.py` (top level) that pinned the old `assert "temporarily unavailable" in result.output`. The scoped local sweep (`tests/api tests/cli`) didn't touch the top-level file, so it stayed green locally. CI (`pytest -m "not e2e and not live"` across the whole tree) caught it and went red on all 3 Python versions. One push wasted.

**How to apply:**
- Before pushing any "restore the stub" / "replace the placeholder" refactor, run:
  ```pwsh
  uv run python -m pytest -q -m "not e2e and not live" --ignore=tests/e2e --ignore=tests/smoke --ignore=tests/test_browser_manager.py
  ```
  This is the CI-equivalent local sweep that respects [[full-test-suite-ooms]] — covers everything except the OOM-prone dirs.
- Or grep first: `Grep tests/ for "temporarily unavailable"` (or whatever the old stub string was). If a test file's whole purpose is "pin the stub behavior", it will need to be deleted when the stub goes away.
- Related: [[full-test-suite-ooms]] — why you can't just run the unscoped suite locally.
