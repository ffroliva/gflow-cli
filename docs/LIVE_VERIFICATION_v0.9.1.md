# Live verification — v0.9.1

> Evidence plan for the v0.9.1 patch release. The release is focused on
> locale/cross-profile/catalog correctness fixes after v0.9.0, with the
> highest-risk paid surface being `gflow video i2v START PROMPT --end-image END`
> on non-English Chrome/Flow sessions.

## Environment

- Date: 2026-05-27
- gflow-cli version: 0.9.1
- Python: 3.11+ (CI matrix: 3.11 / 3.12 / 3.13)
- Chrome: headed, real-Chrome strategy mandatory
- OS: Windows 11 primary dev; macOS / Linux on CI

## Automated evidence

Run before tagging:

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q --cov=gflow_cli
```

If the local MCP/context sandbox closes during the coverage run, re-run the
same default non-live/non-e2e pytest set in smaller chunks without coverage and
rely on CI for the coverage XML:

```bash
uv run python -m pytest -q tests/api
uv run python -m pytest -q tests/auth tests/cli
uv run python -m pytest -q tests/features
uv run python -m pytest -q tests --ignore=tests/api --ignore=tests/auth --ignore=tests/cli --ignore=tests/features
```

Targeted coverage for this release includes:

- `tests/api/transports/test_ui_automation_video.py` — structural I2V Start/End
  frame-slot selection and fallbacks.
- `tests/cli/test_cli_video.py` — `gflow video i2v` CLI contract, including
  `--end-image`.
- `tests/cli/test_cli_data.py` and `tests/data/` — catalog query and migration
  behavior, including first-run DB creation.
- `tests/cli/test_auth_cli.py` — Windows-safe default-profile marker rendering.

## Local verification — 2026-05-27

Executed in the `release/v0.9.1` worktree on Windows:

```bash
uv run python scripts/ci/check_repo_hygiene.py
uv run ruff check src tests
uv run ruff format --check src tests
uv run pyright src
uv run python -m pytest -q tests/test_browser_manager.py
uv run python -m pytest -q <top-level tests/test_*.py files>
uv run python -m pytest -q tests/api
uv run python -m pytest -q tests/auth tests/cli
uv run python -m pytest -q tests/data tests/features tests/image_batch
uv run gflow --version
```

Observed:

- repo hygiene: 292 tracked files checked, no violations
- ruff check: all checks passed
- ruff format: 128 files already formatted
- pyright: 0 errors, 0 warnings, 0 informations
- `tests/test_browser_manager.py`: 52 passed, 4 skipped
- top-level `tests/test_*.py`: 190 passed, 4 skipped
- `tests/api`: 478 passed, 1 skipped
- `tests/auth tests/cli`: 170 passed
- `tests/data tests/features tests/image_batch`: 92 passed
- `gflow --version`: `gflow, version 0.9.1`

## Paid live smoke

Before using the release for the public showcase, run one real-account smoke:

```bash
gflow video i2v .\start.png "slow cinematic transition" --end-image .\end.png --aspect 9:16 --model omni-flash --duration 4 --profile <profile>
```

Pass criteria:

- both Start and End frames attach in Flow;
- `ui_automation_video.frame_attached` appears for both slots;
- one MP4 is downloaded;
- the file is playable and matches the requested vertical aspect.

## Conclusion

v0.9.1 is the right release line for the Compiled Growth showcase because it is
the first patch version intended to promote the fixed I2V start/end-frame
workflow as a released capability rather than a development-branch behavior.
