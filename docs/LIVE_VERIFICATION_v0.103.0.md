# Live verification — v0.103.0

Release ledger for v0.103.0, a minor release and the first beta. It covers one feature merged into `develop` since v0.102.1, #1375 slice 1 (PR #1380), plus documentation-only changes (#1370, #1373, #1377, #1378) that touch no Flow surface.

## Environment

| | |
|---|---|
| Profiles | A funded profile for billed runs; a credit-limited profile (50 credits, below Veo 3.1 Quality's 100) for the insufficient-credits run |
| Host Flow served | `flow.google.com`, classic composer on both profiles |
| Entrypoint | CLI & MCP |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Trees | `feature/1375-model-retirement` at the commits named per row; `develop` at the merge `7883770c`; `chore/release-v0.103.0`, cut from `7883770c`, for the plain `video t2v` |
| Engine | playwright (default) |
| Veo credits spent | 14 video generations: 13 while verifying #1375 (4 Omni 1.1, 2 Omni 1.2, 7 veo-lite), 1 for the release-tree `video t2v` |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Retirement warning on a leaving model, CLI (#1375) | `tests/e2e/test_model_retirement_e2e.py` (CLI `video t2v --model omni-flash --duration 4 --json`) | ✅ At `eff9abeb` and again on the merged head `091fb3dc`: exit 0, the mp4 landed (`ftyp`), stdout parsed as one JSON document, and stderr carried `model.retiring` with `2026-12-05` | 2 Omni 1.1 |
| Same, MCP (`gflow_generate_video`, `wait=true`) | same file | ✅ At `eff9abeb` and `091fb3dc`: `status: completed` and `warnings == [{"type": "model_retiring", "model": "omni_flash", "retires_on": "2026-12-05"}]`, file present | 2 Omni 1.1 |
| `retires_on` in the catalog (#1375) | `test_e2e_models_list_and_show_json`, `test_e2e_models_catalog_json_shape`; `gflow models list` on the release tree | ✅ 2/2 at `091fb3dc`; the release-tree table shows `retires on` `2026-12-05` for `omni_flash`, `veo_3_1_fast`, `veo_3_1_quality` and `-` for the others | $0 |
| A leaving model is never refused on the date; the CLI warns on the merged tree | `gflow video t2v --model veo-quality` on the credit-limited profile at `7883770c` | ✅ `model.retiring` logged; Flow still offers Veo 3.1 Quality, so the run reached submit and Flow showed its insufficient-credits warning: exit 37, `charged: no`, balance 50 before and after | $0 |
| e2e moved off leaving models (#1375 scenario 13) | Omni 1.2 resolution tests (transport-direct 720p; MCP queue 360p), data-layer t2v, i2v frame flags 1–3, prompt-tools t2v, all on `veo-lite` or Omni 1.2 | ✅ The paid run at `eff9abeb` passed 7 of 8 (counting the two retirement tests above). The data-layer t2v failed on an assertion that `flow_operation_id` equals the media id. On flow.google.com it is the workflow id (`migrated_composer.py:490`); fixed in `5c5ef5f7` and re-run: passed. Prompt-tools t2v passed at `091fb3dc` on the default Google endpoint (an override endpoint's key was out of quota) | 2 Omni 1.2, 7 veo-lite |
| Omni 1.1 tests gated to the date (`until_retired`) still run before it | Vietnamese duration radio, Omni r2v duration BDD | ✅ 5/5 (with the two catalog e2e above, 7/7) before the review rounds | $0 |
| Insufficient-credits e2e (gated on `veo-quality`) | `tests/e2e/test_insufficient_credits_e2e.py` on the credit-limited profile at `7883770c` | ✅ 1 passed in 11 s; balance unchanged | $0 |
| Release tree, default engine (live-verify step 0) | `gflow video t2v --model veo-lite --aspect 16:9 --out-file <path> --json` on `chore/release-v0.103.0` | ✅ see "Release-tree run" below | 1 veo-lite |

### Release-tree run

`gflow, version 0.103.0`, funded profile, no `--project` (a fresh project). Exit 0 in 87 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`. The file is MP4 (`ftyp isom`), 3,053,239 bytes, h264 1280x720, 7.92 s. Stderr was empty: no retirement warning for a model that stays, and nothing beside the JSON document.

## Pre-tag gates

- `/gflow:check` on `develop` at `7883770c`: every repository gate passes (hygiene, size, doc links, website PII, website mirror, council memory, plugin skills); `ruff` clean; `pyright` `0 errors`; `7394 passed, 16 skipped` in 248 s, coverage 93%.
- **PR #1380 CI** at `091fb3dc`: 21 checks passed, CodeRabbit skipped on the last (docs-only) commit, SonarCloud green.
- **Reviews:** a fresh `/code-review xhigh` and `/ponytail-review xhigh` with one fix round; `/gflow:pr-council-review` (7 reviewers over 12 dimensions) with one fix round; CodeRabbit's findings accepted and fixed, or declined with a reason on each thread.
- `/gflow:doc-review`: _Council verdict: YELLOW, YELLOW, YELLOW; no Tier 1 findings. Fixed in the release prep: the CHANGELOG and README name the replacement models next to the date; USAGE documents `retires_on` in `gflow models` and the omitted-`--model` limit; the gflow-cli skill notes the retirement and the video-production note points at omni-1.2; `lhm.plugin.json` tool descriptions name `retires_on` and `model_retiring`; AGENTS.md lists `cli_video_reporting.py`; two INDEX anchors predating this release are repaired. Tier 3, not changed: a retirement hint in the CLI `--model` help. Council reports at `tmp/council/0{1,2,3}-*.md` (local-only)._

## Post-tag evidence

- **Signed tag** `v0.103.0` on `b1d133c7` (SSH signature present).
- **`Release` workflow** run 44623129004: `build-and-publish` success, `mcp-registry / publish` success.
- **PyPI:** `gflow-cli 0.103.0` is the latest version, with `gflow_cli-0.103.0-py3-none-any.whl` (21:53:07Z) and `gflow_cli-0.103.0.tar.gz` (21:53:09Z), classifier `Development Status :: 4 - Beta`.
- **GitHub Release:** https://github.com/ffroliva/gflow-cli/releases/tag/v0.103.0, not a prerelease.

### What these runs do not prove

- **The exit-11 refusal that names the date was not observed live.** It fires only when Flow's menu no longer offers a leaving model; on 2026-11-19 Flow still offered all three (measured above). The refusal's text is covered offline (`tests/api/transports/test_retired_model_refusal.py`); the live check after 2026-12-05 is listed on #1379, including whether Flow removes the menu item or leaves it disabled.
- **No warning when `--model` is omitted on t2v/r2v.** gflow cannot read Flow's current default yet (slice 2), so a leaving default goes unannounced (`KNOWN_ISSUES.md`).
- **Windows and `en` accounts only.**
