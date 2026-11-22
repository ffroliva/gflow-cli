# Live verification — v0.105.0

Release ledger for v0.105.0, a minor release. It covers `gflow character create --image`
/ `--body-image` on `flow.google.com` with its MCP twin `gflow_character_create` (#639,
PR #1405), no-replace output publishing (#1406, PR #1415), charge reporting for labs
pre-submit drift and multi-step video failures (#1336, PR #1413), and secret masking in
pytest reports (#1382, PR #1411).

## Environment

| | |
|---|---|
| Profiles | A funded profile served `flow.google.com`'s classic composer (the release-tree t2v); a separate test account (the character create e2e, so its throwaway project and characters stay out of the main account) |
| Entrypoint | CLI; MCP through the e2e scenario named in its row |
| OS | Windows 11 (NTFS only), system Google Chrome (`channel=chrome`) |
| Tree | `chore/release-v0.105.0`, cut from `develop` `5be0f846`, version bumped to 0.105.0 (`python -m gflow_cli, version 0.105.0`, imported from the release worktree) |
| Extras | `chain`, `tts` (`edge-tts`), `dev` |
| Engine | playwright (default) |
| Veo credits spent | 4 (one Omni 1.2 generation, the release-tree `video t2v`); balance 456 before, 452 after; every other run is $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Quote probe before the release-tree run | `video t2v "<prompt>" --model omni-1.2 --duration 4 --resolution 360p --count 1 --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 4`, `max_credits: 0`, `charged: "no"`; balance unchanged | $0 |
| Release tree, default engine (live-verify step 0) | the same command with `--max-credits 4 --out-file t2v.mp4` | ✅ see "Release-tree run" below | 4 credits |
| `character create --image FILE --body-image FILE`, CLI and MCP twin (#639) | `tests/e2e/test_character_create_from_files_bdd.py` (`-m e2e_character`), test account | ✅ 2 passed in 93 s on the release tree. The CLI scenario runs `gflow character create --project <fresh> --name Portable --image portrait.jpg --body-image body.jpg --voice aoede --personality "calm, precise"`; the MCP scenario calls `gflow_character_create` with the same inputs. Each then reads the project back through `character list` and finds "Portable" with voice `Aoede`, 2 images and the personality. Each scenario created one throwaway project (`e2e character from files`, via the free `gflow project create`) holding that one character, both left on the test account | $0 |
| No-replace publishing, `gflow audio narrate` (#1406) | `audio narrate "<text>" --provider edge --out-file <path> --json`, four cases | ✅ (1) a fresh path: exit 0, 15,840 bytes, MPEG audio frame header `ff f3`; (2) the same path again: exit 11, `ConfigurationError` "Narration output already exists", `charged: no`, the existing file unchanged at 15,840 bytes; (3) a zero-byte file created just before: exit 11, refused as possibly another run's in-progress publish ("retry after 60 s or delete it"), left at 0 bytes; (4) a zero-byte file dated 5 minutes back: cleared and published, exit 0, 11,232 bytes, `ff f3` | $0 |
| No-replace publishing, `gflow video assemble` (#1406) | `video assemble t2v.mp4 t2v.mp4 --voiceover n1.mp3 --out-file assembled.mp4 --json`, then the same four cases | ✅ (1) exit 0, `clips_count: 2`, `ducking_applied: true`, h264 640x360 at 24 fps, 8.0 s, 587,181 bytes; (2) the same path again: `VideoAssemblyError` "Output already exists", `charged: no`, the file unchanged; (3) a fresh zero-byte file: refused with the in-progress message, left at 0 bytes; (4) a zero-byte file dated 5 minutes back: cleared and published, MP4 (`ftyp isom`), 294,991 bytes | $0 |
| The no-hard-link fallback (platform no-replace renames) (#1406) | — | ❌ Not exercised live: the host has a single NTFS volume, which supports hard links, so the runs above took the hard-link path. The rename fallback is covered by the offline tests in PR #1415 (`tests/services/test_video_assembly_platform.py`) | — |
| Labs pre-submit drift reports `charged: no` (#1336) | — | ❌ Not verifiable: no available account is served the `labs.google` composer, and the change only affects selector drift raised before the submit. Covered by one offline test per composer phase in PR #1413 | — |
| Multi-step video failures report prior billed work (#1336) | — | ❌ Not verified live: reproducing it needs a billed video step followed by a failing one, which cannot be induced on demand without spending credits on deliberate failures. Covered offline in PR #1413 | — |
| Secrets masked in pytest reports (#1382) | — | ❌ Not applicable: a change to the test harness (`tests/_secret_scrub.py`, `tests/conftest.py`) with no runtime or Flow surface. Covered by `tests/test_e2e_env_redaction.py` | — |

### Release-tree run

`python -m gflow_cli, version 0.105.0`, funded profile, no `--project` (a fresh project),
no `GFLOW_CLI_MAX_CREDITS` in the environment, `--max-credits 4` set to the probed quote.
Exit 0 in 62 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, request model `omni_1_2_flash`
(`omni-1.2` is a CLI alias of `omni-1.2-flash`),
duration 4, count 1. The file is MP4 (`ftyp mp42`), 1,456,088 bytes, h264 640x360 at
24 fps with an audio stream, 4.03 s. Stderr was empty. The charge (4) equalled the quote
(4). This clip is the input of the `video assemble` rows above.

### Caveats

- **Windows, NTFS and an `en` account only.** The no-hard-link publishing path and the
  macOS and Linux rename primitives were not exercised on this host.
- **The character create e2e leaves its throwaway project and characters** on the test
  account (named `e2e character from files`); gflow deletes nothing.

## Pre-tag gates

- `/gflow:check` on `develop` `5be0f846`, the tree the release branch was cut from: every
  repository gate passes (hygiene, size, doc links, website PII, website mirror, council
  memory, plugin skills); `ruff check` and `ruff format --check` clean; `pyright` 0 errors.
  The CLI/MCP parity suites run on their own reported 70 passed, 1 failed:
  `test_mapped_tools_are_registered` did not find `gflow_character_create`, because
  `tests/mcp/conftest.py` pre-imported every auxiliary MCP tool module except
  `character_tools`. The server itself registers the tool at boot, and the full suite passed
  because another module imports it first. Fixed on the release branch by adding that
  import (commit `fix(tests): register character tools in the MCP test conftest`); the
  parity suites then reported 71 passed. The full suite and coverage gate: CI run
  44985902875 on `5be0f846`, every job green (3.11, 3.12, the 3.13 shards and e2e,
  ProfileLease on three OSes), SonarCloud analysis passed. The pylint duplication proxy
  reports six similar-line pairs (report only), as in v0.104.0.
- The shipped slice-3 plan (`docs/superpowers/plans/2026-11-21-987-s3-character-create-from-files/`,
  status COMPLETED, referenced nowhere) is removed in the release prep; the wire spike it
  cites stays.
- On the release tree after the version bump and documentation fixes: the same repository
  gates and `check_release_artifacts.py` pass, `ruff check` and `ruff format --check`
  are clean, and `uv run pyright src` reports 0 errors. The default suite the way CI runs
  it (`pytest -q -n auto --dist worksteal`, no coverage): 8081 passed, 18 skipped, 0
  failed. A first run with an added `--basetemp` under a short directory reported 15
  failures, all in `tests/test_e2e_env_redaction.py`; that module's inner pytest runs
  fail under a custom `--basetemp` (reproduced serially, 1 failed with `-x`) and pass the
  CI way (24 passed), so the failures belong to the extra flag, not to the tree. `uv build`
  produces the `gflow_cli-0.105.0` sdist and wheel.
- `/gflow:doc-review`: _three auditors: completeness YELLOW, drift YELLOW, cross-reference
  RED on one finding, escalated before fixing. Fixed in the release prep:_
  - the RED finding: `docs/ERRORS.md` still said the `labs.google` arm was unchanged and
    limited pre-submit `charged: no` to `flow.google.com`, which #1336 made false;
  - the CHANGELOG #1336 line now scopes "an in-flight extend segment stays `maybe`" to the
    raised error (the `video extend --json` result reads `yes` once credits were spent),
    and the #1406 line names the destination filesystem's clock for the 60 s threshold, as
    the code measures it;
  - `docs/USAGE.md` scopes the "costs credits" banner and the resume guarantee to
    `--face-prompt`, and says the `--image` path is not retryable;
  - the agent skill names the MCP twin `gflow_character_create` and the "run `character
    list` before re-running" rule; `llms.txt` lists `character create --image` among the
    `flow.google.com` capabilities; `docs/MCP.md` notes the 60 s empty-file clearing for
    `gflow_video_assemble`;
  - the website mirror and the plugin skill copy regenerated.

  The remaining Tier 2 findings (README quick start, the USAGE `character create` option
  block, the `labs.google` refusal of `--image` in the docs, CAPABILITIES, CHARACTER,
  CONFIGURATION and INDEX routes, the MCP return shape and no-spend list, a service
  docstring, the `e2e_character` marker text, `lhm.plugin.json`) are tracked in
  [#1407](https://github.com/ffroliva/gflow-cli/issues/1407).

## Post-tag evidence

- **Signed tag** `v0.105.0` (tag object `9bb9634e`) on `309ac34c` (SSH signature present).
- **`Release` workflow** run 44987844098: `build-and-publish` success, `mcp-registry / publish` success.
- **PyPI:** `gflow-cli 0.105.0`, with `gflow_cli-0.105.0-py3-none-any.whl` (05:05:37Z) and `gflow_cli-0.105.0.tar.gz` (05:05:38Z).
- **GitHub Release:** https://github.com/ffroliva/gflow-cli/releases/tag/v0.105.0, not a prerelease.
