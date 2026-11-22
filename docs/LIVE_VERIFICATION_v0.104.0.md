# Live verification — v0.104.0

Release ledger for v0.104.0, a minor release. It covers `gflow media trash` / `restore`
(#1363), `gflow audio narrate` (#1357), `gflow video assemble` (#1359), `gflow video
transcribe` / `captions` (#1358), the `--max-credits` cap (#1355), the agent-only driver
on Flow's 2026-11-20 build (#1387, PR #1403), and the macOS cookie-read fixes (#1396 via
PR #1397, #1400 via PR #1404). Documentation-only changes in the same range (#1390, #1392)
touch no Flow surface.

## Environment

| | |
|---|---|
| Profile | A funded profile served `flow.google.com`'s classic composer |
| Entrypoint | CLI; MCP through the installed-package e2e tests named per row |
| OS | Windows 11, system Google Chrome (`channel=chrome`) |
| Tree | `chore/release-v0.104.0`, cut from `develop` `cdd8ec7a`, version bumped to 0.104.0 (`python -m gflow_cli, version 0.104.0`, imported from the release worktree) |
| Extras | `chain`, `tts` (`edge-tts`), `dev` |
| Engine | playwright (default) |
| Veo credits spent | 10 (one veo-lite generation, the release-tree `video t2v`); balance 466 before, 456 after; every other run is $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Release tree, default engine (live-verify step 0) | `python -m gflow_cli video t2v "<prompt>" --model veo-lite --aspect 16:9 --out-file <path> --json` | ✅ see "Release-tree run" below | 10 credits |
| `--max-credits` refuses an over-limit quote before submit (#1355) | `video t2v --model veo-lite --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 10`, `max_credits: 0`, `charged: "no"`; `migrated.credit_quote_checked` logged; balance unchanged. The quote matched the 10 credits the plain t2v was then charged | $0 |
| The same refusal over MCP (#1355) | `tests/e2e/test_credit_cap_refusal_bdd.py`, CLI and MCP scenarios (omni-1.2, 4 s, x1, cap 3, submits aborted by a route backstop) | ⚠️ Both surfaces returned `CreditCapExceededError`, exit 45, `charged: no`, but both scenarios failed their next step: Flow quoted **12** where the feature file expects 4. The scenarios pass no `--resolution`, and this profile's composer is at 720p; a by-hand probe at `--max-credits 0` shows the quote tracks the request. See "Caveats" | $0 |
| `gflow media trash` / `restore` (#1363) | `media trash <fresh clip> --yes --json`, then `media restore <same> --json` | ✅ `status: trashed`, `archived: true`, `charged: no`, with a `restore_hint`; then `status: restored`, `archived: false`, `charged: no` | $0 |
| The same over CLI and MCP with listing read-back (#1363) | `tests/e2e/test_media_trash_e2e.py`, pointed at the fresh clip and its project | ✅ 6 passed | $0 |
| `gflow audio narrate`, Edge provider (#1357) | `audio narrate "<text>" --provider edge --out-file narration.mp3 --json` | ✅ Exit 0, `media_type: audio/mpeg`; 21,312 bytes, MPEG audio frame header `ff f3`, mp3 24 kHz mono, 3.55 s | $0 |
| The same from an installed wheel, CLI and MCP `--no-spend` (#1357) | `tests/e2e/test_audio_narrate_bdd.py` | ✅ 2 passed; the Gemini scenario skipped (no `GFLOW_CLI_GEMINI_TTS_API_KEY`) | $0 |
| `gflow video captions` with an authored `.srt` (#1358) | `video captions t2v.mp4 --srt boat.srt --out-file t2v-captions.mp4 --json` on the fresh clip | ✅ Exit 0; `frames_burned: 158` of 190; MP4 (`ftyp isom`), h264 1280x720 + aac, 7.93 s, 1,709,732 bytes. At t = 2 s the bottom third differs from the source frame by a mean 8.2 levels, the top third by 1.6 | $0 |
| `gflow video assemble` with voiceover, music and ducking (#1359) | `video assemble t2v.mp4 t2v-captions.mp4 --voiceover narration.mp3 --music narration.mp3 --out-file assembled.mp4 --json` | ✅ Exit 0; `clips_count: 2`, `ducking_applied: true`; MP4 (`ftyp isom`), h264 1280x720 at 24 fps + aac 44.1 kHz stereo, 15.84 s, 3,308,806 bytes | $0 |
| Captions, assemble and the transcription refusal from an installed wheel, CLI and MCP (#1358, #1359) | `tests/e2e/test_video_captions_e2e.py`, `tests/e2e/test_video_assemble_e2e.py` | ✅ 4 passed: assemble CLI + MCP, captions CLI, captions MCP, and Google's endpoint refused before audio is sent on CLI and MCP alike. Skipped: catalogue-id assembly, and both scenarios that need a Whisper-compatible endpoint | $0 |
| `gflow video transcribe` against a real endpoint (#1358) | — | ❌ Not verified: no OpenAI-compatible `/audio/transcriptions` endpoint is configured on this host (`GFLOW_CLI_TRANSCRIBE_MODEL` and `GFLOW_CLI_E2E_TRANSCRIBE_BASE_URL` unset) | — |
| Agent-only driver on the 2026-11-20 build (#1387) | — | ❌ Not verified on the release tree: no available profile is served the agent-only composer (no account without an Agent switch has been observed on that build). The driver was measured on a recoverable account held in agent mode, in both panel states, during PR #1403 ([spike](superpowers/spikes/2026-11-21-agent-settings-on-the-new-build.md)) | — |
| macOS Keychain prompt and `v11` cookie failures (#1396, #1400) | — | ❌ Not verifiable on this Windows host. The contributor's macOS 15 run is recorded on PR #1397; the Windows and Linux paths are unchanged | — |

### Release-tree run

`python -m gflow_cli, version 0.104.0`, funded profile, no `--project` (a fresh project),
no `GFLOW_CLI_MAX_CREDITS` in the environment. Exit 0 in 80 s,
`MEDIA_GENERATION_STATUS_SUCCESSFUL`, model `veo_3_1_lite`. The file is MP4 (`ftyp isom`),
2,278,370 bytes, h264 1280x720 at 24 fps with an audio stream, 7.93 s. Stderr was empty.
This clip is the input of the trash, restore, captions and assemble rows above, so no
existing library item was touched.

### Caveats

- **The #1355 e2e quote expectation.** `tests/features/credit_cap_refusal.feature` expects
  Flow to quote 4 credits for omni-1.2 at 4 s x1, the value measured in the
  [#1355 spike](superpowers/spikes/2026-11-20-credit-quote-before-submit.md). On this
  profile Flow quoted 12, twice. The scenarios pass no `--resolution`, so the quote is for
  whatever resolution the account's composer last used. Probed at `--max-credits 0`
  (every run refused before submit, $0), omni-1.2 quoted: 4 s x1 at 360p **4**, at 720p
  **12**; 4 s x2 **24**; 8 s x1 **24**. The quote follows count, duration and resolution as
  requested, so the settings bind before the quote is read; the fixture's 4 is the 360p
  price. On this profile the veo-lite quote (10) also equalled the credits the following
  generation was charged (10). The refusal held on both surfaces, charged nothing, and the
  balance stayed at 456. The scenarios should pin `--resolution 360p`
  ([#1408](https://github.com/ffroliva/gflow-cli/issues/1408)).
- **Windows and an `en` account only.**

## Pre-tag gates

- `/gflow:check` on `develop` `cdd8ec7a`, the tree the release branch was cut from: every
  repository gate passes (hygiene, size, doc links, website PII, website mirror, council
  memory, plugin skills); the CLI/MCP parity suites 70 passed; `ruff check` and
  `ruff format --check` clean; `pyright` 0 errors. The full suite and coverage gate: CI run
  44964920326 on `cdd8ec7a`, every job green (3.11, 3.12, the 3.13 shards and e2e,
  ProfileLease on three OSes), SonarCloud analysis passed. The pylint duplication proxy
  reports six similar-line pairs (report only); SonarCloud's duplication condition passed
  on that commit.
- On the release tree after the version bump and documentation fixes: the same repository
  gates, `check_release_artifacts.py`, and `ruff` pass again; the plugin-manifest,
  Dockerfile version-pin and smoke tests pass (21); the 44 test modules that read the
  changed documents pass (1486 passed, 2 skipped). The default suite on the release commit
  (`pytest -n 4`, no coverage): 7838 passed, 18 skipped, 11 failed; the 11 are incident-
  bundle tests that exceeded the Windows path limit under a long temporary directory, and
  their four modules pass with a short one (71 passed, 1 skipped). `pyright src` 0 errors;
  `uv build` produces `gflow_cli-0.104.0` sdist and wheel.
- `/gflow:doc-review`: _three auditors, YELLOW, no RED. Fixed in the release prep:_
  - the ledger and INDEX entry for this release;
  - a false MCP claim that rate limiting is the only spend brake and that `--no-spend` is
    still planned (`docs/MCP.md`, the `mcp/tools.py` module docstring);
  - `project_id` on the trash/restore tool signatures and `max_credits` on
    `gflow_generate_video` in `docs/MCP.md`;
  - exit 45 in the ERRORS legacy-to-core table;
  - the #1387 CHANGELOG headline, which read as if the agent-only composer were driven on
    every account;
  - `gflow media` in the AGENTS command surface and the agent skill; the `tts` and `chain`
    extras in the README, the agent skill and the CHANGELOG narrate entry; `--max-credits`
    in `llms.txt`;
  - contributor credit for #1396 and #1402 in the CHANGELOG.

  The remaining Tier 2 findings (architecture module list, USAGE synopsis and option
  blocks, INDEX routes, `lhm.plugin.json` tool list, Docker extras) are tracked in
  [#1407](https://github.com/ffroliva/gflow-cli/issues/1407).

## Post-tag evidence

- **Signed tag** `v0.104.0` (tag object `c6e37535`) on `f7c15da3` (SSH signature present).
- **`Release` workflow** run 44967494090: `build-and-publish` success, `mcp-registry / publish` success.
- **PyPI:** `gflow-cli 0.104.0`, with `gflow_cli-0.104.0-py3-none-any.whl` (00:37:37Z) and `gflow_cli-0.104.0.tar.gz` (00:37:38Z).
- **GitHub Release:** https://github.com/ffroliva/gflow-cli/releases/tag/v0.104.0, not a prerelease.
