# Live verification — v0.107.0

Release ledger for v0.107.0, a minor release. It covers the OpenAI-compatible
`POST /v1/images/generations` on `gflow serve` (#1365, PR #1466), video-to-video edit
(#1467), `video extend` on `flow.google.com` with a local source, `--duration`, `--ref` and a
quote-checked `--max-credits` plus its MCP twin (#1468), `direct_http` counts 2-4 with one
reference or character (#1455, PR #1482), caption position/margin/size/opacity and their
follow-ups (#1432, PR #1475; #1488, PR #1494), short image replies reported (#1489, PRs #1504
and #1508), `isError` from one failure set shared with telemetry (#1464, PR #1479), refusing a
folder or unresolvable `--ref`/frame path (PR #1480), owner-only `gflow.db` (#1437, PR #1493)
and its connection cleanup (#1495, PR #1506), `clip_qa.py` scratch files and truncated clips
(#1501, #1502, PR #1503), the removal of the uncalled `_check_logged_in` (#1289, PR #1481), image thinking level and a 4K video upscale scale (PR #1460), MCP `isError` for failed tools (#1453, PR #1463), media trash for images (#1451, PR #1465), `doctor` before a database exists (PR #1473) and every image file for MCP `count > 1` (#1469, PR #1474).

## Environment

| | |
|---|---|
| Profile | The funded profile served `flow.google.com`'s classic composer (Extend available); a second profile served `flow.google.com` for the image, auth and direct-transport runs |
| Entrypoint | CLI, the MCP server (`mcp.Client`), the MCP worker, and the `gflow serve` HTTP route through the official `openai` SDK |
| OS | Windows 11 (NTFS), system Google Chrome (`channel=chrome`) |
| Tree | `chore/release-v0.107.0`, cut from `develop` `d422691e`, version bumped to 0.107.0 (`gflow_cli.__version__` reads 0.107.0 from the release worktree) |
| Engine | playwright (default) |
| Veo credits spent | 4 for the release-tree `video t2v` (balance 240 before, 236 after); the paid extend and edit e2e runs below spent their quoted extend and edit costs on the PR heads before the cut |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Quote probe before the release-tree run | `video t2v "<prompt>" --model omni-1.2 --duration 4 --resolution 360p --count 1 --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 4`, `max_credits: 0`, `charged: no` | $0 |
| Release tree, default engine (live-verify step 0) | the same command with `--max-credits 4 --out-file t2v.mp4` | ✅ see "Release-tree run" below | 4 credits |
| OpenAI-compatible image route (#1466) | `tests/e2e/test_openai_images_api_e2e.py`, `-m e2e` | ✅ 2 passed on the merged head: a plain request, and `n=2` through the official `openai` SDK with `max_retries=0` returning two images; unit, SDK-contract and `gflow serve` subprocess layers run in CI | $0 (daily image quota) |
| Local-source extend, duration, references, cap (#1468) | `tests/e2e/test_video_extend_migrated_e2e.py`, `tests/e2e/test_video_extend_mcp_e2e.py` | ✅ $0 tier 3 passed: CLI `--max-credits 0` exit 45 with nothing submitted; the same through MCP and the worker; a local upload with `--duration 5 --ref` whose checked submit carries the 5 s key, the ordered reference ids and the source workflow, then aborted. Paid tier 4 passed: a local-upload extend with a reference, the MCP paid twin returning the downloaded file, and catalogued-clip extends including a clip extended twice. A heavily extended source clip was correctly refused with exit 35 (Flow disables Extend once a clip reaches its maximum length) | paid tier: one quoted extend per test |
| Video-to-video edit (#1467) | `tests/e2e/test_video_edit_migrated_e2e.py` | ✅ $0 tier 5 passed: CLI and MCP zero-cap refusal with nothing submitted, the request check on an existing clip without references, a local-file edit whose checked `jIps6` targets the source and carries the uploaded reference, and the refusal (exit 11, `charged: no`) for an existing clip with references. Paid: one edit of an existing clip, passed | paid: one quoted edit |
| `direct_http` counts 2-4 (#1482) | `tests/e2e/test_direct_image_e2e.py -k count_two`, `tests/e2e/test_direct_image_worker_e2e.py -k count_two` | ✅ 2 passed and 1 passed, every image from `direct_http`, on a profile with no proxy configured | $0 |
| Short image replies (#1504, #1508) | `tests/e2e/test_direct_image_e2e.py`, `tests/e2e/test_direct_image_worker_e2e.py`, `tests/e2e/test_out_file_mcp_e2e.py`, `-m "e2e and e2e_image"` | ✅ 16 passed: full replies carry no `images_missing` warning on the direct and UI transports, CLI and worker. A genuinely short reply cannot be triggered on demand; it is covered offline | $0 |
| Caption options and follow-ups (#1475, #1494) | `tests/e2e/test_video_captions_e2e.py`, `-m e2e_media` | ✅ 5 passed; 2 skipped: the transcription tests need a Whisper endpoint (`GFLOW_CLI_E2E_TRANSCRIBE_BASE_URL`), and this release does not change transcription | $0 |
| `isError` shared failure set (#1479) | `tests/e2e/test_auth_status_mcp_e2e.py`, `-m e2e_auth` | ✅ 2 passed, including a test through `mcp.Client`, the only path that sets `isError` | $0 |
| Owner-only `gflow.db` (#1493), connection cleanup (#1506) | the real CLI and MCP on Windows and Linux; CI ProfileLease on Windows, Ubuntu and macOS | ✅ No Flow surface; macOS ProfileLease passed on the first develop run after the merge | $0 |
| Folder `--ref`/frame path (#1480), `_check_logged_in` removal (#1481), `clip_qa.py` (#1503) | argument validation, dead-code removal, a local video-production checker | Not a Flow surface; covered by the suite | — |
| Image thinking level (#1460) | `image t2i "<prompt>" --thinking-level high --out-file think.png --json` on the release tree | ✅ Exit 0, `status: ok`, model NARWHAL, one 768x1376 portrait image saved as JPEG (Flow returned JPEG; the extension follows the bytes) | $0 (daily image quota) |
| Video upscale to 4K (#1460) | `video upscale <media-id> --scale 4k --project <id>` on the release tree | ✅ Exit 22, `UpscaleUnavailableError`, naming the scales Flow listed for the item (270p, 720p); balance unchanged (236). A successful 4K export was not produced: Flow does not offer 4K to this account for this item, which is an account-plan blocker, not something gflow controls | $0 |
| Media trash and restore of an image (#1465) | `media trash <image-id> --yes --json`, then `media restore <image-id> --json` | ✅ `status: trashed, archived: true, charged: no`, then `status: restored, archived: false, charged: no` | $0 |
| `doctor` before a database exists (#1473) | `GFLOW_CLI_DB_PATH=<absent> gflow doctor --json` | ✅ Exit 0; catalog checks report `info` ("no catalog database yet") instead of a `DataStoreError` failure | $0 |
| MCP `count > 1` returns every image file (#1474) | `tests/e2e/test_out_file_mcp_e2e.py` (in the 16-test image run above) | ✅ Passed | $0 |

### Release-tree run

`gflow_cli.__version__` 0.107.0, the funded profile, no `--project` (a fresh project),
`--max-credits 4` set to the probed quote. Exit 0 in 65 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`,
request model `omni_1_2_flash` (`omni-1.2` is a CLI alias of `omni-1.2-flash`), mode `t2v`,
duration 4, count 1, landscape. The file is MP4 (`ftyp mp42`), 1,523,091 bytes, h264 640x360
at 24 fps with an AAC audio stream, 4.03 s.

## Pre-tag gates

- `/gflow:check` on the release tree: repository hygiene, size, doc links, website PII, website mirror, council memory, plugin skills, `ruff check`, `ruff format --check` and `pyright src` (0 errors) pass. The offline suite passes; 15 failures in `tests/test_e2e_env_redaction.py`, present since v0.106.0 whenever `--basetemp` sits outside the repository, were fixed before the tag (PR #1510).
- `/gflow:doc-review`: completeness, cross-reference and drift lenses run; the findings (a duplicate CHANGELOG entry, missing entries for #1460, `video edit` absent from the command-surface summaries, the 4K upscale scale in the skill) were fixed in the release commit.
- `scripts/ci/check_release_artifacts.py` passes on the release commit.

## Post-tag evidence

Recorded after the tag: the `Release` workflow run, the PyPI version and the GitHub Release.

## Not verified

- **Reference images when editing an existing clip** are refused with exit 11 (`charged: no`):
  the clip editor's `@` opens an asset browser in which a freshly uploaded image was not
  observed listed (spike: `docs/superpowers/spikes/2026-11-25-clip-editor-references-asset-browser.md`).
  Tracked in #1507, together with the clip editor's credit-quote label, which is unmeasured; a
  credit cap on editing an existing clip currently refuses.
- **A genuinely short image reply** (#1489) cannot be produced on demand from Flow; the
  reporting paths are covered offline, and the live runs above confirm full replies are not
  misreported.
