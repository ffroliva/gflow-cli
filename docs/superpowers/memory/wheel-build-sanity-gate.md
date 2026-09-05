---
name: wheel-build-sanity-gate
description: "Add `uv build` + ZIP-duplicate check to the release gate before tagging; the Impeccable Routine misses broken wheels"
---

The v0.9.0 release first PyPI publish failed with HTTP 400 "Duplicate filename in local headers." Root cause: `pyproject.toml` had `[tool.hatch.build.targets.wheel.force-include]` AND `[tool.hatch.build.targets.sdist.force-include]` blocks pointing at `src/gflow_cli/data/migrations/`, on top of `packages = ["src/gflow_cli"]`. Hatchling emitted the migrations directory twice (`__init__.py` and `0001_initial.sql` as duplicate ZIP entries), and PyPI rejected the wheel at upload.

The Impeccable Routine ran `hygiene → ruff check → ruff format → pyright → pytest` on every PR. None of those build the actual artifact that ships. A local `uv build` would have surfaced the issue immediately — hatchling even printed `UserWarning: Duplicate name: 'gflow_cli/data/migrations/__init__.py'` at build time.

**Why:** A failed PyPI publish is a release-day fire that costs:
- A wasted signed tag (we had to delete `v0.9.0` and re-sign at a new commit)
- A user-visible incident on the GitHub Actions release workflow page
- The "should we cut v0.9.1?" panic that nearly burned a clean version number

**How to apply:**

- Before any release tag, run:
  ```
  uv build && python -c "import zipfile; from collections import Counter; from pathlib import Path; w = next(Path('dist').glob('*-py3-none-any.whl')); names = zipfile.ZipFile(w).namelist(); d = [n for n,c in Counter(names).items() if c>1]; assert not d, f'Duplicate wheel entries: {d}'; print('Wheel OK:', w.name)"
  ```
- Add this to the `/gflow:check` skill or the `/gflow:release` skill (whichever runs pre-tag).
- If hatchling warns about duplicate names at build time, treat it as a build error, not a warning.
- The wheel artifact going to PyPI is what matters; running tests on source is not a substitute.

Linked: [[release-signing]], [[release-back-merge-gap-recovery]], [[pypi-rejected-filename-reusable]].
