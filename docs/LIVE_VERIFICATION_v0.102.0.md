# Live verification — v0.102.0

Release ledger for v0.102.0. It covers the fixes and additions merged into `develop` since v0.101.2: #1320, #1322, #1326, #1332, #1333, #1334, #1335, #1338, #1343, #1346, #1347 and the `driver-watch` job.

## Environment

| | |
|---|---|
| Profiles | `ffroliva` (funded), `denon82` (low balance, for #1334) |
| Host Flow served | `flow.google.com` |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Trees | each change on its own branch before merge (commit named per row); `chore/release-v0.102.0`, cut from `develop` (`54cee3ca`), for the plain `video t2v` |
| Engine | playwright `1.61.0` (default) |
| Veo credits spent | paid extends for #1320, #1334 and #1346; video exports for #1338; one veo-lite generation (release-tree `video t2v`) |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| `gflow update` keeps installed extras (#1322, PR #1328) | `pytest -m e2e_update tests/e2e/test_update_installer_e2e.py` (local LOW/HIGH wheels, extra pinning a probe at `1.0+e2e` / `2.0+e2e`) | ✅ 6/6 at `83ac574f` (pip venv, pip `--user`, uv tool, pipx, extras pin, refuse while running); the extras-pin, user-site and uv cases re-run 4/4 after the review round at `e35eb1c0` | $0 |
| `charged: no` before a flow.google.com submit (#1320, PR #1329) | `tests/e2e/test_video_extend_migrated_e2e.py`, `test_migrated_host_e2e.py`, `test_migrated_image_character_mention_bdd.py`, `test_migrated_upscale_e2e.py` | ✅ at `9e3b019c`: extend 4/4 (zero-cost contract, refusal before any click, guard abort, one paid extend), MCP i2i with a reference (guarded submit), t2i, character mention 4/4; at `6be65df6` the guarded image path again; image upscale 2K (CLI and MCP), 270p GIF export and reference upload passed | paid extend, quota |
| Weekly report counts engine and host (#1326, PR #1331) | `GFLOW_CLI_E2E_RUN_TELEMETRY_UPLOAD=1 pytest -m e2e_telemetry tests/e2e` against the deployed endpoint | ✅ 14/14, 0 skipped, at `0340c001`; the live upload asserts `engines/patchright` and `hosts/flow` rows in D1 | $0 |
| `driver-watch` job (PR #1330) | `gh workflow run deps-watch.yml --ref chore/driver-watch -f dry_run=false`, run twice | ✅ run 44357602341 created the `driver-watch` label and issue #1337 (playwright 1.65.2); run 44357779970 planned no action | $0 |
| Upscale scale Flow does not list is exit 22 (#1338, PR #1341) | `tests/e2e/test_migrated_upscale_e2e.py`; CLI `gflow video upscale` | ✅ 5/5; a Veo 3.1 Lite clip at `--scale 1080p` exits 22 naming the listed scales; an Omni 1.2 Flash clip at `--scale 720p` exports an MP4 that passes the 720 short-side check | video exports |
| `@Character` image e2e reads the CLI's events (#1332, PR #1340) | `tests/e2e/test_migrated_image_character_mention_bdd.py` | ✅ 4/4 (CLI and MCP attach) | quota |
| Missing `@mention` is exit 32 (#1333, PR #1349) | new missing-character e2e + the existing mention e2e | ✅ CLI exit 32 naming the character; MCP `reference-not-found`, `charged: no`; existing mention scenarios 4/4 | quota |
| Disabled Extend item is exit 35 (#1334, PR #1348) | `tests/e2e/test_video_extend_migrated_e2e.py` on the low-balance and funded profiles | ✅ low balance: CLI exits 35 in about 10 s, nothing sent; funded: 4/4 including a paid extend | paid extend |
| Extend a clip that was already extended (#1346, PR #1351) | `tests/e2e/test_video_extend_migrated_e2e.py` including the new extend-twice case | ✅ module 5 passed with three paid extends; after the review round the contract and extend-twice cases passed again on another extended clip | paid extends |
| Worker failure names its class (#1343, PR #1345) | `tests/worker/test_daemon.py` | Offline only: the change adds a field to an error record built in the worker; an untyped worker failure cannot be produced on demand live | $0 |
| Failure after the submit is `indeterminate` (#1347, PR #1350) | `tests/worker/test_daemon.py` | Offline only: the change classifies a worker failure record; it touches no Flow surface | $0 |
| CI browser cache (#1335, PR #1342) | CI run 44387711147 | ✅ cache miss: Install Chromium 19-270 s; cache hit: restore 2-5 s, Install Chromium 10-14 s; the macOS lease leg runs on pushes only | $0 |
| Release tree, default engine (live-verify step 0) | `gflow video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` | ✅ `gflow, version 0.102.0`; exit 0 in 94 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`; file is MP4 (`ftyp`), h264 1280x720, 7.93 s; stderr empty | 1 veo-lite generation |

## Pre-tag gates

- `/gflow:check` on `develop` (`54cee3ca`): all repository gates pass, `ruff` clean, `pyright` `0 errors`, `7331 passed, 14 skipped` in 198 s, coverage 93.45%.
- Reviews: every PR had a fresh `/code-review xhigh` and `/ponytail-review xhigh` with one fix round, then `/gflow:pr-council-review` and `/gflow:sonar` green before merge.
- `/gflow:doc-review`: _Council verdict: YELLOW, YELLOW, YELLOW; no Tier 1 findings. 8 Tier 2 fixed in the release-prep commit: the one-time manual upgrade for pip installs at 0.101.2 and below (`docs/USAGE.md`); the notice-version reset in every region (`docs/TELEMETRY.md`); image upscale exit 22 for an unlisted scale (`docs/USAGE.md`, `docs/MCP.md`); how `tasks/get` reads an `indeterminate` job (`docs/MCP.md`); the ledger cost in `docs/INDEX.md`; #1332 and #1335 in the milestone row; the `docs/INDEX.md` size in `CLAUDE.md`; `lhm.plugin.json` bumped from 0.99.0 and added to the release skill's version sites. Tier 3, not changed: a #1334 clause in the exit-code table, a contributor note that the macOS lease leg runs on pushes only, and a telemetry mention in `README.md`. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

Recorded after the tag is published: the `Release` workflow run, the PyPI upload and the GitHub Release.

### What these runs do not prove

- The runs cover Windows only.
- #1334: whether the disabled Extend item follows the account's plan or its balance is not settled; the low-balance account showed a plan-upgrade link and no credits warning.
- #1338: the scale offer per model was measured on one profile; whether it also depends on the account tier is open (#1344).
- #1343 and #1347 are verified offline only, for the reasons in their rows.
