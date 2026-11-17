# Live verification — v0.101.1

Release ledger for v0.101.1. It covers:
- The `patchright` extra pin moves from `1.61.2` to an exact `1.64.4`, so `locator.last` and `locator.nth(-1)` resolve a single match under `GFLOW_CLI_BROWSER_ENGINE=patchright` (#1319, PR #1321).
- A real-engine regression test (`tests/api/test_engine_locator_contract.py`) and a dedicated `engine-contract` CI job that installs the patchright extra and requires both engine cases to execute.

## Environment

| | |
|---|---|
| Profiles | `ffroliva` |
| Host Flow served | `flow.google.com` |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Tree | `bugfix/1319-patchright-pin` at the fix commit `25e21af5` (merged in #1321 as `f2d1c747`) for the patchright runs; `chore/release-v0.101.1`, cut from `develop` (`f2d1c747`), for the plain `video t2v` |
| Engines | patchright `1.64.4` (fix runs); playwright `1.61.0` (default engine, release-tree `video t2v`) |
| Veo credits spent | Two veo-lite generations (one `video i2v`, one `video t2v`) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Engine regression, before the fix (#1319) | `tests/api/test_engine_locator_contract.py`, patchright `1.61.2`, system Chrome | ✅ reproduced: `{'filter.last': 0, 'filter.nth(-1)': 0, 'filter.last >> descendant': 0, 'plain.last': 0, 'plain.nth(-1)': 0}`; the playwright case passes | $0 |
| Engine regression, after the fix (#1319) | same test, patchright `1.64.4`, `-m engine_contract` | ✅ `2 passed`; `check_test_count.py`: `executed=2 (collected=2, skipped=0, errors=0), floor=2`; the CI `Browser-engine contract` job passed on PR #1321 | $0 |
| Settings pane under patchright (#1319) | CLI `gflow image t2i --model nano-pro --aspect 16:9 -n 1 --out-file <path> --json` | ✅ exit 0 in 90 s, `status: ok`, model `GEM_PIX_2`; file is JPEG (magic `ffd8ff`), 1376x768 | Daily image quota, 0 credits |
| Settings pane + frame picker under patchright (#1319) | CLI `gflow video i2v --initial-frame <t2i output> --model veo-lite --aspect 16:9 --out-file <path> --json` | ✅ exit 0 in 170 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`; file is MP4 (`ftyp`), h264 1280x720, 7.93 s; log carries `migrated.settings_applied`, `migrated.frame_uploaded`, `migrated.frame_bound`, `migrated.submit_observed`, `migrated.download` | 1 veo-lite generation |
| MCP queued path under patchright (#1319) | `pytest tests/e2e/test_mcp_wait_false_bdd.py -m e2e_image` (real `gflow mcp run` over stdio, `wait=false`; the subprocess inherits `GFLOW_CLI_BROWSER_ENGINE`) | ✅ `2 passed in 79.80s` | Daily image quota, 0 credits |
| Release tree, default engine (live-verify step 0) | CLI `gflow video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` | ✅ `gflow, version 0.101.1`; exit 0 in 92 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`; file is MP4 (`ftyp`), h264 1280x720, 7.93 s; stderr empty (no teardown warning on the default engine) | 1 veo-lite generation |

An earlier `video i2v` attempt passed `--duration 4`, which veo-lite does not offer for this account. It exited 11 (`ConfigurationError`, `charged: no`) after reading **4 option groups** in the settings pane on patchright 1.64.4. The same pane read 0 on 1.61.2.

## Pre-tag gates

- `/gflow:check` on the release tree: all repository gates pass, `ruff` clean, `pyright`: `0 errors`; pytest `7243 passed, 14 skipped`, coverage 93.35%. The one other test, `tests/data/test_packaging.py::test_built_distributions_contain_sql_migrations`, failed only on a local uv-cache hardlink limit (`os error 1142`) and passed (`2 passed`) with `UV_LINK_MODE=copy`.
- `/gflow:doc-review`: _Council verdict: YELLOW across all 3 auditors, no Tier 1 findings. 6 findings: 5 Tier 2 fixed in the release-prep commit (upgrade instructions for the patchright extra in CHANGELOG, PROJECT_STATUS and CONFIGURATION; the milestone row order; the ledger's fix-commit reference; the `website/docs/CONFIGURATION.md` mirror; the unpinned `pip install patchright` hint) and 1 Tier 3 tracked as #1322 (`gflow update` does not re-install extras). Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

### What these runs do not prove

- The runs cover Windows only. The report came from macOS arm64; the defect is in the engine's locator resolution and reproduces off-Flow on Windows, but a macOS run of the fixed build was not made here.
- Every patchright run logged a `browser_teardown.context_close_error` warning: the bounded context close timed out, then the force close returned. It did not affect any result. The release-tree `video t2v` on the default playwright engine logged no such warning. Whether it also occurs on patchright 1.61.2 is unknown, because runs on that version fail before teardown.
- The silent post-upload hang recorded for playwright 1.62.0 did not recur in the one `video i2v` run on patchright 1.64.4. One run cannot rule out an intermittent hang.
