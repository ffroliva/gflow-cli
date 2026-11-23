# Live verification — v0.106.0

Release ledger for v0.106.0, a minor release. It covers the experimental `direct_http` image
transport and the `transports` counter in the weekly usage report (#1442), the caption box
centring (#1431, PR #1444) and wrapping of text without spaces (#1446, PR #1447), `image batch`
failures on `flow.google.com` (#1452, PR #1454), the new-project click timeout (#1434, PR #1435),
the `docs/ERRORS.md` confirmation-required row (#1407, PR #1441) and the `--showlocals`
redaction test (#1429, PR #1440).

## Environment

| | |
|---|---|
| Profile | The default funded profile, served `flow.google.com`'s classic composer |
| Entrypoint | CLI, and the MCP worker for the queued direct run |
| OS | Windows 11 (NTFS), system Google Chrome (`channel=chrome`) |
| Tree | `chore/release-v0.106.0`, cut from `develop` `d8b1cbde`, version bumped to 0.106.0 (`gflow_cli.__version__` reads 0.106.0 from the release worktree) |
| Engine | playwright (default) |
| Veo credits spent | 4 (one Omni 1.2 generation, the release-tree `video t2v`); balance 466 before, 462 after; every other run is $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Quote probe before the release-tree run | `video t2v "<prompt>" --model omni-1.2 --duration 4 --resolution 360p --count 1 --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 4`, `max_credits: 0`, `charged: no` | $0 |
| Release tree, default engine (live-verify step 0) | the same command with `--max-credits 4 --out-file t2v.mp4` | ✅ see "Release-tree run" below | 4 credits |
| `direct_http` image transport (#1442) | `tests/e2e/test_direct_image_e2e.py` and `tests/e2e/test_direct_image_worker_e2e.py`, `-m e2e`, `GFLOW_CLI_E2E_RUN_CHARACTER=1` | ✅ 9 passed in 548 s: plain t2i, three landscape images from one direct post, a model with no measured wire name and a catalogued reference routed to the UI transport, a named character, a bare `@Name` mention, a local file reference uploaded by one direct `maseQ` request, a `gflow run` row referencing an earlier row, and the MCP worker running a queued t2i on the direct transport | $0 |
| `transports` key accepted by the telemetry endpoint (#1442) | the deployed Worker (`wrangler deployments list`) | ✅ Version `dd82a998` from `dba6aeb` ("transports counter map"), 100% of traffic since 2026-11-23 09:45Z, before this release. The endpoint's own tests cover the key; a live upload carrying `transports` was not sent, because the live upload e2e asserts only `engines` and `hosts` | $0 |
| Caption box centring and wrapping (#1431, #1446) | `tests/e2e/test_video_captions_e2e.py`, `-m e2e` | ✅ 3 passed (installed CLI burns an authored SRT, the MCP tool burns inside the output directory, the default-endpoint error reaches CLI and MCP); 2 skipped: the transcription tests need a Whisper endpoint (`GFLOW_CLI_E2E_TRANSCRIBE_BASE_URL`) that is not configured here, and this release does not change transcription | $0 |
| `image batch` on `flow.google.com` (#1452) | `gflow image batch prompts.tsv --profile <funded>` (one prompt) | ✅ Exit 36, `FlowHostMigratedError` with its hint on stdout, no traceback on stderr | $0 |
| New-project click timeout (#1434) | — | ❌ Not verifiable on demand: it needs Flow's create button to render and refuse the click. Covered offline in PR #1435 | — |
| `docs/ERRORS.md` row (#1407), redaction test (#1429) | documentation and a unit test | Not a Flow surface; the test passes in the suite below | — |

### Release-tree run

`gflow_cli.__version__` 0.106.0, the default funded profile, no `--project` (a fresh
project), no `GFLOW_CLI_MAX_CREDITS` in the environment, `--max-credits 4` set to the probed
quote. Exit 0 in 63 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, request model `omni_1_2_flash`
(`omni-1.2` is a CLI alias of `omni-1.2-flash`), duration 4, count 1, landscape. The file is MP4
(`ftyp mp42`), 1,484,597 bytes, h264 640x360 at 24 fps with an AAC audio stream, 4.03 s.
Stderr was empty. The charge (4) equalled the quote (4).

### Caveats

- **Windows, NTFS and an `en` account only.**
- **The direct transport ran on one funded account.** The account reserved for $0 runs was
  signed in to Google but not to Flow on the day, so every run used the funded profile; image
  generation costs no credits on either.

## Pre-tag gates

- `/gflow:check` on the release worktree, started on `develop` `d8b1cbde` (the version bump
  landed while the suite ran; the version and manifest tests are re-run below): every repository gate passes
  (hygiene, size, doc links, website PII, website mirror, council memory, plugin skills),
  `ruff check` and `ruff format --check` are clean, `uv run pyright src` reports 0 errors, and
  the default suite (`pytest -q -n 8 --dist worksteal`, not live, e2e or smoke) reports
  8249 passed, 38 skipped, 0 failed. `uv lock --check` passes.
- `/gflow:doc-review`: the mechanical pass is clean (`check_release_artifacts.py`, doc links,
  repository hygiene, website mirror and PII, plugin skills; every version declaration site reads
  0.106.0). _Council verdict: YELLOW across all 3 auditors (completeness, cross-reference, drift),
  no release-blocking finding. Fixed in the release prep:_
  - `docs/DIRECT_TRANSPORT.md` said a `gflow run` config could name `direct_http`; that route, and
    `--transport direct_http`, also need `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS=1` (the config is
    refused with exit 11 without it). The doc now says so; the environment route needs nothing else;
  - the same doc's "a sent request is never retried" now names its one exception, the redirect /
    401 / 403 resubmission through `ui_automation`, which its own failure table describes;
  - the `--transport` help on `image upload`, `t2i` and `i2i`, and the `transport` setting's
    description, list `direct_http` among the experimental transports;
  - the CHANGELOG's pre-release note about the telemetry endpoint is replaced by the user-facing
    fact: uploads pause in every region until the new notice is shown, and the endpoint accepts
    the key.

  Deferred (Tier 3): `GFLOW_CLI_TRANSPORT` and `GFLOW_CLI_EXPERIMENTAL_TRANSPORTS` have no entry in
  `.env.template` or `docs/CONFIGURATION.md` (both predate this release), the `image batch` section
  of `docs/USAGE.md` does not repeat exit 36 from its exit table, and `docs/DIRECT_TRANSPORT.md` has
  no website mirror page.

## Post-tag evidence

POST_TAG
