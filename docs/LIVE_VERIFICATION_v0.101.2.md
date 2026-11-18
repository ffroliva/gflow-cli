# Live verification — v0.101.2

Release ledger for v0.101.2. It covers:
- The disabled-feature parse reads patchright 1.64's exclusion filter, so the merged `--disable-features` switch carrying `CloudSyncedFolderChecker` (#1140) is passed again under `GFLOW_CLI_BROWSER_ENGINE=patchright` (#1324, PR #1325).
- Every plain-Playwright launch site (sign-in, renewal, verification, cookies, experimental transports) reads Playwright's own feature list rather than the configured engine's.
- The `engine-contract` CI job pins the patchright bundle parse and requires all three engine contracts to execute.

## Environment

| | |
|---|---|
| Profiles | `ffroliva` |
| Host Flow served | `flow.google.com` |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Tree | `bugfix/1324-patchright-disable-features` (`8de44a70`, then `842780ff` after review; merged in #1325 as `c63fcdee`) for the patchright runs; `chore/release-v0.101.2`, cut from `develop` (`c63fcdee`), for the plain `video t2v` |
| Engines | patchright `1.64.4` (fix runs); playwright `1.61.0` (default engine, release-tree `video t2v`) |
| Veo credits spent | One veo-lite generation (release-tree `video t2v`) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Parse, before the fix (#1324) | `disable_features_args()` with `GFLOW_CLI_BROWSER_ENGINE=patchright`, patchright `1.64.4` | ✅ reproduced: `()` and `browser_manager.playwright_disabled_features_unreadable`; patchright `1.61.2` returned the merged switch | $0 |
| Parse, after the fix (#1324) | same call; `tests/test_browser_manager.py` | ✅ 18 features, `MediaRouter` excluded, `CloudSyncedFolderChecker` last; the default playwright parse is unchanged (17 features including `MediaRouter`); the CI `Browser-engine contract` job passed on PR #1325 with `executed=3` | $0 |
| Prompt close under patchright, CLI (#1324) | `gflow image t2i --model nano-pro --aspect 16:9 -n 1 --out-file <path> --json` | ✅ exit 0 in 36 s (32 s after the review round), `status: ok`; `browser_teardown.context_close_error` 0, force close 0, `disabled_features_unreadable` 0. On v0.101.1 the same run took 90 s with a close error and a force close | Daily image quota, 0 credits |
| Plain-Playwright launch under the patchright setting (#1324) | `gflow auth renew --profile <p> --json` | ✅ exit 0 in 24 s, `"result": "authenticated"`; close error 0, force close 0, unreadable 0 | $0 |
| MCP queued path under patchright (#1324) | `pytest tests/e2e/test_mcp_wait_false_bdd.py -m e2e_image` (real `gflow mcp run`, `wait=false`) | ✅ `2 passed in 56.24s` (79.80 s on v0.101.1) | Daily image quota, 0 credits |
| Release tree, default engine (live-verify step 0) | `gflow video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` | ✅ `gflow, version 0.101.2`; exit 0 in 90 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`; file is MP4 (`ftyp`), h264 1280x720, 7.93 s; stderr empty | 1 veo-lite generation |

## Pre-tag gates

- `/gflow:check` on the fix branch after the review round: all repository gates pass, `ruff` clean, `pyright` `0 errors`, `7251 passed, 14 skipped`, coverage 93.35%.
- Reviews: `/code-review xhigh` (9 findings; all applied in `842780ff` except the close-time claim, which the live rows above measure) and `ponytail-review xhigh` (Ship; the docstring wording was applied, while the "unneeded" type annotation stays because `pyright` requires it).
- `/gflow:doc-review`: _Council verdict: YELLOW, YELLOW, GREEN; no Tier 1 findings. 3 Tier 2 fixed in the release-prep commit: `CONTRIBUTING.md` describes the extended `engine-contract` job; cookie export is added to the launch-site list in CHANGELOG and PROJECT_STATUS; `docs/CONFIGURATION.md` tells patchright users to take 0.101.2. Tier 3, not changed: the old hint in a dev script and in a test fixture that supplies its own text. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

### What these runs do not prove

- The runs cover Windows only.
- The patchright close timing was measured on a short t2i and an auth renewal. A long video generation under patchright was not re-run in this cycle; its close uses the same launch arguments.
