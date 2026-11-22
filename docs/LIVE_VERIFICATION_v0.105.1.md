# Live verification — v0.105.1

Release ledger for v0.105.1, a patch release. It covers complete catalog rows and superseded
local copies after `video extend` on `flow.google.com` (#1299, PR #1421, catalog migration
0011), Flow's own error on a failed generation (#1111, PR #1427), the free recovery for a
billed clip whose download fails (#1159, PR #1428), charge guidance for extend and
image-batch failures (#1416, PR #1420), the `lhm.plugin.json` tool list (#1407, PR #1426) and
two e2e expectations (#1383, #1408, PR #1425).

## Environment

| | |
|---|---|
| Profile | The default funded profile, served `flow.google.com`'s classic composer |
| Entrypoint | CLI |
| OS | Windows 11 (NTFS), system Google Chrome (`channel=chrome`) |
| Tree | `chore/release-v0.105.1`, cut from `develop` `f9b93228`, version bumped to 0.105.1 (`python -m gflow_cli, version 0.105.1`, imported from the release worktree) |
| Extras | `chain`, `tts` (`edge-tts`), `dev` |
| Engine | playwright (default) |
| Veo credits spent | 4 (one Omni 1.2 generation, the release-tree `video t2v`); balance 452 before, 448 after; every other run is $0 |

## Summary

| Change | Surface | Verified live? | Cost |
|---|---|---|---|
| Quote probe before the release-tree run | `video t2v "<prompt>" --model omni-1.2 --duration 4 --resolution 360p --count 1 --aspect 16:9 --max-credits 0 --json` | ✅ Exit 45, `CreditCapExceededError`, `quoted: 4`, `max_credits: 0`, `charged: "no"`, `next_action: "fix_input"`; balance unchanged | $0 |
| Release tree, default engine (live-verify step 0) | the same command with `--max-credits 4 --out-file t2v.mp4` | ✅ see "Release-tree run" below | 4 credits |
| Migration 0011 on an existing catalog (#1299) | a consistent copy (SQLite backup API) of the operator's v0.105.0 catalog, opened through `GFLOW_CLI_DB_PATH` by `gflow data list videos` | ✅ Before: `user_version` 10, migrations through `0010`, `local_files` without `superseded_at`. After the first command: `user_version` 11, a `0011_add_local_files_superseded_at.sql` row in `schema_migrations`, the `superseded_at` column and the partial index `idx_local_files_superseded_at` present, 0 rows with `superseded_at` set, row counts unchanged (1,773 assets, 1,745 local files). The operator's real catalog migrated the same way on the first release-tree command against it (`user_version` 11 afterwards) | $0 |
| `gflow data` readers on the migrated catalog (#1299) | `data list videos` / `images` / `projects` / `profiles` / `errors`, `data list videos --all-copies`, `data stats`, `data media <id>` | ✅ every reader exits 0 with nothing on stderr; `data list videos` returns 20 rows (the default limit) | $0 |
| Superseded copies hidden from the readers (#1299, read side) | on the catalog copy, one of the two local copies of a video asset marked superseded by hand, then `data media <id>`, `data list videos --all-copies --json`, `data list videos --json` | ✅ Before: `data media` listed `local_path_1` and `local_path_2`, `--all-copies` returned 2 rows. After: `data media` lists only the remaining copy, `--all-copies` returns 1 row, `copy_count` reads 1. `gflow doctor` (`GFLOW_CLI_WIRE_READS=0`) on the same copy: `db.migration_drift` and `catalog.sha256_null` pass; its other findings are warnings on the catalog's older history (missing names, missing files, stuck operations) | $0 |
| `video extend` completes its catalog row and supersedes copies on `flow.google.com` (#1299, write side) | — | ❌ Not verified live: every extend is a billed Veo generation, so the write path cannot be exercised without spending credits beyond the one release-tree run. Covered offline in PR #1421 (`tests/cli/test_cli_video_extend.py`, which drives the write and read paths on both the new-id and in-place branches) | — |
| Flow's error code and text on a failed generation (#1111) | — | ❌ Not verifiable on demand: it needs Flow to fail a generation, which cannot be induced. Covered offline in PR #1427 (the failed-record tests and the re-recorded composer contract) | — |
| Free recovery hint and `charged: yes` when a billed clip download fails (#1159) | — | ❌ Not verifiable on demand: it needs Flow to serve a non-MP4 body or an off-host signed URL for a finished clip. Covered offline in PR #1428 | — |
| Extend and image-batch charge guidance (#1416) | — | ❌ Not verifiable on demand: each path needs an extend reply or poll failure, or a batch integrity failure, that cannot be induced without billed extends. Covered by one offline regression test per path in PR #1420. The release-tree t2v above shows the unaffected success path | — |
| `lhm.plugin.json` lists the registered MCP tools (#1407) | the manifest on the release tree | ✅ 27 tools, version 0.105.1; `tests/mcp/test_server.py` pins the names to the server's registrations (passed in the parity suites below). No Flow surface | $0 |
| e2e credit-cap refusals pin 360p (#1408) | `tests/e2e/test_credit_cap_refusal_bdd.py -m "e2e and not e2e_video"`, the funded profile, `GFLOW_CLI_E2E_PROJECT` set to the release-tree t2v's project | ✅ CLI and MCP refusal scenarios: 2 passed. The third scenario, "completes when the quote equals max_credits", bills 4 credits and was deselected; the release-tree t2v above is the same case run by hand (360p quote 4, `--max-credits 4`, exit 0) | $0 |
| e2e t2i provenance accepts JPEG (#1383) | `tests/e2e/test_tools_e2e.py::test_t2i_with_tool_records_provenance`, `-m e2e`, the LLM endpoint variables passed in the environment | ⚠️ 1 failed, past the #1383 change: the image was written and accepted (Flow served JPEG), then `expanded_prompt not recorded`. A manual `image t2i --tool creative-director` run shows why: the LLM endpoint refused the call with 403 "Key limit exceeded" (`prompt_expander_failed`), so the tool fell back to the original prompt and recorded no expansion. The file is JPEG (`ff d8 ff e0`), 1,059,021 bytes, 1024x1024. The remaining assertions need an LLM key with quota; an operator account limit, not a gflow defect | $0 (image generation is free) |

### Release-tree run

`python -m gflow_cli, version 0.105.1`, the default funded profile, no `--project` (a fresh
project), no `GFLOW_CLI_MAX_CREDITS` in the environment, `--max-credits 4` set to the probed
quote. Exit 0 in 61 s, `MEDIA_GENERATION_STATUS_SUCCESSFUL`, request model `omni_1_2_flash`
(`omni-1.2` is a CLI alias of `omni-1.2-flash`), duration 4, count 1. The file is MP4
(`ftyp mp42`), 1,548,087 bytes, h264 640x360 at 24 fps with an AAC audio stream, 4.03 s.
Stderr was empty. The charge (4) equalled the quote (4). The catalog row reads
`MEDIA_GENERATION_STATUS_SUCCESSFUL`, `omni_1_2_flash`, landscape, with one local file
(1,548,087 bytes, sha256 recorded, `superseded_at` NULL); `data list videos` and
`data media` return it.

### Caveats

- **Windows, NTFS and an `en` account only.**
- **The superseded-copy check edits a catalog copy by hand.** It exercises the readers'
  filter on real catalog data, not the extend write path that sets the column.

## Pre-tag gates

- `/gflow:check` on `develop` `f9b93228`, the tree the release branch was cut from: every
  repository gate passes (hygiene, size, doc links, website PII, website mirror, council
  memory, plugin skills); the CLI/MCP parity suites report 72 passed; `ruff check` and
  `ruff format --check` clean. The pylint duplication proxy (report only) reports the same
  six findings as on the `v0.105.0` tree; only the line range of the
  `data/_recording_video.py` pair moved.
- On the release tree after the version bump and the documentation fixes below: the same
  repository gates and `check_release_artifacts.py` pass, `ruff check` and
  `ruff format --check` are clean, `uv lock --check` passes, the parity suites report 72
  passed, and `uv run pyright src` reports 0 errors. The default suite the way CI runs it
  (`pytest -q -n auto --dist worksteal`, no coverage): 8118 passed, 18 skipped, 0 failed. The full suite and coverage gate: CI run 45008256814 on
  `f9b93228`, every test job green (3.11, 3.12, 3.13, the 3.13 shards and e2e, ProfileLease
  on three OSes), SonarCloud analysis passed. Its `sharpit` job failed once on
  `tests/test_e2e_env_redaction.py::test_truncated_gh_token_under_showlocals_shows_no_fragment`:
  the test asserts that the fragment `abcd` is absent from a `--showlocals` report, and the
  report carried a `MonkeyPatch` object address (`0x7a873abcdb80`) that contains it. The
  assertion matched a random memory address, not a leaked token; the re-run of the job
  passed, and the run concluded `success`.
- The CHANGELOG `[Unreleased]` block had no entry for PR #1425 (#1383, #1408); a `### Tests`
  entry crediting @aryankalantri (#1414, #1417) is added in the release prep.
- `/gflow:doc-review`: the mechanical pass is clean (version references, INDEX entry for
  this ledger, doc links, website PII and mirror, CHANGELOG footer, memory links;
  `KNOWN_ISSUES.md` has no entry for this release's issues). _Council verdict:
  completeness YELLOW, cross-reference RED, drift RED, on one shared finding, escalated
  before fixing. Fixed in the release prep:_
  - the RED finding: `docs/ERRORS.md` still said an extend that spent credits reads
    `charged: yes` even when no segment finished, which #1416 made false; it now states
    the typed error's values per path (`no` / `fix_input` for refused input, `maybe` /
    `reconcile` for an unfinished segment, the kept `reauth` / `fix_input` advice, and
    the flow.google.com extend);
  - `docs/ERRORS.md` raise-site overrides list the #1159 `WireFormatError` (`charged: yes`,
    `gflow data download <media_id>`) and the #1416 extend poll override;
  - `docs/USAGE.md` and `docs/DATA_LAYER.md` say `--all-copies` and the copy count leave
    out superseded copies, and `docs/DATA_LAYER.md` documents `local_files.superseded_at`
    (migration 0011);
  - the CHANGELOG names migration 0011 and the exit-16 refusal by an older gflow, the
    `video extend --json` change from `yes` to `maybe`, and the image batch integrity
    advice;
  - the INDEX cue says the #1299 write path was not run live because extends are billed;
  - the website mirror regenerated.

  Deferred (Tier 3): the `docs/USAGE.md` exit-7 row and video-failure text do not yet
  quote the #1159 recovery or the #1111 message, and `docs/DATA_LAYER.md` still calls the
  schema "fixed at v1" and points only at `0001_initial.sql`, both stale before this
  release.

## Post-tag evidence

_Filled after the signed tag is published._
